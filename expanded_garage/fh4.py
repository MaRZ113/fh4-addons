"""Generate independent FH4 render archives and explicit qualification reports."""

from collections import defaultdict
from dataclasses import dataclass, replace
from functools import lru_cache
from importlib.resources import files
from io import BytesIO
from pathlib import Path
import json
import os
import re
import struct
import sys
import uuid
from zipfile import ZipFile

import numpy as np

from . import fh4_archive, fh4_db, fh4_model, fh4_physics
from .fh4_scene_contract import NativeCarSceneContract
from .fh4_runtime_rig import NativeRuntimeRigCompiler, modelbin_view, verify_skeleton
from . import fh4_ao_volume, fh4_dirt, fh4_driver_rig, fh4_livery, fh4_paint, fh4_wheel_model
from .fh4_granny_animation import verify_gauge_graph
from .fh4_material_contract import MaterialUse, surface_binding
from .fh4_motion import GAUGES
from .fh4_wheel_transform import WheelTransformCompiler
from . import fh4_vehicle_systems as systems
from .fm4 import rows_for
from .vehicle_ir import member_path, sha256, write_json


ROOT = Path(__file__).resolve().parents[1]
TARGET_BUILD = "FH4 Steam 1.478.564.0"
AO_VOLUME = "aovolumetexture.bin"
PROXY_LOD = "scene/proxylod.modelbin"
PART_TYPES = {"exterior": 2, "cockpit": 2, "brake": 4, "wheel": 43}
# Carbin model record v11 block (18 bytes after the v10 byte): the roster mode per model class over
# 785 native carbins (exterior part type 2 draw 5: 48%, upgrade exterior parts 74%; interior 65%;
# _library brakes 38%; _library wheels 50%). An all-zero block (no native class uses it for exterior)
# left every Suzuki model out of the car bounds that box-space presentation cameras frame.
MODEL_FLAGS = {
    "exterior": bytes.fromhex("010101000000010000000100000001000000"),
    "cockpit": bytes.fromhex("010100000000000000000000000000000000"),
    "brake": bytes.fromhex("000000000000010000000100000001000000"),
    "wheel": bytes.fromhex("010100000000010000000100000001000000"),
}
KNOWN_LIMITS = {
    "geometry": "Rigid source meshes with typed stock/variant IDs. Visibility and LOD presentation need runtime qualification.",
    "materials": "Uses generic FH4 library materials. Per-car XDS-to-swatch textures and shader parameters are pending.",
    "wetness_dirt_ao": "UV2–4 and COLOR0 have diagnostic defaults. Native dirt, water, AO and splatter inputs need pass-specific calibration.",
    "wheel_blur": "Source blur draws and textures stay in the IR. Native blur materials, ordering and speed transitions need a writer.",
    "structure": "Own Modelbin and Granny rest rigs, authored locators, and donor-placed driver IK anchors and scene locators. Runtime GSF, hinges, controls and Forzavista are pending.",
    "lighting": "Geometry-derived positions are emitted as research data. Lights, presets, emitters and FX controllers need event routing.",
    "physics_damage": "The v27 stock two-cloud form is generated from the FM4 envelope. Road contacts, variant shapes and damage remain open.",
    "audio": "Source banks and tuning are indexed. The FH4 engine synth and event formats need translation.",
    "registry": "Insert-only GameDB, shared tire families, optional English localization and source artwork are staged. Registration needs runtime qualification.",
    "acceptance": "A runtime acceptance run is required after the package passes all release gates.",
}
GAUGE_POSES = {f"{label}_{phase}" for label,*_ in GAUGES for phase in ("min","mid","max")}


@dataclass(frozen=True)
class Model:
    member: str
    kind: str
    lod: int
    bone: int
    translation: tuple
    component: str = ""
    rotation: tuple = (0.,0.,0.,1.)
    draw: int = 0
    ao: str = ""    # Carbin AO record path of the model map (Textures/AO/<model>_ao.swatchbin)


# Native plate records (FOR_FiestaST_14, ACU_IntegraR_01, HON_CivicTypeR_97): draw 1, LOD mask 127, <root>.
PLATE_DRAW, PLATE_LOD_MASK = 1, 127
NATIVE_COPY_FOLDERS = ("scene/exterior/plate/",)


def guid(name):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"urn:fh4-expanded-garage:v1:{name}").bytes_le


def virtual(alias, name):
    return f"game:\\media\\cars\\{alias.casefold()}\\" + name.replace("/", "\\").casefold()


def modelbin_box(data):
    """BBox metadata (low, high) of the Modl blob in a generated Modelbin."""
    _, _, _, _, _, _, count = struct.unpack_from("<IBBHIII", data)
    for index in range(count):
        tag, _, _, fields, metadata, _, _, _ = struct.unpack_from("<IBBHIIII", data, 20 + index * 24)
        if tag.to_bytes(4, "little") not in (b"Modl", b"ldoM"):
            continue
        for meta in range(metadata, metadata + fields * 8, 8):
            kind, _, relative = struct.unpack_from("<IHH", data, meta)
            if kind == 0x42426F78:
                values = struct.unpack_from("<6f", data, meta + relative)
                return values[:3], values[3:]
    raise ValueError("Modelbin without a Modl BBox")


# Fixed part groups whose models sit in their own local frame (brakes, wheels). Native carbins give these
# groups the union of their models' local boxes (ACU_RSXTypeS_02 brakes -0.045..0.063 x +-0.161,
# FOR_FiestaST_14 wheels 0..1 x +-0.14), not the car bounds.
LOCAL_GROUPS = (4, 43)


@lru_cache(maxsize=1)
def material_flags():
    """Roster mode of the Carbin per-MatI flag for each library material (research/extract_material_flags.py)."""
    record = json.loads(files(__package__).joinpath("fh4-material-flags-v1.json").read_text(encoding="utf-8"))
    return {name: entry["flags"] for name, entry in record["materials"].items()}


def modelbin_materials(data):
    """[(MatI name, library member)] of a generated Modelbin."""
    _, geometry = previous_readers()
    return [(block["name"], verify_material(data, block, geometry))
            for block in geometry.maps(data)[0] if block["tag"] == "MatI"]


GLASS_FOLDERS = ("exterior_glass/", "interior_glass/", "exterior_litglass/", "exterior_lights/")


def breakable_glass(materials):
    """Carbin model record float after the material lists. Native records carry 0.5 on models with
    breakable glass (windows 575 of 577, lamps 1735 of 1773, cabin glass 549 of 561 over 131 cars) and
    -1 elsewhere; it is never 0. A record written with 0 drew every pane and lens shattered (opaque black
    with the dmg_glassshatter mask) and the lamps dark."""
    glass = any(member.casefold().startswith(GLASS_FOLDERS) or "/gls_" in member.casefold()
                for _, member in materials)
    return 0.5 if glass else -1.0


# Native Carbin records carry exactly one AO-map entry per model (232 of 232 on ACU_RSXTypeS_02 and
# FOR_FiestaST_14): version 3, texture path (empty on glass, lamps and interior LOD), type -1, id -1,
# 16 zero bytes, then 01 00 1f. Without it our glass picked up a stale damage constant: honeycomb
# shatter on the windscreen and panes vanishing with the camera angle in the showroom.
AO_STUB = struct.pack("<H", 3) + fh4_model.string("") + struct.pack("<Ii", 0xFFFFFFFF, -1) + bytes(16) + b"\x01\x00\x1f"


def ao_list(path=""):
    # EXPANDED_GARAGE_AO_STUB=off writes the empty list again (bisection switch).
    if os.environ.get("EXPANDED_GARAGE_AO_STUB") == "off":
        return bytes(4)
    if path:
        # Native record with a map: same layout as the stub, the texture path in place of "".
        return (struct.pack("<I", 1) + struct.pack("<H", 3) + fh4_model.string(path)
                + struct.pack("<Ii", 0xFFFFFFFF, -1) + bytes(16) + b"\x01\x00\x1f")
    return struct.pack("<I", 1) + AO_STUB


# Carbin model record category: native records name the scene folder of the part (Windows 430,
# PrimaryLights 654, SecondaryLights 602, InteriorWindows 417, Wheels, Brakes, BumperF ... over 99 cars).
CATEGORY_RULES = (
    (r"^glass[lr]hl", "PrimaryLights"), (r"^glass[lr]tl", "SecondaryLights"),
    (r"^headlight", "PrimaryLights"), (r"^taillight", "SecondaryLights"),
    (r"^glass", "Windows"), (r"^bumperframe|^exhaust|^undercarriage", "Chassis"),
    (r"^bumperf", "BumperF"), (r"^bumperr", "BumperR"), (r"^hood", "Hood"), (r"^trunk", "Trunk"),
    (r"^skirt", "SideSkirts"), (r"^wing", "Trunk"), (r"^mirror|^door", "Doors"), (r"^body", "Platform"),
    (r"^seat", "Seats"), (r"_min", "Gauges"), (r"pillar", "Pillar"), (r"^interior", "InteriorLOD"),
    (r"^steering", "Dash"), (r"^plate", "Plate"),
)


@lru_cache(maxsize=1)
def light_rig_table():
    return json.loads(files(__package__).joinpath("fh4-light-rig-v1.json").read_text(encoding="utf-8"))


def light_role(model):
    if model.kind not in ("exterior", "cockpit"):
        return None
    stem = Path(model.member).stem.casefold()
    for name, pattern in light_rig_table()["role_patterns"].items():
        if re.search(pattern, stem) and (name == "interior") == (model.kind == "cockpit"):
            return name
    return None


def light_rig(alias, models, payloads):
    """(Lights.bin, LightPresets.bin) from the donor rig (fh4-light-rig-v1.json): each donor light is
    placed at its normalised position inside the bounding box of our models of the same role and hangs
    on the role's housing model. Roles our car lacks are left out. None without any lamp model."""
    table = light_rig_table()
    groups = {}
    for model in models:
        name = light_role(model)
        if name:
            groups.setdefault(name, []).append(model)
    if not any(name.startswith("head_") for name in groups):
        return None
    boxes, attach, paths = {}, {}, []
    for name, members in groups.items():
        lows, highs = zip(*(modelbin_box(payloads[m.member]) for m in members))
        boxes[name] = (np.min(lows, axis=0), np.max(highs, axis=0))
        # Hang the light on the housing (headlight/taillight/interior) rather than its lens.
        housing = sorted(members, key=lambda m: Path(m.member).stem.casefold().startswith("glass"))[0]
        attach[name] = len(paths)
        paths.append(virtual(alias, housing.member))

    def place(point, box):
        low, high = box
        return tuple(float(l + u * (h - l)) for u, l, h in zip(point, low, high))

    lights = [light for light in table["lights"] if light["role"] in groups]
    out = bytearray(struct.pack("<III", 0xDEADBEEF, 1, len(paths)))
    for path in paths:
        out += fh4_model.string(path) + struct.pack("<H", 1)
    out += struct.pack("<I", len(lights))
    for light in lights:
        box = boxes[light["role"]]
        out += struct.pack("<III", light["id"], light["flags"], attach[light["role"]])
        out += struct.pack("<4f", *place(light["pos"], box), light["w"]) + struct.pack("<4f", *light["rot"])
        out += struct.pack("<4f", *place(light["dpos"], box), light["w"]) + struct.pack("<4f", *light["drot"])
    out += struct.pack("<I", 0)
    return bytes(out), bytes.fromhex(table["presets"])


def window_part(key):
    """Window glass by the Carbin category rules (lamp glass is PrimaryLights/SecondaryLights)."""
    stem = key.casefold()
    return next((category for pattern, category in CATEGORY_RULES if re.search(pattern, stem)), None) == "Windows"


def inward(draw):
    """Native cabin panes (glass*Int) are separate models facing into the cabin; FM4's cockpit panes face
    out like the exterior ones, so from the driver's seat they were back-face culled."""
    return replace(draw, indices=np.ascontiguousarray(draw.indices[:, ::-1]),
                   normals=np.asarray(-draw.normals, dtype=np.float32))


