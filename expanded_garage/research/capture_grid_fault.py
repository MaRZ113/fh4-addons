"""Read the deployed FH4 grid context at an exception on an offline test host."""

import argparse
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import struct
import time

from expanded_garage.fh4_installer import EXE_SHA
from expanded_garage.vehicle_ir import sha256, write_json


EXPECTED_RVA = 0x46827e7
STACK_RVAS = (0x4682650,0x4684550,0x4682414,0x467d30d,0x467fd84,0x3325d90,0x467fde0)
NAMES = "rax rcx rdx rbx rsp rbp rsi rdi r8 r9 r10 r11 r12 r13 r14 r15 rip".split()
CONTINUE = 0x00010002
NOT_HANDLED = 0x80010001


def win(name, result, *parameters):
    fn = getattr(kernel,name)
    fn.restype, fn.argtypes = result, parameters
    return fn


class Region(c.Structure):
    _fields_ = [("base",c.c_size_t),("allocation_base",c.c_size_t),("allocation_protect",w.DWORD),
                ("partition_id",w.WORD),("size",c.c_size_t),("state",w.DWORD),("protect",w.DWORD),("type",w.DWORD)]


def checked(ok, operation):
    if not ok:
        raise OSError(c.get_last_error(),operation)


class Target:
    def __init__(self,pid,path):
        self.pid = pid
        self.handle = win("OpenProcess",w.HANDLE,w.DWORD,w.BOOL,w.DWORD)(0x410,False,pid)
        checked(self.handle,"OpenProcess")
        image = c.create_unicode_buffer(4096)
        chars = w.DWORD(len(image))
        checked(win("QueryFullProcessImageNameW",w.BOOL,w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD))(
            self.handle,0,image,c.byref(chars)),"QueryFullProcessImageNameW")
        if Path(image.value).resolve()!=path or sha256(path)!=EXE_SHA:
            raise ValueError("Target differs from the pinned offline FH4 executable")

    def read(self,addr,length):
        raw = c.create_string_buffer(length)
        received = c.c_size_t()
        win("ReadProcessMemory",w.BOOL,w.HANDLE,c.c_void_p,c.c_void_p,c.c_size_t,c.POINTER(c.c_size_t))(
            self.handle,addr,raw,length,c.byref(received))
        return raw.raw[:received.value]

    def region(self,addr):
        region = Region()
        got = win("VirtualQueryEx",c.c_size_t,w.HANDLE,c.c_void_p,c.POINTER(Region),c.c_size_t)(
            self.handle,addr,c.byref(region),c.sizeof(region))
        return {field:getattr(region,field) for field,_ in Region._fields_} if got else None

    def code(self,base,folder):
        for rva in STACK_RVAS:
            (folder/f"code-{rva:08x}.bin").write_bytes(self.read(base+rva,0x900))

    def fault(self,thread,base,fault,folder):
        th = win("OpenThread",w.HANDLE,w.DWORD,w.BOOL,w.DWORD)(0x048,False,thread)
        checked(th,"OpenThread")
        try:
            buffer = c.create_string_buffer(1248)
            offset = -c.addressof(buffer)%16
            address = c.addressof(buffer)+offset
            c.c_uint32.from_address(address+48).value = 0x10000f
            checked(win("GetThreadContext",w.BOOL,w.HANDLE,c.c_void_p)(th,address),"GetThreadContext")
            raw = c.string_at(address,1232)
        finally:
            close(th)
        regs = dict(zip(NAMES,struct.unpack_from("<17Q",raw,120),strict=True))
        grid = self.read(regs["rsi"],0x100)
        table = self.read(regs["rax"],0x100)
        (folder/"grid.bin").write_bytes(grid)
        (folder/"pointer-table-head.bin").write_bytes(table)
        (folder/"stack.bin").write_bytes(self.read(regs["rsp"],0x900))
        (folder/"context.bin").write_bytes(raw)
        self.code(base,folder)
        data = {"thread_id":thread,"base":base,"registers":regs,"fault_address":fault,
                "memory_regions":{name:self.region(addr) for name,addr in (
                    ("grid",regs["rsi"]),("pointer_table",regs["rax"]),("fault_read",fault))}}
        if len(grid)>=0x80:
            ptr,x,y,stride = struct.unpack_from("<Qiii",grid,0x60)
            owner, = struct.unpack_from("<Q",grid,0x78)
            row,column = (c.c_int32(regs[n]).value for n in ("r15","r14"))
            data["grid"] = {"table":ptr,"origin_column":x,"origin_row":y,"stride":stride,
                            "row":row,"column":column,"row_offset":row-y,"column_offset":column-x,
                            "calculated_index":c.c_int32((row-y)*stride+column-x).value,"related_object":owner}
            (folder/"related-object.bin").write_bytes(self.read(owner,0x140))
        write_json(folder/"fault.json",data)


