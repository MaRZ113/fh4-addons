"""Prepare source-owned hardpoints and native locator records."""

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import json
import os
from math import isfinite
import struct
import uuid
import xml.etree.ElementTree as ET
import numpy as np

from .fh4_db_compiler import Source
from .fh4_model import IDENTITY


CORNER_IDS = {5: "LF", 6: "RF", 7: "RR", 8: "LR"}
LOCATOR_IDS = {0: "headlightL", 1: "headlightR", 2: "taillightL", 3: "taillightR",
               **{k: "wheel" + v for k, v in CORNER_IDS.items()}, 9: "steeringWheel"}


@dataclass(frozen=True)
class Hardpoint:
    name: str
    meters: tuple
    source_asset: str
    part_id: int


def hardpoints(root, ir):
    src = Source(root, ir)
    result = {}
    for item in src.raw_records("CarPartPositions", "Ordinal", src.id):
        row = item["record"]
        part = row["PartId"]
        name = LOCATOR_IDS.get(part)
        if name is None:
            continue
        point = tuple(row[f"PartPos{dim}"] for dim in "XYZ")
        if part in result or not all(isfinite(v) for v in point):
            raise ValueError(f"Invalid FM4 hardpoint {part}")
        result[part] = Hardpoint(name, point, item["provenance"]["asset"], part)
    return result


@lru_cache(maxsize=1)
def canonical_locator_guids():
    """Native GUID per locator name (research/extract_locator_guids.py, 785 cars).

    The GUID, not the name, identifies a locator: one GUID covers 90% or more of the
    native cars carrying each of these names, and presentation shots that target a part
    (HomeSpaceCams.xml RotAnimPartToTarget="headlightL") need the native identity.
    """
    record = json.loads(files(__package__).joinpath("fh4-locator-guids-v1.json").read_text(encoding="utf-8"))
    return {name: entry["guid"] for name, entry in record["locators"].items()}


def locator_guid(alias, name):
    return canonical_locator_guids().get(name) or str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"urn:fh4-expanded-garage:locator:{alias.casefold()}:{name}"))


def locator_xml(alias, points, extra=()):
    """Authored hardpoints first, then transferred donor locators (name, matrix, upgrade)."""
    root = ET.Element("CarLocators", Version="2")
    entries = []
    for part, point in sorted(points.items()):
        rest = list(IDENTITY)
        if part in (2, 3):
            rest[0] = rest[10] = -1.0
        rest[12:15] = point.meters
        entries.append(("carLocator_" + point.name, rest, None))
    entries += [(item["name"], item["matrix"], item["upgrade"]) for item in extra]
    for name, rest, upgrade in entries:
        locator = ET.SubElement(root, "Locator", Version="3")
        guid = locator_guid(alias, name)
        for tag, value in (("GUID", guid), ("SrcGUID", guid), ("Name", name)):
            ET.SubElement(locator, tag, value=value)
        ET.SubElement(locator, "SceneTransform", {f"value._{i//4+1}{i%4+1}": f"{v:.9g}"
                                                 for i, v in enumerate(rest)})
        ET.SubElement(locator, "AttachToBone", BoneName="<root>", Snap="0")
        upgrade = upgrade or {"IsUpgradable": "1", "PartType": "CarBody", "UpgradeLevel": "4294967295",
                              "PartId": "-1", "ParentUpgradeId": "-1"}
        ET.SubElement(locator, "UpgradeInfo", upgrade)
    ET.indent(root, space="  ")
    return native_attribute_text(ET.tostring(root, encoding="utf-8"))


def native_attribute_text(data):
    """Native Locators.xml writes bone names raw (BoneName="<root>", 3,189 of 3,189 sampled attachments;
    no entity anywhere). The game's reader takes the attribute text literally, so "&lt;root&gt;" names no bone."""
    return data.replace(b"&lt;", b"<").replace(b"&gt;", b">")


