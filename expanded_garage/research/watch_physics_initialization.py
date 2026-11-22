"""Observe allocations and physical field transitions in the pinned local FH4."""

import argparse
from collections import deque
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import threading
import time

from . import capture_grid_fault as native
from .watch_megatexture_producer import read_pointer


PHYSICS_VTABLES = {
    (0x5B63B38, None): "body-constructor",
    (0x5B63B90, None): "world-body",
    (0x5B666F8, 0x5B667A0): "dynamics-base",
    (0x5B640E8, 0x5B64190): "dynamics",
}
PHASE_BY_PRIMARY = {primary: phase for (primary, _), phase in PHYSICS_VTABLES.items()}
VEHICLE_VTABLE = 0x52D15A8
PRESENTATION_VTABLE = 0x5B849E0
SCENE_VTABLE = 0x55A7140
WATCH_BYTES = 0xB100
REPLAY_BYTES = 0x11070
WHEEL_START = 0x1DB0
WHEEL_STRIDE = 0x15E0
AERO_OFFSETS = (0x1C74, 0x1C78, 0x1C7C, 0x1C80, 0x1CB4, 0x1CB8, 0x1CBC, 0x1CC0)


def classify_f32(bits):
    if bits & 0x7F800000 != 0x7F800000:
        return "finite"
    return "nan" if bits & 0x007FFFFF else "infinity"


def vector_info(data, offset):
    bits = struct.unpack_from("<4I", data, offset)
    return {"bits": [f"{value:08x}" for value in bits],
            "y_class": classify_f32(bits[1])}


def motion_state(data):
    return {field: vector_info(data, offset) for field, offset in (
        ("linear_velocity", 0x20), ("angular_velocity", 0x30), ("position", 0x40))}


def motion_key(record):
    position = record["position"]["bits"]
    return (*(field["y_class"] for field in record.values()),
            position[3], all(bits in ("00000000", "80000000") for bits in position[:3]))


def wheel_sample(data):
    if len(data) < WHEEL_START + 3 * WHEEL_STRIDE + 0x128:
        return []
    return [
        {name: f"{struct.unpack_from('<I', data, WHEEL_START + corner*WHEEL_STRIDE + offset)[0]:08x}"
         for name, offset in (("travel_coordinate", 0), ("projected_rate", 4), ("world_point_y", 0x124))}
        for corner in range(4)
    ]


def memory_regions(target):
    address = 0x10000
    while address < 0x800000000000:
        region = target.region(address)
        if not region or region["size"] <= 0:
            return
        address = region["base"] + region["size"]
        if region["state"] == 0x1000 and region["type"] == 0x20000 and region["protect"] in (4, 8):
            yield region["base"], region["size"]


def discover(target, signatures, done, physics, vehicles):
    while not done.is_set():
        for address, length in memory_regions(target):
            for offset in range(0, length, 0x400000):
                if done.is_set():
                    return
                start = address + offset
                raw = target.read(start, min(0x400000 + 7, length - offset))
                for pattern, kind, secondary in signatures:
                    index = raw.find(pattern)
                    while index >= 0:
                        pointer = start + index
                        if pointer % 8 == 0 and (secondary is None or
                                read_pointer(target, pointer + 0x560) == secondary):
                            if kind == "vehicle":
                                vehicles.add(pointer)
                            else:
                                physics[pointer] = kind
                        index = raw.find(pattern, index + 8)
        done.wait(0.04)


def vehicle_link(target, base, pointer):
    if read_pointer(target, pointer) != base + VEHICLE_VTABLE:
        return None
    dynamics = read_pointer(target, pointer + 0x7D10)
    deferred = read_pointer(target, pointer + 0x7EF8)
    if not dynamics:
        return None
    presentation = read_pointer(target, deferred + 8) if deferred else 0
    scene = (read_pointer(target, presentation + 0x6D8)
             if presentation and read_pointer(target, presentation) == base + PRESENTATION_VTABLE else 0)
    fields = None
    if scene and read_pointer(target, scene) == base + SCENE_VTABLE:
        data = target.read(scene + 0x88, 0x39)
        if len(data) == 0x39:
            ordinal, = struct.unpack_from("<I", data)
            size, capacity = struct.unpack_from("<QQ", data, 0x18)
            if 0 < size <= min(63, capacity):
                name = data[8:8 + size] if capacity < 16 else target.read(
                    read_pointer(target, scene + 0x90), size)
                if len(name) == size:
                    fields = {"car_id": ordinal, "media_name": name.decode("ascii", errors="replace"),
                              "scene_state": data[0x38]}
    return dynamics - 0x560, {"vehicle": hex(pointer), "scene": fields}