GAUGE_BACKING_DEPTH = 0.002
GAUGE_FACES = ("gauge_emissive", "digital_gauge")


def gauge_backing(housing, kind, key):
    """Opaque face 2 mm behind the alpha-tested gauge symbols (native gaugeCluster: plastic under
    int_gauges); without it the dial was see-through to the road."""
    material = surface_binding("gauge_backing", kind, key).member
    result = []
    for d in housing:
        # The dial's front face (by winding) faces the driver; vertex normals there are not consistent.
        p = d.positions.astype(np.float64)
        a, b, c = (p[d.indices[:, i]] for i in range(3))
        face = np.cross(b - a, c - a).sum(0)
        face /= max(np.linalg.norm(face), 1e-12)
        result.append(replace(d, name=d.name + "_backing", source_material="gauge_backing", target_material=material,
                              transparent=False,
                              positions=np.asarray(p - face * GAUGE_BACKING_DEPTH, dtype=np.float32)))
    return result


def model_category(part):
    # EXPANDED_GARAGE_CATEGORIES=off writes the empty category of builds up to 6b269a5e (bisection switch).
    if os.environ.get("EXPANDED_GARAGE_CATEGORIES") == "off":
        return ""
    if part.kind == "wheel":
        return "Wheels"
    if part.kind == "brake":
        return "Brakes"
    stem = Path(part.member).stem.casefold()
    for pattern, category in CATEGORY_RULES:
        if re.search(pattern, stem):
            if category == "Windows" and part.kind == "cockpit":
                return "InteriorWindows"
            return category
    return "Dash" if part.kind == "cockpit" else "Platform"


def material_list(materials):
    """Carbin model record list of MatI names with their colour-slot flags.

    Native records name every MatI of the model (sorted) with a u32 bit field that ties it to a car
    colour slot: carpaint 1, livery 2, secondary paint 256, window glass 128, everything else 0.
    """
    flags = material_flags()
    names = sorted({name: flags.get(member.casefold(), 0) for name, member in materials}.items())
    return struct.pack("<I", len(names)) + b"".join(fh4_model.string(name) + struct.pack("<I", flag)
                                                    for name, flag in names)


def carbin(identity, bones, skel, models, bounds, upgrade_groups=None, model_boxes=None, model_materials=None):
    alias = identity["media_name"].casefold()
    upgrade_groups = upgrade_groups or {}
    conditional_members = {model.member for entries in upgrade_groups.values() for _, owned in entries for model in owned}
    groups = defaultdict(list)
    for model in models:
        if model.member not in conditional_members:
            groups[PART_TYPES[model.kind]].append(model)
    result = bytearray(struct.pack("<H16sBI", 5, guid(alias), 0, identity["car_id"]))
    result.extend(fh4_model.string(alias) + fh4_model.string(virtual(alias, skel)))
    mask = 0
    for model in models:
        mask |= model.lod
    result.extend(struct.pack("<HI", mask, len(groups)))
    bbox = struct.pack("<8f", *bounds[0], 0.0, *bounds[1], 0.0)

    def encode_model(part):
        path = virtual(alias, part.member)
        materials = (model_materials or {}).get(part.member, ())
        # Draw mask: 5 is the native mode for exterior parts; cockpit-only models (bit 0 clear) use 38 in
        # native carbins (169 models on 35 of 197 sampled cars). 2 occurs in no native carbin.
        draw = part.draw or (38 if part.kind == "cockpit" else 5)
        bone = bones[part.bone].name
        return (struct.pack("<H", 16) + fh4_model.string(path)
                + struct.pack("<16fH", *fh4_model.transform(part.translation,part.rotation).flat, part.lod)
                + fh4_model.string(bone) + struct.pack("<hBI", part.bone, 0, draw)
                + bytes(4) + material_list(materials) + bytes(1)
                + struct.pack("<f", breakable_glass(materials)) + ao_list(part.ao) + bytes(1) + MODEL_FLAGS[part.kind]
                + fh4_model.string(model_category(part)) + guid(path) + bytes(20 + 4 + 4))

    for part_type, parts in sorted(groups.items()):
        result.extend(struct.pack("<BHII", part_type, 2, part_type, len(parts)))
        for part in parts:
            result.extend(encode_model(part))
        if part_type in LOCAL_GROUPS and model_boxes:
            boxes = [model_boxes[part.member] for part in parts]
            low = np.min([b[0] for b in boxes], axis=0)
            high = np.max([b[1] for b in boxes], axis=0)
            result.extend(struct.pack("<8f", *low, 0.0, *high, 0.0))
        else:
            result.extend(bbox)
    result.extend(struct.pack("<I", len(upgrade_groups)))
    for part_type, entries in sorted(upgrade_groups.items()):
        result.extend(struct.pack("<HII", 3, part_type, len(entries)))
        for part, _ in entries:
            parent = -1 if part_type == 9 else identity["body_id"]
            result.extend(struct.pack("<HBBiiB", 3, part.level, part.stock, part.native_key, parent, 1) + bbox)
        result.extend(struct.pack("<I", sum(len(owned) for _, owned in entries)))
        for part, owned in entries:
            for model in owned:
                result.extend(struct.pack("<Ii", 1, part.native_key) + encode_model(model))
    return bytes(result)


AO_FOLDER = "Textures/AO/"
# Cockpit parts drawn in both views (hood, mirrors) or see-through get no cabin AO; a_pillar is cockpit-only
# (draw 38) since visual-30 and is shaded with the cabin it borders.
AO_SKIP_PARTS = ("hood", "mirror", "glass", "cage")
EXTERIOR_AO_SIDE = 128
# Response of the exterior bake: linear visibility put our paint at a median of 210-219 against 149-173 under
# native paint (ACU, Fiesta); v ** 2.6 matched those numbers but read too dark and patchy in game (owner,
# visual-55). 1.5: paint median ~0.77. Applied after the cache.
EXTERIOR_AO_GAMMA = 1.5
AO_UNSHADED = set(GAUGE_FACES) | {"gauge_backing", "gauge_needle"}


def unshaded_triangles(draw, exterior):
    """Triangles that keep full AO: dial faces and their backing (the cluster read black under a recess AO),
    and cabin triangles lying on the exterior LOD0 surface (the same skin, seen from outside)."""
    tri = draw.indices.reshape(-1, 3)
    if draw.source_material.casefold() in AO_UNSHADED:
        return np.ones(len(tri), bool)
    corners = draw.positions[tri].astype(np.float64)
    normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    area = np.linalg.norm(normal, axis=1)
    cells = np.rint(corners.mean(axis=1) / OVERLAP_CELL).astype(np.int64)
    out = np.zeros(len(tri), bool)
    for k, (cell, n) in enumerate(zip(cells, normal)):
        if area[k] < 1e-10:
            continue
        n = n / area[k]
        for dx, dy, dz in NEIGHBOURS:
            other = exterior.get((cell[0] + dx, cell[1] + dy, cell[2] + dz))
            # Same facing only: a cabin trim backing onto the body from inside faces the other way.
            if other is not None and float(other @ n) > 0.95:
                out[k] = True
                break
    return out


def part_ao(alias, grouped, nonstock=frozenset(), exterior_sides=None, corners=None):
    """Per-model AO maps for the cabin (fh4_part_ao): unwraps the cockpit components in place (TEXCOORD2)
    and returns ({(kind, key): Carbin AO path}, {member: swatchbin}). EXPANDED_GARAGE_PART_AO=off skips it."""
    if os.environ.get("EXPANDED_GARAGE_PART_AO") == "off":
        return {}, {}
    from . import fh4_cache, fh4_part_ao
    stock = [v for kind in ("exterior", "cockpit") for key, v in grouped[kind].items() if key.casefold() not in nonstock]
    opaque, glass = fh4_part_ao.occluder_triangles(stock), fh4_part_ao.occluder_triangles(stock, glass=True)
    built = []

    def occluders():   # the voxel shells are only needed on a cache miss
        if not built:
            built.append(fh4_part_ao.Occluders(opaque, glass))
        return built[0]
    folder = "\\".join(("game:", "media", "cars", alias.casefold(), "textures", "ao", ""))
    exterior = exterior_surface_keys([d for v in grouped["exterior"].values() for d in v if d.lod == 0])
    paths, payloads = {}, {}
    for key in sorted(grouped["cockpit"]):
        # Gauge needles, panes and the body seen from the cabin (hood, mirrors, pillars, drawn in both views)
        # get no cabin AO: a cabin-dark map on them showed outside (owner, visual-34).
        if key in GAUGE_POSES or key.casefold().startswith(AO_SKIP_PARTS):
            continue
        draws, side = fh4_part_ao.unwrap(grouped["cockpit"][key])
        if not side:
            continue
        grouped["cockpit"][key] = draws
        bright = {id(d): unshaded_triangles(d, exterior) for d in draws if d.uv2 is not None}
        name = re.sub(r"[^a-z0-9_]+", "_", key.lower()).strip("_") + "_ao"
        inputs = (opaque, glass, side, [(d.lod, d.positions, d.normals, d.indices, d.uv2, bright.get(id(d)))
                                        for d in draws])
        image = fh4_cache.cached_array("part-ao", (fh4_part_ao.__name__,), inputs,
                                       lambda: fh4_part_ao.bake(draws, occluders(), side, bright))
        payloads[AO_FOLDER + name + ".swatchbin"] = fh4_part_ao.texture(name, image)
        paths[("cockpit", key)] = folder + name + ".swatchbin"
    # Exterior models: native cars carry an AO map on every exterior model (ACU_RSXTypeS_02: bumperf_a_ao,
    # hood_*_ao, doorjambs_a_ao ...; panes none), read through the model's TEXCOORD2 unwrap (make_models).
    # Under native paint the map reads ~150 of 255 (ACU, Fiesta): without it our bodies took full indirect light
    # and looked brighter than natives in the same paint (owner, visual-53). One bake for the whole car with the
    # ground as occluder, smoothed in 3D across models (fh4_part_ao.bake_exterior; the visual-47 per-model chart
    # maps showed seams). Off by default since visual-57: still uneven in game (RUF tail panel, SX4 door facets
    # near the mirror and handles); EXPANDED_GARAGE_EXTERIOR_AO=on bakes it.
    if os.environ.get("EXPANDED_GARAGE_EXTERIOR_AO") == "on" and exterior_sides and corners:
        keys = [key for key in sorted(exterior_sides)
                if key.casefold() not in nonstock and not key.casefold().startswith("glass")]
        models = [(grouped["exterior"][key], exterior_sides[key]) for key in keys]
        points = np.concatenate([d.positions for draws, _ in models for d in draws])
        ground_y = min(c["origin_m"][1] - c["radius_m"] for c in corners.values())
        pad = fh4_part_ao.FAR_REACH
        ground = fh4_part_ao.ground_tiles(points[:, [0, 2]].min(0) - pad, points[:, [0, 2]].max(0) + pad, ground_y)
        inputs = ("exterior-v1", opaque, glass, ground,
                  [(side, [(d.lod, d.positions, d.normals, d.indices, d.uv2) for d in draws]) for draws, side in models])
        flat = fh4_cache.cached_array(
            "exterior-ao", (fh4_part_ao.__name__,), inputs,
            lambda: fh4_part_ao.bake_exterior(models, fh4_part_ao.Occluders(np.concatenate([opaque, ground]), glass)))
        at = 0
        for key, (draws, side) in zip(keys, models):
            size = min(side, fh4_part_ao.EXTERIOR_MAX_SIDE)
            image = flat[at:at + size * size].reshape(size, size)
            at += size * size
            if (gamma := float(os.environ.get("EXPANDED_GARAGE_EXTERIOR_AO_GAMMA", EXTERIOR_AO_GAMMA))) != 1.0:
                image = np.clip(np.rint(255.0 * (image / 255.0) ** gamma), fh4_part_ao.EXTERIOR_FLOOR, 255).astype(np.uint8)
            name = "ext_" + re.sub(r"[^a-z0-9_]+", "_", key.lower()).strip("_") + "_ao"
            payloads[AO_FOLDER + name + ".swatchbin"] = fh4_part_ao.texture(name, image)
            paths[("exterior", key)] = folder + name + ".swatchbin"
    return paths, payloads