def parse_native_xml(data):
    """Parse native-style XML whose attributes may hold raw angle brackets."""
    import re
    text = data.decode("utf-8-sig")
    text = re.sub(r'="([^"]*)"', lambda m: '="' + m[1].replace("<", "&lt;").replace(">", "&gt;") + '"', text)
    return ET.fromstring(text)


def validate_locator_xml(data, bones):
    if b"&lt;" in data or b"&gt;" in data:
        raise ValueError("Locator XML must carry raw bone names like the native files")
    root = parse_native_xml(data)
    if root.tag != "CarLocators" or root.get("Version") != "2":
        raise ValueError("Unexpected locator envelope")
    names, guids = set(), set()
    for node in root:
        name, guid = node.find("Name").get("value"), uuid.UUID(node.find("GUID").get("value"))
        matrix = node.find("SceneTransform").attrib
        if (node.tag != "Locator" or node.get("Version") != "3" or name in names or guid in guids
                or node.find("AttachToBone").get("BoneName") not in bones
                or len(matrix) != 16 or any(not isfinite(float(v)) for v in matrix.values())):
            raise ValueError(f"Invalid locator {name}")
        names.add(name)
        guids.add(guid)
    return sorted(names)


def collision_prefix(data):
    """Read the verified outer v27 envelope and opaque first shape boundary."""
    count, version, definition = struct.unpack_from("<QII", data)
    if (count, version, definition) != (1, 27, 1):
        raise ValueError("Expected one FH4 v27 vehicle physics definition")
    mass = struct.unpack_from("<f", data, 16)[0]
    extents = struct.unpack_from("<3f", data, 92)
    shapes, shape_type, length = struct.unpack_from("<3I", data, 136)
    if not shapes or not all(isfinite(v) and v > 0 for v in extents) or shape_type != 5:
        raise ValueError("Unexpected FH4 v27 first shape")
    end = 148 + length
    if end > len(data):
        raise ValueError("Truncated v27 first shape")
    return {"version": version, "mass_header_value": mass, "half_extents_m": extents,
            "shape_count": shapes, "first_shape_type": shape_type, "first_payload_bytes": length,
            "unparsed_suffix_bytes": len(data) - end}


def read_collision(data):
    """Read the type-5 point clouds and retain v27 assignments after the shape array."""
    envelope = collision_prefix(data)
    children, = struct.unpack_from("<I", data, 120)
    if children:
        raise ValueError("Child physics definitions are outside this reader")
    cursor = 140
    clouds = []
    for _ in range(envelope["shape_count"]):
        shape, size = struct.unpack_from("<II", data, cursor)
        first, end = cursor + 8, cursor + 8 + size
        flags, offset, count, total, pad0, pad1 = struct.unpack_from("<6Q", data, first)
        if (shape != 5 or flags not in (0xe05, 0xb05) or offset != 48 or total != size
                or size != 48 + count*16 or end > len(data) or pad0 or pad1):
            raise ValueError("Unrecognized FH4 v27 point cloud structure")
        points = np.frombuffer(data[first+offset:end], dtype="<f4").reshape(-1, 4)
        if not len(points) or not np.isfinite(points).all() or not np.all(points[:, 3] == np.float32(1e-4)):
            raise ValueError("Invalid FH4 v27 coordinates or point margin")
        clouds.append((flags, points[:, :3]))
        cursor = end
    return envelope, clouds, data[cursor:]


