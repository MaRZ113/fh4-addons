"""Extract native MTPR parameter blocks for shared library materials from a donor car.

Writes expanded_garage/fh4-material-parameters-v1.json: {library member: MTPR payload hex}.
Only blocks whose text references stay inside the shared media library are kept.

    python -m expanded_garage.research.extract_material_parameters <media/cars/DONOR.zip>
"""

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path
from zipfile import ZipFile

# (donor MatI name, library member) pairs that the compiler binds.
WANTED = {
    ("gls_window", "exterior_glass/gls_window.materialbin"),
    ("gls_window", "exterior_glass/gls_windsheild.materialbin"),
    ("gls_window", "exterior_glass/gls_rearglass.materialbin"),
    ("gls_window", "interior_glass/gls_window_interior.materialbin"),
    ("gls_window", "interior_glass/gls_windsheild_interior.materialbin"),
    ("blackGlass", "specificuse/blackglass.materialbin"),
    ("defrostlines_001", "exterior_misc/defrostlines.materialbin"),
    ("defrostlines_001", "interior_misc/defrostlines.materialbin"),
    ("gls_red", "exterior_glass/gls_colored.materialbin"),
}
PREFIX = "game:\\media\\cars\\_library\\materials\\"


def blobs(data, base=0):
    _, _, _, _, _, _, count = struct.unpack_from("<IBBHIII", data, base)
    for i in range(count):
        tag, _, _, fields, meta, offset, size, _ = struct.unpack_from("<IBBHIIII", data, base + 20 + i * 24)
        names = {}
        for m in range(meta, meta + fields * 8, 8):
            kind, flags, rel = struct.unpack_from("<IHH", data, base + m)
            names[kind] = data[base + m + rel:base + m + rel + (flags >> 4)]
        yield struct.pack(">I", tag).decode(), base + offset, size, names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("donor", type=Path)
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parents[1] / "fh4-material-parameters-v1.json")
    args = parser.parse_args()
    found = {}
    with ZipFile(args.donor) as z:
        for member in z.namelist():
            if not member.lower().endswith(".modelbin"):
                continue
            data = z.read(member)
            for tag, off, _, meta in blobs(data):
                if tag != "MatI":
                    continue
                name = meta.get(0x4E616D65, b"").decode()
                path = mtpr = None
                for t2, o2, s2, _ in blobs(data, off):
                    if t2 == "MATI":
                        raw = data[o2 + 1:o2 + 1 + data[o2]].decode("ascii").lower()
                        path = raw.removeprefix(PREFIX).replace("\\", "/") if raw.startswith(PREFIX) else raw.lstrip("\\").replace("\\", "/")
                    if t2 == "MTPR":
                        mtpr = data[o2:o2 + s2]
                if (name, path) in WANTED and path not in found:
                    texts = [t.decode() for t in re.findall(rb"[ -~]{12,}", mtpr) if b":\\" in t]
                    if all("\\_library\\" in t.lower() for t in texts):
                        found[path] = {"donor_material": name, "donor_member": member, "mtpr": mtpr.hex()}
    record = {"format": "fh4-material-parameters-v1", "donor": args.donor.stem,
              "donor_sha256": hashlib.sha256(args.donor.read_bytes()).hexdigest(),
              "parameters": dict(sorted(found.items()))}
    args.out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    missing = sorted(p for _, p in WANTED if p not in found)
    print(f"{len(found)} parameter blocks written; missing: {missing}")


if __name__ == "__main__":
    main()
