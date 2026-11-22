"""Collect (RGB, paint material) pairs from every native ManufacturerColors.bin.

Writes expanded_garage/fh4-paint-palette-v1.json, used to pick an existing
library paint material for each source manufacturer colour.

    python -m expanded_garage.research.extract_paint_palette <media/cars>
"""

import argparse
import json
import struct
from collections import Counter
from pathlib import Path
from zipfile import ZipFile, BadZipFile

PREFIX = "game:\\media\\cars\\_library\\materials\\"


def entries(data):
    """Yield (header bytes, rgb, path) for each material path in an MNCL 1.1 payload.

    Record headers vary between archives (4 or 5 bytes before the RGB floats),
    so each record is anchored on its length-prefixed library path instead.
    """
    if data[:4] != b"burG" or data[20:24] != b"LCNM":
        raise ValueError("Unexpected ManufacturerColors bundle")
    offset, size = struct.unpack_from("<II", data, 20 + 12)
    payload = data[offset:offset + size]
    start, previous_end = 0, 1
    while (hit := payload.lower().find(b"game:\\media", start)) >= 0:
        length = payload[hit - 1]
        path = payload[hit:hit + length]
        rgb = struct.unpack_from("<3f", payload, hit - 13)
        if not path.lower().endswith(b".materialbin") or not all(0.0 <= v <= 1.0 for v in rgb):
            raise ValueError("Unexpected ManufacturerColors record")
        yield payload[previous_end:hit - 13].hex(), rgb, path.decode("latin-1")
        previous_end = hit + length
        start = previous_end


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cars", type=Path)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fh4-paint-palette-v1.json")
    args = parser.parse_args()
    palette, kinds, cars, failures = {}, Counter(), 0, {}
    for archive in sorted(args.cars.glob("*.zip")):
        try:
            with ZipFile(archive) as z:
                names = {n.lower(): n for n in z.namelist()}
                if "manufacturercolors.bin" not in names:
                    continue
                data = z.read(names["manufacturercolors.bin"])
            records = list(entries(data))
        except (BadZipFile, ValueError, UnicodeDecodeError, struct.error) as error:
            failures[archive.name] = str(error)
            continue
        cars += 1
        for header, rgb, path in records:
            kinds[len(header) // 2] += 1
            member = path.lower()
            if not member.startswith(PREFIX):
                continue
            member = member.removeprefix(PREFIX).replace("\\", "/")
            palette.setdefault(member, {"rgb": [round(v * 255) for v in rgb], "cars": 0})["cars"] += 1
    record = {"format": "fh4-paint-palette-v1", "source_cars": cars, "entry_headers": dict(kinds),
              "unparsed": failures, "materials": dict(sorted(palette.items()))}
    args.out.write_text(json.dumps(record, indent=0) + "\n", encoding="utf-8")
    print(f"{len(palette)} paint materials from {cars} cars; headers {dict(kinds)}; unparsed {len(failures)}")


if __name__ == "__main__":
    main()