def observe(host, pid, base, output, seconds, interval, accept_car=None):
    native.kernel = c.WinDLL("kernel32", use_last_error=True)
    target = native.Target(pid, (host / "ForzaHorizon4.exe").resolve(strict=True))
    close = native.win("CloseHandle", w.BOOL, w.HANDLE)
    alive = native.win("GetExitCodeProcess", w.BOOL, w.HANDLE, c.POINTER(w.DWORD))
    status = w.DWORD()
    done = threading.Event()
    physics, vehicles = {}, set()
    signatures = [(struct.pack("<Q", base + primary), phase, base + secondary if secondary else None)
                  for (primary, secondary), phase in PHYSICS_VTABLES.items()]
    signatures.append((struct.pack("<Q", base + VEHICLE_VTABLE), "vehicle", None))
    detector = threading.Thread(target=discover, args=(target, signatures, done, physics, vehicles),
                                daemon=True)
    try:
        with output.open("x", encoding="utf-8", buffering=1) as stream:
            def emit(record):
                stream.write(json.dumps({"utc": datetime.now(timezone.utc).isoformat(), **record}) + "\n")

            detector.start()
            previous, owners, last_accept = {}, {}, {}
            cycles = 0
            deadline = time.monotonic() + seconds
            emit({"event": "start", "pid": pid, "base": hex(base), "clock": "passive sampling"})
            while (time.monotonic() < deadline and not output.with_suffix(".stop").exists()
                   and alive(target.handle, c.byref(status)) and status.value == 259):
                if cycles % 10 == 0:
                    for vehicle in vehicles.copy():
                        hit = vehicle_link(target, base, vehicle)
                        if hit:
                            address, identity = hit
                            physics.setdefault(address, "vehicle-linked")
                            if identity["scene"] and owners.get(address) != identity:
                                owners[address] = identity
                                emit({"event": "identity", "dynamics": hex(address), **identity})
                            if (accept_car is not None and identity["scene"]
                                    and identity["scene"]["car_id"] == accept_car
                                    and time.monotonic() - last_accept.get(address, 0) >= 1):
                                body = target.read(address, WATCH_BYTES)
                                if len(body) == WATCH_BYTES:
                                    emit({"event": "sample", "dynamics": hex(address), **identity,
                                          "state": motion_state(body), "wheels": wheel_sample(body),
                                          "aero_coefficients": [struct.unpack_from("<f", body, offset)[0]
                                                                for offset in AERO_OFFSETS]})
                                    last_accept[address] = time.monotonic()
                for address in physics.copy():
                    data = target.read(address, 0x50)
                    if len(data) != 0x50:
                        continue
                    primary, = struct.unpack_from("<Q", data)
                    phase = PHASE_BY_PRIMARY.get(primary - base)
                    if phase is None:
                        continue
                    record = motion_state(data)
                    key = motion_key(record)
                    before = previous.get(address)
                    if before is None or before[0] != key or before[1] != phase:
                        emit({"event": "state", "dynamics": hex(address), "phase": phase,
                              "previous": before[2] if before else None, "current": record})
                    previous[address] = (key, phase, record)
                cycles += 1
                time.sleep(interval)
            emit({"event": "end", "cycles": cycles, "dynamics_addresses": len(previous)})
    finally:
        done.set()
        if detector.is_alive():
            detector.join()
        close(target.handle)


def capture_replay_inputs(target, base, address, data, folder):
    """Retain the local memory needed by the wheel-force and integration probes."""
    folder.mkdir()
    regions = {}

    def keep(pointer, size):
        if not 0x10000 <= pointer < 0x800000000000 or pointer in regions:
            return
        raw = target.read(pointer, size)
        if raw:
            regions[pointer] = raw

    def pointer(offset):
        return struct.unpack_from("<Q", data, offset)[0]

    for rva in (0x7FE4000, 0x8065000, 0x8066000, 0x8067000, 0x8068000, 0x8069000):
        keep(base + rva, 0x1000)
    globals_at = regions.get(base + 0x7FE4000, b"")
    if len(globals_at) == 0x1000:
        keep(struct.unpack_from("<Q", globals_at, 0xCA8)[0], 0x120)

    for corner in range(4):
        wheel = WHEEL_START + corner * WHEEL_STRIDE
        for field in (0x1A0, 0x220, 0x5F0):
            ref = pointer(wheel + field)
            if not address <= ref < address + len(data):
                keep(ref, 0x400)
        for field in range(0x1520, 0x15C0, 0x18):
            first, last = struct.unpack_from("<QQ", data, wheel + field)
            if 0 < last - first <= 0x4000:
                keep(first, last - first)
    world = pointer(0x1D0)
    keep(world, 0x100)
    if len(regions.get(world, b"")) >= 0x50:
        keep(struct.unpack_from("<Q", regions[world], 0x48)[0], 0x298 * 32)
    keep(pointer(0x10DE0), 0x800)
    for location, raw in regions.items():
        (folder / f"{location:016x}.bin").write_bytes(raw)
    return len(regions)


