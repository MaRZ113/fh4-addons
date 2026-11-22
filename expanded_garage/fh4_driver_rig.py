"""Driver IK anchors and scene locators transferred from a native donor car.

Every non-traffic FH4 car skeleton carries the driver anchors (anchor_butt,
hands on the steering spindle, pedal bones with foot anchors, shifter) and an
IKAnchorBones.xml. The purchase and entry cutscenes seat the avatar on them.
FM4 has no equivalent data, so the anchors are placed relative to the car's own
steering spindle with the donor's offsets, and the donor's locators are mapped
onto the car with a per-axis affine fit over the shared hardpoints.
"""

from importlib.resources import files
import json
import re
import xml.etree.ElementTree as ET

import numpy as np

from .fh4_model import Bone, transform

RIG_FILE = "fh4-driver-rig-v1.json"
STEERING = "boneSteeringWheelSpindle"
# Shared hardpoints for the locator fit. Wheel locators are excluded: FM4 hub
# positions and FH4 wheel locators (outer tyre face) are different points.
FIT_PAIRS = {0: "carLocator_headlightL", 1: "carLocator_headlightR", 2: "carLocator_taillightL",
             3: "carLocator_taillightR", 9: "carLocator_steeringWheel"}
SCALE_LIMITS = (0.7, 1.4)


def load_rig():
    return json.loads(files(__package__).joinpath(RIG_FILE).read_text(encoding="utf-8"))


def _world(bones):
    world = []
    for bone in bones:
        local = transform(bone.translation, bone.rotation)
        world.append(local @ world[bone.parent] if bone.parent >= 0 else local)
    return world


def add_driver_bones(bones, driver_side, rig=None):
    """Append the donor driver anchors to `bones` in place; existing indices stay stable."""
    rig = rig or load_rig()
    names = {bone.name: i for i, bone in enumerate(bones)}
    if STEERING not in names:
        raise ValueError("Driver anchors need the steering spindle bone")
    if driver_side not in ("L", "R"):
        raise ValueError(f"Unknown driver side {driver_side!r}")
    mirror = -1.0 if driver_side != rig["driver_side"] else 1.0
    spindle = _world(bones)[names[STEERING]][3, :3]
    added = []
    for entry in rig["bones"]:
        if entry["name"] in names:
            continue
        rotation = tuple(entry["rotation"])
        if mirror < 0:
            x, y, z, w = rotation
            rotation = (x, -y, -z, w)
        if entry["parent"] == "root":
            offset = np.array(entry["offset_from_steering_m"]) * (mirror, 1.0, 1.0)
            bone = Bone(entry["name"], names["root"], tuple((spindle + offset).tolist()), rotation)
        else:
            if entry["parent"] not in names:
                raise ValueError(f"Driver anchor parent {entry['parent']} is missing")
            bone = Bone(entry["name"], names[entry["parent"]], tuple(entry["local_m"]), rotation)
        names[bone.name] = len(bones)
        bones.append(bone)
        added.append(bone.name)
    return {"donor": rig["donor"], "donor_sha256": rig["donor_sha256"], "driver_side": driver_side,
            "mirrored": mirror < 0, "bones": added,
            "placement": "donor offsets from the steering spindle; pedal and hand anchors in donor local frames"}


def ik_anchor_xml(rig=None):
    rig = rig or load_rig()
    return rig["ik_anchor_xml"].encode("utf-8")


def validate_ik_anchor_xml(data, bone_names):
    root = ET.fromstring(data)
    if root.tag != "IKAnchorBones":
        raise ValueError("Unexpected IK anchor envelope")
    names = [node.get("Name") for node in root.findall("Bone")]
    missing = sorted(set(names) - set(bone_names))
    if not names or missing:
        raise ValueError(f"IK anchors without skeleton bones: {missing}")
    for node in root.findall("Bone"):
        if any(not np.isfinite(float(node.get(axis))) for axis in ("OffsetX", "OffsetY", "OffsetZ")):
            raise ValueError("Non-finite IK anchor offset")
    return names


