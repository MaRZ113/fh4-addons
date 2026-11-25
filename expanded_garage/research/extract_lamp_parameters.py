"""Pick a native lit-reflector MTPR to drive the lamp surfaces of converted cars.

Native lamps glow through MatI parameters, not through the library material alone: an int light
function (0 brake, 1 reverse, 13 low beam, 15 DRL, 17 high beam, 18 fog, 32 tail, 69 tail+brake,
70 turn, 71 headlamp; over 1 in 8 roster cars), a light colour and a brightness. FM4 has three lamp
techniques (head_light, tail_light, reverse_light); each becomes exterior_lights/lights_smooth_ch1
with this donor block, the function and colour patched per technique.

Picks the most common lights_smooth_ch1 MTPR that carries the function, colour, brightness and
light-map switch parameters. Writes expanded_garage/fh4-lamp-parameters-v1.json.

    python -m expanded_garage.research.extract_lamp_parameters <FH4 media/cars> [--step N]
"""

import argparse
from collections import Counter
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import fh4_modelbin_geometry as raw

from .. import fh4_mtpr
from ..fh4 import previous_readers, verify_material

MATERIAL = "exterior_lights/lights_smooth_ch1.materialbin"
# Raw hashes (public name tables label some of these with names that do not hash to them).
FUNCTION, COLOR, BRIGHTNESS, LIGHT_MAP = 0x19416CFE, 0x448306EA, 0xA7CAF8DD, 0xDFD4F37C


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cars", type=Path)
    parser.add_argument("--step", type=int, default=4)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fh4-lamp-parameters-v1.json")
    args = parser.parse_args()
    _, geometry = previous_readers()
    counts, sources = Counter(), {}
    for path in sorted(args.cars.glob("*.zip"))[::args.step]:
        try:
            archive = ZipFile(path)
        except BadZipFile:
            continue
        for name in archive.namelist():
            if not name.lower().endswith(".modelbin") or "lights" not in name.lower():
                continue
            data = archive.read(name)
            try:
                library = {b["name"]: verify_material(data, b, geometry) for b in geometry.maps(data)[0]
                           if b["tag"] == "MatI"}
            except Exception:
                continue
            for block in raw.blobs(data):
                if block["tag"] != "MatI" or library.get(block["name"]) != MATERIAL:
                    continue
                body = data[block["offset"]:block["offset"] + block["size"]]
                for inner in raw.blobs(body):
                    if inner["tag"] != "MTPR":
                        continue
                    mtpr = body[inner["offset"]:inner["offset"] + inner["size"]]
                    found = fh4_mtpr.values(mtpr, inner["version"])
                    if {FUNCTION, COLOR, BRIGHTNESS, LIGHT_MAP} <= found.keys():
                        key = (mtpr.hex(), inner["version"])
                        counts[key] += 1
                        sources.setdefault(key, {"car": path.name, "member": name, "mati": block["name"]})
    if not counts:
        raise SystemExit("No donor lamp MTPR found")
    (mtpr, version), count = counts.most_common(1)[0]
    record = {"format": "fh4-lamp-parameters-v1", "material": MATERIAL, "mtpr": mtpr, "version": list(version),
              "uses": count, "candidates": sum(counts.values()), "donor": sources[(mtpr, version)],
              "hashes": {"function": FUNCTION, "color": COLOR, "brightness": BRIGHTNESS, "light_map": LIGHT_MAP}}
    args.out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"{args.out}: donor {record['donor']} ({count} of {record['candidates']})")


if __name__ == "__main__":
    main()
