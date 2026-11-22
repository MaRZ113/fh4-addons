"""Compile stock FM4 catalogue records into a separate FH4 SQLite snapshot."""

from contextlib import closing, nullcontext
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from zipfile import ZipFile

from .database_graph import DatabaseGraph, ident
from .fh4_data_car import NativeDataCarContract, wheel_load_contract
from .fh4_db import snapshot
from .fh4_dynamics import NativeDynamicsCompiler, TARGET_FIELDS, aero_offsets, drive_class, localized_rows, ratios
from .fh4_trial_dynamics import TrialDynamics
from .fh4_presentation import NativeLocalization, generate_art
from .fh4_scene_contract import FAMILIES, NativeCarSceneContract
from .fh4_thumbnail import thumbnail_catalogue_path
from .fh4_tires import NativeTireCompiler, UnknownTire, WIDTH_TABLES, dimensions
from .vehicle_ir import read_asset, write_json


GRAPHICS = {
    "List_UpgradeCarBody": 2,
    "List_UpgradeRearWing": 9,
    "List_UpgradeCarBodyFrontBumper": 34,
    "List_UpgradeCarBodyRearBumper": 35,
    "List_UpgradeCarBodyHood": 36,
    "List_UpgradeCarBodySideSkirt": 37,
    "List_UpgradeCarBodyChassisStiffness": 41,
}
TUNED_SOURCE = {
    "FrontSpringDamperPhysicsID": "List_SpringDamperPhysics",
    "RearSpringDamperPhysicsID": "List_SpringDamperPhysics",
    "AntiSwayPhysicsID": "List_AntiSwayPhysics",
    "AeroPhysicsID": "List_AeroPhysics",
    "TorqueCurveFullThrottleID": "List_TorqueCurve",
}


class Source:
    def __init__(self, root, ir):
        self.root, self.ir = root, ir
        self.tables = ir["sections"]["physics"]["data"]["tables"]
        self.id = ir["vehicle"]["source_id"]

    def entries(self, table):
        return self.tables.get(table, [])

    def record(self, table):
        entry, = self.entries(table)
        return entry["record"]

    def stock(self, table):
        matches = [e for e in self.entries(table) if e["record"].get("IsStock") == 1]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous source stock row in {table}")
        return matches[0] if matches else None

    def raw_records(self, table, key, value):
        groups = []
        for digest, item in self.ir["assets"].items():
            entries = [loc["entry"].casefold() for loc in item["sources"] if loc["entry"].lower().endswith(".slt")]
            if not entries:
                continue
            with closing(snapshot(self.root / item["path"])) as db:
                if db.execute("SELECT 1 FROM sqlite_schema WHERE name=? AND type='table'", (table,)).fetchone():
                    rows = db.execute(f"SELECT * FROM {ident(table)} WHERE {ident(key)}=?", (value,)).fetchall()
                    if rows:
                        groups.append((any("merge" in e for e in entries), digest, rows))
        if not groups:
            return []
        precedence = max(layer for layer, _, _ in groups)
        chosen = [(digest, rows) for layer, digest, rows in groups if layer == precedence]
        if len(chosen) != 1:
            raise ValueError(f"Ambiguous source snapshot for {table}.{key}={value}")
        digest, rows = chosen[0]
        return [{"record": dict(row), "provenance": {"asset": digest, "locator": f"{table}.{key}={value}"}}
                for row in rows]

    def localization(self, key):
        return self.ir["presentation"]["strings_en"].get(key, {}).get("value")


@dataclass(frozen=True)
class Vehicle:
    root: Path
    ir: dict
    identity: dict


def register_manufacturer(graph, src, locale):
    make = src.record("List_CarMake")
    code = make["ManufacturerCode"]
    matches = graph.db.execute("SELECT ID FROM List_CarMake WHERE ManufacturerCode=? COLLATE NOCASE",
                               (code,)).fetchall()
    if len(matches) > 1:
        raise ValueError(f"Ambiguous manufacturer code {code}")
    if matches:
        return graph.key("List_CarMake", matches[0][0])
    for row in graph.rows.values():
        if row.key.table == "List_CarMake" and row.values["ManufacturerCode"].casefold() == code.casefold():
            return row.key
    ordinal = 20000 + make["ID"]
    country = graph.key("List_Country", make["CountryID"])
    name = src.localization("list_carmake.str/DisplayName")
    if not name:
        raise ValueError(f"Missing source manufacturer text for {code}")
    logo = f"game:\\media\\expanded_garage\\manufacturers\\{code}"
    return graph.add("List_CarMake", ordinal, code,
                     {"DisplayName": locale.encode("List_CarMake", "DisplayName", code, name),
                      "ManufacturerCode": code, "CountryID": country.value, "IconPathBase": make["IconPathBase"],
                      "Profile": locale.encode("List_CarMake", "Profile", code,
                                               src.localization("list_carmake.str/Profile") or ""),
                      "IconPath": logo + "_small.swatchbin", "IconPathLarge": logo + "_large.swatchbin"},
                     "FM4 manufacturer identity and English text", (country,))