def observe_focused(host, pid, base, output, seconds, interval, address, vehicle,
                    replay_inputs=False):
    """Retain whole-object samples around motion transitions at a known address."""
    native.kernel = c.WinDLL("kernel32", use_last_error=True)
    target = native.Target(pid, (host / "ForzaHorizon4.exe").resolve(strict=True))
    close = native.win("CloseHandle", w.BOOL, w.HANDLE)
    alive = native.win("GetExitCodeProcess", w.BOOL, w.HANDLE, c.POINTER(w.DWORD))
    status = w.DWORD()
    history = deque(maxlen=16)
    previous = None
    last_heartbeat = 0
    block_size = REPLAY_BYTES if replay_inputs else WATCH_BYTES
    started = time.perf_counter_ns()
    try:
        with output.open("x", encoding="utf-8", buffering=1) as stream:
            def emit(record):
                stream.write(json.dumps({"utc": datetime.now(timezone.utc).isoformat(), **record}) + "\n")

            emit({"event": "start", "pid": pid, "base": hex(base), "address": hex(address),
                  "vehicle": hex(vehicle), "clock": "passive sampling", "block_size": block_size})
            while ((time.perf_counter_ns() - started) / 1e9 < seconds
                   and not output.with_suffix(".stop").exists()
                   and alive(target.handle, c.byref(status)) and status.value == 259):
                data = target.read(address, block_size)
                clock = time.perf_counter_ns() - started
                identity = vehicle_link(target, base, vehicle)
                scene = identity[1]["scene"] if identity and identity[0] == address else None
                if len(data) >= 0x50:
                    phase = PHASE_BY_PRIMARY.get(struct.unpack_from("<Q", data)[0] - base)
                else:
                    phase = None
                if phase:
                    state = motion_state(data)
                    key = (phase, scene, motion_key(state))
                    if previous is None or previous["key"] != key:
                        records = [{"ns": time_ns, "state": motion_state(raw),
                                    "full_object": len(raw) == block_size} for time_ns, raw in history]
                        output.with_name(f"private-focused-{clock}.bin").write_bytes(
                            b"".join(raw for _, raw in history if len(raw) == block_size)
                            + (data if len(data) == block_size else b""))
                        emit({"event": "transition", "ns": clock, "phase": phase, "scene": scene,
                              "state": state, "history": records,
                              "wheels": wheel_sample(data),
                              "full_object": len(data) == block_size,
                              "previous_ns": previous["ns"] if previous else None})
                        if (replay_inputs and len(data) == block_size and scene
                                and scene["scene_state"] in (2, 8)):
                            inputs = output.with_name(f"private-inputs-{clock}")
                            count = capture_replay_inputs(target, base, address, data, inputs)
                            emit({"event": "replay-inputs", "ns": clock, "segments": count,
                                  "scope": "sequential reads after the body sample"})
                    if scene and clock - last_heartbeat >= 1_000_000_000:
                        emit({"event": "sample", "ns": clock, "scene": scene, "state": state,
                              "wheels": wheel_sample(data)})
                        last_heartbeat = clock
                    previous = {"key": key, "ns": clock}
                    history.append((clock, data))
                time.sleep(interval)
            emit({"event": "end", "status": status.value})
    finally:
        close(target.handle)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--base", type=lambda v: int(v, 0), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--interval", type=float, default=0.004)
    parser.add_argument("--dynamics", type=lambda v: int(v, 0))
    parser.add_argument("--vehicle", type=lambda v: int(v, 0))
    parser.add_argument("--capture-replay-inputs", action="store_true")
    parser.add_argument("--accept-car", type=int)
    args = parser.parse_args()
    if (args.dynamics is None) != (args.vehicle is None):
        parser.error("--dynamics and --vehicle must be given together")
    if args.dynamics is None:
        observe(args.host, args.pid, args.base, args.out, args.seconds, args.interval, args.accept_car)
    else:
        observe_focused(args.host, args.pid, args.base, args.out, args.seconds, args.interval,
                        args.dynamics, args.vehicle, args.capture_replay_inputs)
