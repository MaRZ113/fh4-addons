"""Extract a donor car's light rig (Lights.bin + LightPresets.bin) in a car-independent form.

Native cars carry Lights.bin (version 1 in FH4: 0xDEADBEEF, version, model attachments as path +
u16 bone, then lights of id, flags, attachment, position, rotation, damage position, damage rotation,
then LOD overrides; ForzaTechStudio LightsBinParser) and their own LightPresets.bin (LDFB v1). A light's
id is the index of its preset. Positions are in the car frame.

Each light is stored by the role of the model it hangs on (head/tail lamp per side, centre stop lamp,
plate, interior) with its position normalised to the bounding box of that role's models, so a converted
car can place it inside its own lamps. Only the stock (`_a`) variants are used.

Writes expanded_garage/fh4-light-rig-v1.json.

    python -m expanded_garage.research.extract_light_rig <FH4 media/cars> [--donor ACU_RSXTypeS_02]
"""

import argparse
import json
from pathlib import Path
import re
import struct
from zipfile import ZipFile

from ..fh4 import modelbin_box

ROLES = (("head_L", r"^(headlightl|glasslhl)"), ("head_R", r"^(headlightr|glassrhl)"),
         ("tail_L", r"^(taillightl|glassltl)"), ("tail_R", r"^(taillightr|glassrtl)"),
         ("tail_C", r"^(taillightc|glassctl)"), ("plate", r"^platelight"), ("interior", r"^interior"))


def role(stem):
    stem = stem.casefold()
    return next((name for name, pattern in ROLES if re.search(pattern, stem)), None)


def read_lights(data):
    offset = 0

    def take(fmt):
        nonlocal offset
        value = struct.unpack_from(fmt, data, offset)
        offset += struct.calcsize(fmt)
        return value

    def string():
        nonlocal offset
        size, = take("<I")
        offset += size
        return data[offset - size:offset].decode("ascii")

    magic, version = take("<II")
    if magic != 0xDEADBEEF or version != 1:
        raise ValueError(f"Expected FH4 Lights.bin v1, got {magic:#x} v{version}")
    models = []
    for _ in range(take("<I")[0]):
        path = string()
        models.append((path, take("<H")[0]))
    lights = []
    for _ in range(take("<I")[0]):
        ident, flags, attachment = take("<III")
        lights.append(dict(id=ident, flags=flags, attachment=attachment, pos=take("<4f"), rot=take("<4f"),
                           dpos=take("<4f"), drot=take("<4f")))
    overrides, = take("<I")
    if overrides or offset != len(data):
        raise ValueError("Lights.bin LOD overrides are not supported")
    return models, lights


def stem(path):
    return re.split(r"[\\/]", path)[-1].rsplit(".", 1)[0]


def normalise(point, box):
    low, high = box
    return [(p - l) / (h - l) if h - l > 1e-6 else 0.5 for p, l, h in zip(point[:3], low, high)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cars", type=Path)
    parser.add_argument("--donor", default="ACU_RSXTypeS_02")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fh4-light-rig-v1.json")
    args = parser.parse_args()
    archive = ZipFile(args.cars / f"{args.donor}.zip")
    names = {n.casefold(): n for n in archive.namelist()}
    models, lights = read_lights(archive.read(names["lights.bin"]))
    boxes = {}
    for name in archive.namelist():
        if name.lower().endswith(".modelbin") and stem(name).casefold().endswith("_a"):
            kind = role(stem(name))
            if kind:
                low, high = modelbin_box(archive.read(name))
                old = boxes.get(kind)
                boxes[kind] = (low, high) if old is None else (
                    [min(a, b) for a, b in zip(old[0], low)], [max(a, b) for a, b in zip(old[1], high)])
    rig = []
    for light in lights:
        path, _ = models[light["attachment"]]
        kind = role(stem(path))
        if kind is None or not stem(path).casefold().endswith("_a") or kind not in boxes:
            continue
        rig.append(dict(role=kind, id=light["id"], flags=light["flags"], w=light["pos"][3],
                        pos=normalise(light["pos"], boxes[kind]), rot=list(light["rot"]),
                        dpos=normalise(light["dpos"], boxes[kind]), drot=list(light["drot"])))
    record = {"format": "fh4-light-rig-v1", "donor": args.donor, "presets": archive.read(names["lightpresets.bin"]).hex(),
              "role_patterns": dict(ROLES), "lights": rig,
              "donor_boxes": {k: [list(v[0]), list(v[1])] for k, v in sorted(boxes.items())}}
    args.out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"{args.out}: {len(rig)} of {len(lights)} donor lights over roles {sorted({l['role'] for l in rig})}")


if __name__ == "__main__":
    main()
