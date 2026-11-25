"""Measure native TEXCOORD1 density per library material.

Native car meshes carry TEXCOORD1 as a metric-like unwrap (ACU_RSXTypeS_02 carPaint spans ~2-3 units per metre,
textured black plastics 10-17) that the material's detail layers (grain, flakes, dirt, normal detail) tile over.
FM4's second UV is several times denser and only present on LOD0, so the converter generates TEXCOORD1 itself
at the density native cars use for the same library material.

Density of a LOD0 mesh = largest TEXCOORD1 extent / largest position extent (from the mesh's quantisation
scale). The table keeps the median per library material with at least --min meshes, plus the overall median.

Writes expanded_garage/fh4-detail-uv-density-v1.json.

    python -m expanded_garage.research.extract_detail_density <FH4 media/cars> [--cars 120]
"""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
import struct
import sys
from zipfile import ZipFile

import numpy as np

from ..fh4 import modelbin_materials

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from fh4_modelbin_geometry import blobs, buffer_info, layout_elements  # noqa: E402


def mesh_densities(data):
    items = list(blobs(data))
    members = dict(modelbin_materials(data))
    material = {i["id"]: members.get(i["name"]) for i in items if i["tag"] == "MatI"}
    streams = {i["id"]: i for i in items if i["tag"] == "VerB"}
    layouts = [i for i in items if i["tag"] == "VLay"]
    for mesh in items:
        if mesh["tag"] != "Mesh" or mesh["name"] == "Shadow":
            continue
        o = mesh["offset"]
        lod, = struct.unpack_from("<H", data, o + 10)
        member = material.get(struct.unpack_from("<h", data, o + 2)[0])
        if not lod & 3 or member is None:
            continue
        layout, count = struct.unpack_from("<ii", data, o + 55)
        refs = [struct.unpack_from("<iIII", data, o + 63 + 16 * k) for k in range(count)]
        transforms = struct.unpack_from("<28f", data, o + 63 + 16 * count + 16)
        size = 2 * max(transforms[20:23])
        elements = {(name, index): (slot, offset) for name, index, slot, _, offset
                    in layout_elements(data, layouts[layout])}
        if ("TEXCOORD", 1) not in elements or size < 0.3:
            continue
        slot, offset = elements[("TEXCOORD", 1)]
        if refs[slot][0] not in streams:
            continue
        start, vertices, stride = buffer_info(data, streams[refs[slot][0]])
        raw = np.frombuffer(data, np.uint8, vertices * stride, start).reshape(vertices, stride)
        uv = raw[:, offset:offset + 4].copy().view("<u2") / 65535
        t = transforms[4:8]
        span = max(np.ptp(uv[:, 0]) * abs(t[1]), np.ptp(uv[:, 1]) * abs(t[3]))
        if span > 0:
            yield member.casefold(), span / size


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cars", type=Path)
    parser.add_argument("--cars", dest="count", type=int, default=120)
    parser.add_argument("--min", type=int, default=5)
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parents[1] / "fh4-detail-uv-density-v1.json")
    args = parser.parse_args()
    archives = sorted(p for p in args.cars.glob("*.zip")
                      if not p.name.startswith("EG_") and "traffic" not in p.name.casefold())
    random.Random(4).shuffle(archives)
    samples, sampled = defaultdict(list), []
    for path in archives[:args.count]:
        try:
            archive = ZipFile(path)
        except OSError:
            continue
        sampled.append(path.stem)
        for name in archive.namelist():
            low = name.casefold()
            if low.startswith("scene/") and low.endswith(".modelbin") and "wheel" not in low:
                try:
                    for member, density in mesh_densities(archive.read(name)):
                        samples[member].append(density)
                except (ValueError, struct.error, IndexError, KeyError):
                    continue
    every = [d for values in samples.values() for d in values]
    table = {member: round(float(np.median(values)), 3)
             for member, values in sorted(samples.items()) if len(values) >= args.min}
    args.out.write_text(json.dumps({
        "format": "fh4-detail-uv-density-v1",
        "unit": "TEXCOORD1 units per metre (largest UV extent / largest mesh extent, LOD0 meshes)",
        "default": round(float(np.median(every)), 3),
        "cars": len(sampled), "meshes": len(every),
        "materials": table,
    }, indent=1) + "\n", encoding="utf-8")
    print(f"{len(table)} materials from {len(every)} meshes of {len(sampled)} cars, default {np.median(every):.2f}")


if __name__ == "__main__":
    main()