DEFROST_LINE_UV = (0.5, 13 / 32)
# Exterior trims whose bent FM4 normals are rebuilt from their own faces with EXPANDED_GARAGE_TRIM_NORMALS=on
# (fh4_model.geometric_normals; off by default since visual-34: it did not change the showroom pillar).
TRIM_SURFACES = {"plastic2", "plastic", "black_glass", "frame", "black", "rubber_trim", "bottom",
                 "rubber", "weatherstrip"}


def uv(data, transform):
    return data * np.asarray(transform[1::2], dtype="<f4") + np.asarray(transform[::2], dtype="<f4")


# Native seating over 706 cars (seat camera locator + CamCockpitOffset, relative to anchor_butt): the first-person
# camera sits 0.693 m over the hips and 0.167 m behind them; the wheel spindle 0.336 m ahead of the hips.
# CamCockpitOffsetY/Z correlate -0.63/-0.62 with the locator's own offset (they compensate it).
NATIVE_EYE = (0.693, -0.167)
NATIVE_WHEEL_REACH = 0.336
COCKPIT_OFFSET_LIMITS = {"CamCockpitOffsetY": (-0.03, 0.08), "CamCockpitOffsetZ": (-0.14, 0.15)}


def bone_world(bones, name):
    index = next(i for i, b in enumerate(bones) if b.name == name)
    matrix = np.eye(4)
    while index >= 0:
        matrix = matrix @ fh4_model.transform(bones[index].translation, bones[index].rotation)
        index = bones[index].parent
    return matrix[3, :3]


def cockpit_camera_offsets(bones, locators, driver_side):
    """({CameraOverrides column: value}, receipt): first-person camera offsets for this car's seating.

    With the native median offsets the camera sat where natives put it relative to the hips, but FM4 places the
    wheel 3-4 cm further ahead than native cars (0.373 vs 0.336 m), so the driver's IK pose leans forward and the
    collar rose into the view at the widest FOV (owner, visual-52). The camera moves forward by that extra reach
    and up to the native eye height, within the native 10-90 % range. EXPANDED_GARAGE_COCKPIT_CAMERA=native: medians."""
    if os.environ.get("EXPANDED_GARAGE_COCKPIT_CAMERA") == "native":
        return {}, None
    names = {b.name for b in bones}
    seat = next((item for item in locators if item["name"] == f"carLocator_seatCam{driver_side}F"), None)
    if seat is None or not {"anchor_butt", "boneSteeringWheelSpindle"} <= names:
        return {}, None
    hips = bone_world(bones, "anchor_butt")
    wheel = bone_world(bones, "boneSteeringWheelSpindle")
    camera = np.asarray(seat["matrix"][12:15], dtype=float) - hips
    reach = float(wheel[2] - hips[2])
    raw = {"CamCockpitOffsetY": NATIVE_EYE[0] - camera[1],
           "CamCockpitOffsetZ": (reach - NATIVE_WHEEL_REACH) + (NATIVE_EYE[1] - camera[2])}
    out = {k: round(float(min(max(v, COCKPIT_OFFSET_LIMITS[k][0]), COCKPIT_OFFSET_LIMITS[k][1])), 3)
           for k, v in raw.items()}
    return out, {"seat_camera_from_hips_m": camera.tolist(), "wheel_reach_m": reach, "offsets": out}


def mirror_uv(positions):
    """UV0 of a mirror glass as native mirrors lay it (ACU_RSXTypeS_02, FOR_FiestaST_14 wing and rear-view
    mirrors): u from car left to right (+X), v from top to bottom, 0..1 over the glass."""
    low, high = positions.min(axis=0), positions.max(axis=0)
    span = np.where(high - low < 1e-6, 1.0, high - low)
    return np.stack(((positions[:, 0] - low[0]) / span[0], (high[1] - positions[:, 1]) / span[1]),
                    axis=1).astype(np.float32)


def load_draws(root, src):
    arrays = root / src["arrays"]
    if src["arrays"] != f'geometry/{src["raw"]}.npz' or sha256(arrays) != src["arrays_sha256"]:
        raise ValueError(f"Invalid array resource for {src['member']}")
    with np.load(arrays, allow_pickle=False) as vertices:
        for section in src["components"]:
            for draw in section["draws"]:
                triangles = vertices[draw["triangles"]]
                ids, compact = np.unique(triangles, return_inverse=True)
                p = section["pools"][draw["pool"]]["arrays"]
                positions = vertices[p["position"]][ids]
                normals = vertices[p["normal"]][ids]
                role = src["role"]
                kind = "exterior" if role.startswith("exterior") else "brake" if role in ("caliper", "rotor") else role
                binding = surface_binding(draw["material"],kind,section["name"])
                uv0 = uv(vertices[p["uv0"]][ids], draw["uv_transform"][:4])
                if binding.member.startswith("specificuse/mirror"):
                    uv0 = mirror_uv(positions)
                uv1 = uv(vertices[p["uv1"]][ids], draw["uv_transform"][4:])
                indices = compact.reshape(-1, 3)
                if binding.member.endswith("defrostlines.materialbin") and np.ptp(uv0, axis=0).max() < 0.01:
                    # FM4 heater wires are real strips sampling one atlas texel; native defrostlines tiles
                    # a stripe opacity mask (defrostlines_001_opac: 64x32, opaque rows 12-13) over the pane.
                    # Point the strips at the opaque row (owner, visual-29: rear-glass lines missing).
                    uv0 = np.tile(np.asarray(DEFROST_LINE_UV, dtype=uv0.dtype), (len(uv0), 1))
                # EXPANDED_GARAGE_DETAIL_UV=source keeps FM4's second UV as TEXCOORD1 (bisection switch).
                if (kind == "exterior" and not binding.transparent and draw["material"].casefold() in TRIM_SURFACES
                        and os.environ.get("EXPANDED_GARAGE_TRIM_NORMALS") == "on"):
                    normals, _ = fh4_model.geometric_normals(positions, normals, compact.reshape(-1, 3))
                # Gauge needle poses (min/mid/max) are matched vertex by vertex: keep their vertex sets.
                # Glass-family shaders keep the pre-visual-29 channels (FM4 UV1, TANGENT1 = TANGENT0): the
                # projected detail unwrap turned every black-glass frit matte (owner, visual-36).
                if (os.environ.get("EXPANDED_GARAGE_DETAIL_UV") != "source" and section["name"] not in GAUGE_POSES
                        and not fh4_model.glass_family(binding.member)):
                    positions, normals, uv0, indices, uv1 = fh4_model.projected_detail(
                        positions, normals, uv0, indices, binding.member)
                yield section["name"], fh4_model.Draw(
                    name=f'{section["name"]}_{draw["triangles"]}', lod=draw["lod"],
                    source_material=draw["material"], target_material=binding.member, transparent=binding.transparent,
                    positions=positions, normals=normals,
                    uv0=uv0, uv1=uv1, indices=indices, origin=draw["provenance"],
                )


def one(rows, table):
    found = rows_for(rows, table)
    if len(found) != 1:
        raise ValueError(f"Expected a single source {table} row")
    return found[0]


def reflected(draw):
    return replace(draw, positions=draw.positions * (-1, 1, 1), normals=draw.normals * (-1, 1, 1),
                   indices=draw.indices[:, [0, 2, 1]])


def guess_lights(exterior):
    result = []
    tags = {"head_light": "head", "tail_light": "tail", "reverse_light": "reverse",
            "brake_light": "brake", "turn_light": "indicator"}
    for name, draws in sorted(exterior.items()):
        for kind in sorted({tags[d.source_material.lower()] for d in draws if d.source_material.lower() in tags}):
            selected = [d for d in draws if tags.get(d.source_material.lower()) == kind]
            highest = min(d.lod for d in selected)
            finest = [d for d in selected if d.lod == highest]
            triangles = np.concatenate([d.positions[d.indices] for d in finest])
            a, b, c = triangles.transpose(1, 0, 2)
            normals = np.cross(b-a, c-a)
            weights = np.linalg.norm(normals, axis=1)
            if weights.sum() < 1e-10:
                continue
            direction = normals.sum(axis=0)
            length = float(np.linalg.norm(direction))
            result.append({"component": name, "kind": kind, "position_m": np.average(triangles.mean(axis=1),
                          axis=0, weights=weights).tolist(),
                           "surface_normal": (direction / length).tolist() if length > 1e-10 else None,
                           "source": [d.origin for d in finest]})
    return result


def fh4_model_box(data, blob):
    """Return the BBox metadata of a Modl blob as ((low, high),)."""
    tag, _, _, _, header, _, count = struct.unpack_from("<IBBHIII", data)
    for index in range(count):
        _, _, _, fields, metadata, offset, _, _ = struct.unpack_from("<IBBHIIII", data, 20 + index * 24)
        if offset != blob["offset"]:
            continue
        for meta in range(metadata, metadata + fields * 8, 8):
            kind, flags, relative = struct.unpack_from("<IHH", data, meta)
            if kind == 0x42426f78:
                values = struct.unpack_from("<6f", data, meta + relative)
                return (("box", (values[:3], values[3:])),)
    raise ValueError("Modl blob without BBox")


def driver_side_from(grouped):
    steering = [d for k, ds in grouped["cockpit"].items() if k.casefold() == "steering_wheel" for d in ds]
    if not steering:
        raise ValueError("Driver side needs the source steering wheel geometry")
    x = float(np.median(np.concatenate([d.positions[:, 0] for d in steering])))
    if abs(x) <= 0.05:
        raise ValueError("Centred steering wheel: driver side is ambiguous")
    return "L" if x < 0 else "R"


GAUGE_MATERIAL = "interior_misc/int_gauges.materialbin"
GAUGE_FOLDER = "textures/gauges/swatches/"


@lru_cache(maxsize=1)
def gauge_donor():
    return json.loads(files(__package__).joinpath("fh4-gauge-parameters-v1.json").read_text(encoding="utf-8"))


def animation_probe():
    """EXPANDED_GARAGE_ANIMATION_PROBE=<native car ZIP>: ship its animation stack unchanged (bisection)."""
    value = os.environ.get("EXPANDED_GARAGE_ANIMATION_PROBE")
    return Path(value) if value else None


def probe_members(donor, alias):
    out = {}
    with ZipFile(donor) as archive:
        for name in archive.namelist():
            low = name.casefold()
            if low.startswith("animations/") and low.endswith(".gsf"):
                out[f"animations/{alias}.gsf"] = archive.read(name)
            elif low.startswith("animations/") and low.endswith(".gr2"):
                out[low] = archive.read(name)
            elif low.startswith("scene/") and low.endswith("_skeleton.gr2"):
                out[f"scene/{alias}_skeleton.gr2"] = archive.read(name)
    return out


def raw_member(root, ir, suffix):
    for member in ir.get("raw_members", ()):
        if member["name"].casefold().endswith(suffix):
            return (root / "blobs" / member["provenance"]["asset"]).read_bytes()
    return None


def livery_sources(root, ir):
    """{basename: bytes} of the FM4 car's liverymasks/ members."""
    out = {}
    for member in ir.get("raw_members", ()):
        name = member["name"].casefold()
        if "/liverymasks/" in name:
            out[name.rsplit("/", 1)[-1]] = (root / "blobs" / member["provenance"]["asset"]).read_bytes()
    return out


OVERLAP_CELL = 0.002   # metres


def surface_cells(draw):
    """Quantised centroids and unit normals of a draw's triangles."""
    tri = draw.positions[draw.indices]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area = np.linalg.norm(normal, axis=1)
    keep = area > 1e-10
    return np.rint(tri[keep].mean(axis=1) / OVERLAP_CELL).astype(np.int64), normal[keep] / area[keep, None]


def exterior_surface_keys(draws):
    keys = {}
    for draw in draws:
        cells, normals = surface_cells(draw)
        for cell, normal in zip(map(tuple, cells), normals):
            keys.setdefault(cell, normal)
    return keys


NEIGHBOURS = [(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)]