def audit_trial_collision(data, contacts):
    """Verify the stock shell profile and its static relation to the wheel plane."""
    header, clouds, tail = read_collision(data)
    if (data[16:56] != bytes(40) or struct.unpack_from("<9I",data,56) != (0xffc00000,)*9
            or data[108:124] != bytes(16) or tail != bytes(12)
            or [role for role,_ in clouds] != [0xe05,0xb05]):
        raise ValueError("v27 mass/inertia, auxiliary fields or point cloud roles differ from the stock profile")
    hull = clouds[0][1]
    lo, hi = hull.min(axis=0), hull.max(axis=0)
    center = struct.unpack_from("<3f",data,124)
    radius, = struct.unpack_from("<f",data,104)
    if (not 0 <= lo[1] <= 0.15 or not np.allclose(center,(hi+lo)/2,rtol=0,atol=1e-5)
            or not np.allclose(header["half_extents_m"],(hi-lo)/2+1e-4,rtol=0,atol=1e-5)
            or not np.isclose(radius,np.linalg.norm(hull,axis=1).max(),rtol=0,atol=1e-5)
            or np.linalg.matrix_rank(hull-hull.mean(axis=0)) != 3):
        raise ValueError("v27 shell bounds, radius and point cloud disagree")
    if set(contacts) != {"LF","RF","LR","RR"}:
        raise ValueError("v27 static shell check requires four unloaded wheel contact points")
    positions = np.asarray([contacts[k] for k in sorted(contacts)])
    if (positions.shape != (4,3) or not np.isfinite(positions).all()
            or not np.all((-0.5 < positions[:,1]) & (positions[:,1] < lo[1]))
            or not np.all((lo[2] < positions[:,2]) & (positions[:,2] < hi[2]))):
        raise ValueError("Unloaded tire contact points fall outside the stock shell operating envelope")
    return {"shape_roles":[role for role,_ in clouds],"points":len(hull),"center_m":list(center),
            "shell_height_above_unloaded_tires_m":float(lo[1]-positions[:,1].max()),
            "mass_and_inertia":"vehicle_systems_catalogue","dynamic_contact":"unverified_runtime"}


def build_collision(vertices):
    """Write the observed zero-child, two-shape stock v27 form."""
    points = np.asarray(vertices, dtype="<f4")
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 4 or not np.isfinite(points).all():
        raise ValueError("A v27 vehicle shell requires finite points in chassis space")
    points = points.copy()
    points[:, 1] = np.maximum(points[:, 1], 0)
    points = np.unique(points, axis=0)
    if np.linalg.matrix_rank(points - points.mean(axis=0)) != 3:
        raise ValueError("The source hull does not form a volume")
    lower, upper = points.min(axis=0), points.max(axis=0)
    extents = (upper-lower)*np.float32(0.5) + np.float32(1e-4)
    center = (upper+lower)*np.float32(0.5)
    radius = np.linalg.norm(points, axis=1).max()
    # This stock form leaves mass and inertia in the vehicle systems catalogue.
    result = bytearray(struct.pack("<QII", 1, 27, 1) + bytes(40)
                       + struct.pack("<9I", *([0xffc00000]*9))
                       + struct.pack("<4f", *extents, radius) + bytes(16)
                       + struct.pack("<3fI", *center, 2))
    elevated = points.copy()
    elevated[:, 1] = np.maximum(elevated[:, 1], np.float32(0.2))
    for flags, xyz in ((0xe05, points), (0xb05, elevated)):
        vertices = np.column_stack((xyz, np.full(len(xyz), 1e-4, dtype="<f4"))).astype("<f4").tobytes()
        size = 48 + len(vertices)
        result.extend(struct.pack("<II6Q", 5, size, flags, 48, len(xyz), size, 0, 0))
        result.extend(vertices)
    result.extend(bytes(12))
    envelope, readback, tail = read_collision(bytes(result))
    if (tail != bytes(12) or len(readback) != 2 or not np.array_equal(readback[0][1], points)
            or not np.array_equal(readback[1][1], elevated)):
        raise ValueError("Generated v27 point clouds failed readback")
    return bytes(result), {
        **envelope, "point_count": len(points), "shape_flags": [0xe05, 0xb05],
        "elevated_plane_m": 0.2, "coordinate_frame": "chassis meters",
        "coverage": "Stock envelope without upgrade shape assignments. Native contacts and damage behavior untested.",
    }


EXHAUST_REAR_BAND_M = 0.03   # vertices this close to an exhaust model's rearmost point form its openings
EXHAUST_SPLIT_M = 0.05       # openings further apart across the car are separate pipes
EXHAUST_LOCATOR = "carLocator_Exhaust_{:03d}"


