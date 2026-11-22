"""Reproduce the v1.2 FM4/FH4 cohort and stock Granny observations."""

import argparse
from collections import Counter
from contextlib import closing
from hashlib import sha256
import math
from pathlib import Path
from statistics import median
import struct
import subprocess
import sys
from zipfile import ZipFile

from expanded_garage.fh4 import previous_readers
from expanded_garage.fh4_db import snapshot
from expanded_garage.fh4_dynamics import STEERING, TARGET_FIELDS
from expanded_garage.fh4_physics import catalogue
from expanded_garage.fh4_runtime_rig import BONE_SIZE, HEADER_SIZE, MAGIC, TYPE
from expanded_garage.vehicle_ir import sha256 as file_hash, write_json


GRANNY_READER = "aec91bbd8e244d82277d1f5435dc20feff3086f9"


def stock_transmission(db, car_id):
    return db.execute(
        "SELECT t.*, d.* FROM List_UpgradeDrivetrainTransmission t "
        "JOIN List_UpgradeDrivetrain u ON t.DrivetrainId=u.DrivetrainId "
        "JOIN Data_Drivetrain d ON d.DrivetrainId=t.DrivetrainId "
        "WHERE u.Ordinal=? AND u.IsStock=1 AND t.IsStock=1", (car_id,)).fetchone()


def numeric_error(samples):
    valid = [(a, b) for a, b in samples if all(
        isinstance(v, (int, float)) and math.isfinite(v) for v in (a, b))]
    return {"pairs": len(valid), "identical": sum(abs(a-b) < 1e-5 for a, b in valid),
            "median_absolute_difference": median(abs(a-b) for a, b in valid) if valid else None}


def dynamics_cohort(source, target):
    with closing(snapshot(source)) as fm4, closing(snapshot(target)) as fh4:
        before, after = catalogue(fm4), catalogue(fh4)
        keys = sorted(before.keys() & after.keys())
        shifts = Counter()
        stock_ratios, final, tires = [], [], Counter()
        for key in keys:
            source_id, target_id = before[key]["Id"], after[key]["Id"]
            st, tt = stock_transmission(fm4, source_id), stock_transmission(fh4, target_id)
            if st and tt:
                shifts[st["GearShiftTime"], tt["ShiftSystemID"]] += 1
                stock_ratios.append((st["NumGears"], tt["NumGears"]))
                final.append((st["FinalDriveRatio"], tt["FinalDriveRatio"]))
            compound = fm4.execute("SELECT t.DisplayName FROM List_UpgradeTireCompound u "
                                   "JOIN List_TireCompound t USING (TireCompoundID) "
                                   "WHERE u.Ordinal=? AND u.IsStock=1", (source_id,)).fetchone()
            native = fh4.execute("SELECT t.DisplayName,u.TireModelName,u.WetTireModelName,u.SnowTireModelName "
                                  "FROM List_UpgradeTireCompound u JOIN List_TireCompound t USING (TireCompoundID) "
                                  "WHERE u.Ordinal=? AND u.IsStock=1", (target_id,)).fetchone()
            if compound and native:
                tires[compound[0], *tuple(native)] += 1
        steering = {field: numeric_error((before[key][field], after[key][field]) for key in keys) for field in STEERING}
        target_only = {}
        for table, fields in TARGET_FIELDS.items():
            for field, classification in fields.items():
                observed = fh4.execute(f'SELECT "{field}", count(*) AS n FROM "{table}" GROUP BY 1 '
                                       f'ORDER BY n DESC, "{field}" LIMIT 4').fetchall()
                target_only[f"{table}.{field}"] = {"classification": classification, "common_native_values": [
                    {"value": v, "rows": count} for v, count in observed]}
    return {"source_sha256": file_hash(source), "target_sha256": file_hash(target), "pair_count": len(keys),
            "pair_rule": "Exact unique media name and year with one stock body per catalogue",
            "shift_time_to_target_types": [{"fm4_time": time, "target_type": key, "count": count}
                                           for (time, key), count in sorted(shifts.items(), key=repr)],
            "gear_counts": numeric_error(stock_ratios), "final_drive": numeric_error(final), "steering": steering,
            "target_only": target_only, "tire_cohort": [{"source_family": source_label, "target_family": label,
                                                      "dry": dry, "wet": wet, "snow": snow, "count": count}
                                                     for (source_label, label, dry, wet, snow), count
                                                     in sorted(tires.items(), key=repr)]}


