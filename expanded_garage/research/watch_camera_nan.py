"""Passively locate the first non-finite field on the FH4 presentation camera path.

Reads the live process with ReadProcessMemory only (no code changes, no
breakpoints). Each sample copies the full CPresentationCamera object and the
heap objects its first 0x480 bytes point to. A ring of recent samples is
flushed to the JSONL output whenever the camera matrix turns non-finite, with
the offsets that changed from finite to NaN/Inf in that step.

    python -m expanded_garage.research.watch_camera_nan --host <FH4 dir> --pid <pid> --base <image base> --out cam.jsonl

Reproduce in game: switch the camera out of the cockpit view, or wait in the
garage while the menu camera flies around the car. Create <out>.stop to end.
"""

import argparse
from collections import deque
import ctypes as c
import ctypes.wintypes as w
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import struct
import time

from . import capture_grid_fault as native
from .watch_megatexture_producer import CAMERA_RTTI_VTABLE, TRACK_RTTI_VTABLE, find_root, read_pointer

CAMERA_SIZE = 0x480
CHILD_SIZE = 0x200
RING = 240


def nonfinite_offsets(data):
    bad = set()
    for offset in range(0, len(data) - 3, 4):
        value, = struct.unpack_from("<f", data, offset)
        bits, = struct.unpack_from("<I", data, offset)
        if not math.isfinite(value) and bits not in (0xFFFFFFFF,):
            bad.add(offset)
    return bad


def heap_pointers(data, base):
    result = {}
    for offset in range(0, len(data) - 7, 8):
        value, = struct.unpack_from("<Q", data, offset)
        if 0x10000 <= value < 0x7FF000000000 and not base <= value < base + 0x9B10E00 and value % 8 == 0:
            result[offset] = value
    return result


def sample(target, root, base):
    world = read_pointer(target, root + 0x4BBE8)
    if not world or read_pointer(target, world) != base + TRACK_RTTI_VTABLE:
        return None
    camera = read_pointer(target, world + 0x1FEC8)
    if not camera or read_pointer(target, camera) != base + CAMERA_RTTI_VTABLE:
        return None
    data = target.read(camera, CAMERA_SIZE)
    if len(data) != CAMERA_SIZE:
        return None
    children = {}
    for offset, pointer in heap_pointers(data, base).items():
        child = target.read(pointer, CHILD_SIZE)
        if len(child) == CHILD_SIZE:
            vtable, = struct.unpack_from("<Q", child)
            children[offset] = (pointer, vtable - base if base <= vtable < base + 0x9B10E00 else None, child)
    return camera, data, children


def describe(step, previous, current, base):
    camera, data, children = current
    newly = sorted(nonfinite_offsets(data) - (nonfinite_offsets(previous[1]) if previous else set()))
    child_newly = {}
    for offset, (pointer, vtable, child) in children.items():
        before = previous[2].get(offset) if previous else None
        fresh = nonfinite_offsets(child) - (nonfinite_offsets(before[2]) if before and before[0] == pointer else set())
        if fresh:
            child_newly[hex(offset)] = {"object": hex(pointer), "vtable_rva": hex(vtable) if vtable is not None else None,
                                         "offsets": [hex(o) for o in sorted(fresh)]}
    return {"step": step, "camera": hex(camera), "camera_new_nonfinite": [hex(o) for o in newly],
            "child_new_nonfinite": child_newly, "camera_bytes": data.hex(),
            "children": {hex(o): {"object": hex(p), "vtable_rva": hex(v) if v is not None else None, "bytes": b.hex()}
                         for o, (p, v, b) in children.items()}}


def observe(host, pid, base, out, seconds, delay):
    native.kernel = c.WinDLL("kernel32", use_last_error=True)
    target = native.Target(pid, (host / "ForzaHorizon4.exe").resolve(strict=True))
    alive = native.win("GetExitCodeProcess", w.BOOL, w.HANDLE, c.POINTER(w.DWORD))
    close = native.win("CloseHandle", w.BOOL, w.HANDLE)
    status = w.DWORD()
    ring = deque(maxlen=RING)
    try:
        with out.open("x", encoding="utf-8", buffering=1) as stream:
            def emit(item):
                stream.write(json.dumps({"utc": datetime.now(timezone.utc).isoformat(), **item}) + "\n")

            def running():
                return bool(alive(target.handle, c.byref(status))) and status.value == 259

            emit({"event": "start", "pid": pid, "base": hex(base)})
            end = time.monotonic() + seconds
            root = None
            while root is None and running() and time.monotonic() < end:
                root = find_root(target, base)
                if root is None:
                    time.sleep(1)
            if root is None:
                raise ValueError("The verified MegaTexture object was not found")
            emit({"event": "verified_manager", "manager": hex(root)})
            step, flushed, was_bad, shot, cuts = 0, 0, False, None, 0
            while running() and time.monotonic() < end and not out.with_suffix(".stop").exists():
                current = sample(target, root, base)
                if current is not None:
                    step += 1
                    ring.append((step, current))
                    bad = bool(nonfinite_offsets(current[1][0x20:0x60]))
                    # A presentation shot cut changes the FOV (+0x1B4) or the shot distances (+0x15C/+0x160).
                    key = current[1][0x15C:0x164] + current[1][0x1B4:0x1B8]
                    if shot is not None and key != shot and not bad and cuts < 12:
                        cuts += 1
                        window = list(ring)[-61:]
                        for (_, before), (n, snap) in zip(window, window[1:]):
                            emit({"event": "sample", **describe(n, before, snap, base)})
                        emit({"event": "shot_cut", "step": step})
                    shot = key
                    if bad and not was_bad:
                        items = list(ring)
                        for i, (n, snap) in enumerate(items):
                            emit({"event": "sample", **describe(n, items[i - 1][1] if i else None, snap, base)})
                        emit({"event": "camera_nonfinite", "step": step})
                        flushed += 1
                        ring.clear()
                    was_bad = bad
                time.sleep(delay)
            if ring and not running():
                # The process died without an observed NaN: keep the last samples for comparison.
                items = list(ring)
                for i, (n, snap) in enumerate(items):
                    emit({"event": "sample", **describe(n, items[i - 1][1] if i else None, snap, base)})
            emit({"event": "end", "samples": step, "nonfinite_transitions": flushed, "process_alive": running()})
    finally:
        close(target.handle)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, type=Path)
    parser.add_argument("--pid", type=int, help="Omit to wait for the next FH4 process")
    parser.add_argument("--base", type=lambda n: int(n, 0))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--delay", type=float, default=0.004)
    args = parser.parse_args()
    if args.pid is None:
        from .dump_runtime_code import find_pid, module_base
        native.kernel = c.WinDLL("kernel32", use_last_error=True)
        deadline = time.monotonic() + args.seconds
        while args.pid is None and time.monotonic() < deadline:
            args.pid = find_pid()
            time.sleep(1)
        if args.pid is None:
            raise SystemExit("FH4 did not start")
        time.sleep(5)
        args.base = module_base(args.pid)
    observe(args.host, args.pid, args.base, args.out, args.seconds, args.delay)
