"""Wait for FH4 to start and passively save decrypted code around given RVAs.

The first .text section of ForzaHorizon4.exe is decrypted only in memory.
This reads it with ReadProcessMemory (no debugger, no writes) a few seconds
after the process appears, and again until each range stops changing.

    python -m expanded_garage.research.dump_runtime_code --exe <FH4 exe> --out <folder> --rva 0x3534115 --rva 0x467a51f
"""

import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
from pathlib import Path
import time

from . import capture_grid_fault as native

SPAN = 0x8000


def find_pid(name="ForzaHorizon4.exe"):
    snapshot = native.win("CreateToolhelp32Snapshot", w.HANDLE, w.DWORD, w.DWORD)(2, 0)

    class Entry(c.Structure):
        _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD), ("heap", c.c_size_t),
                    ("module", w.DWORD), ("threads", w.DWORD), ("parent", w.DWORD), ("priority", c.c_long),
                    ("flags", w.DWORD), ("exe", c.c_wchar * 260)]
    entry = Entry()
    entry.size = c.sizeof(Entry)
    first = native.win("Process32FirstW", w.BOOL, w.HANDLE, c.POINTER(Entry))
    nxt = native.win("Process32NextW", w.BOOL, w.HANDLE, c.POINTER(Entry))
    try:
        ok = first(snapshot, c.byref(entry))
        while ok:
            if entry.exe.lower() == name.lower():
                return entry.pid
            ok = nxt(snapshot, c.byref(entry))
    finally:
        native.win("CloseHandle", w.BOOL, w.HANDLE)(snapshot)
    return None


def module_base(pid):
    psapi = c.WinDLL("psapi", use_last_error=True)
    handle = native.win("OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)(0x410, False, pid)
    modules = (w.HMODULE * 1)()
    needed = w.DWORD()
    psapi.EnumProcessModulesEx.argtypes = [w.HANDLE, c.POINTER(w.HMODULE), w.DWORD, c.POINTER(w.DWORD), w.DWORD]
    try:
        if not psapi.EnumProcessModulesEx(handle, modules, c.sizeof(modules), c.byref(needed), 3):
            return None
        return modules[0]
    finally:
        native.win("CloseHandle", w.BOOL, w.HANDLE)(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--rva", action="append", type=lambda n: int(n, 0), required=True)
    parser.add_argument("--wait", type=int, default=1800)
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
    time.sleep(8)
    base = module_base(pid)
    target = native.Target(pid, args.exe.resolve(strict=True))
    starts = sorted({(rva & ~0xFFF) - SPAN // 2 for rva in args.rva})
    hashes, stable = {}, set()
    log = {"pid": pid, "base": hex(base), "ranges": {}}
    for _ in range(60):
        for start in starts:
            if start in stable:
                continue
            data = target.read(base + start, SPAN)
            if len(data) != SPAN or not any(data):
                continue
            digest = hashlib.sha256(data).hexdigest()
            if hashes.get(start) == digest:
                stable.add(start)
                (args.out / f"code-{start:08x}.bin").write_bytes(data)
                log["ranges"][hex(start)] = {"size": SPAN, "sha256": digest}
            hashes[start] = digest
        if len(stable) == len(starts):
            break
        time.sleep(2)
    (args.out / "dump.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    print(f"Saved {len(stable)}/{len(starts)} stable code ranges from PID {pid}", flush=True)


if __name__ == "__main__":
    main()
