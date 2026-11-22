"""Capture a CCarDynamics Y write in a local, already-running offline FH4 session."""

import argparse
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import time

from . import capture_grid_fault as native
from .watch_physics_initialization import PHASE_BY_PRIMARY, motion_state, vehicle_link
from .watch_megatexture_producer import read_pointer


CONTEXT_BYTES = 1232
CONTEXT_FLAGS = 0x10001F
DEBUG_REGISTERS = 72
WATCH_OFFSETS = (0x24, 0x44)
WATCH_CONTROL = 0x00DD0005
SINGLE_STEP = 0x80000004
BREAKPOINT = 0x80000003


class StackAddress(c.Structure):
    _fields_ = (("offset", c.c_uint64), ("segment", w.WORD), ("mode", c.c_int))


class StackFrame(c.Structure):
    # The final field is KDHELP64 from DbgHelp.h, with 64-bit alignment.
    _fields_ = [(name, StackAddress) for name in ("pc", "ret", "frame", "stack", "store")] + [
        ("function", c.c_void_p), ("params", c.c_uint64 * 4), ("far", w.BOOL),
        ("virtual", w.BOOL), ("reserved", c.c_uint64 * 3), ("kdhelp", c.c_uint64 * 14)]


class ThreadContext:
    def __init__(self, handle):
        self.handle = handle
        self.buffer = c.create_string_buffer(CONTEXT_BYTES + 15)
        self.address = (c.addressof(self.buffer) + 15) & ~15
        c.c_uint32.from_address(self.address + 48).value = CONTEXT_FLAGS
        native.checked(native.win("GetThreadContext", w.BOOL, w.HANDLE, c.c_void_p)(
            handle, self.address), "GetThreadContext")

    def raw(self):
        return c.string_at(self.address, CONTEXT_BYTES)

    def debug_registers(self):
        return struct.unpack_from("<6Q", self.raw(), DEBUG_REGISTERS)

    def set_debug_registers(self, registers):
        c.c_uint32.from_address(self.address + 48).value = 0x100010
        c.memmove(self.address + DEBUG_REGISTERS, struct.pack("<6Q", *registers), 48)
        native.checked(native.win("SetThreadContext", w.BOOL, w.HANDLE, c.c_void_p)(
            self.handle, self.address), "SetThreadContext")


def watch_registers(existing, dynamics):
    if existing[-1] & 0xF:
        raise ValueError("Debug-register slots 0 or 1 are in use")
    return (dynamics + WATCH_OFFSETS[0], dynamics + WATCH_OFFSETS[1], *existing[2:4],
            existing[4] & ~3, (existing[5] & ~0xFF000F) | WATCH_CONTROL)


def state_for(target, base, dynamics, vehicle):
    if read_pointer(target, dynamics) - base not in PHASE_BY_PRIMARY:
        return None, None
    data = target.read(dynamics, 0x50)
    if len(data) != 0x50:
        return None, None
    identity = vehicle_link(target, base, vehicle)
    return motion_state(data), identity[1]["scene"] if identity and identity[0] == dynamics else None


def invalid_y_changes(previous, state, debug_status):
    if previous is None or state is None:
        return []
    return [field for slot, field in enumerate(("linear_velocity", "position"))
            if debug_status & (1 << slot)
            and previous[field]["y_class"] == "finite"
            and state[field]["y_class"] == "nan"]


def unwind(process, thread, context, module_base, registers, symbol_folder):
    dbghelp = c.WinDLL("dbghelp", use_last_error=True)
    initialize = dbghelp.SymInitializeW
    initialize.argtypes, initialize.restype = (w.HANDLE, w.LPCWSTR, w.BOOL), w.BOOL
    cleanup = dbghelp.SymCleanup
    cleanup.argtypes, cleanup.restype = (w.HANDLE,), w.BOOL
    walk = dbghelp.StackWalk64
    walk.argtypes = (w.DWORD, w.HANDLE, w.HANDLE, c.POINTER(StackFrame), c.c_void_p,
                     c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p)
    walk.restype = w.BOOL
    frame = StackFrame(pc=StackAddress(registers["rip"], 0, 3),
                       frame=StackAddress(registers["rbp"], 0, 3),
                       stack=StackAddress(registers["rsp"], 0, 3))
    frames = []
    if not initialize(process, str(symbol_folder), True):
        return frames
    try:
        for _ in range(24):
            if not walk(0x8664, process, thread, c.byref(frame), context, None,
                        dbghelp.SymFunctionTableAccess64, dbghelp.SymGetModuleBase64, None):
                break
            pc = frame.pc.offset
            if not pc or frames and frames[-1]["address"] == hex(pc):
                break
            frames.append({"address": hex(pc), "fh4_rva": hex(pc - module_base)
                           if 0 <= pc - module_base < 0x9B10E00 else None})
    finally:
        cleanup(process)
    return frames


