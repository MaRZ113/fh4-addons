"""Allocate persistent FH4 identities and report source-to-target catalogue coverage."""

from contextlib import closing
from pathlib import Path
import sqlite3

from .vehicle_ir import media_name


ID_MIN, ID_MAX = 12000, 19999
FH1_PILOT_ID_MIN = 12040


def snapshot(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    return db


def lookup(db, table, field, value):
    return db.execute(f'SELECT * FROM "{table}" WHERE "{field}" = ?', (value,)).fetchall()


def reserve(registry, target, vehicle):
    name = media_name(vehicle["media_name"])
    alias = media_name(f"EG_{name}")
    key = f'{vehicle["source_game"]}:{name.lower()}'
    with closing(snapshot(target)) as base:
        occupied = {r[0] for r in base.execute("SELECT Id FROM Data_Car UNION SELECT ModelId FROM Data_Car")}
        if base.execute("SELECT 1 FROM Data_Car WHERE MediaName = ? COLLATE NOCASE", (alias,)).fetchone():
            raise ValueError(f"Target database already has the MediaName {alias}. Use a pristine baseline.")
        body_ids = {r[0] for r in base.execute("SELECT Id FROM Data_CarBody")}
    registry.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(registry, timeout=20)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS identity (
            source_key TEXT PRIMARY KEY,
            source_id INTEGER NOT NULL,
            car_id INTEGER NOT NULL UNIQUE,
            media_name TEXT NOT NULL COLLATE NOCASE UNIQUE
        )""")
        existing = db.execute("SELECT source_id, car_id, media_name FROM identity WHERE source_key=?", (key,)).fetchone()
        if existing:
            source_id, ordinal, prior_alias = existing
            if source_id != vehicle["source_id"] or prior_alias.casefold() != alias.casefold():
                raise ValueError(f"Inconsistent source identity {key}")
            if ordinal in occupied or ordinal * 1000 in body_ids:
                raise ValueError(f"Reserved identity {ordinal} collides with this baseline")
            alias = prior_alias
        else:
            occupied.update(r[0] for r in db.execute("SELECT car_id FROM identity"))
            start = FH1_PILOT_ID_MIN if vehicle["source_game"] == "fh1" else ID_MIN
            ordinal = next((i for i in range(start, ID_MAX + 1) if i not in occupied and i * 1000 not in body_ids), None)
            if ordinal is None:
                raise ValueError("Expanded Garage ID range exhausted")
            db.execute("INSERT INTO identity VALUES (?, ?, ?, ?)", (key, vehicle["source_id"], ordinal, alias))
    return {"car_id": ordinal, "media_name": alias, "body_id": ordinal * 1000}


def catalogue_report(target, rows, identity):
    """Produce a target-schema crosswalk with source keys and explicit unresolved links."""
    source = rows["Data_Car"][0]["record"]
    make = rows["List_CarMake"][0]["record"]
    with closing(snapshot(target)) as db:
        makes = lookup(db, "List_CarMake", "ManufacturerCode", make["ManufacturerCode"])
        report = {}
        for table, records in rows.items():
            fields = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
            if not fields:
                report[table] = {"source_rows": len(records), "absent_in_target": True}
                continue
            values = {k for row in records for k in row["record"]}
            report[table] = {"source_rows": len(records), "shared_field_names": sorted(fields & values),
                             "source_only": sorted(values - fields), "target_only": sorted(fields - values)}
    return {"identity": identity, "source_id": source["Id"],
            "make": {"code": make["ManufacturerCode"], "source_id": make["ID"],
                     "matched_target_ids": [row["ID"] for row in makes],
                     "source_logo": make.get("IconPath"), "source_logo_small": make.get("IconPathLight")},
            "planned_keys": {"Data_Car.Id": identity["car_id"], "Data_Car.ModelId": identity["car_id"],
                             "Data_Car.MediaName": identity["media_name"], "Data_CarBody.Id": identity["body_id"]},
            "table_crosswalk": report,
            "blockers": ["Target-only vehicle columns and physical foreign keys require validated mappings.",
                         "Localization, logos, wheel, drivetrain, engine and upgrade catalogue insertion remain open."]}
