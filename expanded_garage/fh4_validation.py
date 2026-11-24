"""Validate the composed SQLite, CarScene, mount and asset graph."""

from collections import Counter
from contextlib import closing
from io import BytesIO
from zipfile import ZipFile
import json
from hashlib import sha256 as hash_bytes
import math
import re
import xml.etree.ElementTree as ET

from . import fh4, fh4_archive
from .database_graph import audit_insertions, ident
from .fh4_db import snapshot
from .fh4_db_compiler import GRAPHICS
from .fh4_data_car import NativeDataCarContract, wheel_load_contract
from .fh4_dynamics import NativeDynamicsCompiler, STEERING, aero_offsets, finite_between, ratios
from .fh4_presentation import EN_VIRTUAL, StringTable, thumbnail_virtual, update_mount
from .fh4_thumbnail import audit_archive as audit_thumbnail_archive, thumbnail_archives, thumbnail_catalogue_path
from .fh4_tires import MODEL_FIELDS, NativeTireCompiler, TireAssets, WIDTH_TABLES, dimensions
from .fh4_runtime_rig import verify_skeleton
from .fh4_vehicle_systems import CORNER_IDS, audit_trial_collision
from .fh4_trial_dynamics import AXLES, SCALARS, DRIVELINE
from .vehicle_ir import sha256


TYPE_TO_TABLE = {kind: table for table, kind in GRAPHICS.items()}
GOOD_STATES = {"confirmed", "structurally_verified"}


DRIVE_GATES = {
    "archive_mount_geometry": "The archive and native mount must pass full verification.",
    "catalogue_and_scene_ids": "The native GameDB, CarScene and owned upgrade references must agree.",
    "source_owned_wheel_rest": "The wheel bind frames and runtime composition must be verified.",
    "stock_v27_collision": "The v27 collision and runtime road contact contract must be established.",
    "native_animation_rig": "The independent GR2/GSF rig and bound runtime motion graph are required.",
    "tire_physics": "All stock tire curves, tire widths and dry/wet/snow model assets must resolve.",
    "native_dynamics": "Target-driving fields require a qualified dynamics conversion.",
    "autoshow_catalogue": "The target Autoshow bucket and presentation dependencies must resolve.",
    "materials_required_for_drive": "Drive-required materials need a native configuration for every visible mesh.",
}


def drive_blockers(features):
    return [detail for key, detail in DRIVE_GATES.items() if features.get(key) not in GOOD_STATES]


def drive_conclusions(features):
    scopes = {
        "structural_validity": ("archive_mount_geometry","catalogue_and_scene_ids","wheel_bind_frames",
                                "gr2_skeleton","gr2_gsf_graph_structure","tire_physics",
                                "collision_structure","material_resource_structure"),
        "drive_readiness": tuple(DRIVE_GATES),
        "full_vehicle_fidelity": (*DRIVE_GATES,"native_material_channels","lighting_effects","damage_effects",
                                  "audio_event_and_bank_generation","accessory_motion"),
    }
    result = {}
    for name, keys in scopes.items():
        entries = [{"check":key,"state":features.get(key,"missing_dependency"),
                    "evidence":f"#/features/{key}"} for key in keys]
        result[name] = {"passed":all(item["state"] in GOOD_STATES for item in entries),"checks":entries}
    return result


def trial_qualification(report):
    """Keep static installation eligibility independent of pending gameplay observations."""
    scopes = ("archive_mount_geometry","catalogue_and_scene_ids","autoshow_catalogue","tire_physics",
              "native_dynamics","materials_required_for_drive","wheel_bind_frames","gr2_skeleton",
              "gr2_gsf_graph_structure","trial_material_dependencies","trial_controls","trial_collision")
    if any("native_load_contract" in car for car in report["cars"]):
        scopes += ("native_wheel_and_data_car_load","native_thumbnail_archives")
    checks, blockers = [], []
    for key in scopes:
        reasons = [f'{car["car_id"]}: {reason}' for car in report["cars"]
                   for reason in car["trial_prerequisites"]["missing"].get(key,())]
        passed = bool(report["cars"]) and report["features"].get(key) in GOOD_STATES and not reasons
        checks.append({"check":key,"passed":passed,"evidence":f"#/features/{key}"})
        if not passed:
            blockers.extend(reasons or [f"{key}: Required static dependency failed; see its check evidence."])
    return {"passed":not blockers,"checks":checks,"blockers":blockers,
            "runtime_acceptance":{"passed":False,"state":"awaiting_owner_gameplay_and_rollback"}}


def suspension_path(virtual):
    path = virtual.casefold().removeprefix("game:\\").replace("\\","/")
    if not re.fullmatch(r"media/physics/suspension/[a-z0-9_+.-]+\.xml",path):
        raise ValueError(f"Unexpected shared suspension resource {virtual}")
    return path


def owned_stock(db, table, column, owner):
    rows = db.execute(f"SELECT * FROM {ident(table)} WHERE {ident(column)}=? AND IsStock=1", (owner,)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"Expected one stock row in {table} for {column} {owner}")
    return rows[0]


