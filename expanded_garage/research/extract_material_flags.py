"""Survey the per-material flags native Carbin model records carry for each shared material.

Every Carbin model record (v3+) lists the MatI names of its Modelbin with a u32 bit field. The bits
select the car colour slot that drives the material: carpaint 1 (1391 of 1469 sampled), livery 2,
window glass 128 (gls_window 530 of 549), and others for secondary paint, rims and trims. A model
whose MatIs are missing from the list is not tied to any colour slot.

Writes expanded_garage/fh4-material-flags-v1.json: per library material, the flag counts and the mode.

    python -m expanded_garage.research.extract_material_flags <FH4 media/cars> [--step N]
"""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from ..fh4 import previous_readers, verify_material


def model_materials(archive, members, path, geometry):
    """{MatI name (casefolded): library member} of the Modelbin a Carbin path names."""
    relative = path.casefold().replace("\\", "/").split("/", 4)[-1]
    member = members.get(relative)
    if member is None:
        return {}
    data = archive.read(member)
    try:
        chunks = geometry.maps(data)[0]
    except Exception:
        return {}
    found = {}
    for block in chunks:
        if block["tag"] == "MatI":
            try:
                found[block["name"].casefold()] = verify_material(data, block, geometry)
            except Exception:
                pass
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cars", type=Path)
    parser.add_argument("--step", type=int, default=4)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fh4-material-flags-v1.json")
    args = parser.parse_args()
    Reader, geometry = previous_readers()
    counts = defaultdict(Counter)
    cars = 0
    for path in sorted(args.cars.glob("*.zip"))[::args.step]:
        try:
            archive = ZipFile(path)
        except BadZipFile:
            continue
        carbins = [n for n in archive.namelist() if n.casefold().endswith(".carbin")]
        if not carbins:
            continue
        reader = Reader(archive.read(carbins[0]))
        try:
            reader.scene()
        except Exception:
            continue
        cars += 1
        members = {n.casefold(): n for n in archive.namelist()}
        cache = {}
        for entry in reader.material_flags:
            if entry["model"] not in cache:
                cache[entry["model"]] = model_materials(archive, members, entry["model"], geometry)
            material = cache[entry["model"]].get(entry["material"].casefold())
            if material:
                counts[material][entry["flags"]] += 1
    table = {material: {"counts": {str(k): n for k, n in sorted(flags.items())},
                        "flags": flags.most_common(1)[0][0]}
             for material, flags in sorted(counts.items())}
    args.out.write_text(json.dumps({"format": "fh4-material-flags-v1", "cars": cars, "step": args.step,
                                    "materials": table}, indent=1) + "\n", encoding="utf-8")
    print(f"{args.out}: {len(table)} materials over {cars} cars")


if __name__ == "__main__":
    main()