def exterior_overlap(draws, keys):
    """Share of a component's LOD0 triangles that coincide (centroid within ~2 mm, parallel) with exterior LOD0."""
    hits = total = 0
    for draw in draws:
        if draw.lod != 0:
            continue
        cells, normals = surface_cells(draw)
        total += len(cells)
        for cell, normal in zip(cells, normals):
            for dx, dy, dz in NEIGHBOURS:
                other = keys.get((cell[0] + dx, cell[1] + dy, cell[2] + dz))
                if other is not None and abs(float(other @ normal)) > 0.95:
                    hits += 1
                    break
    return hits / total if total else 0.0


def gauge_texture_parameters(root, ir, alias, library=None):
    """({(gauge_emissive, int_gauges): (version, MTPR)}, {member: swatchbin}) for the car's own dial art.

    FM4 dial faces (`gauge_emissive`) sample the cockpit atlas interior_LOD0.xds with atlas UVs (the IR draws
    already carry the FM4 UV transform). Its alpha is FM4's label cut-out mask, 0 over the dials, so the
    diffuse is written opaque (int_gauges alpha-tests). The night illumination keeps the bright marks only.
    EXPANDED_GARAGE_GAUGE_TEXTURES=off falls back to the untextured dial (bisection switch)."""
    if os.environ.get("EXPANDED_GARAGE_GAUGE_TEXTURES") == "off":
        return {}, {}
    atlas = raw_member(root, ir, "/interior_lod0.xds")
    from .fh4_presentation import swatch_bc7 as swatch, xds_codec
    codec = xds_codec(None)
    if atlas is None or codec is None:
        return {}, {}
    width, height, rgba = codec[0](atlas)
    pixels = np.frombuffer(bytes(rgba), np.uint8).reshape(height, width, 4).copy()
    pixels[..., 3] = 255
    luminance = pixels[..., :3].astype(np.float32).mean(axis=2)
    # Native illumination (ACU_RSXTypeS_02 gauges LITE): coloured marks on black, alpha = the same marks
    # (correlation 0.96 with their brightness); an all-255 alpha lit nothing.
    glow = np.clip((luminance - 120) / 80, 0, 1)
    lit = np.zeros_like(pixels)
    lit[..., :3] = np.where((glow > 0)[..., None], pixels[..., :3], 0)   # swatch() premultiplies by alpha
    lit[..., 3] = np.round(255 * glow).astype(np.uint8)
    donor = gauge_donor()
    folder = "\\".join(("Game:", "Media", "cars", alias.casefold(), *GAUGE_FOLDER.strip("/").split("/"), ""))
    textures, members = {}, {}
    for role, image in (("diff", pixels), ("lite", lit)):
        name = f"{alias.casefold()}_gauges_{role}_{uuid.uuid5(uuid.NAMESPACE_URL, f'urn:fh4-expanded-garage:{alias}:gauges:{role}')}"
        members[GAUGE_FOLDER + name + ".swatchbin"] = swatch(name, width, height, image.tobytes())
        textures[donor["diffuse" if role == "diff" else "illumination"]] = folder + name + ".swatchbin"
    from . import fh4_mtpr
    mtpr = fh4_mtpr.set_textures(bytes.fromhex(donor["mtpr"]), textures, tuple(donor["version"]))
    parameters = {("gauge_emissive", GAUGE_MATERIAL): (tuple(donor["version"]), mtpr)}
    if library is not None:
        parameters.update(cockpit_atlas_parameters(library, textures[donor["diffuse"]],
                                                   cockpit_texel_scales(raw_member(root, ir, "/shadersettings.xml"))))
        parameters.update(needle_parameters(library, textures[donor["diffuse"]]))
    return parameters, members


NEEDLE_MATERIAL = "interior_misc/gauge_needle.materialbin"
NEEDLE_GLOW_FALLBACK = (1.0, 1.0, 1.0)   # the library block's EmissiveColorColorParam


def needle_glow(root, ir, needles):
    """Linear RGB of the needle's lit colour: the area-weighted median of the bright cockpit-atlas texels
    under the FM4 needle triangles (SX4: the red face, not the dark hub).

    The lit needle draws EmissiveColorColorParam: natives with red needles set it red (BMW_M5_95, AUD_R8Plus_13:
    0.58, 0, 0, even over a grey diffuse). The library white turned our textured red needles white once the
    dash lights came on (owner, visual-42: red in ForzaVista, white on the road)."""
    atlas = raw_member(root, ir, "/interior_lod0.xds")
    from .fh4_presentation import xds_codec
    codec = xds_codec(None)
    if atlas is None or codec is None or not needles:
        return NEEDLE_GLOW_FALLBACK
    width, height, rgba = codec[0](atlas)
    pixels = np.frombuffer(bytes(rgba), np.uint8).reshape(height, width, 4)[..., :3].astype(np.float64)
    colours, weights = [], []
    for draw in needles:
        corners = draw.positions[draw.indices]
        area = np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1) / 2
        for bary in ((1/3, 1/3, 1/3), (0.6, 0.2, 0.2), (0.2, 0.6, 0.2), (0.2, 0.2, 0.6)):
            point = sum(draw.uv0[draw.indices[:, k]] * bary[k] for k in range(3))
            x = np.clip((point[:, 0] % 1) * width, 0, width - 1).astype(int)
            y = np.clip((point[:, 1] % 1) * height, 0, height - 1).astype(int)
            colours.append(pixels[y, x])
            weights.append(area)
    colours, weights = np.concatenate(colours), np.concatenate(weights)
    bright = colours.max(axis=1) > 60
    if not bright.any() or weights[bright].sum() <= 0:
        return NEEDLE_GLOW_FALLBACK
    colours, weights = colours[bright], weights[bright]
    order = np.argsort(colours.mean(axis=1))
    cumulative = np.cumsum(weights[order])
    median = colours[order][np.searchsorted(cumulative, cumulative[-1] / 2)]
    return tuple(float(round((c / 255) ** 2.2, 4)) for c in median)


def needle_parameters(library, atlas_path):
    """{(gauge_needle, gauge_needle): (version, MTPR)}: the needle's colour from the FM4 cockpit atlas.

    FM4 needles carry no colour of their own: UV0 points at the needle strip of interior_LOD0.xds (SX4: red
    face, dark hub; RUF: white; Integra: yellow). The library block draws DiffuseColorColorParam 0.58 grey
    with its diffuse texture off, so every converted needle read white (owner, visual-40). Native red
    needles set the same colour parameter (FOR_EscortRSCosworth_92, BMW_M5_95: 0.72, 0, 0). Here the
    library block's own diffuse slot (CH1, the slot int_gauges and the cabin atlas use) takes the atlas,
    switched on, under a white colour. EXPANDED_GARAGE_NEEDLE_TEXTURE=off keeps the library needle."""
    if os.environ.get("EXPANDED_GARAGE_NEEDLE_TEXTURE") == "off":
        return {}
    from . import fh4_mtpr
    body, version = library_mtpr(library, NEEDLE_MATERIAL)
    patched = fh4_mtpr.patch(body, {"CH1DiffColTextureSwitchBool": 1,
                                    "DiffuseColorColorParam": (1.0, 1.0, 1.0, 0.0)}, version)
    return {("gauge_needle", NEEDLE_MATERIAL): (tuple(version), fh4_mtpr.set_textures(patched, {ATLAS_DIFFUSE: atlas_path}, version))}


ATLAS_DIFFUSE = 0x810e7224   # int_ch1diffnormglossalpha CH1 diffuse texture (same slot as int_gauges)
EXTERIOR_FOLDER = "textures/exterior/swatches/"


def library_mtpr(library, member):
    data = ZipFile(library).read(member)
    _, geometry = previous_readers()
    return next((data[b["offset"]:b["offset"] + b["size"]], b["version"])
                for b in geometry.blobs(data) if b["tag"] == "MTPR")


def exterior_atlas_parameters(root, ir, alias, library):
    """({(emblem|badge, m_002): (version, MTPR)}, {member: swatchbin}): FM4 nodamage_LOD0.xds as the
    opaque diffuse of the native badge shader. EXPANDED_GARAGE_EXTERIOR_ATLAS=off keeps plain chrome."""
    if os.environ.get("EXPANDED_GARAGE_EXTERIOR_ATLAS") == "off" or library is None:
        return {}, {}
    atlas = raw_member(root, ir, "/nodamage_lod0.xds") or raw_member(root, ir, "/nodamage.xds")
    from .fh4_presentation import swatch_bc7 as swatch, xds_codec
    codec = xds_codec(None)
    if atlas is None or codec is None:
        return {}, {}
    width, height, rgba = codec[0](atlas)
    pixels = np.frombuffer(bytes(rgba), np.uint8).reshape(height, width, 4).copy()
    pixels[..., 3] = 255   # swatch() premultiplies; FM4 alpha here is a paint/dirt mask, not coverage
    name = f"{alias.casefold()}_nodamage_diff_{uuid.uuid5(uuid.NAMESPACE_URL, f'urn:fh4-expanded-garage:{alias}:nodamage:diff')}"
    folder = "\\".join(("Game:", "Media", "cars", alias.casefold(), *EXTERIOR_FOLDER.strip("/").split("/"), ""))
    from . import fh4_mtpr
    from .fh4_material_contract import EXTERIOR_ATLAS_GLOSS, EXTERIOR_ATLAS_MATERIAL
    member = EXTERIOR_ATLAS_MATERIAL + ".materialbin"
    body, version = library_mtpr(library, member)
    out = {}
    for label, gloss in EXTERIOR_ATLAS_GLOSS.items():
        patched = fh4_mtpr.patch(body, {"CH1DiffColTextureSwitchBool": 1, "CH1OpacityMaskSwitchBool": 0,
                                        "DiffuseColorGroupColorParam": (1.0, 1.0, 1.0, 1.0),
                                        "GlossA_floatVal": gloss}, version)
        out[(label, member)] = (tuple(version), fh4_mtpr.set_textures(
            patched, {ATLAS_DIFFUSE: folder + name + ".swatchbin"}, version))
    return out, {EXTERIOR_FOLDER + name + ".swatchbin": swatch(name, width, height, pixels.tobytes())}


SHADER_SECTION = re.compile(r"<\s*(cockpit_\w+)\s*>(.*?)<\s*/\s*\1\s*>", re.S | re.I)
SHADER_VALUE = re.compile(r'<\s*(\w+)\s+value\s*=\s*"([^"]+)"', re.I)


def cockpit_texel_scales(car_settings=None):
    """{FM4 cockpit class: texel multiplier (linear)}: fm4-cockpit-shading-v1.json (FM4 shared Normal.xml)
    overridden by the car's own ShaderSettings.xml. EXPANDED_GARAGE_COCKPIT_SCALE=off gives 1 everywhere."""
    if os.environ.get("EXPANDED_GARAGE_COCKPIT_SCALE") == "off":
        return {}
    record = json.loads(files(__package__).joinpath("fm4-cockpit-shading-v1.json").read_text(encoding="utf-8"))
    priority = record["priority"]
    scales = {name: entry["scale"] for name, entry in record["techniques"].items()}
    if car_settings:
        for technique, body in SHADER_SECTION.findall(car_settings.decode("utf-8", "replace")):
            values = {k: float(v) for k, v in SHADER_VALUE.findall(body)}
            key = next((k for k in priority if k in values), None)
            if key is not None and np.isfinite(values[key]):
                scales[technique.casefold()[len("cockpit_"):]] = values[key]
    return scales


LAMP_FOLDER = "textures/lights/swatches/"
REFLECTOR_GLOSS = 0.85
LAMP_GLOW_TEXTURE = 0x8f890e20   # lights_smooth_ch1 texture slot (donor: _library lightglows/light_32_lite)


