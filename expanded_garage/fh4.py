"""Generate independent FH4 render archives and explicit qualification reports."""

from collections import defaultdict
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
import re
import struct
import sys
import uuid
from zipfile import ZipFile

import numpy as np

from . import fh4_archive, fh4_db, fh4_model, fh4_physics
from .fh4_scene_contract import NativeCarSceneContract
from .fh4_runtime_rig import NativeRuntimeRigCompiler, verify_skeleton
from . import fh4_driver_rig
from .fh4_granny_animation import verify_gauge_graph
from .fh4_material_contract import surface_binding
from .fh4_motion import GAUGES
from .fh4_wheel_transform import WheelTransformCompiler
from . import fh4_vehicle_systems as systems
from .fm4 import rows_for
from .vehicle_ir import member_path, sha256, write_json


ROOT = Path(__file__).resolve().parents[1]
TARGET_BUILD = "FH4 Steam 1.478.564.0"
PART_TYPES = {"exterior": 2, "cockpit": 2, "brake": 4, "wheel": 43}
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


def guid(name):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"urn:fh4-expanded-garage:v1:{name}").bytes_le


def virtual(alias, name):
    return f"game:\\media\\cars\\{alias.casefold()}\\" + name.replace("/", "\\").casefold()


def carbin(identity, bones, skel, models, bounds, upgrade_groups=None):
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
        draw = 2 if part.kind == "cockpit" else 5
        bone = bones[part.bone].name
        return (struct.pack("<H", 16) + fh4_model.string(path)
                + struct.pack("<16fH", *fh4_model.transform(part.translation,part.rotation).flat, part.lod)
                + fh4_model.string(bone) + struct.pack("<hBI", part.bone, 0, draw)
                + bytes(4 + 4 + 1 + 4 + 4 + 1 + 18)
                + fh4_model.string("") + guid(path) + bytes(20 + 4 + 4))

    for part_type, parts in sorted(groups.items()):
        result.extend(struct.pack("<BHII", part_type, 2, part_type, len(parts)))
        for part in parts:
            result.extend(encode_model(part))
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


def driver_side_from(grouped):
    steering = [d for k, ds in grouped["cockpit"].items() if k.casefold() == "steering_wheel" for d in ds]
    if not steering:
        raise ValueError("Driver side needs the source steering wheel geometry")
    x = float(np.median(np.concatenate([d.positions[:, 0] for d in steering])))
    if abs(x) <= 0.05:
        raise ValueError("Centred steering wheel: driver side is ambiguous")
    return "L" if x < 0 else "R"


def make_models(root, ir, identity, library):
    rows = ir["sections"]["physics"]["data"]["tables"]
    points = systems.hardpoints(root, ir)
    authored = {corner: points[p].meters for p, corner in systems.CORNER_IDS.items() if p in points}
    bones, corners = WheelTransformCompiler().compile(rows,authored)
    native_rig = NativeRuntimeRigCompiler()
    contract = NativeCarSceneContract(ir, identity["body_id"])
    geometry = ir["sections"]["geometry"]["data"]
    grouped = {category: defaultdict(list) for category in ("exterior", "cockpit", "wheel", "brake")}
    filtered = set()
    for source in geometry["models"]:
        role = source["role"]
        kind = "exterior" if role.startswith("exterior") else "brake" if role in ("caliper", "rotor") else role
        for section, draw in load_draws(root, source):
            if ((contract.use_component(role, section) or role=="cockpit" and section in GAUGE_POSES)
                    and not draw.source_material.casefold().startswith("blur")
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

        def emit(kind, key, draws, bone=0, translation=(0.0, 0.0, 0.0), attachment=None, component=None):
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
            local = re.sub(r"[^a-z0-9_]+", "_", key.lower()).strip("_")
            path = f"scene/{kind}/{local}.modelbin"
            if path in payloads:
                raise ValueError(f"Duplicate output model name {path}")
            # The scene attachment is in the chassis rig. Rigid wheel and brake files have their own local frame.
            local_rig = kind in ("wheel", "brake") or attachment is not None
            component_rig = bones[:2] if local_rig else bones
            rigid_bone = 0 if local_rig else 1
            payloads[path], mask = fh4_model.assemble(draws, component_rig, rigid_bone, shadows=kind != "cockpit")
            models.append(Model(path, kind, mask, bone, translation, component or key.casefold(),rotation))

        for kind in ("exterior", "cockpit"):
            for key, draws in sorted(grouped[kind].items()):
                if key in GAUGE_POSES:
                    if not key.endswith("_min"):
                        continue
                    needles = [d for d in draws if d.source_material.casefold()=="gauge_needle"]
                    housing = [d for d in draws if d.source_material.casefold()!="gauge_needle"]
                    if housing:
                        emit(kind,key+"_housing",housing)
                    if needles:
                        emit(kind,key,needles,attachment=attachments.get(key))
                else:
                    emit(kind, key, draws,attachment=attachments.get(key))
        wheel = [d for draws in grouped["wheel"].values() for d in draws]
        if wheel:
            for corner, binding in corners.items():
                local = [reflected(d) for d in wheel] if corner.startswith("R") else wheel
                emit("wheel", f"wheel{corner}", local, binding["spindle"], binding["origin_m"])
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
    payloads[f"{aliases}.carbin"] = carbin(identity, bones, skel, models, bounds, contract.groups(models))
    if points:
        authored = {"carLocator_" + point.name for point in points.values()}
        extra, driver_rig["locators"] = fh4_driver_rig.transferred_locators(points, authored, driver_side)
        payloads["Locators.xml"] = systems.locator_xml(aliases, points, extra)
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
    return payloads, {
        "modelbins": len(models) + 1, "models_by_role": {k: sum(m.kind == k for m in models) for k in PART_TYPES},
        "bone_names": [bone.name for bone in bones], "scene_contract": contract.receipt(),
        "driver_rig": driver_rig,
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
        for path in references:
            if not path.startswith(prefix):
                raise ValueError(f"Nonlocal Carbin binding {path}")
            key = path[len(prefix):].replace("\\", "/")
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
        if "ikanchorbones.xml" in names:
            fh4_driver_rig.validate_ik_anchor_xml(archive.read(names["ikanchorbones.xml"]), scene_bones)
            used.add("ikanchorbones.xml")
        gr2 = scene["skeleton"][len(prefix):].replace("\\", "/").removesuffix(".modelbin") + ".gr2"
        if gr2 in names:
            rig = verify_skeleton(archive.read(names[gr2]), aliases)
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
                if label.startswith(("hub","spindle","boneSteeringWheel","boneFuel","boneTach","boneSpeed")) and not (
                        np.allclose(placements[path],world[bone],rtol=0,atol=2e-6)):
                    raise ValueError(f"Component placement disagrees with its Granny bind point: {path}")
            used.add(gr2)
            gsf = f"animations/{aliases}.gsf"
            if gsf in names:
                clips = verify_gauge_graph(archive.read(names[gsf]),lambda p: archive.read(names[p]),
                                           {b["name"]:b for b in rig},{label for _,label in bindings.values()})
                used.update(clips | {gsf})
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
            wheel_bindings = {path.rsplit("\\",1)[-1].removesuffix(".modelbin").removeprefix("wheel").upper(): label
                              for path,(_,label) in bindings.items()
                              if path.startswith(virtual(aliases,"scene/wheel/"))}
            trial.update(material_members=sorted(material_members), wheel_bindings=wheel_bindings,
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
