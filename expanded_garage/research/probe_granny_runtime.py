"""Load generated Granny containers in a separate local middleware test process."""

import argparse
import ctypes
from pathlib import Path
from zipfile import ZipFile

from expanded_garage.vehicle_ir import sha256, write_json


def reload_assets(library, archive):
    granny = ctypes.WinDLL(str(library.resolve()))
    read = granny.GrannyReadEntireFileFromMemory
    read.argtypes = (ctypes.c_int32,ctypes.c_void_p)
    read.restype = ctypes.c_void_p
    release = granny.GrannyFreeFile
    release.argtypes = (ctypes.c_void_p,)
    release.restype = None
    inspected = []
    with ZipFile(archive) as native:
        for name in native.namelist():
            if not name.lower().endswith((".gr2",".gsf")):
                continue
            content = native.read(name)
            saved = ctypes.create_string_buffer(content)
            handle = read(len(content),saved)
            inspected.append({"member":name,"middleware_load":bool(handle)})
            if handle:
                release(handle)
    return {"archive":archive.name,"archive_sha256":sha256(archive),"library_sha256":sha256(library),
            "members":inspected,"coverage":"Native Granny container reload. FH4 graph use and physics remain unobserved."}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--library",type=Path,required=True)
    p.add_argument("--archive",type=Path,action="append",required=True)
    p.add_argument("--out",type=Path,required=True)
    args = p.parse_args()
    write_json(args.out,[reload_assets(args.library,archive) for archive in args.archive])


if __name__ == "__main__":
    main()