def monitor(target,folder,seconds):
    attach = win("DebugActiveProcess",w.BOOL,w.DWORD)
    detach = win("DebugActiveProcessStop",w.BOOL,w.DWORD)
    wait = win("WaitForDebugEvent",w.BOOL,c.c_void_p,w.DWORD)
    onward = win("ContinueDebugEvent",w.BOOL,w.DWORD,w.DWORD,w.DWORD)
    checked(attach(target.pid),"DebugActiveProcess")
    try:
        checked(win("DebugSetProcessKillOnExit",w.BOOL,w.BOOL)(False),"DebugSetProcessKillOnExit")
        end = time.monotonic()+seconds
        event = c.create_string_buffer(176)
        base = 0
        initial_break = True
        fault_captured = False
        while time.monotonic()<end and not (folder/"stop").exists():
            if not wait(event,500):
                error = c.get_last_error()
                if error not in (0,121,258):
                    raise OSError(error,"WaitForDebugEvent")
                continue
            kind,pid,tid = struct.unpack_from("<III",event)
            status = CONTINUE
            try:
                if kind==3:
                    handle,_,_,base = struct.unpack_from("<4Q",event,16)
                    if handle:
                        close(handle)
                    write_json(folder/"attached.json",{"pid":pid,"base":base})
                    print(f"Attached to FH4 PID {pid}, module at {base:#x}",flush=True)
                elif kind==6:
                    handle, = struct.unpack_from("<Q",event,16)
                    if handle:
                        close(handle)
                elif kind==1:
                    code,_,_,pc,params = struct.unpack_from("<IIQQI",event,16)
                    first_chance, = struct.unpack_from("<I",event,168)
                    if code==0x80000003 and initial_break:
                        initial_break = False
                    else:
                        status = NOT_HANDLED
                    if (code==0xc0000005 and pc-base==EXPECTED_RVA and params>=2 and not fault_captured):
                        fault, = struct.unpack_from("<Q",event,56)
                        target.fault(tid,base,fault,folder)
                        fault_captured = True
                        print("Captured the grid fault before Windows exception handling",flush=True)
                    elif not first_chance:
                        write_json(folder/"other-exception.json",{"code":hex(code),"rva":hex(pc-base),"thread":tid})
            finally:
                checked(onward(pid,tid,status),"ContinueDebugEvent")
            if kind==5:
                return
    finally:
        detach(target.pid)


if __name__=="__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid",required=True,type=int)
    parser.add_argument("--exe",required=True,type=Path)
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--seconds",default=900,type=int)
    parser.add_argument("--code-base",type=lambda x:int(x,0))
    args = parser.parse_args()
    kernel = c.WinDLL("kernel32",use_last_error=True)
    close = win("CloseHandle",w.BOOL,w.HANDLE)
    args.out.mkdir(parents=True,exist_ok=True)
    target = Target(args.pid,args.exe.resolve(strict=True))
    try:
        if args.code_base is None:
            monitor(target,args.out,args.seconds)
        else:
            target.code(args.code_base,args.out)
    finally:
        close(target.handle)