def lamp_texture_parameters(root, ir, alias, library=None):
    """({(lamp label, lights_smooth_ch1): (version, MTPR)}, {member: swatchbin}): the donor lamp block with its
    glow texture pointed at the car's own FM4 lamp atlas (lights_LOD0.xds; alpha is FM4's glow mask).

    The donor slot held _library lightglows/light_32_lite (256x256); FM4 lamp UV0 addresses its own atlas, so
    the donor pattern was stretched into blocks on the lenses (owner, visual-35: the high-mount stop lamp).
    LOD1-5 draws read lights.xds, a separate layout, through `<label>_lod15` MatIs (fh4_source_atlas).
    EXPANDED_GARAGE_LAMP_TEXTURE=off keeps the donor texture."""
    if os.environ.get("EXPANDED_GARAGE_LAMP_TEXTURE") == "off":
        return {}, {}
    from .fh4_presentation import swatch_bc7, xds_codec
    from .fh4_source_atlas import far_name
    codec = xds_codec(None)
    near = raw_member(root, ir, "/lights_lod0.xds") or raw_member(root, ir, "/lights.xds")
    if near is None or codec is None:
        return {}, {}
    far = raw_member(root, ir, "/lights.xds") or near
    from . import fh4_mtpr
    out, payloads = {}, {}
    if library is not None:
        from .fh4_material_contract import EXTERIOR_ATLAS_MATERIAL
        member = EXTERIOR_ATLAS_MATERIAL + ".materialbin"
        body, badge_version = library_mtpr(library, member)
        reflector = fh4_mtpr.patch(body, {"CH1DiffColTextureSwitchBool": 1, "CH1OpacityMaskSwitchBool": 0,
                                          "DiffuseColorGroupColorParam": (1.0, 1.0, 1.0, 1.0),
                                          "GlossA_floatVal": REFLECTOR_GLOSS}, badge_version)
    for role, atlas in (("lite", near), ("lite_lod15", far)):
        width, height, rgba = codec[0](atlas)
        pixels = np.frombuffer(bytes(rgba), np.uint8).reshape(height, width, 4)
        name = f"{alias.casefold()}_lights_{role}_{uuid.uuid5(uuid.NAMESPACE_URL, f'urn:fh4-expanded-garage:{alias}:lights:{role}')}"
        path = "\\".join(("Game:", "Media", "cars", alias.casefold(), *LAMP_FOLDER.strip("/").split("/"), "")) + name + ".swatchbin"
        payloads[LAMP_FOLDER + name + ".swatchbin"] = swatch_bc7(name, width, height, pixels.tobytes())
        named = (lambda label: label) if role == "lite" else far_name
        for label in fh4_model.LAMP_FUNCTIONS:
            block = fh4_model.lamp_parameters(label, fh4_model.LAMP_MATERIAL)
            version = tuple(fh4_model.lamp_donor()["version"])
            out[(named(label), fh4_model.LAMP_MATERIAL)] = (version, fh4_mtpr.set_textures(block, {LAMP_GLOW_TEXTURE: path}, version))
        if library is not None:
            # textured_reflector / reflector: the badge shader with the lamp atlas as its diffuse.
            for label in ("textured_reflector", "reflector"):
                out[(named(label), member)] = (tuple(badge_version), fh4_mtpr.set_textures(
                    reflector, {ATLAS_DIFFUSE: path}, badge_version))
    return out, payloads


def cockpit_atlas_parameters(library, atlas_path, scales=None):
    """{(FM4 class, plastic_perforated_004): (version, MTPR)}: the library block with its diffuse texture
    switched on and pointed at the car's cockpit atlas, normal/AO/opacity layers off, the FM4 class texel scale as tint and the
    class gloss. EXPANDED_GARAGE_COCKPIT_ATLAS=off keeps the untextured library materials."""
    if os.environ.get("EXPANDED_GARAGE_COCKPIT_ATLAS") == "off":
        return {}
    from . import fh4_mtpr
    from .fh4_material_contract import COCKPIT_ATLAS_GLOSS, COCKPIT_ATLAS_MATERIAL
    member = COCKPIT_ATLAS_MATERIAL + ".materialbin"
    body, version = library_mtpr(library, member)
    out = {}
    for label, gloss in COCKPIT_ATLAS_GLOSS.items():
        patched = fh4_mtpr.patch(body, {
            "CH1DiffColTextureSwitchBool": 1, "CH1NormalMapSwitchBool": 0, "CH1LocalAOSwitchBool": 0,
            "CH1OpacityMaskSwitchBool": 0,
            "DiffuseColorGroupColorParam": (1.0, 1.0, 1.0, 1.0), "GlossA_floatVal": gloss,
            # FM4 cockpit_<class> scales the atlas texel in linear light (cockpit_texel_scales).
            "DiffTintColorParam": (*(min(1.0, max(0.0, (scales or {}).get(label, 1.0))),) * 3, 1.0),
            "GlossB_floatVal": gloss}, version)
        out[(label, member)] = (tuple(version), fh4_mtpr.set_textures(patched, {ATLAS_DIFFUSE: atlas_path}, version))
    return out


