"""Passively locate the loaded presentation shots and record which one runs at each cut.

Reads the live FH4 process with ReadProcessMemory only (no debugger, no writes).

1. Every <Cam> in the given decoded camera XML files (Camera.zip, ForzaCryptoTool decrypt) has a first
   Anim key (PosX, PosY, PosZ). The game parses the same text, so the three float32 values appear
   verbatim in the loaded key arrays. One pass over committed read-write memory finds them.
2. The Cam reader stores the Anim block at Cam+0x80 with the key vector begin at Anim+0x20
   (parser 0x32869C0 called from 0x28A181D), so a pointer to a key array at P marks a Cam at P-0xA0.
3. A third pass finds every 8-byte slot that points at one of those Cams (shot lists, current-shot
   fields). Those slots are then sampled with the presentation camera; each change is logged, and
   a non-finite or 100/999 camera frame flushes the active Cam bytes and the slot neighbourhoods.

A beep (and a "ready" line in the log) says the scan is done. Scan while a stable car is in the
garage, then switch to the car under test.

    python -m expanded_garage.research.watch_shot_director --host <FH4 dir> --cams <decoded xml dir> --out shots.jsonl
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
import xml.etree.ElementTree as ET

import numpy as np

from . import capture_grid_fault as native
from .watch_camera_nan import nonfinite_offsets, sample

CHUNK = 0x4000000
CAM_KEYS = 0xA0
CAM_SIZE = 0x300
SLOT_WINDOW = 0x40
RING = 120


def shot_patterns(folder):
    """{12-byte first-key pattern: [(file, cam index, anim types)]} for every Cam."""
    patterns = {}
    for path in sorted(folder.glob("*.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for index, cam in enumerate(root.iter("Cam")):
            anim = cam.find("Anim")
            key = anim.find("Key") if anim is not None else None
            if key is None or key.get("PosX") is None:
                continue
            data = struct.pack("<3f", *(float(key.get(a)) for a in ("PosX", "PosY", "PosZ")))
            patterns.setdefault(data, []).append({"file": path.name, "cam": index,
                                                  "pos": anim.get("PosAnimType"), "rot": anim.get("RotAnimType"),
                                                  "part": anim.get("RotAnimPartToTarget"),
                                                  "start": cam.get("StartCut")})
    return patterns


def regions(target):
    address = 0x10000
    while address < 0x7FFFFFFF0000:
        info = target.region(address)
        if not info or not info["size"]:
            break
        if (info["state"] == 0x1000 and info["type"] in (0x20000, 0x40000)
                and info["protect"] & 0xFF in (0x04, 0x40) and not info["protect"] & 0x100):
            yield info["base"], info["size"]
        address = info["base"] + info["size"]


def scan(target, match, step):
    """Yield (address, chunk, offset) for aligned values that `match` accepts (numpy view of a chunk)."""
    for base, size in regions(target):
        for start in range(base, base + size, CHUNK):
            data = target.read(start, min(CHUNK, base + size - start))
            usable = len(data) // step * step
            if not usable:
                continue
            view = np.frombuffer(data[:usable], dtype="<u4" if step == 4 else "<u8")
            for index in np.nonzero(match(view))[0]:
                yield start + int(index) * step, data, int(index) * step


def locate(target, patterns, log):
    heads = np.array(sorted({struct.unpack_from("<I", p)[0] for p in patterns}), dtype="<u4")
    keys = {}
    for address, data, offset in scan(target, lambda v: np.isin(v, heads), 4):
        if data[offset:offset + 12] in patterns:
            keys[address] = patterns[data[offset:offset + 12]]
    log({"event": "key_arrays", "count": len(keys)})
    if not keys:
        return {}, {}
    wanted = np.array(sorted(keys), dtype="<u8")
    cams = {}
    for address, _, _ in scan(target, lambda v: np.isin(v, wanted), 8):
        array = struct.unpack("<Q", target.read(address, 8))[0]
        # The surrounding bytes keep the evidence if the runtime Cam layout differs from the parser's.
        cams[address - CAM_KEYS] = {"keys": hex(array), "shots": keys[array], "pointer_at": hex(address),
                                    "around": target.read(address - 0x100, 0x140).hex()}
    log({"event": "cams", "count": len(cams),
         "cams": {hex(a): v for a, v in sorted(cams.items())}})
    if not cams:
        return cams, {}
    wanted = np.array(sorted(cams), dtype="<u8")
    slots = {}
    for address, _, _ in scan(target, lambda v: np.isin(v, wanted), 8):
        slots[address] = struct.unpack("<Q", target.read(address, 8))[0]
    log({"event": "slots", "count": len(slots),
         "slots": {hex(a): hex(v) for a, v in sorted(slots.items())}})
    return cams, slots


def label(cams, pointer):
    cam = cams.get(pointer)
    return [f"{s['file']}#{s['cam']}" for s in cam["shots"]] if cam else None


def observe(host, pid, base, folder, out, seconds, delay):
    native.kernel = c.WinDLL("kernel32", use_last_error=True)
    target = native.Target(pid, (host / "ForzaHorizon4.exe").resolve(strict=True))
    alive = native.win("GetExitCodeProcess", w.BOOL, w.HANDLE, c.POINTER(w.DWORD))
    status = w.DWORD()

    def running():
        return bool(alive(target.handle, c.byref(status))) and status.value == 259

    from .watch_megatexture_producer import find_root
    patterns = shot_patterns(folder)
    with out.open("x", encoding="utf-8", buffering=1) as stream:
        def log(item):
            stream.write(json.dumps({"utc": datetime.now(timezone.utc).isoformat(), **item}) + "\n")

        log({"event": "start", "pid": pid, "base": hex(base), "patterns": len(patterns)})
        end = time.monotonic() + seconds
        root = None
        while root is None and running() and time.monotonic() < end:
            root = find_root(target, base)
            if root is None:
                time.sleep(1)
        if root is None:
            raise ValueError("The verified MegaTexture object was not found")
        started = time.monotonic()
        cams, slots = locate(target, patterns, log)
        log({"event": "ready", "scan_seconds": round(time.monotonic() - started, 1)})
        print("ready", flush=True)
        try:
            import winsound
            winsound.Beep(880, 400)
            winsound.Beep(1320, 400)
        except (ImportError, RuntimeError):
            pass
        state = {a: v for a, v in slots.items()}
        ring = deque(maxlen=RING)
        was_bad = False
        step = 0
        while running() and time.monotonic() < end and not out.with_suffix(".stop").exists():
            step += 1
            changes = {}
            for address in slots:
                raw = target.read(address, 8)
                if len(raw) != 8:
                    continue
                value = struct.unpack("<Q", raw)[0]
                if value != state[address]:
                    changes[hex(address)] = {"from": hex(state[address]), "from_shot": label(cams, state[address]),
                                             "to": hex(value), "to_shot": label(cams, value)}
                    state[address] = value
            current = sample(target, root, base)
            brief = None
            if current is not None:
                data = current[1]
                eye = struct.unpack_from("<3f", data, 0x110)
                brief = {"eye": eye, "w15c": struct.unpack_from("<f", data, 0x15C)[0],
                         "w160": struct.unpack_from("<f", data, 0x160)[0],
                         "pitch": struct.unpack_from("<f", data, 0x19C)[0],
                         "fov": struct.unpack_from("<f", data, 0x1B4)[0],
                         "car": (current[3] or {}).get("car_id")}
                bad = bool(nonfinite_offsets(data[0x20:0x60]) or nonfinite_offsets(data[0x110:0x11C])
                           or brief["w160"] == 999.0)
            else:
                bad = False
            ring.append((step, time.monotonic(), changes, brief))
            if changes:
                log({"event": "slot_change", "step": step, "changes": changes, "camera": brief})
            if bad and not was_bad:
                active = {hex(a): v for a, v in state.items() if v in cams}
                snapshot = {}
                for address, pointer in active.items():
                    snapshot[address] = {"cam": hex(pointer), "shot": label(cams, pointer),
                                         "cam_bytes": target.read(pointer, CAM_SIZE).hex(),
                                         "slot_window": target.read(int(address, 16) - SLOT_WINDOW,
                                                                    2 * SLOT_WINDOW).hex()}
                log({"event": "bad_frame", "step": step, "camera": brief,
                     "camera_bytes": current[1].hex() if current else None,
                     "recent": [{"step": n, "changes": ch, "camera": b} for n, _, ch, b in list(ring)[-40:]],
                     "active": snapshot})
            was_bad = bad
            time.sleep(delay)
        log({"event": "end", "steps": step, "process_alive": running()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, type=Path)
    parser.add_argument("--cams", required=True, type=Path, help="Folder with decoded Camera.zip XML files")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seconds", type=int, default=3600)
    parser.add_argument("--delay", type=float, default=0.003)
    args = parser.parse_args()
    from .dump_runtime_code import find_pid, module_base
    native.kernel = c.WinDLL("kernel32", use_last_error=True)
    pid = None
    deadline = time.monotonic() + args.seconds
    while pid is None and time.monotonic() < deadline:
        pid = find_pid()
        time.sleep(1)
    if pid is None:
        raise SystemExit("FH4 did not start")
    time.sleep(5)
    observe(args.host, pid, module_base(pid), args.cams, args.out, args.seconds, args.delay)
