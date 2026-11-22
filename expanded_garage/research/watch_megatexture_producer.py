"""Read the pinned offline FH4 MegaTexture input cache and its terrain rings."""

import argparse
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path
import struct
import time

from . import capture_grid_fault as native


QUEUE_VTABLES = (0x5DBFC20, 0x5DBFC58)
GRIDS = (0x3280, 0x27730)
INPUT = 0x90
LOOK = 0x100
TRACK_RTTI_VTABLE = 0x5B85128
CAMERA_RTTI_VTABLE = 0x5B72CE8
CAR_PRESENTATION_VTABLE = 0x5B849E0
CAR_SCENE_VTABLE = 0x55A7140


def wire_value(value):
    """Retain nonfinite values in JSON without nonstandard numeric tokens."""
    if isinstance(value, float) and not isfinite(value):
        return "NaN" if value != value else ("-Infinity" if value < 0 else "Infinity")
    if isinstance(value, (tuple, list)):
        return [wire_value(item) for item in value]
    if isinstance(value, dict):
        return {key: wire_value(item) for key, item in value.items()}
    return value


def decode_root(data):
    if len(data) < 0x174:
        raise ValueError("Incomplete MegaTexture input cache")
    return {
        "origin_i32": struct.unpack_from("<2i", data, INPUT),
        "view_direction_f32": struct.unpack_from("<4f", data, INPUT + 0x10),
        "motion_f32": struct.unpack_from("<4f", data, INPUT + 0x20),
        "filtered_direction_f32": struct.unpack_from("<4f", data, LOOK),
        "alternate_location_f32": struct.unpack_from("<4f", data, 0x120),
        "override_first_level": struct.unpack_from("<i", data, 0x130)[0],
        "override_frames": struct.unpack_from("<i", data, 0x134)[0],
        "override_add_offset": data[0x138],
        "offset_profile_f32": struct.unpack_from("<2f", data, 0xEC),
        "update_scale_f32": struct.unpack_from("<f", data, 0x170)[0],
        "input_bytes": data[INPUT:INPUT + 0x30].hex(),
    }


def grid_descriptor(data, signature):
    return (len(data) == 0x80 and data[8:16] == signature
            and struct.unpack_from("<I", data)[0] == 1023
            and struct.unpack_from("<4i", data, 0x68) == (-24, -24, 48, 48)
            and struct.unpack_from("<Q", data, 0x60)[0] != 0)


def find_root(target, base):
    first, second = (struct.pack("<Q", base + rva) for rva in QUEUE_VTABLES)
    address = 0x10000
    while address < 0x800000000000:
        region = target.region(address)
        if not region:
            break
        start, size = region["base"], region["size"]
        address = start + size
        if region["state"] != 0x1000 or region["type"] != 0x20000 or region["protect"] not in (4, 8):
            continue
        for offset in range(0, size, 0x800000):
            p = start + offset
            chunk = target.read(p, min(0x800000 + 7, size - offset))
            pos = chunk.find(first)
            while pos != -1:
                grid = p + pos - 8
                for delta in GRIDS:
                    root = grid - delta
                    if all(grid_descriptor(target.read(root + other, 0x80), first)
                           and target.read(root + other + 0x30, 8) == second for other in GRIDS):
                        return root
                pos = chunk.find(first, pos + 8)
    return None


def ring_snapshot(target, pointer):
    data = target.read(pointer, 0xE0)
    if len(data) != 0xE0:
        return None
    return {
        "address": hex(pointer),
        "previous_rectangle": struct.unpack_from("<4i", data, 0x58),
        "world_scale_and_mask": struct.unpack_from("<2i", data, 0x90),
        "origin_and_wrap": struct.unpack_from("<4i", data, 0xD0),
    }


def read_pointer(target, address):
    raw = target.read(address, 8)
    return struct.unpack("<Q", raw)[0] if len(raw) == 8 else 0