def make_models(root, ir, identity, library):
    rows = ir["sections"]["physics"]["data"]["tables"]
    points = systems.hardpoints(root, ir)
    authored = {corner: points[p].meters for p, corner in systems.CORNER_IDS.items() if p in points}
    bones, corners = WheelTransformCompiler().compile(rows,authored)
    native_rig = NativeRuntimeRigCompiler()
    contract = NativeCarSceneContract(ir, identity["body_id"])
    geometry = ir["sections"]["geometry"]["data"]
    car_parameters, texture_payloads = gauge_texture_parameters(root, ir, identity["media_name"], library)
    lamp_parameters, lamp_payloads = lamp_texture_parameters(root, ir, identity["media_name"], library)
    car_parameters.update(lamp_parameters)
    texture_payloads.update(lamp_payloads)
    grouped = {category: defaultdict(list) for category in ("exterior", "cockpit", "wheel", "brake")}
    filtered = set()
    for source in geometry["models"]:
        role = source["role"]
        kind = "exterior" if role.startswith("exterior") else "brake" if role in ("caliper", "rotor") else role
        for section, draw in load_draws(root, source):
            if ((contract.use_component(role, section) or role=="cockpit" and section in GAUGE_POSES)
                    and (kind == "wheel" or not draw.source_material.casefold().startswith("blur"))
                    and 0 <= draw.lod <= 5):
                grouped[kind][section].append(draw)
            else:
                filtered.add((role, section, draw.source_material))
    if not grouped["exterior"]:
        raise ValueError("No source exterior geometry available")
    # Cabin badges get their own MatI label, on the cockpit models and on their exterior-view copies.
    from .fh4_material_contract import CABIN_BADGE, COCKPIT_EXTERIOR_PARTS as _BODY_PARTS
    if os.environ.get("EXPANDED_GARAGE_CABIN_BADGE") != "atlas":
        cabin_parts = {k.casefold() for k in grouped["cockpit"] if not k.casefold().startswith(_BODY_PARTS)}
        badge_binding = surface_binding(CABIN_BADGE, "cockpit", "")
        for kind in ("cockpit", "exterior"):
            for key, draws in grouped[kind].items():
                if key.casefold() in cabin_parts:
                    draws[:] = [replace(d, source_material=CABIN_BADGE, target_material=badge_binding.member,
                                        transparent=badge_binding.transparent)
                                if d.source_material.casefold() == "badge" else d for d in draws]
    # Native exterior models carry TEXCOORD2 as their own non-overlapping 0..1 unwrap (ACU body_a carPaint
    # 0.01-0.98, bumpers their own square); FH4's DynamicRainDropRenderer draws the drops into a texture through
    # TexCoord2. Ours was zero outside the cabin, and the car never got rain or splash drops (owner, visual-43).
    # Same unwrap as the cabin AO maps (fh4_part_ao.unwrap), no map baked. EXPANDED_GARAGE_EXTERIOR_UV2=off.
    exterior_sides = {}
    if os.environ.get("EXPANDED_GARAGE_EXTERIOR_UV2") != "off":
        from . import fh4_part_ao
        for key in sorted(grouped["exterior"]):
            draws, side = fh4_part_ao.unwrap(grouped["exterior"][key])
            if side:
                grouped["exterior"][key] = draws
                exterior_sides[key] = side
    # Per-vertex COLOR0 (dirt by height, exposure) on the opaque exterior, like native carPaint meshes.
    # EXPANDED_GARAGE_DIRT=off keeps one median colour per material (visual-42).
    if os.environ.get("EXPANDED_GARAGE_DIRT") != "off" and corners:
        wheels = [(np.asarray(c["origin_m"], float), c["radius_m"], c["width_m"]) for c in corners.values()]
        ground = min(c[1] - r for c, r, _ in wheels)
        from .fh4_material_contract import COCKPIT_EXTERIOR_PARTS
        cabin = {name.casefold() for name in grouped["cockpit"]   # cabin parts also drawn in the exterior view
                 if not name.casefold().startswith(COCKPIT_EXTERIOR_PARTS)}
        for section, draws in grouped["exterior"].items():
            if section.casefold() in cabin:
                continue
            draws[:] = [d if d.transparent or fh4_model.glass_family(d.target_material)
                        or d.target_material.startswith("interior_") else
                        replace(d, color=fh4_dirt.vertex_colors(d.positions, d.normals, wheels, ground))
                        for d in draws]
    # FM4 source textures (car/lamp/wheel atlases per LOD, shared grille tiles) on native shaders.
    from . import fh4_source_atlas
    source_parameters, component_parameters, source_payloads = fh4_source_atlas.parameters(
        root, ir, identity["media_name"], library, grouped)
    car_parameters.update(source_parameters)
    texture_payloads.update(source_payloads)
    needle_key = ("gauge_needle", NEEDLE_MATERIAL)
    if needle_key in car_parameters:
        glow = needle_glow(root, ir, [d for draws in grouped["cockpit"].values() for d in draws
                                      if d.source_material.casefold() == "gauge_needle"])
        from . import fh4_mtpr
        version, block = car_parameters[needle_key]
        car_parameters[needle_key] = (version, fh4_mtpr.patch(block, {"EmissiveColorColorParam": (*glow, 1.0)}, version))
    ao_paths, ao_payloads = part_ao(identity["media_name"], grouped,
                                    {n.casefold() for part in contract.parts if not part.stock for n in part.components},
                                    exterior_sides, corners)
    aliases = identity["media_name"].lower()
    attachments, animation_payloads, motion = native_rig.bind_motion(aliases,ir,bones,points,grouped)
    driver_side = driver_side_from(grouped)
    driver_rig = fh4_driver_rig.add_driver_bones(bones, driver_side)
    skel = f"scene/{aliases}_skeleton.modelbin"
    payloads = {skel: fh4_model.rig_model(bones), **animation_payloads}
    models, references = [], {}
    materials = {}
    library_hash = sha256(library)
    with ZipFile(library) as archive:
        available = {name.lower(): name for name in archive.namelist()}

        wheel_mask = [0]
        exterior_finest = {k.casefold(): min(d.lod for d in v) for k, v in grouped["exterior"].items()}
        cockpit_keys = {k.casefold() for k in grouped["cockpit"]}
        exterior_near = exterior_surface_keys([d for v in grouped["exterior"].values() for d in v if d.lod == 0])

        def view_split(kind, key):
            """Native split of near and far geometry between the exterior and cockpit views.

            FM4 leaves LOD0 of interior, seats, mirrors, side and front glass and hoods to the cockpit set.
            Native carbins draw such parts with one model in both views (draw 39, LOD0/1: 3,697 models), and
            give exterior glass every LOD while the inner glass stays cockpit-only (glass*int, draw 38).
            """
            name = key.casefold()
            glass = name.startswith("glass")
            if kind == "exterior" and glass and exterior_finest.get(name, 0) > 0 and name in cockpit_keys:
                return "fill"
            if kind == "cockpit" and not glass and exterior_finest.get(name, 99) > 0:
                # FM4 cockpit parts that are the body seen from inside (a_pillar: the B-pillar applique and
                # roof edge) repeat exterior LOD0 triangles; drawn in both views they z-fight with the body
                # (owner, visual-29). Those stay cockpit-only. EXPANDED_GARAGE_COCKPIT_OVERLAP=keep: old rule.
                if (os.environ.get("EXPANDED_GARAGE_COCKPIT_OVERLAP") != "keep"
                        and exterior_overlap(grouped["cockpit"][key], exterior_near) >= 0.5):
                    return None
                return "both_views"
            return None

        def view_mask(kind, key, near):
            """Carbin draw mask by camera (bits over 40 native carbins: 1|4 exterior, 2 shadow/reflection,
            8 bonnet camera, 16 driver camera, 32 cockpit/dashboard cameras). Steering wheels are 23 on 30 of 30
            sampled cars (no 32: hidden in the dashboard camera; ours drew in every view, owner); bonnets 47
            (+8: drawn in the bonnet camera; FM4 keeps hood LOD0 in the cockpit set, our both-view copy, so
            the bonnet camera showed no hood, owner). EXPANDED_GARAGE_VIEW_MASKS=off: the visual-49 masks."""
            name = key.casefold()
            if os.environ.get("EXPANDED_GARAGE_VIEW_MASKS") == "off":
                return 39 if near == "both_views" else 0
            if kind == "cockpit" and name.startswith("steering"):
                return 23 if near == "both_views" else 22
            if name.startswith("hood"):
                return 47 if near == "both_views" else (13 if kind == "exterior" else 0)
            return 39 if near == "both_views" else 0

        def emit(kind, key, draws, bone=0, translation=(0.0, 0.0, 0.0), attachment=None, component=None,
                 path=None, rig=None, mesh_bone=False, rotation=(0.,0.,0.,1.)):
            if attachment:
                bone, translation, rotation = attachment.index, attachment.point, attachment.rotation
                axes = fh4_model.transform(translation,rotation)[:3,:3]
                draws = [replace(d,positions=np.asarray((d.positions-translation) @ axes.T,dtype=np.float32),
                                 normals=np.asarray(d.normals @ axes.T,dtype=np.float32)) for d in draws]
            for draw in draws:
                mat = surface_binding(draw.source_material,kind,key)
                if mat.member != draw.target_material:
                    raise ValueError("Inconsistent material semantics during scene emission")
                materials[(kind,key,draw.source_material)] = {"role":kind,"component":key,"source":draw.source_material,
                    "member":mat.member,"category":mat.use.value,"drive_resolved":mat.drive_resolved,
                    "fidelity":mat.fidelity,"trial_compatibility":mat.trial_compatibility}
            supported = sorted({d.target_material for d in draws})
            for material in supported:
                if material not in available:
                    raise ValueError(f"Missing shared FH4 material {material}")
                if material not in references:
                    item = archive.getinfo(available[material])
                    references[material] = {"member": item.filename, "crc32": f"{item.CRC:08x}"}
            if rig is not None:
                # Native stock wheel: one normalised model at the convention path.
                if path in payloads:
                    raise ValueError(f"Duplicate output model name {path}")
                # EXPANDED_GARAGE_WHEEL_MORPH=off drops the morph targets and the wheel mesh role (bisection switch).
                morph = os.environ.get("EXPANDED_GARAGE_WHEEL_MORPH") != "off"
                payloads[path], wheel_mask[0] = fh4_model.assemble(draws, rig, 0, shadows=True, wheel=morph,
                                                                   parameters=car_parameters)
                return
            local = re.sub(r"[^a-z0-9_]+", "_", key.lower()).strip("_")
            path = f"scene/{kind}/{local}.modelbin"
            if path in payloads:
                raise ValueError(f"Duplicate output model name {path}")
            # The scene attachment is in the chassis rig. Rigid wheel and brake files have their own local frame.
            local_rig = kind in ("wheel", "brake") or attachment is not None
            component_rig = bones[:2] if local_rig else bones
            rigid_bone = 0 if local_rig else 1
            if mesh_bone and attachment:
                # Native needles: a <root> model whose meshes are rigidly bound (RigidBoneIndex) to the gauge
                # bone, vertices in that bone's frame; the GSF pose reaches meshes, not Carbin attachments.
                component_rig, rigid_bone = bones, attachment.index
                bone, translation, rotation = 0, (0.0, 0.0, 0.0), (0., 0., 0., 1.)
            near = view_split(kind, key)
            parameters = {**car_parameters, **component_parameters.get((kind, key.casefold()), {})}
            payloads[path], mask = fh4_model.assemble(draws, component_rig, rigid_bone, shadows=kind != "cockpit",
                                                      fill_near=near == "fill", parameters=parameters)
            models.append(Model(path, kind, mask, bone, translation, component or key.casefold(), rotation,
                                draw=view_mask(kind, key, near), ao=ao_paths.get((kind, key), "")))

        for kind in ("exterior", "cockpit"):
            for key, draws in sorted(grouped[kind].items()):
                if key in GAUGE_POSES:
                    if not key.endswith("_min"):
                        continue
                    needles = [d for d in draws if d.source_material.casefold()=="gauge_needle"]
                    housing = [d for d in draws if d.source_material.casefold()!="gauge_needle"]
                    if housing:
                        emit(kind,key+"_housing",housing+gauge_backing(housing,kind,key+"_housing"))
                    if needles:
                        emit(kind,key,needles,attachment=attachments.get(key),mesh_bone=True)
                else:
                    if kind == "cockpit" and window_part(key):
                        draws = [inward(d) for d in draws]
                    if kind == "cockpit":
                        # Dial faces also live in the cabin body (FM4 `interior`: gauge_emissive, digital_gauge).
                        faces = [d for d in draws if d.source_material.casefold() in GAUGE_FACES]
                        draws = draws + gauge_backing(faces, kind, key)
                    emit(kind, key, draws,attachment=attachments.get(key))
        paint = None
        colors = rows_for(rows, "Combo_Colors") if "Combo_Colors" in rows else []
        if colors:
            payloads["ManufacturerColors.bin"], paint = fh4_paint.manufacturer_colors(colors, set(available))
        wheel = [d for draws in grouped["wheel"].values() for d in draws]
        wheel_model = None
        if wheel and os.environ.get("EXPANDED_GARAGE_WHEELS", "native") == "carbin":
            # Bisection switch: the pre-v1.4.6 metre-scale wheels bound in the Carbin.
            for corner, binding in corners.items():
                local = [reflected(d) for d in wheel] if corner.startswith("R") else wheel
                emit("wheel", f"wheel{corner}", local, binding["spindle"], binding["origin_m"])
        elif wheel:
            mount = brake_mounts(grouped["brake"])
            normalised, wheel_bones, wheel_model = fh4_wheel_model.normalise(wheel, mount.get("LF"))
            wheel_model["member"] = fh4_wheel_model.wheel_path(identity["media_name"])
            emit("wheel", "wheelLF", normalised, path=wheel_model["member"], rig=wheel_bones)
            # Native carbins reference that one model four times in part group 43 (785 of 785 sampled):
            # bone spindleXX, origin at the outer rim face, right side turned 180 degrees about Y.
            # Across 32 sampled cars the entry X sits within a few cm of ModelFrontTrackOuter / 2.
            outer = -wheel_model["outer_face_m"]
            entries = []
            for corner, binding in sorted(corners.items()):
                right = corner.startswith("R")
                hub = np.asarray(binding["origin_m"], dtype=float)
                point = (hub[0] + (outer if right else -outer), hub[1], hub[2])
                rotation = (0.0, 1.0, 0.0, 0.0) if right else (0.0, 0.0, 0.0, 1.0)
                models.append(Model(wheel_model["member"], "wheel", wheel_mask[0], binding["spindle"],
                                    tuple(map(float, point)), f"wheel{corner}".casefold(), rotation))
                entries.append({"corner": corner, "bone": bones[binding["spindle"]].name,
                                "origin_m": [float(v) for v in point]})
            wheel_model["carbin_entries"] = entries
        for key, draws in sorted(grouped["brake"].items()):
            corner = key[-2:].upper()
            if corner in corners:
                bone = corners[corner]["hub" if key.lower().startswith("caliper") else "spindle"]
                origin = corners[corner]["origin_m"]
                # Native right-side brakes (FOR_FiestaST_14, ACU_RSXTypeS_02) are placed turned 180 degrees about Y,
                # like the right wheels, with the model in that turned frame. Ours sat unturned and the right discs
                # showed their hub hat outboard (owner, visual-51 RUF). EXPANDED_GARAGE_BRAKE_TURN=off: unturned.
                if corner.startswith("R") and os.environ.get("EXPANDED_GARAGE_BRAKE_TURN") != "off":
                    turned = [replace(d, positions=np.asarray(d.positions * (-1, 1, -1), dtype=np.float32),
                                      normals=np.asarray(d.normals * (-1, 1, -1), dtype=np.float32)) for d in draws]
                    emit("brake", key, turned, bone, origin, rotation=(0.0, 1.0, 0.0, 0.0))
                else:
                    emit("brake", key, draws, bone, origin)
    nonstock = {name for part in contract.parts if not part.stock for name in part.components}
    # Native licence plate and plate lamp in the FM4 plate recess (fh4_plate; EXPANDED_GARAGE_PLATE=off).
    from . import fh4_plate
    plate_payloads, driver_rig["plate"] = fh4_plate.plate_models(
        Path(library).parents[1], fh4_plate.plate_draws(grouped["exterior"], nonstock))
    with ZipFile(library) as archive:
        available = {name.casefold(): name for name in archive.namelist()}
        for member, data in sorted(plate_payloads.items()):
            payloads[member] = data
            component = Path(member).stem.casefold()
            models.append(Model(member, "exterior", PLATE_LOD_MASK, 0, (0.0, 0.0, 0.0), component, draw=PLATE_DRAW))
            for mati, library_member in modelbin_materials(data):
                if library_member not in available:
                    raise ValueError(f"Missing shared FH4 material {library_member}")
                item = archive.getinfo(available[library_member])
                references[library_member] = {"member": item.filename, "crc32": f"{item.CRC:08x}"}
                materials[("exterior", component, mati)] = {
                    "role": "exterior", "component": component, "source": mati, "member": library_member,
                    "category": MaterialUse.FIDELITY.value, "drive_resolved": True,
                    "fidelity": "native_plate_model", "trial_compatibility": "native_shared_binding"}
    surface_geometry = [d.positions for component, draws in grouped["exterior"].items()
                        if component.casefold() not in nonstock
                        for d in draws if d.lod == min(x.lod for x in draws)]
    bounds = (np.min([p.min(axis=0) for p in surface_geometry], axis=0),
              np.max([p.max(axis=0) for p in surface_geometry], axis=0))
    payloads[f"{aliases}.carbin"] = carbin(identity, bones, skel, models, bounds, contract.groups(models),
                                          {m.member: modelbin_box(payloads[m.member]) for m in models},
                                          {m.member: modelbin_materials(payloads[m.member]) for m in models})
    payloads.update(texture_payloads)
    payloads.update(ao_payloads)
    # Vinyl/decal editor projection masks (native LiveryMasks/, carried over from FM4 liverymasks/).
    payloads.update(fh4_livery.livery_members(livery_sources(root, ir)))
    # EXPANDED_GARAGE_LIGHT_RIG=off leaves the car without light sources (bisection switch).
    if os.environ.get("EXPANDED_GARAGE_LIGHT_RIG") != "off":
        rig_files = light_rig(aliases, models, payloads)
        if rig_files:
            payloads["Lights.bin"], payloads["LightPresets.bin"] = rig_files
    # Native scene/ProxyLOD.modelbin (convention-loaded): coarsest LOD of every stock exterior part.
    proxy_draws = [d for component, draws in sorted(grouped["exterior"].items())
                   if component.casefold() not in nonstock
                   for d in draws if d.lod == max(x.lod for x in draws)]
    payloads[PROXY_LOD] = fh4_model.proxy_model(proxy_draws)
    # Ground AO / soft shadow under the car (native aovolumetexture.bin, convention-loaded): occluder
    # proxy = coarsest stock exterior plus a cylinder per tyre. EXPANDED_GARAGE_AO_VOLUME=off skips it.
    if os.environ.get("EXPANDED_GARAGE_AO_VOLUME") != "off":
        occluders = [d.positions.astype(np.float64)[d.indices] for d in proxy_draws]
        occluders += [fh4_ao_volume.tyre(np.asarray(c["origin_m"], float), c["radius_m"], c["width_m"])
                      for c in corners.values()]
        from . import fh4_cache
        triangles = np.concatenate(occluders)
        template = json.loads(files(__package__).joinpath("fh4-ground-ao-v1.json").read_text(encoding="utf-8"))
        modules = (fh4_ao_volume.__name__,)
        payloads[AO_VOLUME] = fh4_cache.cached_bytes("ao-volume", modules, (triangles,),
                                                     lambda: fh4_ao_volume.build(triangles))
        payloads[fh4_ao_volume.GROUND_MEMBER] = fh4_cache.cached_bytes(
            "ao-ground", modules, (triangles, template), lambda: fh4_ao_volume.ground_texture(triangles, template))
    # Dirt mask and wiper ramps (native Textures/Damage, Textures/Wiper; convention-loaded, fh4_dirt).
    stock_exterior = {component: draws for component, draws in grouped["exterior"].items()
                      if component.casefold() not in nonstock}
    dirt_draws = [d for draws in stock_exterior.values() for d in draws
                  if d.lod == min(x.lod for x in draws) and not d.transparent
                  and not fh4_model.glass_family(d.target_material)]
    panes = [d for component, draws in stock_exterior.items() if component.casefold() == "glassf"
             for d in draws if d.lod == min(x.lod for x in draws) and d.transparent]
    windscreen = [max(panes, key=lambda d: len(d.indices))] if panes else []
    payloads.update(fh4_dirt.members(
        dirt_draws, windscreen,
        [(c["origin_m"], c["radius_m"], c["width_m"]) for c in corners.values()], driver_side))
    if points:
        authored = {"carLocator_" + point.name for point in points.values()}
        extra, driver_rig["locators"] = fh4_driver_rig.transferred_locators(
            points, authored, driver_side, spindle=fh4_driver_rig.steering_spindle(bones))
        # Backfire flames at the FM4 tail pipes, not at the donor's (EXPANDED_GARAGE_EXHAUST_LOCATORS=donor).
        if os.environ.get("EXPANDED_GARAGE_EXHAUST_LOCATORS") != "donor":
            tips = systems.exhaust_tips(stock_exterior)
            extra, driver_rig["exhaust"] = systems.exhaust_locators(extra, tips)
            driver_rig["exhaust_tips_m"] = tips
        bonnet = [d.positions for component, draws in [*stock_exterior.items(), *grouped["cockpit"].items()]
                  if component.casefold().startswith(("hood", "body")) for d in draws
                  if not d.transparent and not fh4_model.glass_family(d.target_material)]
        extra, driver_rig["hood_locator"] = systems.hood_locator(
            extra, np.concatenate(bonnet) if bonnet else np.zeros((0, 3)))
        payloads["Locators.xml"] = systems.locator_xml(aliases, points, extra)
        identity["cockpit_camera"], driver_rig["cockpit_camera"] = cockpit_camera_offsets(bones, extra, driver_side)
        locator_names = authored | {item["name"] for item in extra}
        avpins, driver_rig["autovista"] = fh4_driver_rig.autovista(points, locator_names, driver_side)
        # Bisection (visual-48): the Integra, which gets no .avpins (right-hand drive), shows rain drops; the
        # SX4 and RUF, which do, never did. EXPANDED_GARAGE_AUTOVISTA=off ships none.
        if os.environ.get("EXPANDED_GARAGE_AUTOVISTA") == "off":
            avpins = None
        if avpins is not None:
            payloads[f"{aliases}.avpins"] = avpins
    payloads["IKAnchorBones.xml"] = fh4_driver_rig.ik_anchor_xml()
    steering = next((d for k, ds in grouped["cockpit"].items() if k.casefold() == "steering_wheel"
                     for d in ds), None)
    seat = {}
    if steering is not None and abs(x := float(np.median(steering.positions[:, 0]))) > 0.05:
        side = "L" if x < 0 else "R"
        seat_draws = [d for key, ds in grouped["cockpit"].items() if key.casefold() == f"seat{side.lower()}" for d in ds]
        if seat_draws:
            p = np.concatenate([d.positions for d in seat_draws])
            seat = {"driver_side": side, "steering_center_x_m": x, "seat_bounds_m": [p.min(0).tolist(), p.max(0).tolist()],
                    "source": [steering.origin, seat_draws[0].origin]}
    material_contract = [materials[key] for key in sorted(materials)]
    fallback = sorted({m["source"] for m in material_contract if not m["drive_resolved"]})
    analysis = {
        "mechanical": fh4_physics.stock_mechanical(rows),
        "collision_spheres": fh4_physics.maxdata_spheres(ir["sections"]["structure"]["data"]),
        "convex_envelope": fh4_physics.hull_envelope(np.concatenate(surface_geometry)),
        "convex_envelope_frame": "FH4 frame, meters; approximate support envelope of selected exterior geometry",
        "source": sorted({d.origin["asset"] for ds in grouped["exterior"].values() for d in ds}),
    }
    payloads["physicsdefinition.bin"], analysis["native_collision"] = systems.build_collision(
        analysis["convex_envelope"]["vertices_m"])
    gr2, rig_report = native_rig.compile(aliases, bones, grouped["exterior"].keys() | grouped["cockpit"].keys(), seat)
    rig_report["motion_contract"] = motion
    if animation_payloads:
        rig_report["gsf"] = {"state":"structurally_verified","member":f"animations/{aliases}.gsf",
                             "runtime":"unverified","clips":[g["member"] for g in motion["gauges"]]}
    payloads[rig_report["gr2"]["member"]] = gr2
    if probe := animation_probe():
        # Diagnostic: the donor's whole animation stack, byte for byte (skeleton GR2, GSF, clips).
        for member in [m for m in payloads if m.startswith("animations/")]:
            del payloads[member]
        payloads.update(probe_members(probe, aliases))
        rig_report["animation_probe"] = str(probe)
    return payloads, {
        "modelbins": len(models) + 1, "models_by_role": {k: sum(m.kind == k for m in models) for k in PART_TYPES},
        "bone_names": [bone.name for bone in bones], "scene_contract": contract.receipt(),
        "driver_rig": driver_rig, "stock_wheel_model": wheel_model, "manufacturer_colors": paint,
        "material_library": {"sha256": library_hash, "references": references},
        "materials_with_diagnostic_fallback": fallback,"material_contract":material_contract,
        "retained_in_ir_only": sorted(filtered),
        "geometry_bounds_m": [x.tolist() for x in bounds], "driver_geometry": seat, "runtime_rig": rig_report,
        "inferred_wheel_hubs": corners, "inferred_lamp_surfaces": guess_lights(grouped["exterior"]),
        "authored_hardpoints": [vars(point) for point in points.values()],
        "wheel_rest_frame": "Chassis-space placement and bone rest are serialized separately as in stock scenes. Runtime composition is unverified.",
    }, analysis


