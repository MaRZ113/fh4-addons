"""Pick a native gauge-face MTPR whose two texture slots a converted car points at its own dial texture.

Native analog clusters draw their dial faces with interior_misc/int_gauges; the MatI parameters carry two
car-owned textures (diffuse and night illumination) by path, e.g. ACU_RSXTypeS_02 `gauge_001`:
Game:\\Media\\cars\\acu_rsxtypes_02\\textures\\gauges\\swatches\\acu_rsxtypes_02_gauges_diff_<guid>.swatchbin,
each followed by the CRC-32 of the lower-case path. Writes expanded_garage/fh4-gauge-parameters-v1.json with
the donor block and the hashes of its texture parameters (first = diffuse, second = illumination).

    python -m expanded_garage.research.extract_gauge_parameters <FH4 media/cars> [--donor ACU_RSXTypeS_02]
"""

import argparse
import json
from pathlib import Path
import struct
from zipfile import ZipFile

import fh4_modelbin_geometry as raw

from .. import fh4_mtpr
from ..fh4 import previous_readers, verify_material

MATERIAL = "interior_misc/int_gauges.materialbin"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cars", type=Path)
    parser.add_argument("--donor", default="ACU_RSXTypeS_02")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fh4-gauge-parameters-v1.json")
    args = parser.parse_args()
    _, geometry = previous_readers()
    archive = ZipFile(args.cars / f"{args.donor}.zip")
    for name in archive.namelist():
        if not name.lower().endswith(".modelbin"):
            continue
        data = archive.read(name)
        try:
            library = {b["name"]: verify_material(data, b, geometry) for b in geometry.maps(data)[0]
                       if b["tag"] == "MatI"}
        except (ValueError, struct.error):   # skeleton-only Modelbins, empty MatIs
            continue
        for block in raw.blobs(data):
            if block["tag"] != "MatI" or library.get(block["name"]) != MATERIAL:
                continue
            body = data[block["offset"]:block["offset"] + block["size"]]
            for inner in raw.blobs(body):
                if inner["tag"] != "MTPR":
                    continue
                mtpr = body[inner["offset"]:inner["offset"] + inner["size"]]
                records, _ = fh4_mtpr.parse(mtpr, inner["version"])
                textures = [key for key, kind, *_ in records if kind == fh4_mtpr.TEXTURE]
                # The dial face proper: both textures are the car's own gauge swatches.
                if len(textures) != 2 or mtpr.lower().count(rb"\textures\gauges" + b"\\") != 2:
                    continue
                record = {"format": "fh4-gauge-parameters-v1", "material": MATERIAL, "mtpr": mtpr.hex(),
                          "version": list(inner["version"]), "diffuse": textures[0], "illumination": textures[1],
                          "donor": {"car": args.donor, "member": name, "mati": block["name"]}}
                args.out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
                print(f"{args.out}: {record['donor']}")
                return
    raise SystemExit("No two-texture int_gauges MatI in the donor")


if __name__ == "__main__":
    main()