def previous_instruction(code, start, registers, watched):
    from capstone import CS_AC_WRITE, CS_ARCH_X86, CS_MODE_64, Cs
    from capstone.x86 import X86_OP_MEM

    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    decoder.detail = True
    result = []
    for offset in range(max(0, len(code) - 15), len(code)):
        for instruction in decoder.disasm(code[offset:], start + offset, 1):
            if instruction.address + instruction.size != registers["rip"]:
                break
            for index, operand in enumerate(instruction.operands):
                move_destination = index == 0 and instruction.mnemonic.startswith(("mov", "vmov"))
                if operand.type != X86_OP_MEM or not (operand.access & CS_AC_WRITE or move_destination):
                    continue
                memory = operand.mem
                base = decoder.reg_name(memory.base)
                index_reg = decoder.reg_name(memory.index)
                address = (registers.get(base, 0) + registers.get(index_reg, 0) * memory.scale
                           + memory.disp) & 0xFFFFFFFFFFFFFFFF
                if any(address <= location < address + operand.size for location in watched):
                    result.append({"address": hex(instruction.address), "bytes": instruction.bytes.hex(),
                                   "assembly": f"{instruction.mnemonic} {instruction.op_str}"})
    return result


def save_write(target, folder, report, context):
    raw = context.raw()
    registers = dict(zip(native.NAMES, struct.unpack_from("<17Q", raw, 120), strict=True))
    pc = registers["rip"]
    report["next_instruction_rva"] = hex(pc - report["module_base"])
    report["registers"] = {key: hex(value) for key, value in registers.items()}
    report["xmm"] = [raw[416+i*16:432+i*16].hex() for i in range(16)]
    instructions = target.read(pc - 32, 64)
    if len(instructions) == 64:
        watched = tuple(report["dynamics"] + offset for offset in WATCH_OFFSETS)
        report["write_candidates"] = previous_instruction(instructions[:32], pc - 32, registers, watched)
        for candidate in report["write_candidates"]:
            candidate["rva"] = hex(int(candidate["address"], 16) - report["module_base"])
    report["call_stack"] = unwind(target.handle, context.handle, context.address, report["module_base"],
                                  registers, folder.resolve())
    (folder / "context.bin").write_bytes(raw)
    (folder / "stack.bin").write_bytes(target.read(registers["rsp"], 0x4000))
    (folder / "instructions.bin").write_bytes(instructions)
    (folder / "body.bin").write_bytes(target.read(report["dynamics"], 0x11070))
    (folder / "first-y-write.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def observe(target, base, dynamics, vehicle, name, car_id, folder, seconds):
    attach = native.win("DebugActiveProcess", w.BOOL, w.DWORD)
    detach = native.win("DebugActiveProcessStop", w.BOOL, w.DWORD)
    wait = native.win("WaitForDebugEvent", w.BOOL, c.c_void_p, w.DWORD)
    onward = native.win("ContinueDebugEvent", w.BOOL, w.DWORD, w.DWORD, w.DWORD)
    open_thread = native.win("OpenThread", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
    close = native.win("CloseHandle", w.BOOL, w.HANDLE)
    previous, _ = state_for(target, base, dynamics, vehicle)
    link = vehicle_link(target, base, vehicle)
    if previous is None or not link or link[0] != dynamics:
        raise ValueError("Selected addresses no longer identify the pinned showroom vehicle")
    event = c.create_string_buffer(176)
    armed = {}
    seen_scenes = set()
    first_break = True
    finished = False
    interrupt_requested = False

    def restore_threads():
        for handle, original in armed.values():
            ThreadContext(handle).set_debug_registers(original)

    native.checked(attach(target.pid), "DebugActiveProcess")
    try:
        native.checked(native.win("DebugSetProcessKillOnExit", w.BOOL, w.BOOL)(False),
                       "DebugSetProcessKillOnExit")
        deadline = time.monotonic() + seconds
        with (folder / "watchpoints.jsonl").open("x", encoding="utf-8", buffering=1) as log:
            def emit(record):
                log.write(json.dumps({"utc": datetime.now(timezone.utc).isoformat(), **record}) + "\n")

            while not finished:
                if not wait(event, 100):
                    error = c.get_last_error()
                    if error not in (0, 121, 258):
                        raise OSError(error, "WaitForDebugEvent")
                    if (not interrupt_requested
                            and (time.monotonic() >= deadline or (folder / "stop").exists())):
                        native.checked(native.win("DebugBreakProcess", w.BOOL, w.HANDLE)(target.debug_handle),
                                       "DebugBreakProcess")
                        interrupt_requested = True
                    continue
                kind, pid, tid = struct.unpack_from("<III", event)
                status = native.CONTINUE
                try:
                    if kind in (2, 3):
                        if kind == 3:
                            file_handle, target.debug_handle = struct.unpack_from("<2Q", event, 16)
                            if file_handle:
                                close(file_handle)
                        # Windows owns the initial process and thread handles supplied in debug events.
                        handle = open_thread(0x18, False, tid)
                        native.checked(handle, "OpenThread")
                        try:
                            context = ThreadContext(handle)
                            original = context.debug_registers()
                            context.set_debug_registers(watch_registers(original, dynamics))
                        except Exception:
                            close(handle)
                            raise
                        armed[tid] = (handle, original)
                    elif kind == 4:
                        if tid in armed:
                            close(armed.pop(tid)[0])
                    elif kind == 5:
                        emit({"event": "process-exit", "code": hex(struct.unpack_from("<I", event, 16)[0])})
                        return
                    elif kind == 6:
                        file_handle, = struct.unpack_from("<Q", event, 16)
                        if file_handle:
                            close(file_handle)
                    elif kind == 1:
                        exception, = struct.unpack_from("<I", event, 16)
                        if exception == BREAKPOINT and first_break:
                            first_break = False
                            emit({"event": "armed", "threads": len(armed), "dynamics": hex(dynamics)})
                        elif exception == SINGLE_STEP and tid in armed:
                            context = ThreadContext(armed[tid][0])
                            dr = context.debug_registers()
                            if not dr[4] & 3:
                                status = native.NOT_HANDLED
                                continue
                            state, scene = state_for(target, base, dynamics, vehicle)
                            if scene and scene["car_id"] not in seen_scenes:
                                seen_scenes.add(scene["car_id"])
                                emit({"event": "first-write-seen", "scene": scene, "state": state})
                            changes = invalid_y_changes(previous, state, dr[4])
                            if (changes and scene and scene["car_id"] in (0, car_id)
                                    and scene["media_name"].casefold() == name.casefold()):
                                save_write(target, folder, {"thread": tid, "module_base": base,
                                    "dynamics": dynamics, "scene": scene, "fields": changes,
                                    "previous": previous, "current": state}, context)
                                emit({"event": "invalid-write", "scene": scene, "fields": changes})
                                finished = True
                            previous = state
                            context.set_debug_registers((*dr[:4], dr[4] & ~3, dr[5]))
                        else:
                            status = native.NOT_HANDLED
                            if not struct.unpack_from("<I", event, 168)[0]:
                                pc = struct.unpack_from("<Q", event, 32)[0]
                                emit({"event": "second-chance", "code": hex(exception),
                                      "rva": hex(pc - base), "thread": tid})
                                finished = True
                    if finished or time.monotonic() >= deadline or (folder / "stop").exists():
                        if kind == 1 and exception == BREAKPOINT and interrupt_requested:
                            status = native.CONTINUE
                        restore_threads()
                        finished = True
                except Exception:
                    restore_threads()
                    raise
                finally:
                    native.checked(onward(pid, tid, status), "ContinueDebugEvent")
    finally:
        detach(target.pid)
        for handle, _ in armed.values():
            close(handle)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("pid", "base", "dynamics", "vehicle", "car-id"):
        parser.add_argument("--" + arg, required=True, type=lambda value: int(value, 0))
    parser.add_argument("--media-name", required=True)
    parser.add_argument("--host", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=180)
    args = parser.parse_args()
    if not (args.host / "steam_settings" / "disable_networking.txt").is_file():
        parser.error("Expected the isolated offline FH4 host")
    native.kernel = c.WinDLL("kernel32", use_last_error=True)
    target = native.Target(args.pid, (args.host / "ForzaHorizon4.exe").resolve(strict=True))
    try:
        args.out.mkdir(parents=True, exist_ok=False)
        observe(target, args.base, args.dynamics, args.vehicle, args.media_name, args.car_id,
                args.out, args.seconds)
    finally:
        native.win("CloseHandle", w.BOOL, w.HANDLE)(target.handle)