def brake_mounts(brakes):
    """Hub-frame X of each corner's brake mounting plane: the outboard face of its rotor (hat), LOD0.

    Left corners face outward along -X, right corners along +X (FM4 hub frames). Empty with
    EXPANDED_GARAGE_BRAKE_MOUNT=hub (pre-visual-54: spindle at the hub centre, rotors on the hub).
    """
    if os.environ.get("EXPANDED_GARAGE_BRAKE_MOUNT") == "hub":
        return {}
    mounts = {}
    for key, draws in brakes.items():
        if not key.lower().startswith("rotor"):
            continue
        finest = min(d.lod for d in draws)
        xs = np.concatenate([d.positions[:, 0] for d in draws if d.lod == finest])
        corner = key[-2:].upper()
        mounts[corner] = float(xs.max() if corner.startswith("R") else xs.min())
    return mounts


def previous_readers():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        from fh4_carbin import Reader
        import fh4_modelbin_geometry as geometry
    finally:
        sys.path.pop(0)
    return Reader, geometry


def skeleton_bones(original, record):
    offset = record["offset"]
    stop = offset + record["size"]
    count, = struct.unpack_from("<H", original, offset)
    offset += 2
    bones = []
    for _ in range(count):
        length, = struct.unpack_from("<I", original, offset)
        start = offset + 4
        offset = start + length + 70
        if offset > stop:
            raise ValueError("Skeleton bone exceeds its declared range")
        matrix = start + length + 6
        parent, = struct.unpack_from("<h", original, start + length)
        transform = struct.unpack_from("<16f", original, matrix)
        bones.append((original[start:start+length].decode("utf-8"),parent,transform))
    if not bones or len({b[0] for b in bones}) != len(bones) or offset + 4 != stop:
        raise ValueError("Unexpected generated skeleton record")
    return bones


def skeleton_names(original, record):
    return [b[0] for b in skeleton_bones(original, record)]


def verify_material(original, blob, geometry):
    nested = original[blob["offset"]:blob["offset"] + blob["size"]]
    reference, = [r for r in geometry.blobs(nested) if r["tag"] == "MATI"]
    offset = reference["offset"]
    length = nested[offset]
    encoded = nested[offset + 1:offset + reference["size"]]
    if length >= 128 or len(encoded) != length:
        raise ValueError("Invalid shared material path length")
    prefix = "game:\\media\\cars\\_library\\materials\\"
    path = encoded.decode("ascii").casefold()
    if not path.startswith(prefix) or not path.endswith(".materialbin"):
        raise ValueError(f"Nonlibrary Modelbin material {path}")
    return member_path(path.removeprefix(prefix))


