"""Survey the render bucket native Modelbin meshes use for each shared material.

Writes expanded_garage/fh4-render-buckets-v1.json: per library material, the counts of
(bucket) over render meshes and the bucket a translucent draw should use (the most common
bucket other than the opaque 17; 17 when the roster never draws the material translucent).

    python -m expanded_garage.research.extract_render_buckets <FH4 media/cars> [--step N]
"""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import struct
from zipfile import BadZipFile, ZipFile

from ..fh4 import previous_readers, verify_material

OPAQUE = 17


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cars", type=Path)
    parser.add_argument("--step", type=int, default=4)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fh4-render-buckets-v1.json")
    args = parser.parse_args()
    _, geometry = previous_readers()
    counts = defaultdict(Counter)
    cars = 0
    for path in sorted(args.cars.glob("*.zip"))[::args.step]:
        try:
            archive = ZipFile(path)
        except BadZipFile:
            continue
        cars += 1
        for name in archive.namelist():
            if not name.lower().endswith(".modelbin"):
                continue
            data = archive.read(name)
            try:
                chunks = geometry.maps(data)[0]
            except Exception:
                continue
            # Meshes name their material by the MatI id, which need not follow file order.
            materials = {}
            for block in chunks:
                if block["tag"] == "MatI":
                    try:
                        materials[block["id"]] = verify_material(data, block, geometry)
                    except Exception:
                        materials[block["id"]] = None
            for block in chunks:
                if block["tag"] == "Mesh":
                    fields = struct.unpack_from("<5hHBBH5BH", data, block["offset"])
                    if materials.get(fields[1]):
                        counts[materials[fields[1]]][fields[8]] += 1
    table = {}
    for material, buckets in sorted(counts.items()):
        translucent = [(n, b) for b, n in buckets.items() if b != OPAQUE]
        table[material] = {"counts": {str(b): n for b, n in sorted(buckets.items())},
                           "translucent": max(translucent)[1] if translucent else OPAQUE}
    args.out.write_text(json.dumps({"format": "fh4-render-buckets-v1", "cars": cars, "step": args.step,
                                    "materials": table}, indent=1) + "\n", encoding="utf-8")
    print(f"{args.out}: {len(table)} materials over {cars} cars")


if __name__ == "__main__":
    main()