def exhaust_tips(components):
    """[(x, y, z)] centres of the tail-pipe openings of the stock exhaust models (finest LOD).

    FM4 has no exhaust hardpoint; the game spawns backfire flames at carLocator_Exhaust_NNN. These were the donor's
    (FOR_FiestaST_14, two tips right of centre) moved by the hardpoint fit, so every car's flames came out beside
    its pipe and the RUF's centre pipe fired off-centre (owner). The rear band of each exhaust model, split
    where the openings are more than 5 cm apart across the car, gives one locator per opening."""
    tips = []
    for name, draws in sorted(components.items()):
        if not name.casefold().startswith("exhaust"):
            continue
        finest = min(d.lod for d in draws)
        points = np.concatenate([d.positions for d in draws if d.lod == finest and not d.transparent])
        rear = points[points[:, 2] < points[:, 2].min() + EXHAUST_REAR_BAND_M]
        rear = rear[np.argsort(rear[:, 0])]
        for group in np.split(rear, np.nonzero(np.diff(rear[:, 0]) > EXHAUST_SPLIT_M)[0] + 1):
            low, high = group.min(axis=0), group.max(axis=0)
            tips.append((float((low[0] + high[0]) / 2), float((low[1] + high[1]) / 2), float(low[2])))
    return sorted(tips)


def exhaust_locators(extra, tips):
    """`extra` (transferred donor locators) with the donor exhaust locators replaced by one per FM4 tip, keeping
    the donor's rearward frame; Audio_Exhaust moves to the first tip. Unchanged when no tip was found."""
    donor = [item for item in extra if item["name"].startswith("carLocator_Exhaust_")]
    if not tips or not donor:
        return extra, []
    frame, upgrade = donor[0]["matrix"], donor[0]["upgrade"]
    kept = [item for item in extra if not item["name"].startswith("carLocator_Exhaust_")]
    for item in kept:
        if item["name"] == "Audio_Exhaust":
            item["matrix"] = list(item["matrix"][:12]) + list(tips[0]) + [1.0]
    added = [{"name": EXHAUST_LOCATOR.format(i + 1), "matrix": list(frame[:12]) + list(tip) + [1.0],
              "upgrade": dict(upgrade)} for i, tip in enumerate(tips)]
    return kept + added, [item["name"] for item in added]


HOOD_CAMERA_OFFSET = (0.2, -0.5)    # native median CamHoodHeightOffset, CamHoodZOffset (fh4_native_defaults)
HOOD_CAMERA_CLEARANCE_M = 0.10
HOOD_SAMPLE_M = 0.10


def surface_height(points, x, z, half=HOOD_SAMPLE_M):
    near = points[(np.abs(points[:, 0] - x) < half) & (np.abs(points[:, 2] - z) < half)]
    return float(near[:, 1].max()) if len(near) else None


def hood_locator(extra, points):
    """`extra` with carLocator_hood on the bonnet surface, high enough that the bonnet camera (the locator plus
    the native median offsets, 0.2 m up and 0.5 m back) clears the body under it by 10 cm.

    The locator came from the donor by the hardpoint fit, 7-14 cm under our bonnets; the camera then rode 1-2 cm
    over the rising bonnet and dipped into it (owner, visual-51, all three cars)."""
    if os.environ.get("EXPANDED_GARAGE_HOOD_LOCATOR") == "donor" or not len(points):
        return extra, None
    for item in extra:
        if item["name"] != "carLocator_hood":
            continue
        x, y, z = item["matrix"][12:15]
        surface = surface_height(points, x, z)
        under_camera = surface_height(points, x, z + HOOD_CAMERA_OFFSET[1])
        if surface is None:
            return extra, None
        height = max(surface, (under_camera if under_camera is not None else surface)
                     + HOOD_CAMERA_CLEARANCE_M - HOOD_CAMERA_OFFSET[0])
        item["matrix"] = list(item["matrix"][:13]) + [height] + list(item["matrix"][14:])
        return extra, {"donor_y_m": y, "bonnet_y_m": surface, "under_camera_y_m": under_camera, "locator_y_m": height}
    return extra, None
