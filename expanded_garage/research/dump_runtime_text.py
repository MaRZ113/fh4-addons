"""Wait for FH4 and passively save every executable section as decrypted in memory.

The first .text section of ForzaHorizon4.exe is encrypted on disk. This reads
each executable section with ReadProcessMemory (no debugger, no writes) once the
game has loaded, repeating until two reads agree, and writes <out>/<name>-<rva>.bin
plus sections.json for offline disassembly at the image base.

    python -m expanded_garage.research.dump_runtime_text --exe <FH4 exe> --out <folder>
"""

import argparse
import ctypes as c
import hashlib
import json
from pathlib import Path
import struct
import time

from . import capture_grid_fault as native
from .dump_runtime_code import find_pid, module_base

CHUNK = 0x100000


def sections(exe):
    data = exe.read_bytes()[:0x2000]
    pe, = struct.unpack_from("<I", data, 0x3C)
    count, = struct.unpack_from("<H", data, pe + 6)
    optional, = struct.unpack_from("<H", data, pe + 20)
    table = pe + 24 + optional
    for i in range(count):
        name, vsize, rva, _, _, _, _, _, _, flags = struct.unpack_from("<8sIIIIIIHHI", data, table + 40 * i)
        if flags & 0x20000000:
            yield name.rstrip(b"\0").decode("ascii", "replace"), rva, vsize


def read_section(target, base, rva, size):
    parts = []
    for offset in range(0, size, CHUNK):
        part = target.read(base + rva + offset, min(CHUNK, size - offset))
        if len(part) != min(CHUNK, size - offset):
            return None
        parts.append(part)
    return b"".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--wait", type=int, default=1800)
    parser.add_argument("--settle", type=int, default=60, help="Seconds after the process appears")
    args = parser.parse_args()
    native.kernel = c.WinDLL("kernel32", use_last_error=True)
    args.out.mkdir(parents=True, exist_ok=True)
    end = time.monotonic() + args.wait
    pid = None
    while pid is None and time.monotonic() < end:
        pid = find_pid()
        time.sleep(1)
    if pid is None:
        raise SystemExit("FH4 did not start")
    time.sleep(args.settle)
    base = module_base(pid)
    target = native.Target(pid, args.exe.resolve(strict=True))
    log = {"pid": pid, "base": hex(base), "sections": {}}
    for name, rva, size in sections(args.exe):
        previous = None
        for _ in range(10):
            data = read_section(target, base, rva, size)
            if data is None:
                break
            digest = hashlib.sha256(data).hexdigest()
            if digest == previous:
                (args.out / f"{name.strip('.')}-{rva:08x}.bin").write_bytes(data)
                log["sections"][f"{name}@{rva:#x}"] = {"rva": hex(rva), "size": size, "sha256": digest}
                break
            previous = digest
            time.sleep(3)
    (args.out / "sections.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    print(f"Saved {len(log['sections'])} executable sections from PID {pid}", flush=True)


if __name__ == "__main__":
    main()
