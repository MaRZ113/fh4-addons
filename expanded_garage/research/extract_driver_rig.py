"""Extract the driver IK rig and scene locators of a native donor car.

Writes expanded_garage/fh4-driver-rig-v1.json. The compiler reads only that
file, so builds stay reproducible without the game install.

    python -m expanded_garage.research.extract_driver_rig <media/cars/DONOR.zip>
"""

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path
from zipfile import ZipFile

import numpy as np

DRIVER_BONES = ("anchor_butt", "boneBrake", "anchor_brake", "boneClutch", "anchor_clutch",
                "boneGas", "anchor_gas", "anchor_shifter", "anchor_lefthand", "anchor_righthand")
STEERING = "boneSteeringWheelSpindle"


def chunks(data, base=0):
    _, _, _, _, _, _, count = struct.unpack_from("<IBBHIII", data, base)
    for i in range(count):
        tag, _, _, _, _, offset, size, _ = struct.unpack_from("<IBBHIIII", data, base + 20 + i * 24)
        yield struct.pack(">I", tag).decode(), base + offset, size


def skeleton(data):
    for tag, off, _ in chunks(data):
        if tag != "Skel":
            continue
        count, = struct.unpack_from("<H", data, off)
        o, bones = off + 2, []
        for _ in range(count):
            n, = struct.unpack_from("<I", data, o)
            name = data[o + 4:o + 4 + n].decode("utf-8")
            o += 4 + n
            parent, _, _ = struct.unpack_from("<3h", data, o)
            local = np.array(struct.unpack_from("<16f", data, o + 6), dtype=np.float64).reshape(4, 4)
            o += 6 + 64
            bones.append((name, parent, local))
        return bones
    raise ValueError("Donor skeleton has no Skel chunk")


def quaternion(m):
    """Quaternion (x, y, z, w) of a row-vector rotation block, matching fh4_model.transform."""
    r = m[:3, :3].T
    w = np.sqrt(max(0.0, 1 + r[0, 0] + r[1, 1] + r[2, 2])) / 2
    x = np.copysign(np.sqrt(max(0.0, 1 + r[0, 0] - r[1, 1] - r[2, 2])) / 2, r[2, 1] - r[1, 2])
    y = np.copysign(np.sqrt(max(0.0, 1 - r[0, 0] + r[1, 1] - r[2, 2])) / 2, r[0, 2] - r[2, 0])
    z = np.copysign(np.sqrt(max(0.0, 1 - r[0, 0] - r[1, 1] + r[2, 2])) / 2, r[1, 0] - r[0, 1])
    q = np.array((x, y, z, w))
    return (q / np.linalg.norm(q)).tolist()


def locators(text):
    result = []
    for block in re.findall(r"<Locator .*?</Locator>", text, re.S):
        name = re.search(r'<Name value="([^"]*)"', block)[1]
        bone = re.search(r'BoneName="([^"]*)"', block)[1]
        matrix = dict(re.findall(r'value\._(\d\d)="([^"]*)"', block))
        upgrade = dict(re.findall(r'(\w+)="([^"]*)"', re.search(r"<UpgradeInfo [^>]*>", block)[0]))
        result.append({"name": name, "donor_bone": bone,
                       "matrix": [float(matrix[f"{r}{c}"]) for r in "1234" for c in "1234"],
                       "upgrade": upgrade})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("donor", type=Path)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fh4-driver-rig-v1.json")
    args = parser.parse_args()
    media = args.donor.stem
    with ZipFile(args.donor) as z:
        names = {n.lower(): n for n in z.namelist()}
        skel = skeleton(z.read(names[f"scene/{media.lower()}_skeleton.modelbin"]))
        ik = z.read(names["ikanchorbones.xml"]).decode("utf-8")
        locs = locators(z.read(names["locators.xml"]).decode("utf-8"))
    world = []
    for name, parent, local in skel:
        world.append(local @ world[parent] if parent >= 0 else local)
    index = {name: i for i, (name, _, _) in enumerate(skel)}
    steering = world[index[STEERING]][3, :3]
    bones = []
    for name in DRIVER_BONES:
        if name not in index:
            continue
        i = index[name]
        parent = skel[skel[i][1]][0]
        local = skel[i][2]
        entry = {"name": name, "parent": parent, "rotation": quaternion(local)}
        if parent in ("root", "<root>"):
            entry["parent"] = "root"
            entry["offset_from_steering_m"] = (world[i][3, :3] - steering).round(6).tolist()
        else:
            entry["local_m"] = local[3, :3].round(6).tolist()
        bones.append(entry)
    record = {
        "format": "fh4-driver-rig-v1",
        "donor": media,
        "donor_sha256": hashlib.sha256(args.donor.read_bytes()).hexdigest(),
        "driver_side": "L" if steering[0] < 0 else "R",
        "steering_spindle_m": steering.round(6).tolist(),
        "bones": bones,
        "ik_anchor_xml": ik,
        "locators": locs,
    }
    args.out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"{args.out}: {len(bones)} bones, {len(locs)} locators from {media}")


if __name__ == "__main__":
    main()