def camera_snapshot(target, root, base):
    world = read_pointer(target, root + 0x4BBE8)
    if not world or read_pointer(target, world) != base + TRACK_RTTI_VTABLE:
        return None
    camera = read_pointer(target, world + 0x1FEC8)
    if not camera or read_pointer(target, camera) != base + CAMERA_RTTI_VTABLE:
        return None
    matrix = target.read(camera + 0x20, 64)
    motion = target.read(camera + 0x190, 16)
    focused = read_pointer(target, camera + 0x2F0)
    if not len(matrix) == 64 or not len(motion) == 16:
        return None
    vtable = read_pointer(target, focused) if focused else 0
    pose, velocity = b"", b""
    scene_identity = None
    if vtable == base + CAR_PRESENTATION_VTABLE:
        pose = target.read(focused + 0xA0, 64)
        velocity = target.read(focused + 0x280, 16)
        scene = read_pointer(target, focused + 0x6D8)
        if scene and read_pointer(target, scene) == base + CAR_SCENE_VTABLE:
            state = target.read(scene + 0x88, 0x39)
            if len(state) == 0x39:
                ordinal, = struct.unpack_from("<I", state)
                size, capacity = struct.unpack_from("<QQ", state, 0x18)
                if 0 < size <= min(63, capacity):
                    name = state[8:8 + size] if capacity < 16 else target.read(
                        read_pointer(target, scene + 0x90), size)
                    if len(name) == size:
                        scene_identity = {"car_id": ordinal, "media_name": name.decode("ascii", errors="replace"),
                                          "state_byte": state[0x38]}
    blend_distance = target.read(camera + 0x160, 4)
    return {"matrix_bytes": matrix.hex(), "matrix_rows": list(struct.iter_unpack("<4f", matrix)),
            "motion_f32": struct.unpack("<4f", motion),
            "camera_blend_input_f32": struct.unpack("<f", blend_distance)[0] if len(blend_distance) == 4 else None,
            "camera": hex(camera), "focused_object": hex(focused) if focused else None,
            "focus_pose_bytes": pose.hex() if len(pose) == 64 else None,
            "focus_pose_rows": list(struct.iter_unpack("<4f", pose)) if len(pose) == 64 else None,
            "focus_velocity_f32": struct.unpack("<4f", velocity) if len(velocity) == 16 else None,
            "focus_velocity_bits": velocity.hex() if len(velocity) == 16 else None,
            "scene_identity": scene_identity,
            "focus_vtable_rva": hex(vtable - base) if base <= vtable < base + 0x9B10E00 else None}


def snapshot(target, root, base):
    data = target.read(root, 0x180)
    if len(data) != 0x180:
        return None
    rings = [ring_snapshot(target, address) if address else None
             for address in struct.unpack_from("<7Q", data)]
    return {"update": struct.unpack_from("<I", data, 0x110)[0],
            "input": decode_root(data), "rings": rings, "camera": camera_snapshot(target, root, base)}


def observe(host, pid, base, out, seconds, delay, known_root=None):
    native.kernel = c.WinDLL("kernel32", use_last_error=True)
    target = native.Target(pid, (host / "ForzaHorizon4.exe").resolve(strict=True))
    close = native.win("CloseHandle", w.BOOL, w.HANDLE)
    alive = native.win("GetExitCodeProcess", w.BOOL, w.HANDLE, c.POINTER(w.DWORD))
    status = w.DWORD()
    try:
        with out.open("x", encoding="utf-8", buffering=1) as stream:
            def emit(item):
                stream.write(json.dumps(wire_value({"utc": datetime.now(timezone.utc).isoformat(), **item}),
                                        allow_nan=False) + "\n")

            def running():
                return bool(alive(target.handle, c.byref(status))) and status.value == 259

            end = time.monotonic() + seconds
            emit({"event": "start", "pid": pid, "base": hex(base)})
            root = known_root
            if root is not None:
                for offset in GRIDS:
                    if not grid_descriptor(target.read(root + offset, 0x80),
                                           struct.pack("<Q", base + QUEUE_VTABLES[0])):
                        raise ValueError("Expected the verified MegaTexture object at the supplied address")
            while root is None and running() and time.monotonic() < end:
                root = find_root(target, base)
                if root is None:
                    time.sleep(1)
            if root is None:
                raise ValueError("The verified MegaTexture object was not found")
            emit({"event": "verified_manager", "manager": hex(root)})
            last = None
            packets = 0
            while running() and time.monotonic() < end and not out.with_suffix(".stop").exists():
                packet = snapshot(target, root, base)
                if packet is not None:
                    key = (packet["input"]["input_bytes"], packet["input"]["override_frames"],
                           tuple(tuple(r["origin_and_wrap"]) if r else () for r in packet["rings"]),
                           tuple(packet["camera"][field] for field in ("matrix_bytes", "focused_object", "focus_pose_bytes"))
                           if packet["camera"] else None)
                    if key != last:
                        emit(packet)
                        last = key
                        packets += 1
                time.sleep(delay)
            emit({"event": "end", "sampled_changes": packets, "process_alive": running()})
    finally:
        close(target.handle)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, type=Path)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--base", required=True, type=lambda n: int(n, 0))
    parser.add_argument("--known-root", type=lambda n: int(n, 0))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--delay", type=float, default=0.008)
    args = parser.parse_args()
    observe(args.host, args.pid, args.base, args.out, args.seconds, args.delay, args.known_root)
