"""Inspect quiescent FH4 SQLite files and stage base-game car definitions."""

import argparse
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
STOCK_SHA256 = "5e4a76877b94e66342e7e24cef5a211f0d4a4725fc9fd3be656ead6a8ebc3c1d"
DONOR = 368
CANDIDATE = 3477
MEDIA = "ACU_IntegraR_01"
ORDINAL_COLUMNS = {"carid", "ordinal", "carordinal", "modelid", "midnightcarmodelid"}
BODY_CATALOGS = (
    "ChassisStiffness", "FrontBumper", "Hood", "RearBumper", "SideSkirt",
    "TireWidthFront", "TireWidthRear", "TrackSpacingFront", "TrackSpacingRear", "Weight",
)
ORDINAL_CATALOGS = (
    "AntiSwayFront", "AntiSwayRear", "Brakes", "CarBody", "Drivetrain",
    "Engine", "RearWing", "RimSizeFront", "RimSizeRear", "SpringDamper", "TireCompound",
)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@contextmanager
def read_only(path: Path) -> Iterator[sqlite3.Connection]:
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).exists():
            raise ValueError(f"Expected a quiescent SQLite snapshot, found {path.name}{suffix}")
    with path.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\0":
            raise ValueError(f"{path} is not plaintext SQLite")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def tables(db: sqlite3.Connection) -> list[str]:
    return [r[0] for r in db.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
    ) if not r[0].startswith("sqlite_")]


def columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in db.execute(f"PRAGMA table_info({ident(table)})")]


def rows_for(db: sqlite3.Connection, table: str, column: str, value: int) -> list[sqlite3.Row]:
    return db.execute(
        f"SELECT * FROM {ident(table)} WHERE {ident(column)} = ?", (value,)
    ).fetchall()


def check_integrity(db: sqlite3.Connection) -> list[tuple]:
    result = [r[0] for r in db.execute("PRAGMA integrity_check")]
    if result != ["ok"]:
        raise ValueError(f"integrity_check: {result}")
    return sorted((tuple(r) for r in db.execute("PRAGMA foreign_key_check")), key=repr)


def id_hits(db: sqlite3.Connection, car_id: int) -> dict[str, int]:
    hits = {}
    for table in tables(db):
        for column in columns(db, table):
            direct = (column.lower() in ORDINAL_COLUMNS
                      or (table, column) in {("Data_Car", "Id"), ("Demo_CarList", "Id")})
            offer = table in {"ContentOffersMapping", "OnDiscContent"} and column == "ContentId"
            if not (direct or offer):
                continue
            condition = f"{ident(column)} = ?"
            if offer:
                condition += " AND ContentType = 1"
            count = db.execute(
                f"SELECT count(*) FROM {ident(table)} WHERE {condition}", (car_id,)
            ).fetchone()[0]
            if count:
                hits[f"{table}.{column}"] = count
    return hits


def derived_hits(db: sqlite3.Connection, car_id: int) -> dict[str, list[int]]:
    hits = {}
    low = car_id * 1000
    for table in tables(db):
        for column in columns(db, table):
            if not column.lower().endswith("id"):
                continue
            key = ident(column)
            values = [r[0] for r in db.execute(
                f"SELECT DISTINCT {key} FROM {ident(table)} WHERE {key} BETWEEN ? AND ? "
                f"AND typeof({key}) = 'integer' ORDER BY {key}", (low, low + 999)
            )]
            if values:
                hits[f"{table}.{column}"] = values
    return hits


def inspect(db: sqlite3.Connection, car_id: int) -> dict:
    fk = check_integrity(db)
    return {
        "integrity_check": "ok",
        "schema_objects": dict(db.execute("SELECT type,count(*) FROM sqlite_schema GROUP BY type")),
        "car_count": db.execute("SELECT count(*) FROM Data_Car").fetchone()[0],
        "foreign_key_violations": len(fk),
        "foreign_key_violations_by_table": Counter(row[0] for row in fk),
        "car": [dict(r) for r in rows_for(db, "Data_Car", "Id", car_id)],
        "id_hits": id_hits(db, car_id),
        "derived_key_hits": derived_hits(db, car_id),
    }


