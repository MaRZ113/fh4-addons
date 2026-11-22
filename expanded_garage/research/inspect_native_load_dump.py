"""Extract just the wheel-selection fault and SQL evidence from an FH4 minidump."""

import argparse
from hashlib import sha256
from pathlib import Path
import re
import struct

from expanded_garage.fh4_installer import EXE_SHA
from expanded_garage.vehicle_ir import write_json


def read(fmt,data,offset):
    return struct.unpack_from("<"+fmt,data,offset)


def inspect(dump,exe):
    raw = dump.read_bytes()
    image = exe.read_bytes()
    digest = sha256(image).hexdigest()
    if raw[:4]!=b"MDMP" or digest!=EXE_SHA:
        raise ValueError("Expected the pinned FH4 Steam executable and Windows minidump")
    count,directory = read("II",raw,8)
    streams = {kind:rva for kind,_,rva in (read("III",raw,directory+12*n) for n in range(count))}
    modules = streams[4]
    pe, = read("I",image,60)
    expected_size, = read("I",image,pe+24+56)
    expected_checksum, = read("I",image,pe+24+64)
    expected_stamp, = read("I",image,pe+8)
    for n in range(read("I",raw,modules)[0]):
        base,size,checksum,stamp,path_at = read("QIIII",raw,modules+4+108*n)
        path_len, = read("I",raw,path_at)
        path = raw[path_at+4:path_at+4+path_len].decode("utf-16-le")
        if path.casefold().endswith("\\forzahorizon4.exe"):
            break
    else:
        raise ValueError("Missing FH4 module in minidump")
    if (size,checksum,stamp)!=(expected_size,expected_checksum,expected_stamp):
        raise ValueError("The captured module identity differs from the pinned executable")
    exc = streams[6]
    tid, = read("I",raw,exc)
    code, = read("I",raw,exc+8)
    pc, = read("Q",raw,exc+24)
    access,fault = read("QQ",raw,exc+40)
    _,context = read("II",raw,exc+160)
    rax,rcx,_,_,rsp,rbp,rsi,rdi,*_,rip = read("17Q",raw,context+120)
    memory = streams[5]
    ranges = [read("QII",raw,memory+4+16*n) for n in range(read("I",raw,memory)[0])]

    def at(addr,num):
        for start,length,file_at in ranges:
            delta = addr-start
            if 0<=delta<=length-num:
                return raw[file_at+delta:file_at+delta+num]
        raise ValueError(f"Minidump has no complete memory range for {addr:#x}")

    prefix = at(pc-5,23)
    expected = bytes.fromhex("48634c2468f30f108cbe74070000f30f5e8c8874070000")
    if (pc!=rip or pc-base!=0x095599ae or code!=0xc0000005 or access!=0 or prefix!=expected
            or rsi+rdi*4+0x774!=fault):
        raise ValueError("Captured instruction and read address do not match this wheel-selection fault")
    sql_region = at(rbp-64,160)
    query, = re.findall(rb"SELECT Mass, MassLevel FROM List_Wheels WHERE Id = [0-9]+",sql_region)
    raw_factors = read("5f",at(rsi+0x774,20),0)
    return {"module":"ForzaHorizon4.exe","exe_sha256":digest,"dump_sha256":sha256(raw).hexdigest(),
            "thread_id":tid,"exception":hex(code),"access":"read","image_base":hex(base),
            "fault_rva":hex(pc-base),"fault_address":hex(fault),"code_bytes_from_rva_95599a9":prefix.hex(),
            "fault_instruction":"movss xmm1, dword ptr [rsi + rdi*4 + 0x774]",
            "next_instruction":"divss xmm1, dword ptr [rax + rcx*4 + 0x774]",
            "indices":{"rdi":rdi,"rcx":rcx,"rhs_saved_at_rsp_plus_0x68":read("i",at(rsp+0x68,4),0)[0]},
            "coefficient_base_rva":hex(rsi-base+0x774),"coefficient_values":[round(x,6) for x in raw_factors],
            "stack_sql":query.decode("ascii"),"module_size":size,
            "scope":"Captured AMD64 exception context and SQL text on its own thread stack; no symbolic unwind."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump",type=Path,required=True)
    parser.add_argument("--exe",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args = parser.parse_args()
    write_json(args.out,inspect(args.dump,args.exe))