MIRROR = np.diag([-1.0, 1.0, 1.0, 1.0])


def mirrored_name(name):
    """Swap the side of corner and side-specific locator names (LF<->RF, skirtL<->skirtR, Left<->Right)."""
    swap = {"L": "R", "R": "L"}
    if m := re.fullmatch(r"(.*)(L|R)(F|R)", name):
        return m[1] + swap[m[2]] + m[3]
    # A single trailing L/R is a side, except bumperR (rear bumper).
    if (m := re.fullmatch(r"(.*)(L|R)", name)) and not m[1].endswith("bumper"):
        return m[1] + swap[m[2]]
    if m := re.fullmatch(r"(.*)(Left|Right)", name):
        return m[1] + ("Right" if m[2] == "Left" else "Left")
    return name


def donor_locators(rig, driver_side):
    """Donor locators, mirrored across X when the driver sides differ."""
    if driver_side == rig["driver_side"]:
        return [dict(loc) for loc in rig["locators"]]
    result = []
    for loc in rig["locators"]:
        matrix = (MIRROR @ np.array(loc["matrix"]).reshape(4, 4) @ MIRROR).ravel().tolist()
        result.append({**loc, "name": mirrored_name(loc["name"]), "matrix": matrix})
    return result


def fit_axes(points, locators):
    """Map donor positions onto the car: X and Z by least squares, Y by offset only.

    Lamp and steering heights do not constrain a vertical scale (the fit is
    degenerate on some cars), so the vertical axis keeps the donor's spacing.
    """
    donor = {loc["name"]: np.array(loc["matrix"][12:15]) for loc in locators}
    pairs = [(donor[name], np.array(points[part].meters)) for part, name in FIT_PAIRS.items()
             if part in points and name in donor]
    if len(pairs) < 3:
        raise ValueError("Too few shared hardpoints for the locator transfer")
    src = np.array([p for p, _ in pairs])
    dst = np.array([q for _, q in pairs])
    coefficients = []
    for axis in range(3):
        if axis == 1:
            a, b = 1.0, float(np.mean(dst[:, 1] - src[:, 1]))
        else:
            a, b = np.linalg.lstsq(np.c_[src[:, axis], np.ones(len(src))], dst[:, axis], rcond=None)[0]
        if not SCALE_LIMITS[0] <= a <= SCALE_LIMITS[1]:
            raise ValueError(f"Implausible donor locator scale {a:.3f} on axis {'XYZ'[axis]}")
        coefficients.append((float(a), float(b)))
    residual = float(np.max(np.abs(src * [c[0] for c in coefficients] + [c[1] for c in coefficients] - dst)))
    return coefficients, residual


def transferred_locators(points, authored_names, driver_side, rig=None):
    rig = rig or load_rig()
    locators = donor_locators(rig, driver_side)
    coefficients, residual = fit_axes(points, locators)
    result = []
    for loc in locators:
        if loc["name"] in authored_names:
            continue
        matrix = list(loc["matrix"])
        for axis, (a, b) in enumerate(coefficients):
            matrix[12 + axis] = a * matrix[12 + axis] + b
        result.append({"name": safe_name(loc["name"]), "matrix": matrix, "upgrade": loc["upgrade"]})
    receipt = {"donor": rig["donor"], "mirrored": driver_side != rig["driver_side"],
               "fit": {"XYZ"[i]: {"scale": a, "offset_m": b} for i, (a, b) in enumerate(coefficients)},
               "max_hardpoint_residual_m": residual, "locators": [r["name"] for r in result],
               "bones": "donor door and accessory bones are absent; every transferred locator attaches to <root>"}
    return result, receipt


def safe_name(name):
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError(f"Unexpected locator name {name!r}")
    return name