def assets(game: Path, car_id: int, manifest: Path | None, media: str = MEDIA) -> dict:
    event = game / "media/audio/cars/CarEventMapping.xml"
    ordinals = Counter(int(r.attrib["ordinal"]) for r in ET.parse(event).iter("Car"))
    dlc = game / "DLC"
    layouts = sorted(dlc.glob("*/layout*.xml"))
    regex = re.compile(rf"(?<![0-9]){car_id}(?![0-9])")
    thumbnail = re.compile(r"thumbnail_(\d+)(?:_(?:big|small)\.swatchbin|\.png)$", re.I)
    archives = set((game / "media/stripped").glob("RC*.zip"))
    archives.update(f for f in dlc.rglob("*.zip") if re.fullmatch(r"rc\d+(hires)?", f.stem, re.I))
    thumbnail_ids = set()
    thumbnail_matches = []
    for archive in sorted(archives):
        with zipfile.ZipFile(archive) as carrier:
            for entry in carrier.namelist():
                match = thumbnail.fullmatch(PurePosixPath(entry.replace("\\", "/")).name)
                if match:
                    ordinal = int(match[1])
                    thumbnail_ids.add(ordinal)
                    if ordinal == car_id:
                        thumbnail_matches.append(f"{archive.relative_to(game)}!{entry}")

    if not re.fullmatch(r"[a-zA-Z0-9_]+", media):
        raise ValueError("Unexpected media name")
    car_zip = game / f"media/cars/{media}.zip"
    with zipfile.ZipFile(car_zip) as carrier:
        entries = len(carrier.infolist())
        start = carrier.start_dir
        scene = f"{media}.carbin".casefold() in {name.casefold() for name in carrier.namelist()}

    result = {
        "audio_ordinal_count": len(ordinals),
        "audio_max_ordinal": max(ordinals),
        "audio_matches": ordinals[car_id],
        "thumbnail_archives_scanned": len(archives),
        "thumbnail_ordinal_count": len(thumbnail_ids),
        "thumbnail_matches": thumbnail_matches,
        "dlc_layouts_scanned": [str(f.relative_to(game)) for f in layouts],
        "dlc_text_matches": [str(f.relative_to(game)) for f in layouts
                             if regex.search(f.read_text(encoding="utf-8-sig"))],
        "donor_archive": str(car_zip.relative_to(game)),
        "donor_archive_sha256": sha256(car_zip),
        "donor_archive_entries": entries,
        "donor_carbin_present": scene,
    }
    if manifest is not None:
        paths = [r.attrib for r in ET.parse(manifest).iter("Zip")]
        donor_path = f"game:\\media\\cars\\{media.lower()}.zip"
        matching = [r for r in paths if r.get("path", "").lower() == donor_path]
        result["manifest_count"] = len(paths)
        result["donor_manifest_entry"] = matching
        result["donor_manifest_matches_archive"] = len(matching) == 1 and (
            int(matching[0]["dirstart"]) == start and int(matching[0]["direntries"]) == entries
        )
    return result


def compare(left: sqlite3.Connection, right: sqlite3.Connection) -> dict:
    schema = "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
    if list(left.execute(schema)) != list(right.execute(schema)):
        raise ValueError("Schema differs")
    deltas = {}
    for table in tables(left):
        query = f"SELECT * FROM {ident(table)}"
        old, new = (Counter(map(tuple, db.execute(query))) for db in (left, right))
        if old != new:
            deltas[table] = {"removed": sum((old - new).values()), "added": sum((new - old).values())}
    old_ids, new_ids = ({r[0] for r in db.execute("SELECT Id FROM Data_Car")} for db in (left, right))
    return {"schema_equal": True, "removed_car_ids": sorted(old_ids-new_ids),
            "added_car_ids": sorted(new_ids-old_ids), "changed_tables": deltas}