def audit_mechanical(db, car, body, native):
    car_id = car["Id"]
    source = native["source_checks"]
    drive_id = owned_stock(db, "List_UpgradeDrivetrain", "Ordinal", car_id)["DrivetrainID"]
    engine_id = owned_stock(db, "List_UpgradeEngine", "Ordinal", car_id)["EngineID"]
    transmission = owned_stock(db, "List_UpgradeDrivetrainTransmission", "DrivetrainID", drive_id)
    if ratios(dict(transmission), car["NumGears"]) != source["ratios_including_reverse"] or (
            transmission["FinalDriveRatio"] != source["final_drive"]):
        raise ValueError("Native stock transmission differs from the source ratio proof")
    clutch = owned_stock(db, "List_UpgradeDrivetrainClutch", "DrivetrainID", drive_id)
    differential = owned_stock(db, "List_UpgradeDrivetrainDifferential", "DrivetrainID", drive_id)
    if not (clutch["ClutchMaxTorque"] > 0 and clutch["ClutchInTime"] >= 0 and clutch["ClutchOutTime"] >= 0
            and finite_between(differential["RearToqueSplit"], 0, 1)):
        raise ValueError("Invalid native clutch or differential controls")
    mode, = db.execute("SELECT ShiftSystemID FROM Data_Drivetrain WHERE DrivetrainID=?", (drive_id,)).fetchone()
    shift = native["shift"]
    if mode != (shift["id"] if shift else None) or mode is not None and db.execute(
            "SELECT 1 FROM List_ShiftSystem WHERE ShiftSystemID=?", (mode,)).fetchone() is None:
        raise ValueError("Unresolved native shift system")
    weight = owned_stock(db, "List_UpgradeCarBodyWeight", "CarBodyId", body)
    if (weight["Mass"], weight["CMBackFront"]) != (source["mass_kg"], source["front_weight_fraction"]):
        raise ValueError("Stock mass or weight distribution changed")
    cam = owned_stock(db, "List_UpgradeEngineCamshaft", "EngineID", engine_id)
    curve, = db.execute("SELECT * FROM List_TorqueCurve WHERE TorqueCurveID=?",
                        (cam["TorqueCurveFullThrottleID"],)).fetchall()
    count = curve["NumTorqueValues"]
    values = [curve[f"v{i}"] * curve["TorqueScale"] for i in range(count)]
    if count < 2 or not math.isclose(max(values), source["source_curve_peak_torque_nm"], abs_tol=1e-6):
        raise ValueError("Native engine torque curve disagrees with its source")
    spring = owned_stock(db, "List_UpgradeSpringDamper", "Ordinal", car_id)
    if {k: car[k] for k in STEERING} != source["steering_target_fields"] or any(
            spring[k] != car[k] for k in STEERING if k in spring.keys()):
        raise ValueError("Native steering controls disagree with the converted upgrade")
    owned_springs, owned_sways = {}, {}
    for axle in AXLES:
        physics, = db.execute("SELECT * FROM List_SpringDamperPhysics WHERE SpringDamperPhysicsID=? AND Ordinal=?",
                              (spring[f"{axle}SpringDamperPhysicsID"], car_id)).fetchall()
        rule = NativeDynamicsCompiler.spring(dict(physics))
        if any(physics[k] != v for k, v in rule.items()):
            raise ValueError(f"{axle} high-velocity damping conversion failed readback")
        sway = owned_stock(db, f"List_UpgradeAntiSway{axle}", "Ordinal", car_id)
        owned_springs[axle] = physics
        owned_sways[axle] = sway["AntiSwayPhysicsID"]
        if db.execute("SELECT 1 FROM List_AntiSwayPhysics WHERE AntiSwayPhysicsID=?",
                      (sway["AntiSwayPhysicsID"],)).fetchone() is None:
            raise ValueError(f"{axle} anti-roll dependency is missing")
    brakes = owned_stock(db, "List_UpgradeBrakes", "Ordinal", car_id)
    if any(not math.isclose(brakes[f"{axle}BrakeSizeMM"], brakes[f"{axle}BrakeSize"] * 25.4)
           for axle in ("Front", "Rear")):
        raise ValueError("Native brake units disagree with the source size")
    for axle, evidence in (native.get("brake_conversion") or {}).items():
        ref, = db.execute("SELECT Density FROM List_BrakeType WHERE BrakeTypeID=?",
                          (brakes[axle+"BrakeTypeID"],)).fetchone()
        if not ref == brakes[axle+"BrakeRotorMassDensity"] == evidence["density_kg_m3"]:
            raise ValueError("Native brake rotor density does not match the semantic brake type")
        if spans := evidence.get("outer_annulus_measures"):
            expected = sum(r["thickness_mm"] for r in spans.values())/len(spans)
            if not math.isclose(expected,brakes[axle+"BrakeRotorThicknessMM"],abs_tol=1e-7):
                raise ValueError("Native rotor thickness does not match the measured FM4 annulus")
    result = dict(native, catalogue_readback="structurally_verified")
    aero_keys = db.execute(
        """SELECT AeroPhysicsID FROM List_UpgradeCarBodyFrontBumper WHERE CarBodyID=?
           UNION SELECT AeroPhysicsID FROM List_UpgradeRearWing WHERE Ordinal=?
           ORDER BY AeroPhysicsID""", (body, car_id)).fetchall()
    if not aero_keys:
        raise ValueError(f"Car {car_id} has no native front or rear aero reference")
    result["aero_readback"] = {}
    for (aero_id,) in aero_keys:
        record = db.execute("SELECT * FROM List_AeroPhysics WHERE AeroPhysicsID=?", (aero_id,)).fetchone()
        if record is None:
            raise ValueError(f"Car {car_id} has an unresolved AeroPhysicsID {aero_id}")
        result["aero_readback"][str(aero_id)] = aero_offsets(dict(record))
    if "trial_settings" in native:
        covered, missing, resources = {}, [], {}
        for item in native["trial_settings"]:
            table, key, row_id = item["table"], item["pk"], item["id"]
            fields = item["fields"]
            record, = db.execute(f"SELECT * FROM {ident(table)} WHERE {ident(key)}=?", (row_id,)).fetchall()
            if not item["evidence"].get("rule") and table != "List_UpgradeBrakes":
                raise ValueError(f"Missing conversion provenance for {table}.{row_id}")
            if any(not finite_between(value,-1e8,1e8) or record[field] != value for field,value in fields.items()):
                raise ValueError(f"Compiled trial fields failed catalogue readback in {table}.{row_id}")
            covered[table,row_id] = fields.keys()
        def require(table, row_id, fields, axle=""):
            for field in sorted(set(fields)-covered.get((table,row_id),set())):
                missing.append(f"{table}.{field} {axle} [{row_id}] lacks an evidenced native mapping".strip())
        require("Data_Car",car_id,(*SCALARS,"FrontDownforceClampKG","RearDownforceClampKG"))
        require("List_UpgradeDrivetrainDriveline",
                owned_stock(db,"List_UpgradeDrivetrainDriveline","DrivetrainID",drive_id)["Id"],DRIVELINE)
        for axle, physics in owned_springs.items():
            key = physics["SpringDamperPhysicsID"]
            require("List_SpringDamperPhysics",key,("SuspensionPhysicsTypeID","UseProgressiveSpring","BumpStopHeight"),axle)
            require("List_AntiSwayPhysics",owned_sways[axle],("BeamStiffness",),axle)
            require("List_UpgradeBrakes",brakes["Id"],(axle+"BrakeTorqueClamp",axle+"BrakeNonSpinningMass"),axle)
            path, = db.execute("SELECT Path FROM List_SuspensionPhysicsType WHERE SuspensionPhysicsTypeID=?",
                               (physics["SuspensionPhysicsTypeID"],)).fetchone()
            if path:
                resources[axle] = suspension_path(path)
        result["trial_missing"] = missing
        result["trial_suspension"] = resources
    return result


