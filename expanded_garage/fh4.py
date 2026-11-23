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
from . import fh4_ao_volume, fh4_driver_rig, fh4_paint, fh4_wheel_model
from .fh4_granny_animation import verify_gauge_graph
from .fh4_material_contract import surface_binding
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


def ao_list():
    # EXPANDED_GARAGE_AO_STUB=off writes the empty list again (bisection switch).
    if os.environ.get("EXPANDED_GARAGE_AO_STUB") == "off":
        return bytes(4)
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
    (r"^steering", "Dash"),
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
                + struct.pack("<f", breakable_glass(materials)) + ao_list() + bytes(1) + MODEL_FLAGS[part.kind]
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


def uv(data, transform):
    return data * np.asarray(transform[1::2], dtype="<f4") + np.asarray(transform[::2], dtype="<f4")


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
                yield section["name"], fh4_model.Draw(
                    name=f'{section["name"]}_{draw["triangles"]}', lod=draw["lod"],
                    source_material=draw["material"], target_material=binding.member, transparent=binding.transparent,
                    positions=positions, normals=normals,
                    uv0=uv(vertices[p["uv0"]][ids], draw["uv_transform"][:4]),
                    uv1=uv(vertices[p["uv1"]][ids], draw["uv_transform"][4:]),
                    indices=compact.reshape(-1, 3), origin=draw["provenance"],
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


def gauge_texture_parameters(root, ir, alias, library=None):
    """({(gauge_emissive, int_gauges): (version, MTPR)}, {member: swatchbin}) for the car's own dial art.

    FM4 dial faces (`gauge_emissive`) sample the cockpit atlas interior_LOD0.xds with atlas UVs (the IR draws
    already carry the FM4 UV transform). Its alpha is FM4's label cut-out mask, 0 over the dials, so the
    diffuse is written opaque (int_gauges alpha-tests). The night illumination keeps the bright marks only.
    EXPANDED_GARAGE_GAUGE_TEXTURES=off falls back to the untextured dial (bisection switch)."""
    if os.environ.get("EXPANDED_GARAGE_GAUGE_TEXTURES") == "off":
        return {}, {}
    atlas = raw_member(root, ir, "/interior_lod0.xds")
    from .fh4_presentation import swatch, xds_codec
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
        parameters.update(cockpit_atlas_parameters(library, textures[donor["diffuse"]]))
    return parameters, members


ATLAS_DIFFUSE = 0x810e7224   # int_ch1diffnormglossalpha CH1 diffuse texture (same slot as int_gauges)


def cockpit_atlas_parameters(library, atlas_path):
    """{(FM4 class, plastic_perforated_004): (version, MTPR)}: the library block with its diffuse texture
    switched on and pointed at the car's cockpit atlas, normal/AO/opacity layers off, white tint and the
    class gloss. EXPANDED_GARAGE_COCKPIT_ATLAS=off keeps the untextured library materials."""
    if os.environ.get("EXPANDED_GARAGE_COCKPIT_ATLAS") == "off":
        return {}
    from . import fh4_mtpr
    from .fh4_material_contract import COCKPIT_ATLAS_GLOSS, COCKPIT_ATLAS_MATERIAL
    member = COCKPIT_ATLAS_MATERIAL + ".materialbin"
    data = ZipFile(library).read(member)
    _, geometry = previous_readers()
    block = next((data[b["offset"]:b["offset"] + b["size"]], b["version"])
                 for b in geometry.blobs(data) if b["tag"] == "MTPR")
    body, version = block
    out = {}
    for label, gloss in COCKPIT_ATLAS_GLOSS.items():
        patched = fh4_mtpr.patch(body, {
            "CH1DiffColTextureSwitchBool": 1, "CH1NormalMapSwitchBool": 0, "CH1LocalAOSwitchBool": 0,
            "CH1OpacityMaskSwitchBool": 0, "DiffTintColorParam": (1.0, 1.0, 1.0, 1.0),
            "DiffuseColorGroupColorParam": (1.0, 1.0, 1.0, 1.0), "GlossA_floatVal": gloss,
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
                return "both_views"
            return None

        def emit(kind, key, draws, bone=0, translation=(0.0, 0.0, 0.0), attachment=None, component=None,
                 path=None, rig=None, mesh_bone=False):
            rotation = (0.,0.,0.,1.)
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
                payloads[path], wheel_mask[0] = fh4_model.assemble(draws, rig, 0, shadows=True, wheel=morph)
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
            payloads[path], mask = fh4_model.assemble(draws, component_rig, rigid_bone, shadows=kind != "cockpit",
                                                      fill_near=near == "fill", parameters=car_parameters)
            models.append(Model(path, kind, mask, bone, translation, component or key.casefold(), rotation,
                                draw=39 if near == "both_views" else 0))

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
            normalised, wheel_bones, wheel_model = fh4_wheel_model.normalise(wheel)
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
                emit("brake", key, draws, bone, corners[corner]["origin_m"])
    nonstock = {name for part in contract.parts if not part.stock for name in part.components}
    surface_geometry = [d.positions for component, draws in grouped["exterior"].items()
                        if component.casefold() not in nonstock
                        for d in draws if d.lod == min(x.lod for x in draws)]
    bounds = (np.min([p.min(axis=0) for p in surface_geometry], axis=0),
              np.max([p.max(axis=0) for p in surface_geometry], axis=0))
    payloads[f"{aliases}.carbin"] = carbin(identity, bones, skel, models, bounds, contract.groups(models),
                                          {m.member: modelbin_box(payloads[m.member]) for m in models},
                                          {m.member: modelbin_materials(payloads[m.member]) for m in models})
    payloads.update(texture_payloads)
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
        payloads[AO_VOLUME] = fh4_ao_volume.build(np.concatenate(occluders))
        payloads[fh4_ao_volume.GROUND_MEMBER] = fh4_ao_volume.ground_texture(
            np.concatenate(occluders), json.loads(files(__package__).joinpath("fh4-ground-ao-v1.json").read_text(encoding="utf-8")))
    if points:
        authored = {"carLocator_" + point.name for point in points.values()}
        extra, driver_rig["locators"] = fh4_driver_rig.transferred_locators(
            points, authored, driver_side, spindle=fh4_driver_rig.steering_spindle(bones))
        payloads["Locators.xml"] = systems.locator_xml(aliases, points, extra)
        locator_names = authored | {item["name"] for item in extra}
        avpins, driver_rig["autovista"] = fh4_driver_rig.autovista(points, locator_names, driver_side)
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
                if label.startswith(("hub","spindle","boneSteeringWheel","boneFuel","boneTach","boneSpeed")) and not (
                        np.allclose(placements[path],world[bone],rtol=0,atol=2e-6)):
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
        for name in names:
            if name.startswith(GAUGE_FOLDER) and name.endswith(".swatchbin"):
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