def plan(db: sqlite3.Connection, donor: int, candidate: int, price: int) -> dict[str, list[dict]]:
    def remap(key: int) -> int:
        if type(key) is not int or key // 1000 != donor:
            raise ValueError(f"{key} is outside the supported donor key block")
        return candidate * 1000 + key % 1000

    source_rows = rows_for(db, "Data_Car", "Id", donor)
    if len(source_rows) != 1:
        raise ValueError(f"No unique source definition for {donor}")
    root = dict(source_rows[0])
    if (root["ModelId"], root["ContentId"], root["IsDrivable"]) != (donor, 0, 1):
        raise ValueError("Unexpected donor identity or content")
    if id_hits(db, candidate) or derived_hits(db, candidate):
        raise ValueError("Destination identity or derived key block is in use")
    root.update(Id=candidate, ModelId=candidate, BaseCost=price)
    changes = {"Data_Car": [root]}
    release = db.execute(
        "SELECT * FROM OnDiscContent WHERE ContentType=1 AND ContentId=?", (donor,)
    ).fetchall()
    if len(release) > 1:
        raise ValueError("Ambiguous on-disc release records for the donor")
    if release:
        release_id = db.execute("SELECT max(Id)+1 FROM OnDiscContent").fetchone()[0]
        changes["OnDiscContent"] = [dict(release[0]) | {"Id": release_id, "ContentId": candidate}]
    for table, key in (("CameraOverrides", "CarId"), ("CarExceptions", "CarID"),
                       ("CarRarities", "CarID"), ("Data_Car_Buckets", "CarId"),
                       ("EventBlueprintFlyerBackgrounds", "CarId")):
        changes[table] = [dict(row) | {key: candidate} for row in rows_for(db, table, key, donor)]

    specs = {
        "CarPartPositions": ("ID", ()),
        "Combo_Colors": ("ID", ()),
        "List_AntiSwayPhysics": ("AntiSwayPhysicsID", ()),
        "List_SpringDamperPhysics": ("SpringDamperPhysicsID", ()),
    }
    cross_refs = {
        "AntiSwayFront": ("AntiSwayPhysicsID",),
        "AntiSwayRear": ("AntiSwayPhysicsID",),
        "CarBody": ("CarBodyID",),
        "RearWing": ("AeroPhysicsID",),
        "SpringDamper": ("FrontSpringDamperPhysicsID", "RearSpringDamperPhysicsID"),
    }
    for suffix in ORDINAL_CATALOGS:
        specs[f"List_Upgrade{suffix}"] = ("Id", cross_refs.get(suffix, ()))
    for table, (key, refs) in specs.items():
        changes[table] = [dict(row) | {"Ordinal": candidate, key: remap(row[key])}
                          | {col: remap(row[col]) for col in refs}
                          for row in rows_for(db, table, "Ordinal", donor)]

    bodies = sorted({row["CarBodyID"] for row in rows_for(db, "List_UpgradeCarBody", "Ordinal", donor)})
    for table in ("Data_CarBody", "Data_CarBody_DummyAxle"):
        changes[table] = [dict(row) | {"Id": remap(body)}
                          for body in bodies for row in rows_for(db, table, "Id", body)]
    for suffix in BODY_CATALOGS:
        table = f"List_UpgradeCarBody{suffix}"
        key = next(col for col in columns(db, table) if col.lower() == "carbodyid")
        changes[table] = [dict(row) | {"Id": remap(row["Id"]), key: remap(body)}
                          for body in bodies for row in rows_for(db, table, key, body)]
    # A front bumper can share an aero row or point into the donor-owned namespace.
    front = changes["List_UpgradeCarBodyFrontBumper"]
    front_aero = {row["AeroPhysicsID"] for row in front if row["AeroPhysicsID"] // 1000 == donor}
    for row in front:
        if row["AeroPhysicsID"] in front_aero:
            row["AeroPhysicsID"] = remap(row["AeroPhysicsID"])

    wing_ids = {row["AeroPhysicsID"] for row in rows_for(db, "List_UpgradeRearWing", "Ordinal", donor)}
    changes["List_AeroPhysics"] = [
        dict(row) | {"AeroPhysicsID": remap(key)} for key in sorted(wing_ids | front_aero)
        for row in rows_for(db, "List_AeroPhysics", "AeroPhysicsID", key)
    ]
    if not changes["Data_Car_Buckets"]:
        raise ValueError("Donor has no category mapping")
    return {table: rows for table, rows in changes.items() if rows}


def validate_links(db: sqlite3.Connection, changes: dict[str, list[dict]]) -> None:
    links = {
        "Data_Car": (("ClassID", "CarClasses", "Id"), ("CarTypeID", "List_CarType", "Id"),
                     ("MakeID", "List_CarMake", "Id"), ("StockWheelID", "List_Wheels", "Id")),
        "Data_Car_Buckets": (("CarBucket", "CarBuckets", "Id"),),
        "OnDiscContent": (("ContentId", "Data_Car", "Id"),),
        "List_UpgradeCarBody": (("CarBodyID", "Data_CarBody", "Id"),),
        "List_UpgradeEngine": (("EngineID", "Data_Engine", "EngineID"),),
        "List_UpgradeDrivetrain": (("DrivetrainID", "Data_Drivetrain", "DrivetrainID"),),
        "List_UpgradeTireCompound": (("TireCompoundID", "List_TireCompound", "TireCompoundID"),),
        "List_UpgradeRearWing": (("AeroPhysicsID", "List_AeroPhysics", "AeroPhysicsID"),),
        "List_UpgradeCarBodyFrontBumper": (("AeroPhysicsID", "List_AeroPhysics", "AeroPhysicsID"),),
    }
    for suffix in ("Front", "Rear"):
        links[f"List_UpgradeAntiSway{suffix}"] = (
            ("AntiSwayPhysicsID", "List_AntiSwayPhysics", "AntiSwayPhysicsID"),
        )
    links["List_UpgradeSpringDamper"] = tuple(
        (f"{side}SpringDamperPhysicsID", "List_SpringDamperPhysics", "SpringDamperPhysicsID")
        for side in ("Front", "Rear")
    )
    for suffix in BODY_CATALOGS:
        table = f"List_UpgradeCarBody{suffix}"
        key = next(col for col in columns(db, table) if col.lower() == "carbodyid")
        links[table] = links.get(table, ()) + ((key, "Data_CarBody", "Id"),)
    for table, refs in links.items():
        for col, parent, key in refs:
            for row in changes.get(table, []):
                if len(rows_for(db, parent, key, row[col])) != 1:
                    raise ValueError(f"Broken reference {table}.{col}={row[col]} -> {parent}.{key}")
    for table in (f"List_Upgrade{suffix}" for suffix in ORDINAL_CATALOGS):
        if sum(row["IsStock"] == 1 for row in changes.get(table, [])) != 1:
            raise ValueError(f"Expected one stock row in {table}")


def stage(source: Path, output: Path, donor: int, new_id: int, price: int,
          carbin: Path | None, game: Path | None) -> dict:
    from fh4_carbin import audit, read_scene

    if not (0 < donor <= 2147482 and 0 < new_id <= 2147482 and donor != new_id
            and 0 <= price <= 2147483647):
        raise ValueError("IDs must differ and fit the signed derived key range; price must fit int32")
    source, output = source.resolve(), output.resolve()
    if not output.is_relative_to(ROOT / ".scratch") or output == source or output.exists():
        raise ValueError("Stage output must be a new file under the workspace .scratch directory")
    if sha256(source) != STOCK_SHA256:
        raise ValueError("Stage requires the exact plaintext stock snapshot reviewed in Phase 1")
    with read_only(source) as baseline:
        baseline_fk = check_integrity(baseline)
        changes = plan(baseline, donor, new_id, price)
        media = changes["Data_Car"][0]["MediaName"]
        asset_scan = assets(game, new_id, None, media) if game else None
        if asset_scan and (asset_scan["audio_matches"] or asset_scan["thumbnail_matches"]
                           or asset_scan["dlc_text_matches"] or not asset_scan["donor_carbin_present"]):
            raise ValueError("Destination conflicts with supplied game assets or donor Carbin is missing")
        carbin_source = carbin or (game / f"media/cars/{media}.zip" if game else None)
        scene = read_scene(carbin_source)[1] if carbin_source else None
        if scene and (scene["ordinal"] != donor or scene["media_name"].casefold() != media.casefold()):
            raise ValueError("Carbin source does not identify the requested donor")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output.parent) as directory:
            trial = Path(directory) / "candidate.sqlite"
            shutil.copyfile(source, trial)
            if sha256(trial) != STOCK_SHA256:
                raise ValueError("Stock snapshot changed during the copy")
            db = sqlite3.connect(trial)
            try:
                db.execute("PRAGMA foreign_keys=ON")
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    for table, rows in changes.items():
                        cols = columns(db, table)
                        names = ", ".join(map(ident, cols))
                        placeholders = ", ".join("?" for _ in cols)
                        db.executemany(
                            f"INSERT INTO {ident(table)} ({names}) VALUES ({placeholders})",
                            [tuple(row[col] for col in cols) for row in rows],
                        )
                    validate_links(db, changes)
            finally:
                db.close()
            with read_only(trial) as candidate:
                if check_integrity(candidate) != baseline_fk:
                    raise ValueError("Foreign-key violations differ from the stock baseline")
                delta = compare(baseline, candidate)
                expected = {t: {"removed": 0, "added": len(rows)} for t, rows in changes.items()}
                if (delta["changed_tables"] != expected or delta["removed_car_ids"]
                        or delta["added_car_ids"] != [new_id]):
                    raise ValueError("Unexpected row changes in the candidate")
                binding = audit(candidate, new_id, scene) if scene else None
            if sha256(source) != STOCK_SHA256:
                raise ValueError("Stock snapshot changed during staging")
            output_sha = sha256(trial)
            os.link(trial, output)
    return {
        **delta, "status": "OFFLINE_SQLITE_VALIDATED", "source_sha256": STOCK_SHA256,
        "output": str(output), "output_sha256": output_sha,
        "foreign_key_violations_baseline_and_candidate": len(baseline_fk),
        "total_insertions": sum(len(rows) for rows in changes.values()),
        "donor_id": donor, "new_id": new_id, "price": price,
        "asset_collision_scan": asset_scan, "carbin_bindings": binding,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "compare", "stage"):
        command = commands.add_parser(name)
        command.add_argument("db", type=Path)
        if name == "inspect":
            command.add_argument("--id", type=int, required=True)
        elif name == "compare":
            command.add_argument("other", type=Path)
        else:
            command.add_argument("output", type=Path)
            command.add_argument("--donor-id", type=int, default=DONOR)
            command.add_argument("--new-id", type=int, default=CANDIDATE)
            command.add_argument("--price", type=int, default=26000)
            command.add_argument("--carbin", type=Path)
            command.add_argument("--game", type=Path)
    command = commands.add_parser("assets")
    command.add_argument("game", type=Path)
    command.add_argument("--id", type=int, required=True)
    command.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "stage":
            report = stage(args.db, args.output, args.donor_id, args.new_id, args.price,
                           args.carbin, args.game)
        elif args.command == "assets":
            report = assets(args.game, args.id, args.manifest)
        else:
            with read_only(args.db) as db:
                if args.command == "inspect":
                    report = {"sha256": sha256(args.db), **inspect(db, args.id)}
                else:
                    with read_only(args.other) as other:
                        report = compare(db, other)
        print(json.dumps(report, ensure_ascii=True, indent=2))
    except (OSError, ValueError, sqlite3.Error, ET.ParseError, zipfile.BadZipFile) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