def audit_wheel_frames(db, car, rig):
    motors = {b["name"]: b for b in rig}
    anchor = {}
    for p, corner in CORNER_IDS.items():
        rows = db.execute("SELECT PartPosX,PartPosY,PartPosZ FROM CarPartPositions WHERE Ordinal=? AND PartId=?",
                          (car["car_id"], p)).fetchall()
        if not rows:
            return {"state": "missing_dependency", "missing_authored_corner": corner}
        hub, spindle = (motors[n + corner] for n in ("hub", "spindle"))
        position, = rows
        if any(not math.isclose(a, b, abs_tol=1e-6) for a, b in zip(position, hub["bind_m"], strict=True)):
            raise ValueError(f"GameDB and hub rest positions disagree for {corner}")
        if spindle["local_m"] != [0, 0, 0] or hub["bind_m"] != spindle["bind_m"]:
            raise ValueError(f"Incorrect relative wheel spindle rest transform for {corner}")
        anchor[corner] = list(position)
    for corner, pos in anchor.items():
        if pos[0] * (-1 if corner.startswith("L") else 1) <= 0:
            raise ValueError(f"Wheel bind is on the wrong side: {corner}")
    base = (anchor["LF"][2] + anchor["RF"][2] - anchor["LR"][2] - anchor["RR"][2]) * 0.5
    if not math.isclose(base, car["dynamics"]["source_checks"]["wheelbase_m"], rel_tol=0.05, abs_tol=0.03):
        raise ValueError("Wheel hardpoints disagree with the source wheelbase")
    return {"state": "structurally_verified", "anchors_chassis_m": anchor, "wheelbase_m": base,
            "runtime_composition": "unverified_runtime"}


def audit_vehicle_systems(candidate, car, native, tire_assets):
    car_id = car["Id"]
    if native["car_id"] != car_id:
        raise ValueError("Mismatched native systems receipt")
    dims = dimensions(car)
    for axle in dims:
        if NativeTireCompiler.stock_size(dims, axle) != native["tires"]["dimensions"][axle]:
            raise ValueError(f"Tire size receipt disagrees with the staged {axle} axle")
    tire_links = candidate.execute("SELECT * FROM List_UpgradeTireCompound WHERE Ordinal=? ORDER BY Id",
                                  (car_id,)).fetchall()
    if [r["Id"] for r in tire_links] != sorted(x["id"] for x in native["tires"]["rows"]):
        raise ValueError("GameDB tire rows disagree with the compiler receipt")
    rows, missing = [], []
    tire_compiler = NativeTireCompiler(candidate)
    for row in tire_links:
        saved, = (x for x in native["tires"]["rows"] if x["id"] == row["Id"])
        saved_fields = {"TireCompoundID": row["TireCompoundID"], **{f: row[f] for f in MODEL_FIELDS}}
        compound = candidate.execute("SELECT * FROM List_TireCompound WHERE TireCompoundID=?",
                                     (row["TireCompoundID"],)).fetchone()
        if saved_fields != saved["fields"] or compound is None or set(row[f] for f in MODEL_FIELDS) & {"", None}:
            raise ValueError("Missing or mismatched staged FH4 tire fields")
        if bool(row["IsStock"]) != saved["stock"] or compound["DisplayName"] != saved["target_family"]:
            raise ValueError("Staged tire compound family or stock status changed")
        tire_compiler.curve_keys(compound)
        report = {"id": row["Id"], "stock": bool(row["IsStock"]), "fields": saved_fields, "models": {}}
        if tire_assets:
            for field in MODEL_FIELDS:
                try:
                    report["models"][field] = tire_assets.resolve(row[field])
                except ValueError as error:
                    missing.append({"row": row["Id"], "stock": bool(row["IsStock"]), "field": field, "reason": str(error)})
        rows.append(report)
    body, = candidate.execute("SELECT CarBodyID FROM List_UpgradeCarBody WHERE Ordinal=? AND IsStock=1",
                              (car_id,)).fetchone()
    for table, axle in WIDTH_TABLES.items():
        native_width = candidate.execute(
            f"SELECT {axle}TireWidth FROM {table} WHERE CarBodyId=? AND IsStock=1", (body,)).fetchall()
        if len(native_width) != 1 or native_width[0][0] != dims[axle][0]:
            raise ValueError(f"Native {axle} stock width differs from physical configuration")
    if sum(r["stock"] for r in rows) != (0 if any(x["stock"] for x in native["tires"]["unsupported"]) else 1):
        raise ValueError("Unexpected native stock tire count")
    dynamics = audit_mechanical(candidate, car, body, native["dynamics"])
    bucket = candidate.execute(
        "SELECT b.CarBucket FROM Data_Car_Buckets b JOIN CarBuckets c ON c.Id=b.CarBucket "
        "WHERE b.CarId=? AND c.ParentBucketId>0",
        (car_id,)).fetchall()
    if [r[0] for r in bucket] != ([native["bucket"]["id"]] if native["bucket"] else []):
        raise ValueError("Unresolved native Autoshow bucket")
    return {"tires": {"rows": rows, "missing_assets": missing,
                      "unsupported": native["tires"]["unsupported"],
                      "dimensions": native["tires"]["dimensions"],
                      "structural_complete": bool(tire_assets and not missing and any(r["stock"] for r in rows))},
            "bucket": native["bucket"], "dynamics": dynamics}


