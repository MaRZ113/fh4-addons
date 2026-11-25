"""Survey the COLOR0 vertex channel native Modelbin meshes carry for each shared material.

FH4 shaders read per-vertex COLOR0 (R varies with the surface, G and B are near zero on glass,
lenses and paint, white on rotors and wheels). The generator has no source for these values, so
each material takes the roster median per channel.

Writes expanded_garage/fh4-vertex-colors-v1.json.

    python -m expanded_garage.research.extract_vertex_colors <FH4 media/cars> [--step N]
"""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import struct
from zipfile import BadZipFile, ZipFile

import numpy as np

from ..fh4 import previous_readers, verify_material

SAMPLES = 64


def layout_color_offset(geometry_module, data, layout):
    for name, _, slot, _, offset in geometry_module.layout_elements(data, layout):
        if name == "COLOR" and slot == 1:
            return offset
    return None


def main():
    import fh4_modelbin_geometry as raw
    parser = argparse.ArgumentParser()
    parser.add_argument("cars", type=Path)
    parser.add_argument("--step", type=int, default=4)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fh4-vertex-colors-v1.json")
    args = parser.parse_args()
    _, geometry = previous_readers()
    colors = defaultdict(list)
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
                items = list(raw.blobs(data))
                layouts = {i["id"]: i for i in items if i["tag"] == "VLay"}
                buffers = {i["id"]: i for i in items if i["tag"] == "VerB"}
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
                if block["tag"] != "Mesh":
                    continue
                material = struct.unpack_from("<h", data, block["offset"] + 2)[0]
                if not materials.get(material):
                    continue
                layout, slots = struct.unpack_from("<ii", data, block["offset"] + 55)
                if slots < 2 or layout not in layouts:
                    continue
                try:
                    offset = layout_color_offset(raw, data, layouts[layout])
                except Exception:
                    continue
                if offset is None:
                    continue
                stream, _, stride, _ = struct.unpack_from("<iIII", data, block["offset"] + 63 + 16)
                buffer = buffers.get(stream)
                if buffer is None:
                    continue
                count = struct.unpack_from("<i", data, buffer["offset"])[0]
                base = buffer["offset"] + 16
                step = max(1, count // SAMPLES)
                sample = b"".join(data[base + i * stride + offset: base + i * stride + offset + 4]
                                  for i in range(0, count, step))
                colors[materials[material]].append(np.frombuffer(sample, dtype=np.uint8).reshape(-1, 4))
    table = {}
    for material, chunks in sorted(colors.items()):
        values = np.concatenate(chunks)
        table[material] = {"rgba": [int(v) for v in np.median(values, axis=0)], "meshes": len(chunks)}
    args.out.write_text(json.dumps({"format": "fh4-vertex-colors-v1", "cars": cars, "step": args.step,
                                    "materials": table}, indent=1) + "\n", encoding="utf-8")
    print(f"{args.out}: {len(table)} materials over {cars} cars")


if __name__ == "__main__":
    main()
