"""Capture the state of FH4 at an access violation in its own code (offline test host only).

Attaches as a debugger (the game keeps running; detaching does not kill it).
On an access violation inside the main module it saves, before Windows
exception handling: registers, 0x2000 bytes of stack, the unpacked code around
RIP and around every return address found on the stack, and 0x200 bytes at
each register that points into readable memory. The first .text section is
decrypted only at runtime, so these code bytes are the only static view of
the faulting functions.

    python -m expanded_garage.research.capture_any_fault --pid <pid> --exe <FH4 exe> --out <folder>
"""

import argparse
import ctypes as c
from ctypes import wintypes as w
import struct
import time
from pathlib import Path

from . import capture_grid_fault as native
from expanded_garage.vehicle_ir import write_json

NAMES = "rax rcx rdx rbx rsp rbp rsi rdi r8 r9 r10 r11 r12 r13 r14 r15 rip".split()
CONTINUE = 0x00010002
NOT_HANDLED = 0x80010001
IMAGE_SIZE = 0x9B10E00
MAX_CAPTURES = 4


def context(tid):
    th = native.win("OpenThread", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)(0x048, False, tid)
    native.checked(th, "OpenThread")
    try:
        buffer = c.create_string_buffer(1248)
        address = c.addressof(buffer) + (-c.addressof(buffer) % 16)
        c.c_uint32.from_address(address + 48).value = 0x10000F
        native.checked(native.win("GetThreadContext", w.BOOL, w.HANDLE, c.c_void_p)(th, address), "GetThreadContext")
        raw = c.string_at(address, 1232)
    finally:
        native.win("CloseHandle", w.BOOL, w.HANDLE)(th)
    return raw, dict(zip(NAMES, struct.unpack_from("<17Q", raw, 120), strict=True))


def capture(target, base, tid, code, fault, first_chance, folder):
    raw, regs = context(tid)
    rva = regs["rip"] - base
    out = folder / f"fault-{rva:08x}-{tid}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "context.bin").write_bytes(raw)
    stack = target.read(regs["rsp"], 0x2000)
    (out / "stack.bin").write_bytes(stack)
    (out / f"code-{rva - 0x1000:08x}.bin").write_bytes(target.read(regs["rip"] - 0x1000, 0x2000))
    returns = []
    for offset in range(0, len(stack) - 7, 8):
        value, = struct.unpack_from("<Q", stack, offset)
        if base + 0x1000 <= value < base + IMAGE_SIZE:
            back = value - base
            returns.append({"stack_offset": hex(offset), "rva": hex(back)})
            path = out / f"code-{back - 0x200:08x}.bin"
            if not path.exists():
                path.write_bytes(target.read(value - 0x200, 0x240))
    pointed = {}
    for name, value in regs.items():
        if name in ("rip", "rsp") or not 0x10000 <= value < 0x7FFFFFFFFFFF:
            continue
        data = target.read(value, 0x200)
        if data:
            (out / f"reg-{name}.bin").write_bytes(data)
            pointed[name] = {"address": hex(value), "bytes": len(data)}
    write_json(out / "fault.json", {
        "code": hex(code), "rva": hex(rva), "thread": tid, "first_chance": bool(first_chance),
        "fault_address": hex(fault) if fault is not None else None, "base": hex(base),
        "registers": {k: hex(v) for k, v in regs.items()}, "register_memory": pointed,
        "stack_return_candidates": returns[:64], "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    print(f"Captured {code:#x} at RVA {rva:#x} (thread {tid}, first chance {bool(first_chance)}) -> {out}", flush=True)


def monitor(target, folder, seconds):
    attach = native.win("DebugActiveProcess", w.BOOL, w.DWORD)
    detach = native.win("DebugActiveProcessStop", w.BOOL, w.DWORD)
    wait = native.win("WaitForDebugEvent", w.BOOL, c.c_void_p, w.DWORD)
    onward = native.win("ContinueDebugEvent", w.BOOL, w.DWORD, w.DWORD, w.DWORD)
    close = native.win("CloseHandle", w.BOOL, w.HANDLE)
    native.checked(attach(target.pid), "DebugActiveProcess")
    seen = set()
    try:
        native.checked(native.win("DebugSetProcessKillOnExit", w.BOOL, w.BOOL)(False), "DebugSetProcessKillOnExit")
        end = time.monotonic() + seconds
        event = c.create_string_buffer(176)
        base, initial_break = 0, True
        while time.monotonic() < end and not (folder / "stop").exists():
            if not wait(event, 500):
                if c.get_last_error() not in (0, 121, 258):
                    raise OSError(c.get_last_error(), "WaitForDebugEvent")
                continue
            kind, pid, tid = struct.unpack_from("<III", event)
            status = CONTINUE
            try:
                if kind == 3:
                    handle, _, _, base = struct.unpack_from("<4Q", event, 16)
                    if handle:
                        close(handle)
                    write_json(folder / "attached.json", {"pid": pid, "base": hex(base)})
                    print(f"Attached to FH4 PID {pid}, module at {base:#x}", flush=True)
                elif kind == 6:
                    handle, = struct.unpack_from("<Q", event, 16)
                    if handle:
                        close(handle)
                elif kind == 1:
                    code, _, _, pc, params = struct.unpack_from("<IIQQI", event, 16)
                    first_chance, = struct.unpack_from("<I", event, 168)
                    if code == 0x80000003 and initial_break:
                        initial_break = False
                    else:
                        status = NOT_HANDLED
                    rva = pc - base
                    if (code in (0xC0000005, 0xC000001D, 0xC0000096) and base and 0 <= rva < IMAGE_SIZE
                            and rva not in seen and len(seen) < MAX_CAPTURES):
                        fault = struct.unpack_from("<Q", event, 56)[0] if params >= 2 else None
                        seen.add(rva)
                        capture(target, base, tid, code, fault, first_chance, folder)
            finally:
                native.checked(onward(pid, tid, status), "ContinueDebugEvent")
            if kind == 5:
                print("FH4 exited", flush=True)
                return
    finally:
        detach(target.pid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seconds", default=1800, type=int)
    args = parser.parse_args()
    native.kernel = c.WinDLL("kernel32", use_last_error=True)
    native.close = native.win("CloseHandle", w.BOOL, w.HANDLE)
    args.out.mkdir(parents=True, exist_ok=True)
    target = native.Target(args.pid, args.exe.resolve(strict=True))
    try:
        monitor(target, args.out, args.seconds)
    finally:
        native.close(target.handle)