def audit_catalogue(candidate, identity, scene, scene_contract, native=None, tire_assets=None, load_contract=None):
    car, = candidate.execute("SELECT * FROM Data_Car WHERE Id=?", (identity["car_id"],)).fetchall()
    if (car["MediaName"], car["ModelId"], scene["ordinal"], scene["media_name"]) != (
            identity["media_name"], identity["car_id"], identity["car_id"], identity["media_name"].casefold()):
        raise ValueError("Scene and GameDB identity disagree")
    for name in ("List_UpgradeCarBody", "List_UpgradeEngine", "List_UpgradeDrivetrain", "List_UpgradeSpringDamper",
                 "List_UpgradeBrakes"):
        count, = candidate.execute(f"SELECT count(*) FROM {ident(name)} WHERE Ordinal=? AND IsStock=1",
                                   (identity["car_id"],)).fetchone()
        if count != 1:
            raise ValueError(f"Expected one stock {name} for {identity['car_id']}")
    expected = {(row["part_type"], row["native_key"]): row for row in scene_contract["variant_bindings"]}
    seen = set()
    for group in scene["groups"]:
        kind = group["type"]
        table = TYPE_TO_TABLE[kind]
        for part in group["upgrades"]:
            row, = candidate.execute(f"SELECT * FROM {ident(table)} WHERE Id=?", (part["id"],)).fetchall()
            # Wing uses Ordinal in the catalogue and -1 in the serialized parent field.
            parent, target, native_parent = (("Ordinal", identity["car_id"], -1) if kind == 9 else
                                             ("CarBodyID", identity["body_id"], identity["body_id"]))
            if (row[parent], row["IsStock"], row["Level"], row["RequiresGraphics"], part["parent"]) != (
                    target, int(part["stock"]), part["level"], 1, native_parent):
                raise ValueError(f"GameDB upgrade parent or flags differ: {table}/{part['id']}")
            seen.add((kind, part["id"]))
    if seen != expected.keys():
        raise ValueError("Scene variants differ from the compiler's expected catalogue keys")
    for field, table, pk in (("MakeID", "List_CarMake", "ID"), ("StockWheelID", "List_Wheels", "ID")):
        link, = candidate.execute(f"SELECT * FROM {ident(table)} WHERE {ident(pk)}=?", (car[field],)).fetchall()
        if field == "StockWheelID" and link["MediaName"] != identity["media_name"]:
            raise ValueError("Stock wheel references another vehicle")
        if field == "StockWheelID" and native and "native_load_contract" in native:
            reported = native["native_load_contract"]
            if load_contract is None or any(reported[k]!=v for k,v in load_contract.audit(car).items()):
                raise ValueError("Native Data_Car completeness proof does not match the baseline")
            if (any(car[k]!=v for k,v in reported["converted_fields"].items())
                    or wheel_load_contract(link["Mass"],link["MassLevel"])!=reported["stock_wheel"]):
                raise ValueError("Native Data_Car or stock wheel load contract differs from its compiled values")
    report = {"car_id": car["Id"], "make_id": car["MakeID"], "variant_count": len(seen)}
    if native is not None:
        report.update(audit_vehicle_systems(candidate, car, native, tire_assets))
        if "native_load_contract" in native:
            if car["Thumbnail"]!=thumbnail_catalogue_path(car["Id"]):
                raise ValueError("Native thumbnail catalogue key does not match its archive namespace")
            report["native_load_contract"] = native["native_load_contract"]
    return report


def audit_trial_assets(archive, frames, zip_evidence):
    missing = []
    carbin_wheels = len(zip_evidence["carbin_wheel_models"])
    if zip_evidence["stock_wheel_model"] == bool(carbin_wheels) or carbin_wheels not in (0, 4):
        missing.append("Exactly one wheel form is required: the normalised stock wheel model "
                       "scene/_library/scene/wheels/<media>_wheellf.modelbin, or four Carbin wheels "
                       "(EXPANDED_GARAGE_WHEELS=carbin bisection build)")
    for key, label in (("steering_binding","Carbin steering wheel on boneSteeringWheelSpindle"),
                       ("locator_xml","owned Locators.xml"),
                       ("gr2","own GR2 skeleton"),("gsf","named GSF with resolved accessory clips")):
        if not zip_evidence[key]:
            missing.append(f"Missing {label}")
    collision = None
    if "physicsdefinition.bin" in archive.namelist() and frames.get("unloaded_contact_m"):
        collision = audit_trial_collision(archive.read("physicsdefinition.bin"), frames["unloaded_contact_m"])
    return {"asset_checks":zip_evidence,"control_missing":missing,"collision":collision}