def catalogue_bucket(car, drive):
    if car["CarTypeID"] != 1:
        return None
    if car["FamilyBodyID"] == 4 and car["EnginePlacementID"] == 1:
        return "Retro Hot Hatch" if car["Year"] < 2005 else "Hot Hatch"
    if (car["FamilyBodyID"] == 10 and drive["DrivetypeID"] == 1 and car["EnginePlacementID"] == 1
            and 1990 <= car["Year"] < 2005):
        return "Retro Hot Hatch"
    if car["FamilyBodyID"] == 11:
        return "Retro Supercars" if car["Year"] < 2006 else "Modern Supercars"
    return None


def compile_vehicle(graph, vehicle, locale, tires, dynamics, buckets, families, car_contract):
    src = Source(vehicle.root, vehicle.ir)
    car_id = vehicle.identity["car_id"]
    body_id = vehicle.identity["body_id"]
    alias = vehicle.identity["media_name"]
    make = register_manufacturer(graph, src, locale)
    old_make = src.record("List_CarMake")["ID"]
    actual_schema = {r[0] for r in graph.db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    trial_proof = []

    def unresolved(table, detail):
        graph.unresolved.append({"owner": alias, "dependency": table, "detail": detail})

    def borrow(table, number):
        return graph.key(table, number)

    def insert(table, key, source, overrides, rule, links=(), trial_fields=(), evidence=None):
        pk, columns = graph.schema(table)
        lookup = {k.casefold(): v for k, v in source.items()}
        projected = {c: lookup[c.casefold()] for c in columns if c != pk and c.casefold() in lookup}
        projected.update(overrides)
        missing = set(columns) - projected.keys() - {pk}
        if table == "List_TorqueCurve":
            missing = {c for c in missing if not c.startswith("v") or not c[1:].isdigit()
                       or int(c[1:]) < source["NumTorqueValues"]}
        if missing:
            unresolved(table, {"target_columns_unmapped": sorted(missing)})
        if trial_fields:
            trial_proof.append({"table":table,"pk":pk,"id":key,"fields":dict(trial_fields),"evidence":evidence})
        return graph.add(table, key, alias, projected, rule, links)

    original = src.record("Data_Car")
    layout = src.record("Data_CarBody")
    engine = src.record("Data_Engine")
    drive = src.record("Data_Drivetrain")
    bucket_name = catalogue_bucket(original,drive)
    bucket = buckets.get(bucket_name.casefold()) if bucket_name else None
    load_fields, load_provenance = car_contract.road_fields(src,bucket,families)
    mechanical = dynamics.analyze(src)
    shift = dynamics.shift(src)
    trial = TrialDynamics(graph.db, src, shift)
    car_settings, car_evidence = trial.car_fields()
    spring_settings, spring_sources = {}, {}
    stock_spring = src.stock("List_UpgradeSpringDamper")["record"]
    for axle in ("Front","Rear"):
        spring_id = stock_spring[axle+"SpringDamperPhysicsID"]
        spring_sources[axle] = spring_id
        source_spring, = src.raw_records("List_SpringDamperPhysics","SpringDamperPhysicsID",spring_id)
        source_type, = src.raw_records("List_SuspensionPhysicsType","SuspensionPhysicsTypeID",
                                       source_spring["record"]["SuspensionPhysicsTypeID"])
        spring_settings[axle] = trial.spring(axle,source_type["record"]["Name"])
    tire_dimensions = dimensions(original)
    tire_rows = []
    unsupported_tires = []
    physical_unmapped = []
    for table, fields in TARGET_FIELDS.items():
        physical_unmapped.extend({"table": table, "field": field, "classification": status,
                                  "drive_class": drive_class(table, field)}
                                 for field, status in fields.items()
                                 if status in ("unmapped", "native_default_unverified"))
    if not shift or shift["inferred"]:
        physical_unmapped.append({"table":"Data_Drivetrain","field":"ShiftSystemID",
                                  "classification":"missing_authoritative_shift_mode",
                                  "drive_class":"physics_startup"})
    brake_proof = None
    steering = mechanical["steering_target_fields"]
    # FM4 stock PI is a source measure. The target class is an offline threshold projection.
    pi = original["PerformanceIndex"]
    chosen_class = graph.db.execute("SELECT Id FROM CarClasses WHERE MaxPerformanceIndex>=? "
                                    "ORDER BY MaxPerformanceIndex,Id LIMIT 1", (pi,)).fetchone()
    if chosen_class is None:
        raise ValueError(f"Source performance index lies outside the target class grid: {pi}")
    country = borrow("List_Country", original["CountryID"])
    class_key = borrow("CarClasses", chosen_class[0])
    car_type = borrow("List_CarType", original["CarTypeID"])
    drive_type = borrow("List_DriveType", drive["DrivetypeID"])
    engine_config = borrow("List_EngineConfig", engine["ConfigID"])
    placement = borrow("List_EnginePlacement", original["EnginePlacementID"])
    steering_override = borrow("AutoSteerOverrides", original["AutoSteerOverrideID"])
    for table, key, field in (("List_DriveType", drive_type, "DriveType"), ("List_EngineConfig", engine_config, "Config")):
        _, cols = graph.schema(table)
        if field not in cols:
            field = cols[1]
        native = graph.db.execute(f"SELECT {ident(field)} FROM {ident(table)} WHERE {ident(key.field)}=?",
                                  (key.value,)).fetchone()
        source, = src.raw_records(table, key.field, key.value)
        aliases = {k.casefold(): v for k, v in source["record"].items()}
        if native is None or str(aliases[field.casefold()]).casefold() != str(native[0]).casefold():
            raise ValueError(f"Semantic mismatch for common catalogue key {key}")

    source_wheel = src.record("List_Wheels")
    # The owned stock assembly is the mass reference. Use the observed unit
    # coefficient when FM4 supplies mass without an FH4 relative mass tier.
    wheel_load = wheel_load_contract(source_wheel["Mass"],0)
    wheel = insert("List_Wheels", car_id, source_wheel,
                   {"MediaName": alias, "IconPath": alias, "DisplayName": "NULL",
                    "PartManufacturerID": 0, "Price": 0, "IsStock": 1, "Type": "", "MassLevel":0},
                   "Own FM4 wheel mass and native unit mass coefficient for its stock reference")
    name = src.localization("data_car.str/DisplayName")
    short = src.localization("data_car.str/ModelShort")
    if not name or not short:
        raise ValueError(f"Missing source vehicle display strings for {alias}")
    native_make_name = (graph.rows[make].values["DisplayName"] if make in graph.rows else
                        graph.db.execute("SELECT DisplayName FROM List_CarMake WHERE ID=?", (make.value,)).fetchone()[0])
    car = insert("Data_Car", car_id, original,
                 {"ModelId": car_id, "MediaName": alias, "MakeID": make.value, "ClassID": class_key.value,
                  "StockWheelID": wheel.value, "ContentId": 0, "IsPurchased": 1, "IsInstalled": 1,
                  "IsDrivable": 1, "NotAvailableInAutoshow": 0, "VisibleOnlyIfOwned": 0,
                  **steering, **car_settings, **load_fields,
                  "DisplayName": locale.encode("Data_Car", "DisplayName", alias, name),
                  "ModelShort": locale.encode("Data_Car", "ModelShort", alias, short), "MakeName": native_make_name,
                  "Thumbnail": thumbnail_catalogue_path(car_id)},
                 "FM4 stock values plus independent FH4 identity, on-disc availability and provisional class",
                 (make, wheel, country, class_key, car_type, drive_type, engine_config, placement, steering_override,
                  borrow("List_FamilyBody",load_fields["FamilyBodyID"])),
                 car_settings,car_evidence)
    load_contract = car_contract.audit({"Id":car_id,**graph.rows[car].values})
    load_contract.update(field_provenance=load_provenance, converted_fields=load_fields,
                         stock_wheel=wheel_load)
    unresolved("Data_Car", "Source PI and projected class require target PI measurement.")
    unresolved("presentation", "Wheel icon, non-English locales and native frontend behavior are pending.")
    insert("OnDiscContent", car_id, {}, {"ContentId": car_id, "ContentType": 1, "ReleaseOrder": 0},
           "Base on-disc car relation", (car,))
    insert("CarRarities", car_id, {}, {"Rarity": 0}, "Common catalogue rarity", (car,))
    if bucket is not None:
        insert("Data_Car_Buckets", car_id, {}, {"CarBucket": bucket, "BucketHero": 0},
               f"FH4 catalogue leaf {bucket_name} classified from FM4 body family, era and drivetrain",
               (car, borrow("CarBuckets", bucket)))
    else:
        unresolved("Data_Car_Buckets", "No supported native road-car bucket for the source body and era.")
    body = insert("Data_CarBody", body_id, layout, {
        "Wheelbase": layout["ModelWheelbase"],
        **{label: layout[f"PristineBoundingBoxMax{axis}"] - layout[f"PristineBoundingBoxMin{axis}"]
           for label, axis in zip(("Width", "Height", "Length"), ("X", "Y", "Z"), strict=True)},
    }, "FM4 body measurements and extent spans in meters")
    own_engine = insert("Data_Engine", car_id, engine, {"MediaName": alias},
                        "Own FM4-derived engine identity, audio awaiting binding", (engine_config,))
    shift_fields = {"ShiftSystemID": shift["id"]} if shift else {}
    shift_links = (borrow("List_ShiftSystem", shift["id"]),) if shift else ()
    own_drive = insert("Data_Drivetrain", car_id, drive, shift_fields,
                       "FM4 drivetype and mounting orientation; shift mode from source rig and gearing",
                       (drive_type, *shift_links))
    owners = {"ordinal": car, "carbodyid": body, "engineid": own_engine, "drivetrainid": own_drive}

    if src.entries("CameraOverrides"):
        insert("CameraOverrides", car_id, src.record("CameraOverrides"), {},
               "FM4 camera offsets, native camera behavior pending", (car,))
    for item in src.raw_records("CarPartPositions", "Ordinal", src.id):
        row = item["record"]
        insert("CarPartPositions", body_id + row["PartId"], row, {"Ordinal": car_id},
               f'FM4 authored position {item["provenance"]["asset"]}, native field frame retained', (car,))

    mapped = {}
    contract = NativeCarSceneContract(vehicle.ir, body_id)
    variant_tables = {family.table for family in FAMILIES}
    variant_keys = {(part.family.table, part.source_key) for part in contract.parts}

    def selected_upgrades():
        for table, entries in sorted(src.tables.items()):
            if not table.startswith("List_Upgrade"):
                continue
            for entry in entries:
                row = entry["record"]
                if (row["IsStock"] or (table, row["Id"]) in variant_keys or table in WIDTH_TABLES
                        or table == "List_UpgradeTireCompound"):
                    yield table, entry

    for table, entry in selected_upgrades():
        if table not in actual_schema:
            unresolved(table, "No corresponding FH4 table. Source row retained in the IR.")
            continue
        row = entry["record"]
        if table == "List_UpgradeDrivetrainTransmission":
            ratios(row, original["NumGears"] if row["IsStock"] else None)
        _, cols = graph.schema(table)
        lookup = {col.casefold(): col for col in cols}
        source_fields = {k.casefold() for k in row}
        links, overrides = [], {}
        row_trial, row_evidence = {}, {}
        for field, owner in owners.items():
            if field in source_fields and field in lookup:
                links.append(owner)
                overrides[lookup[field]] = owner.value
        if not links:
            unresolved(table, "No typed vehicle owner identified for stock upgrade.")
            continue
        for field, dependency in TUNED_SOURCE.items():
            column = lookup.get(field.casefold())
            if not column or column not in row:
                continue
            old = row[column]
            pk = graph.schema(dependency)[0]
            if (dependency, old) not in mapped:
                candidates = [e for e in src.entries(dependency) if e["record"].get(pk) == old]
                if not candidates:
                    candidates = src.raw_records(dependency, pk, old)
                if len(candidates) != 1:
                    raise ValueError(f"Missing or ambiguous FM4 row for {dependency} {old} from {table}")
                owned = candidates[0]
                data = owned["record"]
                out = {}
                extra = []
                trial_fields, evidence = {}, {}
                if "Ordinal" in graph.schema(dependency)[1] and "Ordinal" in data:
                    out["Ordinal"] = car_id
                    extra.append(car)
                if dependency == "List_SpringDamperPhysics":
                    # Zero-based FM4 and one-based FH4 IDs differ. Match the source enum by name.
                    raw_type, = src.raw_records("List_SuspensionPhysicsType", "SuspensionPhysicsTypeID",
                                                data["SuspensionPhysicsTypeID"])
                    typ, = graph.db.execute("SELECT SuspensionPhysicsTypeID FROM List_SuspensionPhysicsType WHERE Name=?",
                                            (raw_type["record"]["Name"],)).fetchall()
                    out["SuspensionPhysicsTypeID"] = typ[0]
                    out.update(dynamics.spring(data))
                    axle = field.removesuffix("SpringDamperPhysicsID")
                    if row["IsStock"] and old == spring_sources.get(axle):
                        trial_fields, evidence = spring_settings[axle]
                        out.update(trial_fields)
                    extra.append(borrow("List_SuspensionPhysicsType", out["SuspensionPhysicsTypeID"]))
                if dependency == "List_AntiSwayPhysics" and row["IsStock"]:
                    trial_fields,evidence = trial.anti_sway(table.removeprefix("List_UpgradeAntiSway"))
                    out.update(trial_fields)
                if dependency == "List_AeroPhysics":
                    trial_fields = aero_offsets(data, source=True)
                    evidence = {
                        "rule": "Preserve supplied conditional additions and use neutral offsets for FM4 curves without them",
                        "native_initializer_rva": "0x38A8F00",
                        "neutral_source_fields": [field for field in trial_fields if data.get(field) is None],
                    }
                    out.update(trial_fields)
                next_owned = sum(1 for parent, _ in mapped if parent == dependency)
                mapped[dependency, old] = insert(
                    dependency, body_id + next_owned, data, out,
                    f'FM4 source {owned["provenance"]["asset"]} {owned["provenance"]["locator"]}', extra,
                    trial_fields,evidence)
            new_key = mapped[dependency, old]
            links.append(new_key)
            overrides[column] = new_key.value

        if "manufacturerid" in lookup:
            column = lookup["manufacturerid"]
            old = next((v for k, v in row.items() if k.casefold() == column.casefold()), 0)
            if old == old_make:
                links.append(make)
                overrides[column] = make.value
            elif old:
                overrides[column] = 0
                unresolved(table, f"FM4 upgrade supplier {old} needs a target manufacturer mapping.")
        for field in ("DamageModDefID", "SuspensionDamageModDefID", "TiresDamageModDefID"):
            if field in row and field in cols and row[field] > 0:
                links.append(borrow("List_DamageModDef", row[field]))
        if table == "List_UpgradeBrakes":
            overrides.update({axle+"BrakeSizeMM":row[axle+"BrakeSize"]*25.4 for axle in ("Front","Rear")})
            brake_fields, brake_proof, brake_types = dynamics.brakes(src,row)
            physical_unmapped = [item for item in physical_unmapped if not (
                item["table"]==table and item["field"] in brake_fields)]
            links.extend(borrow("List_BrakeType",x) for x in brake_types)
            overrides.update(brake_fields)
            if row["IsStock"]:
                for axle in ("Front","Rear"):
                    if type_id := brake_fields.get(axle + "BrakeTypeID"):
                        fields, evidence = trial.brakes(axle,type_id)
                        row_trial.update(fields)
                        if evidence:
                            row_evidence[axle] = evidence
        if table == "List_UpgradeDrivetrainDriveline" and row["IsStock"]:
            row_trial,row_evidence = trial.driveline()
        if table == "List_UpgradeTireCompound":
            try:
                tire = tires.select(src, row)
            except UnknownTire as error:
                status = {"source_id": row["Id"], "source_level": row["Level"], "stock": bool(row["IsStock"]),
                          "source": entry["provenance"], "reason": str(error)}
                unsupported_tires.append(status)
                unresolved(table, status)
                continue
            overrides.update(tire.fields)
            links.extend((borrow("List_TireCompound", tire.fields["TireCompoundID"]),
                          *(borrow(parent, key) for parent, key in tire.shared_keys)))
            tire_rows.append({"id": body_id + row["Id"] % 1000, "stock": bool(row["IsStock"]),
                              "source": entry["provenance"], "source_family": tire.source_family,
                              "target_family": tire.target_family, "stock_examples": tire.stock_sample_count,
                              "fields": tire.fields})
        if table in WIDTH_TABLES:
            axle = WIDTH_TABLES[table]
            width = row[axle + "TireWidth"]
            stock_width = tire_dimensions[axle][0]
            if not stock_width <= width <= 650 or row["IsStock"] and stock_width != width:
                raise ValueError(f"Source tire width upgrade disagrees with {axle} axle dimensions")
        if table == "List_UpgradeSpringDamper":
            overrides.update({col: steering[col] for col in cols if col in steering})
        if "RequiresGraphics" in cols:
            overrides["RequiresGraphics"] = int(
                (table in GRAPHICS and table not in variant_tables) or table == "List_UpgradeBrakes"
                or (table, row["Id"]) in variant_keys)
        if "partsstringid" in lookup:
            column = lookup["partsstringid"]
            dtype = next(c[2] for c in graph.db.execute(f"PRAGMA table_info({ident(table)})") if c[1] == column)
            overrides[column] = "0" if dtype == "TEXT" else 0
        pk = graph.schema(table)[0]
        source_pk = row[pk]
        insert(table, body_id + source_pk % 1000, row, {**overrides,**row_trial},
               f'FM4 upgrade {entry["provenance"]["asset"]} {entry["provenance"]["locator"]}', links,
               row_trial,row_evidence)
    coverage = {(entry["table"],field) for entry in trial_proof for field in entry["fields"]}
    coverage.update(("Data_Car",field) for field in load_fields)
    physical_unmapped = [item for item in physical_unmapped if (item["table"],item["field"]) not in coverage]
    unresolved("Upgrades", {"detail": "Unresolved alternatives remain in the IR.", "source_variants": contract.unresolved})
    return {"car_id": car_id, "bucket": {"id": bucket, "name": bucket_name} if bucket is not None else None,
            "native_load_contract":load_contract,
            "tires": {"dimensions": {axle: tires.stock_size(tire_dimensions, axle) for axle in tire_dimensions},
                      "rows": tire_rows, "unsupported": unsupported_tires},
            "dynamics": {"source_checks": mechanical, "shift": shift, "unresolved_target_fields": physical_unmapped,
                         "brake_conversion":brake_proof,"trial_settings":trial_proof}}


def compile_database(baseline, vehicles, output, *, strings=None, source_tools=None):
    if not vehicles:
        raise ValueError("At least one vehicle is required")
    with closing(snapshot(baseline)) as db:
        graph = DatabaseGraph(db)
        locale = NativeLocalization(db, strings)
        with (ZipFile(strings) if strings else nullcontext()) as native_strings:
            dynamics = NativeDynamicsCompiler(db, native_strings)
            buckets = (localized_rows(db, native_strings, "CarBuckets", "Id", "Name", leaves_only=True)
                       if native_strings else {})
            families = localized_rows(db,native_strings,"List_FamilyBody","Id","DisplayName") if native_strings else {}
        tires = NativeTireCompiler(db)
        car_contract = NativeDataCarContract(db)
        systems = [compile_vehicle(graph, vehicle, locale, tires, dynamics, buckets,families,car_contract)
                   for vehicle in sorted(vehicles, key=lambda v: v.identity["car_id"])]
        proof = graph.stage(baseline, output / "gamedb.sqlite")
        report = graph.receipt()
        manufacturers = {row.values["ManufacturerCode"] for row in graph.rows.values()
                         if row.key.table == "List_CarMake"}
    presentation = generate_art(output, vehicles, manufacturers, source_tools)
    presentation["english"] = locale.write(output)
    write_json(output / "presentation.json", presentation)
    sources = output / "presentation-source"
    sources.mkdir()
    for vehicle in vehicles:
        target = sources / vehicle.identity["media_name"]
        target.mkdir()
        original_art = vehicle.ir["presentation"]
        write_json(target / "presentation.json", original_art)
        for texture in original_art["textures"]:
            digest = texture["provenance"]["asset"]
            (target / (digest + ".xds")).write_bytes(read_asset(vehicle.root, vehicle.ir, digest))
    write_json(output / "database-graph.json", {"state": "structural-preview", **report, "insert_only_proof": proof})
    write_json(output / "native-catalogue-systems.json", systems)
    return {"state": "structural-preview", "insertions": proof["insertions"],
            "presentation": presentation,
            "unresolved": len(report["unresolved"]), "sha256": proof["output_sha256"]}
