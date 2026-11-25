"""Survey the two ATST bytes native MatI resources carry for each shared material.

Every MatI's MATI resource has an `ATST` metadata entry of two bytes: byte 0 is set on alpha-tested
materials (stitches, emblems, grilles, embossed alpha), byte 1 on translucent ones (gls_window 175 of
175, gls_windsheild 39 of 39, glass patterns, plate shadows, wheel_blur). A translucent glass MatI
written with 00 00 drew as opaque black.

Writes expanded_garage/fh4-material-atst-v1.json: per library material, the counts and the mode.

    python -m expanded_garage.research.extract_material_atst <FH4 media/cars> [--step N]
"""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import struct
from zipfile import BadZipFile, ZipFile

PREFIX = b"Game:\\Media\\cars\\_library\\materials\\"


def atst_entries(data):
    """(ATST bytes, library member) of every MatI. A metadata entry is tag, u16 (size << 4 | version) and
    u16 data offset counted from the entry itself; the MATI payload (length byte + path) follows."""
    position = data.find(b"TSTA")
    while position >= 0:
        packed, offset = struct.unpack_from("<HH", data, position + 4)
        value = data[position + offset: position + offset + (packed >> 4)]
        path = data.find(PREFIX, position, position + 512)
        if len(value) == 2 and path > 0:
            end = data.find(b".materialbin", path)
            if end > 0:
                yield value, data[path + len(PREFIX): end + len(".materialbin")].decode("ascii", "replace")
        position = data.find(b"TSTA", position + 4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cars", type=Path)
    parser.add_argument("--step", type=int, default=4)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fh4-material-atst-v1.json")
    args = parser.parse_args()
    counts = defaultdict(Counter)
    cars = 0
    for path in sorted(args.cars.glob("*.zip"))[::args.step]:
        try:
            archive = ZipFile(path)
        except BadZipFile:
            continue
        cars += 1
        for name in archive.namelist():
            if name.lower().endswith(".modelbin"):
                for value, member in atst_entries(archive.read(name)):
                    counts[member.replace("\\", "/").lower()][value.hex()] += 1
    table = {material: {"counts": dict(sorted(c.items())), "atst": c.most_common(1)[0][0]}
             for material, c in sorted(counts.items())}
    args.out.write_text(json.dumps({"format": "fh4-material-atst-v1", "cars": cars, "step": args.step,
                                    "materials": table}, indent=1) + "\n", encoding="utf-8")
    print(f"{args.out}: {len(table)} materials over {cars} cars")


if __name__ == "__main__":
    main()