def integrated_report(baseline, baseline_manifest, library, output, mount, builds):
    graph = json.loads((output / "database-graph.json").read_text(encoding="utf-8"))
    presentation = json.loads((output / "presentation.json").read_text(encoding="utf-8"))
    systems_path = output / "native-catalogue-systems.json"
    native = ({r["car_id"]: r for r in json.loads(systems_path.read_text(encoding="utf-8"))}
              if systems_path.is_file() else None)
    if native is not None and set(native) != {receipt["identity"]["car_id"] for _, receipt in builds}:
        raise ValueError("Native catalogue evidence has a different vehicle inventory")
    tire_assets = TireAssets(baseline_manifest.read_bytes(), library) if native is not None else None
    expected = Counter(row["key"]["table"] for row in graph["rows"])
    old_mounts = [e.attrib for e in ET.fromstring(update_mount(baseline_manifest.read_bytes(), presentation["english"]))]
    all_mounts = [e.attrib for e in ET.fromstring(mount)]
    thumb_count = sum(item.get("kind")=="thumbnail_archive" for item in presentation["art"])
    if (old_mounts != all_mounts[:len(old_mounts)] or len(all_mounts) != len(old_mounts) + len(builds)+thumb_count
            or len({e["path"].casefold() for e in all_mounts}) != len(all_mounts)):
        raise ValueError("Mount accumulator changed baseline entries or added unexpected paths")
    find_mount = {e["path"].casefold(): e for e in all_mounts}
    results = []
    bridge = all("material_contract" in receipt["render"] for _,receipt in builds)
    trial = native is not None and all("trial_settings" in row["dynamics"] for row in native.values())
    art = {entry["path"].casefold(): entry for entry in presentation["art"]}
    for item in art.values():
        if sha256(output / item["path"]) != item["sha256"]:
            raise ValueError("Vehicle artwork changed after compilation")
        if item.get("kind")=="thumbnail_archive":
            audit_thumbnail_mount(item,output,mounts=find_mount)
    translations = {}
    english = presentation["english"]
    if english:
        native_english = output / english["path"]
        if (sha256(native_english) != english["archive_sha256"]
                or vars(fh4_archive.verify(native_english.read_bytes())) != english["directory"]):
            raise ValueError("English archive integrity mismatch")
        with ZipFile(native_english) as archive:
            translations = {table: StringTable(archive.read(table+".str"), table).rows
                            for table in ("Data_Car", "List_CarMake")}
        if EN_VIRTUAL not in find_mount:
            raise ValueError("English archive mount missing")
    with closing(snapshot(baseline)) as before, closing(snapshot(output / "gamedb.sqlite")) as candidate:
        before_proof = audit_insertions(before, candidate, expected)
        load_contract = NativeDataCarContract(before) if native and all(
            "native_load_contract" in n for n in native.values()) else None
        if sha256(output / "gamedb.sqlite") != graph["insert_only_proof"]["output_sha256"]:
            raise ValueError("Catalogue bytes changed after compilation")
        reader, _ = fh4.previous_readers()
        for folder, receipt in builds:
            identity = receipt["identity"]
            path = folder / receipt["archive"]
            blob = path.read_bytes()
            if sha256(path) != receipt["archive_sha256"]:
                raise ValueError(f"Archive changed after compilation: {path}")
            alias = identity["media_name"]
            zip_evidence = {} if trial else None
            layout = fh4.validate_zip(blob, alias, identity["car_id"], trial=zip_evidence)
            row = find_mount[fh4_archive.mount_path(alias)]
            if (row["dirstart"], row["dirsize"], row["direntries"]) != tuple(
                    map(str, (layout.start, layout.size, layout.entries))):
                raise ValueError("Native mount size or offset mismatch")
            with ZipFile(BytesIO(blob)) as archive:
                scene = reader(archive.read(alias.casefold() + ".carbin")).scene()
                report = audit_catalogue(candidate, identity, scene, receipt["render"]["scene_contract"],
                                         native[identity["car_id"]] if native is not None else None, tire_assets,
                                         load_contract)
                members = {n.casefold() for n in archive.namelist()}
                report["missing_native_roles"] = sorted(label for label, key in {
                    "collision": "physicsdefinition.bin",
                    "gr2": scene["skeleton"].removeprefix(fh4.virtual(alias, "")).replace("\\", "/").replace(".modelbin", ".gr2"),
                    "animation": f"animations/{alias.casefold()}.gsf",
                    "lighting": "lights.bin", "light_presets": "lightpresets.bin",
                }.items() if key not in members)
                if native is not None:
                    native_gr2 = receipt["render"]["runtime_rig"]["gr2"]["member"]
                    if receipt["render"]["runtime_rig"].get("animation_probe"):
                        # Diagnostic donor skeleton in the archive; audit wheels on the authored rig.
                        report["animation_probe"] = receipt["render"]["runtime_rig"]["animation_probe"]
                        skeleton = receipt["render"]["runtime_rig"]["gr2"]["bones"]
                    else:
                        skeleton = verify_skeleton(archive.read(native_gr2), alias.casefold())
                    if skeleton != receipt["render"]["runtime_rig"]["gr2"]["bones"]:
                        raise ValueError("Granny readback differs from the rig receipt")
                    report["wheel_frames"] = audit_wheel_frames(candidate, report, skeleton)
                    if bridge and report["wheel_frames"]["state"]=="structurally_verified":
                        wheel = receipt["render"]["inferred_wheel_hubs"]
                        plane = {}
                        for label,point in report["wheel_frames"]["anchors_chassis_m"].items():
                            axle = "Front" if label.endswith("F") else "Rear"
                            tire = report["tires"]["dimensions"][axle]
                            radius = tire["radius_m"]
                            contact = (point[0],point[1]-radius,point[2])
                            if not math.isclose(radius,wheel[label]["radius_m"],abs_tol=1e-9) or (
                                    any(not math.isclose(x,y,abs_tol=1e-8) for x,y in
                                        zip(contact,wheel[label]["unloaded_contact_m"],strict=True))):
                                raise ValueError("Unloaded wheel contact does not match its own stock tire dimensions")
                            plane[label] = list(contact)
                        report["wheel_frames"]["unloaded_contact_m"] = plane
                    if trial:
                        report["trial_prerequisites"] = audit_trial_assets(
                            archive,report["wheel_frames"],zip_evidence)
            if sha256(library) != receipt["render"]["material_library"]["sha256"]:
                raise ValueError("Material library hash changed during integration")
            report["locators"] = "structurally_verified" if "locators.xml" in members else "missing_dependency"
            car, = candidate.execute("SELECT DisplayName,ModelShort,MakeName,Thumbnail FROM Data_Car WHERE Id=?",
                                     (identity["car_id"],)).fetchall()
            make, = candidate.execute("SELECT IconPath, IconPathLarge, DisplayName, Profile FROM List_CarMake WHERE ID=?",
                                      (report["make_id"],)).fetchall()
            if english:
                def translated(table, token):
                    return token.startswith("_&") and int(token[2:]) & 0xffffffff in translations[table]
                if not all(translated("Data_Car", car[field]) for field in ("DisplayName", "ModelShort")):
                    raise ValueError("Missing English vehicle string")
                if report["make_id"] >= 20000 and not all(
                        translated("List_CarMake", make[field]) for field in ("DisplayName", "Profile")):
                    raise ValueError("Missing English manufacturer string")
            if "native_load_contract" in report:
                thumb_paths = thumbnail_archives(identity["car_id"])
                report["thumbnail_archives"] = [art[p.casefold()] for p in thumb_paths if p.casefold() in art]
                artwork = list(thumb_paths)
            else:
                artwork = [car["Thumbnail"]]
            if report["make_id"] >= 20000:
                artwork.extend((make["IconPath"], make["IconPathLarge"]))
            report["missing_artwork"] = [path for path in artwork
                                         if path.casefold().removeprefix("game:\\").replace("\\", "/") not in art]
            if native is not None:
                report["native_rig"] = receipt["render"]["runtime_rig"]
                report["materials_with_diagnostic_fallback"] = receipt["render"]["materials_with_diagnostic_fallback"]
                report["archive"] = {"name": receipt["archive"], "sha256": receipt["archive_sha256"],
                                     "directory": vars(layout)}
                if bridge:
                    report["material_contract"] = receipt["render"]["material_contract"]
                    right_side = {}
                    for row in report["tires"]["rows"]:
                        for field, model in row["models"].items():
                            normalized = row["fields"][field].casefold()
                            if normalized in right_side:
                                continue
                            cases = before.execute(
                                f"SELECT count(*) FROM List_UpgradeTireCompound WHERE IsStock=1 AND "
                                f"lower({ident(field)})=? AND Ordinal<12000",(normalized,)).fetchone()[0]
                            right_side[normalized] = {"explicit_target_right":model["has_distinct_right_resource"],
                                "native_stock_examples":cases,
                                "asset_status":("distinct_right_member" if model["has_distinct_right_resource"]
                                                else "shared_left_only_seen_in_stock" if cases else "missing_right_evidence"),
                                "runtime_contact":"unverified_runtime"}
                    report["tires"]["right_side"] = right_side
                    report["dynamics"]["required_drive_fields"] = [
                        item for item in report["dynamics"]["unresolved_target_fields"]
                        if item["drive_class"] in ("physics_startup","stable_motion","shared_native_setting")]
                if trial:
                    checks = report["trial_prerequisites"]
                    root = library.parents[3]
                    checks["suspension_assets"], missing_suspension = {}, []
                    for axle, path in report["dynamics"]["trial_suspension"].items():
                        asset = root / path
                        if asset.is_file() and asset.stat().st_size:
                            checks["suspension_assets"][axle] = {"path":path,"sha256":sha256(asset)}
                        else:
                            missing_suspension.append(f"{axle} suspension resource {path} is missing")
                    references = receipt["render"]["material_library"]["references"]
                    material_members = checks["asset_checks"]["material_members"]
                    with ZipFile(library) as shared:
                        known = {item.filename.casefold():item for item in shared.infolist()}
                        bad_materials = [key for key in material_members if key not in known or key not in references
                            or f"{known[key].CRC:08x}" != references[key]["crc32"]]
                    if set(material_members) != {entry["member"] for entry in report["material_contract"]}:
                        bad_materials.append("Modelbin material inventory differs from its surface compilation")
                    missing_dynamics = report["dynamics"]["trial_missing"] + missing_suspension + [
                        f'{r["table"]}.{r["field"]} requires a native mapping'
                        for r in report["dynamics"]["required_drive_fields"] if not any(
                            issue.startswith(f'{r["table"]}.{r["field"]} ')
                            for issue in report["dynamics"]["trial_missing"])]
                    checks["missing"] = {
                        "trial_controls":checks["control_missing"],
                        "trial_collision":[] if checks["collision"] else ["Missing coherent v27 shell and wheel plane"],
                        "trial_material_dependencies":[f"Unresolved shared material {m}" for m in bad_materials],
                        "native_dynamics":missing_dynamics,
                        "materials_required_for_drive":[f'Unclassified {m["role"]}/{m["component"]} material {m["source"]}'
                            for m in report["material_contract"] if not m["drive_resolved"]],
                        "tire_physics":[f"No native right-side evidence for {key}" for key,e in
                            report["tires"]["right_side"].items() if e["asset_status"]=="missing_right_evidence"] +
                            [f'{item["field"]}: {item["reason"]}' for item in report["tires"]["missing_assets"]] +
                            [item["reason"] for item in report["tires"]["unsupported"] if item["stock"]],
                    }
            results.append(report)

    collision = all("collision" not in r["missing_native_roles"] for r in results)
    english_state = ("structurally_verified" if english and all(not r["missing_artwork"] for r in results)
                     else "missing_dependency")
    common = {"target": fh4.TARGET_BUILD, "gameplay_confirmed": False, "game_db_insertions": before_proof,
              "cars": results, "unresolved_database_items": graph["unresolved"],
              "payload_hashes": {"manifest": hash_bytes(mount).hexdigest(),
                                 "database": graph["insert_only_proof"]["output_sha256"]}}
    if native is None:
        return {**common, "state": "native-integration-preview", "installable": False, "functional_complete": False,
                "features": {"archive_mount_geometry": "structurally_verified",
                             "catalogue_and_scene_ids": "structurally_verified",
                             "source_owned_wheel_rest": "structurally_verified",
                             "stock_v27_collision": "structurally_verified" if collision else "missing_dependency",
                             "native_animation_rig_and_effects": "missing_dependency", "tire_physics": "missing_dependency",
                             "english_presentation": english_state, "native_material_channels": "unsupported",
                             "audio_event_and_bank_generation": "unsupported"},
                "first_drive_blockers": ["The native stock tire catalogue and model selection are unresolved.",
                    "Target-specific powertrain, suspension and steering fields lack a runtime-validated mapping.",
                    "The generated skeleton lacks the independent GR2/GSF runtime integration contract."]}
    features = {
        "archive_mount_geometry": "structurally_verified",
        "catalogue_and_scene_ids": "structurally_verified",
        "wheel_bind_frames": ("structurally_verified" if all(r["wheel_frames"]["state"] == "structurally_verified"
                                                            for r in results) else "missing_dependency"),
        "source_owned_wheel_rest": ("unverified_runtime" if all(
            r["wheel_frames"]["state"] == "structurally_verified" for r in results) else "missing_dependency"),
        "stock_v27_collision": "unverified_runtime" if collision else "missing_dependency",
        "gr2_skeleton": ("structurally_verified" if all("gr2" not in r["missing_native_roles"] for r in results)
                         else "missing_dependency"),
        "native_animation_rig": ("structurally_verified" if all(
            r["native_rig"]["gsf"]["state"] in GOOD_STATES and "animation" not in r["missing_native_roles"]
            for r in results) else "missing_dependency"),
        "tire_physics": ("structurally_verified" if all(r["tires"]["structural_complete"] for r in results)
                         else "missing_dependency"),
        "native_dynamics": ("structurally_verified" if all(not r["dynamics"]["unresolved_target_fields"]
            and r["dynamics"]["shift"] is not None and not r["dynamics"]["shift"]["inferred"]
            for r in results) else "missing_dependency"),
        "autoshow_catalogue": ("structurally_verified" if english and all(r["bucket"] and not r["missing_artwork"] for r in results)
                               else "missing_dependency"),
        "materials_required_for_drive": ("missing_dependency" if any(r["materials_with_diagnostic_fallback"] for r in results)
                                         else "structurally_verified"),
        "english_presentation": english_state,
        "native_material_channels": "unsupported",
        "audio_event_and_bank_generation": "unsupported",
    }
    blockers = drive_blockers(features)
    common["payload_hashes"]["native_systems"] = sha256(systems_path)
    if bridge:
        features.update(
            gr2_gsf_graph_structure=("structurally_verified" if all(
                r["native_rig"]["gsf"]["state"] in GOOD_STATES and "animation" not in r["missing_native_roles"]
                for r in results) else "missing_dependency"),
            collision_structure="structurally_verified" if collision else "missing_dependency",
            material_resource_structure="structurally_verified",
            lighting_effects="missing_dependency",
            damage_effects="unsupported",
            accessory_motion="unverified_runtime")
        features["native_animation_rig"] = ("unverified_runtime" if features["gr2_gsf_graph_structure"] in GOOD_STATES
                                            else "missing_dependency")
        features["native_dynamics"] = ("structurally_verified" if all(
            not r["dynamics"]["required_drive_fields"] and r["dynamics"]["shift"] is not None
            and not r["dynamics"]["shift"]["inferred"] for r in results) else "missing_dependency")
        features["materials_required_for_drive"] = ("missing_dependency" if any(
            not info["drive_resolved"] and info["category"] in ("mandatory_runtime","required_for_visibility")
            for r in results for info in r["material_contract"]) else "structurally_verified")
        blockers = drive_blockers(features)
        common["conclusions"] = drive_conclusions(features)
    if trial:
        for key in ("native_dynamics","materials_required_for_drive","trial_material_dependencies",
                    "trial_controls","trial_collision","tire_physics"):
            original_ok = features.get(key,"structurally_verified") in GOOD_STATES
            features[key] = ("structurally_verified" if original_ok and all(
                not r["trial_prerequisites"]["missing"][key] for r in results) else "missing_dependency")
        if load_contract:
            features["native_wheel_and_data_car_load"] = "structurally_verified"
            features["native_thumbnail_archives"] = ("structurally_verified" if all(
                len(r["thumbnail_archives"]) == 2 for r in results) else "missing_dependency")
        common["conclusions"] = drive_conclusions(features)
        qualification = trial_qualification({**common,"features":features})
        return {**common,"state":"offline-trial-eligible" if qualification["passed"] else "integration-preview",
                "features":features,"offline_trial_eligibility":qualification,
                "first_drive_blockers":qualification["blockers"],"functional_complete":False,
                "shared_materials_sha256":sha256(library),"installable":qualification["passed"]}
    return {**common, "state": "integration-preview" if blockers else "drive-ready-candidate",
            "features": features,
            "first_drive_blockers": blockers,
            "functional_complete": set(features.values()) <= GOOD_STATES and not graph["unresolved"],
            "shared_materials_sha256": sha256(library),
            "installable": not blockers}