def validate_zip(blob, name, car_id, *, trial=None):
    directory = fh4_archive.verify(blob)
    reader, geometry = previous_readers()
    with ZipFile(BytesIO(blob)) as archive:
        names = {member.casefold(): member for member in archive.namelist()}
        aliases = name.casefold()
        bindings = {}
        placements = {}

        class SceneReader(reader):
            def model(self):
                start = self.pos
                entry = super().model()
                self.pos, end = start, self.pos
                self.take(2)
                self.take(self.number("I"))
                transform = struct.unpack("<16f", self.take(64))
                placements[entry["path"]] = np.asarray(transform).reshape(4,4)
                self.take(2)
                bone_name = self.take(self.number("I")).decode("utf-8")
                bindings[entry["path"]] = self.number("h"), bone_name
                self.pos = end
                return entry

        scene = SceneReader(archive.read(names[f"{aliases}.carbin"])).scene()
        if (scene["media_name"], scene["ordinal"]) != (aliases, car_id):
            raise ValueError("FH4 Carbin identity failed readback")
        prefix = virtual(aliases, "")
        references = [scene["skeleton"]] + [m["path"] for p in scene["fixed"] for m in p["models"]]
        for group in scene["groups"]:
            keys = {part["id"] for part in group["upgrades"]}
            if len(keys) != len(group["upgrades"]) or sum(bool(part["stock"]) for part in group["upgrades"]) != 1:
                raise ValueError("Inconsistent FH4 upgrade group")
            for binding in group["shared"]:
                if not binding["ids"] or any(item["id"] not in keys for item in binding["ids"]):
                    raise ValueError("Invalid shared upgrade model binding")
                references.append(binding["model"]["path"])
        used, scene_bones, scene_rest = set(), [], []
        material_members = set()
        mesh_bound = set()   # bones that mesh RigidBoneIndex drives (native needles)
        for path in references:
            if not path.startswith(prefix):
                raise ValueError(f"Nonlocal Carbin binding {path}")
            key = path[len(prefix):].replace("\\", "/")
            if key in used and key == fh4_wheel_model.wheel_path(aliases):
                continue   # the native stock wheel is referenced once per corner
            if key in used:
                raise ValueError(f"Duplicate Carbin model {key}")
            used.add(key)
            original = archive.read(names[key])
            chunks = list(geometry.blobs(original))
            skel, = [b for b in chunks if b["tag"] == "Skel"]
            local_rest = skeleton_bones(original, skel)
            local_bones = [b[0] for b in local_rest]
            if path == scene["skeleton"]:
                scene_bones = local_bones
                scene_rest = local_rest
                continue
            bone, label = bindings[path]
            if not 0 <= bone < len(scene_bones) or scene_bones[bone] != label:
                raise ValueError(f"Carbin scene attachment mismatch in {key}")
            chunks, materials, streams, indices, layouts = geometry.maps(original)
            for b in chunks:
                if b["tag"] == "VLay":
                    list(geometry.layout_elements(original, b))
                elif b["tag"] == "MatI":
                    material_members.add(verify_material(original, b, geometry))
                elif b["tag"] == "Mesh":
                    mesh = geometry.mesh_info(original, b, materials, indices)
                    if not 0 <= mesh["bone"] < len(local_bones):
                        raise ValueError(f"Unknown rigid bone index in {key}")
                    mesh_bound.add(local_bones[mesh["bone"]])
                    raw = original[mesh["start"]:mesh["start"] + mesh["indices"] * 4]
                    maximum = int(np.frombuffer(raw, dtype="<u4").max())
                    if key.startswith(NATIVE_COPY_FOLDERS):
                        continue   # native models byte for byte (base-vertex offsets the check does not model)
                    layout, slots = struct.unpack_from("<ii", original, b["offset"] + 55)
                    if not 0 <= layout < len(layouts) or slots not in (1, 2):
                        raise ValueError(f"Invalid mesh layout in {key}")
                    for i in range(slots):
                        stream, slot, stride, offset = struct.unpack_from("<iIII", original, b["offset"] + 63 + 16*i)
                        _, count, width = geometry.buffer_info(original, streams[stream & 0xffffffff])
                        if slot != i or offset % stride or stride != width or offset + (maximum+1)*stride > count*width:
                            raise ValueError(f"Mesh vertex stream range mismatch in {key}")
        if "locators.xml" in names:
            systems.validate_locator_xml(archive.read(names["locators.xml"]), scene_bones)
            used.add("locators.xml")
        if f"{aliases}.avpins" in names:
            if "locators.xml" not in names:
                raise ValueError("Autovista file without Locators.xml")
            located = set(systems.validate_locator_xml(archive.read(names["locators.xml"]), scene_bones))
            fh4_driver_rig.validate_autovista(archive.read(names[f"{aliases}.avpins"]), located)
            used.add(f"{aliases}.avpins")
        if "ikanchorbones.xml" in names:
            fh4_driver_rig.validate_ik_anchor_xml(archive.read(names["ikanchorbones.xml"]), scene_bones)
            used.add("ikanchorbones.xml")
        gr2 = scene["skeleton"][len(prefix):].replace("\\", "/").removesuffix(".modelbin") + ".gr2"
        if animation_probe() and gr2 in names:
            used.update(m for m in names if m == gr2 or m.startswith("animations/"))
        elif gr2 in names:
            rig = modelbin_view(verify_skeleton(archive.read(names[gr2]), aliases))
            if len(rig) != len(scene_rest) or any(
                    (bone["name"],bone["parent"]) != ("GrannyRootBone" if i==0 else label,parent)
                    or not np.allclose(matrix,fh4_model.transform(bone["local_m"],
                        bone.get("local_rotation",(0.,0.,0.,1.))).ravel(),atol=2e-6,rtol=0)
                    for i,(bone,(label,parent,matrix)) in enumerate(zip(rig,scene_rest))):
                raise ValueError("Modelbin rest rig and Granny skeleton disagree")
            world = []
            for record in rig:
                local = fh4_model.transform(record["local_m"],record.get("local_rotation",(0.,0.,0.,1.)))
                world.append(local @ world[record["parent"]] if record["parent"]>=0 else local)
            for path, (bone, label) in bindings.items():
                if path == virtual(aliases, fh4_wheel_model.wheel_path(aliases)):
                    continue   # native wheel entries sit at the outer rim face, not at the spindle
                # Right-side brakes sit at their bone turned 180 degrees about Y, like native right brakes.
                turned = fh4_model.transform((0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0)) @ world[bone]
                right_brake = "\\scene\\brake\\" in path and label.endswith(("RF", "RR"))
                if label.startswith(("hub","spindle","boneSteeringWheel","boneFuel","boneTach","boneSpeed")) and not (
                        np.allclose(placements[path],world[bone],rtol=0,atol=2e-6)
                        or right_brake and np.allclose(placements[path],turned,rtol=0,atol=2e-6)):
                    raise ValueError(f"Component placement disagrees with its Granny bind point: {path}")
            used.add(gr2)
            gsf = f"animations/{aliases}.gsf"
            if gsf in names:
                clips = verify_gauge_graph(archive.read(names[gsf]),lambda p: archive.read(names[p]),
                                           {b["name"]:b for b in rig},
                                           {label for _,label in bindings.values()} | mesh_bound)
                used.update(clips | {gsf})
        if PROXY_LOD in names:
            data = archive.read(names[PROXY_LOD])
            kinds = [b["tag"] for b in geometry.blobs(data)]
            if kinds.count("Mesh") != 2 or kinds.count("MatI") != 1:
                raise ValueError("Unexpected ProxyLOD structure")
            for b in geometry.blobs(data):
                if b["tag"] == "Modl":
                    low, high = dict(fh4_model_box(data, b))["box"]
                    if not all(h - l > 0.5 for l, h in zip(low, high)):
                        raise ValueError(f"Degenerate ProxyLOD bounds {low} {high}")
            used.add(PROXY_LOD)
        if AO_VOLUME in names:
            volume = fh4_ao_volume.read(archive.read(names[AO_VOLUME]))
            if volume["dims"] != (128, 64, 128) or not len(volume["vertices"]):
                raise ValueError("Unexpected aovolumetexture.bin shape")
            used.add(AO_VOLUME)
        ground = fh4_ao_volume.GROUND_MEMBER.casefold()
        if ground in names:
            if len(archive.read(names[ground])) != 248 + 349525:
                raise ValueError("Unexpected ground_AO.swatchbin size")
            used.add(ground)
        carbin_bytes = archive.read(names[f"{aliases}.carbin"]).lower()
        for name in names:
            if name.startswith(AO_FOLDER.casefold()) and name.endswith("_ao.swatchbin") and name != ground:
                data = archive.read(names[name])
                _, _, _, _, meta, _, _, _ = fh4_model.BLOB.unpack_from(data, fh4_model.HEADER.size)
                _, flags, relative = struct.unpack_from("<IHH", data, meta)
                fields = struct.unpack_from("<17I", data, meta + relative)
                if fields[16] != 19 or fields[6] != fields[7] or fields[9] >> 16 < 2:
                    raise ValueError(f"Unexpected model AO map layout {name}")
                stem = name.rsplit("/", 1)[-1]
                if ("\\textures\\ao\\" + stem).encode() not in carbin_bytes:
                    raise ValueError(f"Model AO map without a Carbin AO record: {name}")
                used.add(name)
            if name.startswith((GAUGE_FOLDER, EXTERIOR_FOLDER, LAMP_FOLDER)) and name.endswith(".swatchbin"):
                if len(archive.read(names[name])) <= 140:
                    raise ValueError(f"Empty gauge texture {name}")
                used.add(name)
        if "lights.bin" in names or "lightpresets.bin" in names:
            from .research.extract_light_rig import read_lights
            presets = archive.read(names["lightpresets.bin"])
            magic, size, version, count = struct.unpack_from("<4sIII", presets)
            if (magic, version, size) != (b"LDFB", 1, len(presets)):
                raise ValueError("LightPresets.bin is not an LDFB v1 preset library")
            attachments, lights = read_lights(archive.read(names["lights.bin"]))
            models_here = {virtual(aliases, n) for n in names if n.endswith(".modelbin")}
            if any(path not in models_here for path, _ in attachments):
                raise ValueError("Lights.bin hangs a light on a model the archive lacks")
            if any(light["attachment"] >= len(attachments) or light["id"] >= count for light in lights):
                raise ValueError("Lights.bin references a missing attachment or preset")
            used.update({"lights.bin", "lightpresets.bin"})
        used.update(fh4_livery.verify(lambda n: archive.read(n), names))
        used.update(fh4_dirt.verify(lambda n: archive.read(n), names))
        if "manufacturercolors.bin" in names:
            if not fh4_paint.read_manufacturer_colors(archive.read(names["manufacturercolors.bin"])):
                raise ValueError("Empty ManufacturerColors.bin")
            used.add("manufacturercolors.bin")
        wheel_member = fh4_wheel_model.wheel_path(aliases)
        wheel_model = False
        if wheel_member in names:
            data = archive.read(names[wheel_member])
            for b in geometry.blobs(data):
                if b["tag"] == "Modl":
                    meta = dict(fh4_model_box(data, b))
                    fh4_wheel_model.check_bounds(*meta["box"])
                    wheel_model = True
                elif b["tag"] == "MatI":
                    material_members.add(verify_material(data, b, geometry))
            used.add(wheel_member)
        if "physicsdefinition.bin" in names:
            _, clouds, tail = systems.read_collision(archive.read(names["physicsdefinition.bin"]))
            if tail != bytes(12) or [flags for flags, _ in clouds] != [0xe05, 0xb05]:
                raise ValueError("Unexpected generated v27 shape pairing")
            primary, elevated = (points for _, points in clouds)
            if (not np.array_equal(primary[:, (0, 2)], elevated[:, (0, 2)])
                    or not np.array_equal(np.maximum(primary[:, 1], np.float32(0.2)), elevated[:, 1])):
                raise ValueError("Generated v27 ground shells disagree")
            used.add("physicsdefinition.bin")
        if trial is not None:
            # Native handlers are separate from the accessory GSF motion graph.
            carbin_wheels = sorted(path for path in bindings if path.startswith(virtual(aliases,"scene/wheel/")))
            trial.update(material_members=sorted(material_members), stock_wheel_model=wheel_model,
                         carbin_wheel_models=carbin_wheels,
                         steering_binding=any(label=="boneSteeringWheelSpindle" for _,label in bindings.values()),
                         locator_xml="locators.xml" in used, gsf=f"animations/{aliases}.gsf" in used,
                         gr2=gr2 in used)
        if used != set(names) - {f"{aliases}.carbin"}:
            raise ValueError("Unreferenced model resources")
    return directory


def build(root, ir, output, registry, db, manifest, library, *, catalogue=True, strings=None, source_tools=None):
    identity = fh4_db.reserve(registry, db, ir["vehicle"])
    payloads, render, analysis = make_models(root, ir, identity, library)
    package, directory = fh4_archive.pack(payloads)
    name, car_id = identity["media_name"], identity["car_id"]
    if directory != validate_zip(package, name, car_id):
        raise ValueError("FH4 ZIP directory check disagrees with the archive writer")
    (output / f"{name}.zip").write_bytes(package)
    mount = fh4_archive.add_mount(manifest.read_bytes(), name, directory)
    (output / "zipmanifest.xml").write_bytes(mount)
    rows = ir["sections"]["physics"]["data"]["tables"]
    write_json(output / "catalogue.json", fh4_db.catalogue_report(db, rows, identity))
    write_json(output / "physics-input.json", analysis)
    receipt = {
        "target": TARGET_BUILD, "state": "integration-preview", "installable": False,
        "identity": identity, "archive": f"{name}.zip", "archive_directory": vars(directory),
        "archive_sha256": sha256(output / f"{name}.zip"),
        "baseline_database_sha256": sha256(db), "baseline_manifest_sha256": sha256(manifest),
        "limits": KNOWN_LIMITS, "render": render,
    }
    if catalogue:
        from .fh4_db_compiler import Vehicle, compile_database
        from .fh4_presentation import presentation_mount
        from .fh4_validation import integrated_report

        receipt["database"] = compile_database(db, [Vehicle(root, ir, identity)], output, strings=strings,
                                                source_tools=source_tools)
        mount = presentation_mount(mount, receipt["database"]["presentation"])
        (output / "zipmanifest.xml").write_bytes(mount)
        report = integrated_report(db, manifest, library, output, mount, [(output, receipt)])
        write_json(output / "validation.json", report)
        receipt["installable"] = report["installable"]
        receipt["state"] = report["state"]
    write_json(output / "build.json", receipt)
    return identity
