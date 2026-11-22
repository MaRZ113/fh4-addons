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


def steering_spindle(bones):
    names = {bone.name: i for i, bone in enumerate(bones)}
    return _world(bones)[names[STEERING]][3, :3]


# Locators that frame the driver's head: placed from the car's steering spindle with the donor's offsets,
# like anchor_butt, so the cockpit camera sits where the avatar's head is.
DRIVER_FRAMED = ("carLocator_seatCamLF", "carLocator_seatCamRF")


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


def transferred_locators(points, authored_names, driver_side, rig=None, spindle=None):
    rig = rig or load_rig()
    locators = donor_locators(rig, driver_side)
    coefficients, residual = fit_axes(points, locators)
    donor_spindle = np.array(rig["steering_spindle_m"]) * ((-1.0 if driver_side != rig["driver_side"] else 1.0), 1, 1)
    result = []
    framed = []
    for loc in locators:
        if loc["name"] in authored_names:
            continue
        matrix = list(loc["matrix"])
        if spindle is not None and loc["name"] in DRIVER_FRAMED:
            matrix[12:15] = (np.asarray(spindle) + np.array(matrix[12:15]) - donor_spindle).tolist()
            framed.append(loc["name"])
        else:
            for axis, (a, b) in enumerate(coefficients):
                matrix[12 + axis] = a * matrix[12 + axis] + b
        result.append({"name": safe_name(loc["name"]), "matrix": matrix, "upgrade": loc["upgrade"]})
    receipt = {"donor": rig["donor"], "mirrored": driver_side != rig["driver_side"],
               "fit": {"XYZ"[i]: {"scale": a, "offset_m": b} for i, (a, b) in enumerate(coefficients)},
               "max_hardpoint_residual_m": residual, "locators": [r["name"] for r in result],
               "driver_framed": framed,
               "bones": "donor door and accessory bones are absent; every transferred locator attaches to <root>"}
    return result, receipt


def safe_name(name):
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError(f"Unexpected locator name {name!r}")
    return name


AUTOVISTA_FILE = "fh4-autovista-v1.json"


def autovista(points, locator_names, driver_side):
    """<media>.avpins from the native donor: root-frame positions mapped onto the car.

    Every native archive (299 of 300 sampled) carries an Autovista points-of-interest
    file with seat views, camera distances and apex angles. Without it the garage
    presentation falls back to a 100/999 shot whose camera turns non-finite.
    Positions under Locator="root" are in car space and use the hardpoint fit;
    positions under other locators are local offsets and stay unchanged.
    """
    record = json.loads(files(__package__).joinpath(AUTOVISTA_FILE).read_text(encoding="utf-8"))
    if driver_side != record["driver_side"]:
        # Seat states and views are side-specific (InLeftFrontSeat, LeftFrontSeatView); no mirror yet.
        return None, {"donor": record["donor"], "generated": False,
                      "reason": "donor has the opposite driver side; Autovista mirroring is not implemented"}
    donor = [{"name": name, "matrix": [0.0] * 12 + list(xyz) + [1.0]} for name, xyz in record["fit_points"].items()]
    coefficients, residual = fit_axes(points, donor)
    root = ET.fromstring(record["avpins"])
    referenced, moved = set(), 0

    def walk(node, frame):
        nonlocal moved
        frame = node.get("Locator", frame)
        if node.get("Locator", "").strip():
            referenced.add(node.get("Locator"))
        if node.tag == "Pos" and frame == "root":
            for axis, (a, b) in zip("XYZ", coefficients):
                node.set(axis, f"{a * float(node.get(axis)) + b:.6f}")
            moved += 1
        for child in node:
            walk(child, frame)

    walk(root, "root")
    unknown = sorted(r for r in referenced if r != "root" and r not in locator_names)
    if unknown:
        raise ValueError(f"Autovista references locators the car lacks: {unknown}")
    data = b"\xef\xbb\xbf" + ET.tostring(root, encoding="utf-8")
    return data, {"donor": record["donor"], "donor_sha256": record["donor_sha256"],
                  "template": root.get("Template"), "moved_root_positions": moved,
                  "max_hardpoint_residual_m": residual, "referenced_locators": sorted(referenced)}


def validate_autovista(data, locator_names):
    root = ET.fromstring(data.decode("utf-8-sig"))
    if root.tag != "PointsOfInterest" or not root.get("Template"):
        raise ValueError("Unexpected Autovista envelope")
    refs = {n.get("Locator") for n in root.iter() if (n.get("Locator") or "").strip()}
    unknown = sorted(r for r in refs if r != "root" and r not in locator_names)
    if unknown:
        raise ValueError(f"Autovista references unknown locators {unknown}")
    for pos in root.iter("Pos"):
        if any(not np.isfinite(float(pos.get(a))) for a in "XYZ"):
            raise ValueError("Non-finite Autovista position")
    return root.get("Template")