def verify_integration(output, baseline=None, manifest=None, library=None):
    saved = json.loads((output / "validation.json").read_text(encoding="utf-8"))
    graph = json.loads((output / "database-graph.json").read_text(encoding="utf-8"))
    presentation = json.loads((output / "presentation.json").read_text(encoding="utf-8"))
    mount = (output / "zipmanifest.xml").read_bytes()
    database = output / "gamedb.sqlite"
    checks = {"database": sha256(database), "manifest": hash_bytes(mount).hexdigest()}
    systems = output / "native-catalogue-systems.json"
    native = {}
    if systems.is_file():
        checks["native_systems"] = sha256(systems)
        native = {r["car_id"]: r for r in json.loads(systems.read_text(encoding="utf-8"))}
    if saved["payload_hashes"] != checks or graph["insert_only_proof"]["output_sha256"] != checks["database"]:
        raise ValueError("Integration payload hash mismatch")
    verify_qualification(saved)
    receipts = ([output / "build.json"] if (output / "build.json").is_file()
                else sorted((output / "cars").glob("*/build.json")))
    cars = [(r.parent, json.loads(r.read_text(encoding="utf-8"))) for r in receipts]
    if sorted(c["car_id"] for c in saved["cars"]) != sorted(r["identity"]["car_id"] for _, r in cars):
        raise ValueError("Unexpected integrated vehicle inventory")
    supplied = (baseline, manifest, library)
    if any(supplied):
        if not all(supplied):
            raise ValueError("A full audit needs the baseline GameDB, manifest and material library")
        checks = integrated_report(baseline, manifest, library, output, mount, cars)
        if checks != saved:
            raise ValueError("Saved integration report differs from independent replay")
    else:
        by_id = {car["car_id"]:car for car in saved["cars"]}
        trial = "offline_trial_eligibility" in saved
        with closing(snapshot(database)) as db:
            reader, _ = fh4.previous_readers()
            load_contract = NativeDataCarContract(db,imported=tuple(native)) if native and all(
                "native_load_contract" in n for n in native.values()) else None
            for folder, receipt in cars:
                identity = receipt["identity"]
                archive = folder / receipt["archive"]
                if sha256(archive) != receipt["archive_sha256"]:
                    raise ValueError("Integrated vehicle archive changed")
                asset_checks = {} if trial else None
                fh4.validate_zip(archive.read_bytes(), identity["media_name"], identity["car_id"], trial=asset_checks)
                with ZipFile(archive) as assets:
                    scene = reader(assets.read(identity["media_name"].casefold() + ".carbin")).scene()
                    audited = audit_catalogue(db, identity, scene, receipt["render"]["scene_contract"],
                                              native.get(identity["car_id"]),load_contract=load_contract)
                    if trial:
                        prior = by_id[identity["car_id"]]
                        readback = audit_trial_assets(assets,prior["wheel_frames"],asset_checks)
                        if any(prior["trial_prerequisites"][key] != value for key,value in readback.items()):
                            raise ValueError("Actual CarScene or collision differs from trial qualification")
                        if (saved["installable"] and (readback["control_missing"] or not readback["collision"]
                                or audited["dynamics"]["trial_missing"] or len(audited["dynamics"]["trial_suspension"])!=2)):
                            raise ValueError("The compiled assets and physical catalogue fail the trial contract")
        for art in presentation["art"]:
            if sha256(output / art["path"]) != art["sha256"]:
                raise ValueError("Vehicle art hash changed")
            if art.get("kind")=="thumbnail_archive":
                audit_thumbnail_mount(art,output,mounts={e.get("path").casefold():e.attrib for e in ET.fromstring(mount)})
        if en := presentation["english"]:
            if sha256(output / en["path"]) != en["archive_sha256"]:
                raise ValueError("English archive hash changed")
        if (output / "audio.json").is_file():
            from . import fh4_audio
            fh4_audio.verify(output, json.loads((output / "audio.json").read_text(encoding="utf-8")))
    return saved


