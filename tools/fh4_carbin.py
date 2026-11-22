"""Read the FH4 Horizon v5 Carbin layout and audit serialized upgrade bindings."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
from zipfile import ZipFile


CATALOGS = {
    2: ("List_UpgradeCarBody", "Ordinal"),
    9: ("List_UpgradeRearWing", "Ordinal"),
    34: ("List_UpgradeCarBodyFrontBumper", "CarBodyID"),
    35: ("List_UpgradeCarBodyRearBumper", "CarBodyID"),
    36: ("List_UpgradeCarBodyHood", "CarBodyID"),
    37: ("List_UpgradeCarBodySideSkirt", "CarBodyID"),
    41: ("List_UpgradeCarBodyChassisStiffness", "CarBodyID"),
}


class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.model_versions = Counter()
        self.ao_references = []
        self.drop_references = []
        self.material_flags = []
        self.model_values = []
        self.strings = []

    def take(self, length):
        end = self.pos + length
        if length < 0 or end > len(self.data):
            raise ValueError(f"Invalid length {length} at 0x{self.pos:x}")
        result = self.data[self.pos:end]
        self.pos = end
        return result

    def number(self, fmt):
        fmt = "<" + fmt
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def count(self):
        count = self.number("I")
        if count > len(self.data) - self.pos:
            raise ValueError(f"Invalid element count at 0x{self.pos-4:x}")
        return count

    def string(self):
        size = self.count()
        offset = self.pos
        value = self.take(size).decode("utf-8")
        self.strings.append((offset, value))
        return value

    def model(self):
        version = self.number("H")
        if version not in range(1, 19):
            raise ValueError(f"Unsupported Horizon model version {version}")
        self.model_versions[version] += 1
        path = self.string()
        self.take(64)
        lod = self.number("H" if version >= 5 else "I")
        self.string()  # bone name
        self.take(3)
        draw = self.number("I")
        if version < 9:
            self.string()
        if version >= 2:
            for _ in range(self.count()):
                self.string()
                self.take(self.count())
        if version >= 3:
            # Per-MatI colour-slot flags (carpaint 1, livery 2, window glass 128, ...).
            for _ in range(self.count()):
                name = self.string()
                self.material_flags.append({"model": path, "material": name,
                                            "flags": struct.unpack("<I", self.take(4))[0]})
        if version >= 6 and self.number("B"):
            self.take(4)
            self.drop_references.append({"offset": self.pos, "id": self.number("i"), "model": path})
        if version >= 8:
            self.model_values.append({"model": path, "draw": draw, "value": self.number("f")})
        if version >= 9:
            for _ in range(self.count()):
                ao_version = self.number("H")
                if ao_version not in (1, 2, 3):
                    raise ValueError(f"Unsupported AO map version {ao_version}")
                ao_path = self.string()
                part_type = self.number("I")
                self.ao_references.append({"offset": self.pos, "id": self.number("i"),
                                           "type": part_type, "model": path, "path": ao_path})
                self.take((16 if ao_version >= 2 else 3) + 1 + (2 if ao_version >= 3 else 0))
        if version >= 10:
            self.take(1)
        if version >= 11:
            self.take(18)
        if version >= 12:
            self.model_values[-1]["category"] = self.string() if version >= 8 else self.string()
        if version >= 13:
            self.take(16)
        if version >= 14:
            self.take(20)
        if version >= 15:
            self.take(4)
        if version >= 16:
            self.take(16 * self.count())
        if version >= 17:
            self.take(1)
        if version >= 18:
            self.take(4)
        return {"path": path, "lod": lod, "draw": draw}

    def scene(self):
        version = self.number("H")
        if version != 5:
            raise ValueError(f"Expected FH4 Horizon scene v5, got {version}")
        self.take(17)
        ordinal_offset = self.pos
        ordinal = self.number("I")
        media_name = self.string()
        skeleton = self.string()
        self.take(2)
        fixed = []
        for _ in range(self.count()):
            part_type, part_version, inner_type = (self.number(fmt) for fmt in ("B", "H", "I"))
            if part_version not in (1, 2) or part_type != inner_type:
                raise ValueError(f"Unsupported fixed part at 0x{self.pos:x}")
            fixed.append({"type": part_type, "models": [self.model() for _ in range(self.count())]})
            if part_version == 2:
                self.take(32)
        groups = []
        for _ in range(self.count()):
            part_version, part_type = self.number("H"), self.number("I")
            if part_version not in (1, 2, 3):
                raise ValueError(f"Unsupported upgrade group version {part_version}")
            group = {"type": part_type, "version": part_version, "count_offset": self.pos,
                     "upgrades": [], "shared": []}
            for _ in range(self.count()):
                start = self.pos
                up_version = self.number("H")
                if up_version not in (1, 2, 3):
                    raise ValueError(f"Unsupported upgrade version {up_version}")
                level, stock = self.number("B"), bool(self.number("B"))
                upgrade_id, parent_id = self.number("i"), self.number("i")
                parent_stock = bool(self.number("B"))
                models = [self.model() for _ in range(self.count())] if up_version < 3 else []
                if up_version >= 2:
                    self.take(32)
                group["upgrades"].append(dict(
                    offset=start, end=self.pos, id_offset=start+4, parent_offset=start+8,
                    id=upgrade_id, parent=parent_id, level=level, stock=stock,
                    parent_stock=parent_stock, models=models,
                ))
            if part_version == 3:
                for _ in range(self.count()):
                    ids = []
                    for _ in range(self.count()):
                        ids.append({"offset": self.pos, "id": self.number("i")})
                    group["shared"].append({"ids": ids, "model": self.model()})
            groups.append(group)
        if self.pos != len(self.data):
            raise ValueError(f"Unparsed scene tail at 0x{self.pos:x}")
        return dict(version=version, ordinal=ordinal, ordinal_offset=ordinal_offset,
                    media_name=media_name, skeleton=skeleton, fixed=fixed, groups=groups,
                    ao_references=self.ao_references, drop_references=self.drop_references,
                    model_versions=self.model_versions, bytes=self.pos,
                    sha256=hashlib.sha256(self.data).hexdigest())


def read_scene(path, member=None):
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            files = [name for name in archive.namelist() if name.lower().endswith(".carbin")]
            if member is None and len(files) != 1:
                raise ValueError("Archive must have exactly one Carbin or specify --member")
            data = archive.read(member if member is not None else files[0])
    else:
        data = path.read_bytes()
    return data, Reader(data).scene()


def audit(db, car_id, scene):
    car = db.execute("SELECT MediaName FROM Data_Car WHERE Id=?", (car_id,)).fetchone()
    if car is None:
        raise ValueError(f"No Data_Car row for {car_id}")
    bodies = {row[0] for row in db.execute(
        "SELECT CarBodyID FROM List_UpgradeCarBody WHERE Ordinal=?", (car_id,)
    )}
    results = {}
    for part_type, (table, key) in CATALOGS.items():
        scope = bodies if key == "CarBodyID" else {car_id}
        rows = [dict(zip(("id", "level", "stock", "graphics", "parent"), row))
                for value in sorted(scope) for row in db.execute(
                    f"SELECT Id,Level,IsStock,RequiresGraphics,{key} FROM {table} WHERE {key}=? ORDER BY Id",
                    (value,),
                )]
        groups = [group for group in scene["groups"] if group["type"] == part_type]
        nodes = [node for group in groups for node in group["upgrades"]]
        fixed = [model["path"] for part in scene["fixed"] if part["type"] == part_type
                 for model in part["models"]]
        for row in rows:
            matches = [node for node in nodes if node["id"] == row["id"]
                       and node["level"] == row["level"] and node["stock"] == bool(row["stock"])
                       and (key != "CarBodyID" or node["parent"] == row["parent"])]
            row["nodes"] = [node["id_offset"] for node in matches]
            row["models"] = [model["path"] for node in matches for model in node["models"]]
            if matches:
                row["models"].extend(shared["model"]["path"] for group in groups
                                     for shared in group["shared"]
                                     if any(ref["id"] == row["id"] for ref in shared["ids"]))
            row["fixed_stock"] = bool(row["stock"] and fixed and not nodes)
        results[part_type] = {"table": table, "fixed_models": fixed, "rows": rows,
                              "unmatched_graphics_ids": [row["id"] for row in rows
                                                         if row["graphics"] and not (
                                                             row["models"] or row["fixed_stock"])]}
    return {"car_id": car_id, "scene_ordinal_matches": car_id == scene["ordinal"],
            "media_matches": car[0].casefold() == scene["media_name"].casefold(),
            "catalogs": results}


def main():
    from fh4_gamedb import read_only

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("carbin", type=Path)
    parser.add_argument("--member")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--id", type=int, action="append", default=[])
    args = parser.parse_args()
    if bool(args.db) != bool(args.id):
        parser.error("--db and --id must be supplied together")
    try:
        _, scene = read_scene(args.carbin, args.member)
        if args.db:
            with read_only(args.db) as db:
                scene["bindings"] = [audit(db, car_id, scene) for car_id in args.id]
        print(json.dumps(scene, indent=2))
    except (OSError, ValueError, KeyError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