class NativeGranny:
    def __init__(self, data, read_bytes, load_sections):
        if data[:16] != MAGIC or struct.unpack_from("<I", data, 32)[0] != 7:
            raise ValueError("Expected stock 64-bit little-endian Granny v7")
        import zlib
        if struct.unpack_from("<II", data, 36) != (len(data), zlib.crc32(data[HEADER_SIZE:])):
            raise ValueError("Stock Granny bytes fail v7 checksum")
        loaded = load_sections(read_bytes(data))
        self.header = loaded.gr2.header
        self.sections = loaded.sections_original
        self.fixups = {(f.source_section, f.source_offset): (f.target.section, f.target.offset)
                       for f in loaded.pointer_fixups}
        if len(self.fixups) != sum(sect.pointer_fixup_count for sect in loaded.gr2.sections):
            raise ValueError("Unexpected incompleteness in stock Granny fixups")
        self.section_types = [sect.compression for sect in loaded.gr2.sections]

    def read(self, fmt, address):
        return struct.unpack_from(fmt, self.sections[address[0]], address[1])

    def ptr(self, section, address):
        return self.fixups.get((section, address))

    def text(self, address):
        if address is None:
            return None
        raw = self.sections[address[0]]
        return raw[address[1]:raw.index(0, address[1])].decode("ascii", "replace")

    def declaration(self, address):
        section, offset = address
        fields = []
        while True:
            kind, _, _, width, *_ = TYPE.unpack_from(self.sections[section], offset)
            if not kind:
                return fields
            fields.append((kind, self.text(self.ptr(section, offset + 4)), width, self.ptr(section, offset + 12)))
            offset += TYPE.size

    def array(self, section, offset, stride, references):
        count, = self.read("<I", (section, offset))
        dest = self.ptr(section, offset + 4)
        if count > 65535 or count and not dest:
            raise ValueError("Bad Granny array")
        if not count:
            return []
        s, first = dest
        return [(self.ptr(s, first + i * stride) if references else (s, first + i * stride)) for i in range(count)]

    def skeleton(self):
        section, root = self.header.root_object
        [(s, offset)] = self.array(section, root + 48, 8, True)
        bones = []
        for b, n in self.array(s, offset + 8, BONE_SIZE, False):
            parent, flags, x, y, z = self.read("<iI3f", (b, n + 8))
            bones.append({"name": self.text(self.ptr(b, n)), "parent": parent, "flags": flags,
                          "local_m": [x, y, z], "inverse_translation_m": list(self.read("<3f", (b, n + 128)))})
        return bones

    def gsf(self, names):
        section, root = self.header.root_object
        state_type = self.ptr(section, root + 4)
        state_object = self.ptr(section, root + 12)
        references = []
        slots = []
        sets = []
        for s, offset in self.array(section, root + 20, 8, True):
            slots.append({"name": self.text(self.ptr(s, offset)), "index": self.read("<i", (s, offset + 8))[0]})
        for s, offset in self.array(section, root + 32, 8, True):
            paths = []
            for r, start in self.array(s, offset + 8, 8, True):
                path = self.text(self.ptr(r, start))
                expected, checksum = self.read("<II", (r, start + 8))
                relative = path.replace("\\", "/").removeprefix("./").casefold() if path else None
                paths.append({"path": path, "expected_count": expected, "crc": checksum})
                if relative:
                    references.append({"relative": relative, "owned_clip": "animations/" + relative in names})
            specs = [{"expected_name": self.text(self.ptr(q, start + 12))}
                     for q, start in self.array(s, offset + 20, 20, False)]
            sets.append({"name": self.text(self.ptr(s, offset)), "source_files": paths, "animation_specs": specs})
        return {"slot_count": len(slots), "slots": slots, "animation_sets": sets,
                "unique_tokens": self.read("<I", (section, root))[0], "state_object_present": state_object is not None,
                "state_type": [f[1] for f in self.declaration(state_type)] if state_type else [],
                "clip_dependencies": references}


def rig_cohort(cars, decoder):
    revision = subprocess.check_output(["git", "-C", str(decoder), "rev-parse", "HEAD"], text=True).strip()
    if revision != GRANNY_READER:
        raise ValueError(f"Expected pinned Blender Granny reader {GRANNY_READER}")
    sys.path.insert(0, str(decoder))
    from io_scene_gr2.gr2.file import read_gr2_bytes
    from io_scene_gr2.gr2.fixup import load_sections

    reader, _ = previous_readers()

    class SceneReader(reader):
        def model(self):
            start = self.pos
            model = super().model()
            end = self.pos
            self.pos = start
            version = self.number("H")
            self.string()
            matrix = struct.unpack("<16f", self.take(64))
            self.take(2 if version >= 5 else 4)
            model.update(placement_m=list(matrix[12:15]), bone_name=self.string(), bone_index=self.number("h"))
            self.pos = end
            return model

    reports = []
    for path in cars:
        with ZipFile(path) as archive:
            names = {n.casefold(): n for n in archive.namelist()}
            scene_name, = (n for n in names if "/" not in n and n.endswith(".carbin"))
            scene = SceneReader(archive.read(names[scene_name])).scene()
            wheel_models = [m for p in scene["fixed"] for m in p["models"]
                            if m["bone_name"].casefold().startswith(("hub", "spindle"))]
            records = {}
            for n, name in names.items():
                if not n.endswith((".gsf", "_skeleton.gr2")):
                    continue
                raw = archive.read(name)
                stock = NativeGranny(raw, read_gr2_bytes, load_sections)
                rig = {"member": name, "sha256": sha256(raw).hexdigest(), "type_tag": hex(stock.header.type_tag),
                       "compression_types": stock.section_types,
                       "root_fields": [line[1] for line in stock.declaration(stock.header.root_type)]}
                if n.endswith(".gsf"):
                    rig.update(stock.gsf(names))
                    records["gsf"] = rig
                else:
                    bones = stock.skeleton()
                    rig.update(bone_count=len(bones), bones=[b for b in bones if b["name"].startswith((
                        "hub", "spindle", "boneSteeringWheel", "boneClutch")) or b["name"] in ("root", "GrannyRootBone")])
                    records["gr2"] = rig
            reports.append({"archive": path.name, "sha256": file_hash(path), "id": scene["ordinal"],
                            "carbin_wheel_placements": wheel_models, **records})
    return {"decoder_revision": revision, "stock_vehicles": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    dynamics = commands.add_parser("dynamics")
    dynamics.add_argument("--fm4-db", type=Path, required=True)
    dynamics.add_argument("--fh4-db", type=Path, required=True)
    rigs = commands.add_parser("rigs")
    rigs.add_argument("--decoder", type=Path, required=True)
    rigs.add_argument("--archive", type=Path, action="append", required=True)
    for command in (dynamics, rigs):
        command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = (dynamics_cohort(args.fm4_db, args.fh4_db) if args.action == "dynamics"
              else rig_cohort(args.archive, args.decoder))
    write_json(args.out, result)


if __name__ == "__main__":
    main()