def audit_thumbnail_mount(item,root,*,mounts):
    path = item["path"]
    if path != thumbnail_archives(item["car_id"])[int(item["hires"])]:
        raise ValueError("Thumbnail archive path does not match its owned car ordinal")
    shape = audit_thumbnail_archive((root/path).read_bytes(),item["car_id"],item["hires"])
    native = mounts[thumbnail_virtual(f'media/stripped/RC0{"HiRes" if item["hires"] else ""}.zip')]
    expected = {"version":native["version"],"priority":native["priority"],"path":thumbnail_virtual(path),
                "dirstart":str(shape["start"]),"dirsize":str(shape["size"]),"direntries":str(shape["entries"])}
    if shape!=item["directory"] or mounts.get(thumbnail_virtual(path))!=expected:
        raise ValueError(f"Native thumbnail archive mount is incorrect: {path}")


def verify_qualification(saved):
    if "conclusions" in saved and saved["conclusions"] != drive_conclusions(saved["features"]):
        raise ValueError("Native qualification conclusions disagree with their checks")
    if "offline_trial_eligibility" in saved:
        qualification = trial_qualification(saved)
        if saved["offline_trial_eligibility"] != qualification:
            raise ValueError("Offline trial qualification disagrees with the static checks")
        passed, blockers = qualification["passed"], qualification["blockers"]
        success = "offline-trial-eligible"
    else:
        blockers = drive_blockers(saved["features"])
        passed, success = not blockers, "drive-ready-candidate"
        if "conclusions" not in saved and "native_systems" not in saved.get("payload_hashes",{}):
            return saved["first_drive_blockers"]
    if (saved["first_drive_blockers"] != blockers or saved["installable"] is not passed
            or saved["state"] != (success if passed else "integration-preview")):
        raise ValueError("Native package state disagrees with its qualification")
    return blockers
