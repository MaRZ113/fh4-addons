"""Sample target-process ZIP handles without attaching a debugger."""

import argparse
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import time

from . import capture_grid_fault as native


def watch(host,pid,paths,out,seconds):
    native.kernel = c.WinDLL("kernel32",use_last_error=True)
    target = native.Target(pid,(host/"ForzaHorizon4.exe").resolve(strict=True))
    create = native.win("CreateFileW",w.HANDLE,w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE)
    close = native.win("CloseHandle",w.BOOL,w.HANDLE)
    ntdll = c.WinDLL("ntdll")
    query = ntdll.NtQueryInformationFile
    query.argtypes = (w.HANDLE,c.c_void_p,c.c_void_p,w.ULONG,w.ULONG)
    query.restype = c.c_long
    read = c.create_string_buffer(8192)
    status = c.create_string_buffer(16)
    handles = {}
    try:
        for name in paths:
            path = (host/name).resolve(strict=True)
            if not path.is_relative_to(host) or path.suffix.casefold()!=".zip":
                raise ValueError(f"Expected a game ZIP under {host}")
            handle = create(str(path),0,7,None,3,0x80,None)
            if handle==c.c_void_p(-1).value:
                raise OSError(c.get_last_error(),"CreateFileW")
            handles[name] = handle
        with out.open("x",encoding="utf-8",buffering=1) as stream:
            def emit(value):
                stream.write(json.dumps({"utc":datetime.now(timezone.utc).isoformat(),**value})+"\n")
            emit({"pid":pid,"sample_period_ms":50,"paths":paths})
            end = time.monotonic()+seconds
            state = {}
            samples = 0
            exit_status = w.DWORD()
            alive = native.win("GetExitCodeProcess",w.BOOL,w.HANDLE,c.POINTER(w.DWORD))
            while time.monotonic()<end and not out.with_suffix(".stop").exists():
                native.checked(alive(target.handle,c.byref(exit_status)),"GetExitCodeProcess")
                if exit_status.value!=259:
                    break
                for name,handle in handles.items():
                    rc = query(handle,status,read,len(read),47)
                    if rc:
                        raise OSError(rc,"NtQueryInformationFile")
                    n, = struct.unpack_from("<I",read)
                    held = pid in struct.unpack_from(f"<{n}Q",read,8)
                    if state.get(name)!=held:
                        emit({"archive":name,"held":held})
                        state[name] = held
                samples += 1
                time.sleep(.05)
            emit({"samples":samples})
    finally:
        for handle in (*handles.values(),target.handle):
            close(handle)


if __name__=="__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host",required=True,type=Path)
    parser.add_argument("--pid",required=True,type=int)
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--seconds",default=600,type=int)
    parser.add_argument("paths",nargs="+")
    args = parser.parse_args()
    watch(args.host.resolve(strict=True),args.pid,args.paths,args.out,args.seconds)
