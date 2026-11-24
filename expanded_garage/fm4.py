"""Read FM4 disc and DLC resources into a portable Vehicle IR bundle."""

from contextlib import ExitStack, closing
from dataclasses import dataclass
import importlib
import json
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import numpy as np

from .vehicle_ir import Assets, COORDINATE_SYSTEM, VERSION, media_name, member_path, provenance, sha256, write_json


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_AXES = np.array([1, 1, -1], dtype="<f4")


def source_tools(checkout):
    source = (checkout / "src").resolve()
    if not (source / "dsfforza/formats/carbin.py").is_file():
        raise ValueError(f"driversf-forza source checkout is missing: {source}")
    sys.path.insert(0, str(source))
    try:
        return (importlib.import_module("dsfforza.formats.forzazip").ForzaZip,
                importlib.import_module("dsfforza.formats.carbin"),
                importlib.import_module("dsfforza.formats.forza_audio").parse_fsb)
    finally:
        sys.path.remove(str(source))


def child(root, *parts):
    for part in parts:
        if not root.is_dir():
            return None
        exact = root / part
        matches = [p for p in root.iterdir() if p.name.casefold() == part.casefold()]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous source path {exact}")
        if not matches:
            return None
        root = matches[0]
    return root


def media_root(path):
    root = child(path, "Media") or path
    if not child(root, "db", "gamedb.slt"):
        raise ValueError(f"Expected an extracted FM4 disc with Media/db/gamedb.slt: {path}")
    return root


@dataclass(frozen=True)
class Entry:
    container: Path
    name: str
    read: object


def archive_entries(stack, reader, path):
    archive = reader(path)
    stack.callback(archive.zip.close)
    return {key: Entry(path, info.filename, lambda i=info: archive.read(i))
            for key, info in archive_index(archive.zip).items()}


def archive_index(archive):
    entries = {}
    for item in archive.infolist():
        if item.is_dir():
            continue
        name = member_path(item.filename).casefold()
        if name in entries:
            raise ValueError(f"Case-insensitive duplicate in {archive.filename}: {name}")
        entries[name] = item
    return entries


def loose_entries(folder, prefix=""):
    if not folder:
        return {}
    entries = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            relative = prefix + path.relative_to(folder).as_posix()
            name = member_path(relative).casefold()
            if name in entries:
                raise ValueError(f"Case-insensitive duplicate in {folder}: {relative}")
            entries[name] = Entry(path, relative, path.read_bytes)
    return entries


def dlc_entries(stack, reader, paths, stfs_tool):
    for path in paths:
        if path.is_file() and path.suffix.lower() != ".zip":
            with path.open("rb") as stream:
                header = stream.read(0x364)
            if header[:4] not in (b"LIVE", b"PIRS", b"CON ") or header[0x360:0x364] != bytes.fromhex("4d530910"):
                raise ValueError(f"Expected a local FM4 DLC STFS container: {path}")
            if len(header) < 0x364 or header[0x344:0x348] != bytes.fromhex("00000002"):
                raise ValueError(f"Unexpected FM4 STFS content type: {path}")
            if stfs_tool is None:
                raise ValueError("Supply --stfs-tool for raw FM4 DLC containers")
            folder = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="garage-stfs-"))) / "content"
            subprocess.run(["pwsh", "-NoProfile", "-File", str(stfs_tool),
                            "-Path", str(path), "-OutputDir", str(folder)], check=True)
            for mounted in dlc_entries(stack, reader, [folder], stfs_tool):
                yield {key: Entry(path, f"{entry.container.relative_to(folder).as_posix()}!{entry.name}",
                                  entry.read) for key, entry in mounted.items()}
        elif path.is_file():
            yield archive_entries(stack, reader, path)
        elif path.is_dir():
            media = child(path, "media") or path
            payloads = child(media, "DLCZips") or media
            archives = sorted(p for p in payloads.iterdir() if p.is_file()
                              and re.search(r"_pri_\d+\.zip$", p.name, re.I))
            for pack in archives:
                entries = archive_entries(stack, reader, pack)
                if pack.name.casefold().startswith("stringtables_"):
                    entries = {f"media/stringtables/{k}": v for k, v in entries.items()}
                elif pack.name.casefold().startswith("carmodeltuning_"):
                    entries = {f"media/audio/cars/carmodeltuning/{k}": v for k, v in entries.items()}
                yield entries
                sidecar = child(payloads, pack.stem, "Media")
                if sidecar:
                    yield loose_entries(sidecar, "media/")
            if child(media, "cars") or child(media, "db", "patch"):
                yield loose_entries(media, "media/")
            elif archives:
                yield loose_entries(child(media, "audio"), "media/audio/")
            else:
                raise ValueError(f"No FM4 DLC payload discovered in {path}")
        else:
            raise ValueError(f"Missing FM4 DLC input {path}")


def query(db, table, column, value):
    return db.execute(f'SELECT * FROM "{table}" WHERE "{column}" = ?', (value,)).fetchall()


def open_db(stack, path):
    db = stack.enter_context(closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)))
    db.row_factory = sqlite3.Row
    return db


def json_row(row):
    return {key: {"hex": value.hex()} if isinstance(value, bytes) else value for key, value in dict(row).items()}


def collect_rows(databases, car_id):
    """Materialize the known stock graph, resolving specific rows over ordered DLC patches."""
    tables_by_db = [(db, dig, {name: [row[1] for row in db.execute(f'PRAGMA table_info("{name}")')]
                              for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                              if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)})
                    for db, dig in databases]
    tables = {t for _, _, schema in tables_by_db for t in schema}
    result = {}

    def select(table, column, value):
        found = {}
        for db, digest, schema in tables_by_db:
            cols = schema.get(table, [])
            matches = [c for c in cols if c.casefold() == column.casefold()]
            if matches:
                for row in query(db, table, matches[0], value):
                    pk = row[0]
                    found[pk] = {
                        "record": json_row(row),
                        "provenance": provenance(digest, f"{table}:first-column={pk}", "source-native"),
                    }
        items = result.setdefault(table, {})
        items.update(found)
        return [entry["record"] for entry in found.values()]

    def stock(rows):
        return next((r for r in rows if r.get("IsStock") == 1), {})

    car, = select("Data_Car", "Id", car_id)
    select("List_CarMake", "Id", car["MakeID"])
    select("List_Wheels", "Id", car["StockWheelID"])
    select("CameraOverrides", "CarID", car_id)
    if "Combo_Colors" in tables:
        select("Combo_Colors", "Ordinal", car_id)
    for table in sorted(tables):
        if table.startswith("List_Upgrade"):
            select(table, "Ordinal", car_id)
    body = stock(rows_for(result, "List_UpgradeCarBody")).get("CarBodyID")
    engine = stock(rows_for(result, "List_UpgradeEngine")).get("EngineID")
    drive = stock(rows_for(result, "List_UpgradeDrivetrain")).get("DrivetrainID")
    for table, key, value in (("Data_CarBody", "Id", body), ("Data_Engine", "EngineID", engine),
                              ("Data_Drivetrain", "DrivetrainID", drive)):
        if value is not None:
            select(table, key, value)
    select("CarPartPositions", "PartId", body)
    for table in sorted(tables):
        if table.startswith("List_UpgradeCarBody") and body is not None:
            select(table, "CarBodyID", body)
        if table.startswith("List_UpgradeEngine") and engine is not None:
            select(table, "EngineID", engine)
        if table.startswith("List_UpgradeDrivetrain") and drive is not None:
            select(table, "DrivetrainID", drive)
    for rows in list(result.values()):
        for entry in list(rows.values()):
            for column, value in entry["record"].items():
                table = {
                    "torquecurvefullthrottleid": ("List_TorqueCurve", "TorqueCurveID"),
                    "torquecurvedragid": ("List_TorqueCurve", "TorqueCurveID"),
                    "frontspringdamperphysicsid": ("List_SpringDamperPhysics", "SpringDamperPhysicsID"),
                    "rearspringdamperphysicsid": ("List_SpringDamperPhysics", "SpringDamperPhysicsID"),
                    "aerophysicsid": ("List_AeroPhysics", "AeroPhysicsID"),
                    "suspensiondamagemoddefid": ("List_DamageModDef", "ID"),
                    "tiresdamagemoddefid": ("List_DamageModDef", "ID"),
                }.get(column.casefold())
                if table and isinstance(value, int) and value > 0:
                    select(*table, value)
    return {t: list(rows.values()) for t, rows in sorted(result.items()) if rows}


def rows_for(result, table):
    rows = result.get(table, [])
    return [entry["record"] for entry in (rows.values() if isinstance(rows, dict) else rows)]


def assign_role(name):
    if "/wheels/" in name or name.startswith("wheels/"):
        return "wheel"
    stem = Path(name).stem
    for pattern, role in ((r"_caliper[lr][fr]_lod0$", "caliper"), (r"_rotor[lr][fr]_lod0$", "rotor"),
                          (r"_cockpit$", "cockpit"), (r"_lod0$", "exterior-near")):
        if re.search(pattern, stem):
            return role
    return "exterior"


def decode_carbin(root, parser, name, raw, digest):
    parsed = parser.parse_full(raw)
    arrays, components, issues = {}, [], []
    for i, section in enumerate(parsed.sections):
        pools, draws = {}, []
        for key, vertices in (("near", section.lod0_vertices), ("main", section.lod_vertices)):
            if not vertices:
                continue
            prefix = f"s{i}_{key}"
            keys = {attribute: f"{prefix}_{attribute}" for attribute in ("position", "normal", "uv0", "uv1", "native_extra")}
            pools[key] = {"arrays": keys, "count": len(vertices)}
            for attribute, array in keys.items():
                values = [getattr(v, "extra" if attribute == "native_extra" else attribute) for v in vertices]
                arrays[array] = (np.array([list(v) for v in values], dtype="u1") if attribute == "native_extra"
                                 else np.asarray(values, dtype="<f4"))
                if attribute in ("position", "normal"):
                    arrays[array] *= CANONICAL_AXES
        for j, draw in enumerate(section.subsections):
            pool = "near" if draw.lod == 0 else "main"
            if not draw.indices:
                continue
            try:
                indices = np.asarray(draw.triangles(), dtype="<u4").reshape(-1, 3)[:, [0, 2, 1]]
                if indices.size and (pool not in pools or indices.max() >= pools[pool]["count"]):
                    raise ValueError("index outside vertex pool")
            except ValueError as error:
                issues.append(f"{section.name}/{j}: {error}")
                continue
            if not len(indices):
                continue
            key = f"s{i}_draw{j}"
            arrays[key] = indices
            draws.append({"pool": pool, "triangles": key, "count": len(indices),
                          "material": draw.material, "lod": draw.lod,
                          "uv_transform": list(draw.uv_transform), "source_topology": draw.index_type,
                          "provenance": provenance(digest, f"section/{i}/draw/{j}", "index, dimensionless",
                                                   "triangulate native topology",
                                                   "reverse winding for reflected Z axis")})
        box = np.array([section.target_min, section.target_max], dtype="<f4") * CANONICAL_AXES
        components.append({
            "name": section.name, "offset": (section.offset * CANONICAL_AXES).tolist(),
            "bounds_min": box.min(axis=0).tolist(), "bounds_max": box.max(axis=0).tolist(),
            "pools": pools, "draws": draws,
            "provenance": provenance(digest, f"section/{i}", "m, unit vectors, dimensionless, native bytes",
                                     "decode section bounds, SNORM positions, quaternion normals and UNORM UVs",
                                     "FM4 vehicle mesh basis to IR: reflect Z"),
        })
    directory = root / "geometry"
    directory.mkdir(exist_ok=True)
    payload = directory / f"{digest}.npz"
    np.savez_compressed(payload, **arrays)
    return {"raw": digest, "member": name, "role": assign_role(name), "components": components,
            "arrays": payload.relative_to(root).as_posix(), "arrays_sha256": sha256(payload),
            "coverage": {"decoded_sections": len(components), "declared_sections": parsed.declared,
                         "section_table_offset": parsed.start, "parsed_end": parsed.end, "issues": issues},
            "provenance": provenance(digest, "entire member", "source-native")}


def capture(assets, entry, role):
    raw = entry.read()
    return assets.add(raw, entry.container, entry.name, role), raw


def xml_tree(node):
    return {"tag": node.tag, "attributes": dict(node.attrib), "text": (node.text or "").strip(),
            "children": [xml_tree(child) for child in node]}


def parse_xml(assets, entry, role):
    digest, raw = capture(assets, entry, role)
    try:
        node = ET.fromstring(raw.strip())
    except ET.ParseError as error:
        return None, {"issue": str(error), "provenance": provenance(digest, "entire member", "source-native")}
    return node, {"xml": xml_tree(node), "provenance": provenance(digest, "/", "source-native")}


def localization(raw, ref):
    if raw[:4] != b"LSB2" or not isinstance(ref, str) or not re.fullmatch(r"_&\d+", ref):
        return None
    key = int(ref[2:]) & 0xffff
    section, = struct.unpack_from(">I", raw, 20)
    size, count = struct.unpack_from(">II", raw, section)
    table = section + 8
    strings = table + (count + 1) * 6
    end = section + size
    if not table < strings <= end <= len(raw):
        raise ValueError("Invalid FM4 LSB2 text section")
    for index in range(count):
        number, offset = struct.unpack_from(">HI", raw, table + index * 6)
        if number != key:
            continue
        start = strings + 2 * offset
        stop = start
        while stop + 2 <= end and raw[stop:stop+2] != b"\0\0":
            stop += 2
        if not strings <= start <= stop < end:
            raise ValueError("Invalid FM4 LSB2 string range")
        return raw[start:stop].decode("utf-16-be")
    return None


def presentation_assets(stack, reader, base, overrides, rows, assets):
    car = rows_for(rows, "Data_Car")[0]
    makes = rows_for(rows, "List_CarMake")
    make = makes[0] if makes else {}
    strings = {}
    english = child(base, "StringTables", "EN.zip")
    tables = archive_entries(stack, reader, english) if english else {}
    for table, fields in (("data_car.str", ("DisplayName", "ModelShort", "MakeName")),
                          ("list_carmake.str", ("DisplayName", "Profile"))):
        records = car if table == "data_car.str" else make
        for entry in (tables.get(table), overrides.get(f"media/stringtables/en/{table}")):
            if entry is None:
                continue
            digest, raw = capture(assets, entry, "localization")
            for field in fields:
                value = localization(raw, records.get(field))
                if value is not None:
                    strings[f"{table}/{field}"] = {"value": value, "provenance":
                                                  provenance(digest, records[field], "text")}
    textures = child(base, "UI", "Textures.zip")
    icons = archive_entries(stack, reader, textures) if textures else {}
    icons.update({k.removeprefix("media/ui/textures/"): v for k, v in overrides.items()
                  if k.startswith("media/ui/textures/")})
    requested = {Path(make[key].replace("\\", "/")).stem.casefold()
                 for key in ("IconPath", "IconPathLight") if make.get(key)}
    requested.add(f'thumbnail_{car["Id"]}')
    info = []
    for key, entry in sorted(icons.items()):
        if Path(key).stem not in requested:
            continue
        digest, _ = capture(assets, entry, "identity-texture")
        info.append({"name": key, "provenance": provenance(digest, entry.name, "encoded texels")})
    return {"strings_en": strings, "textures": info}


def audio_entries(stack, reader, fsb_parser, base, overrides, name, assets):
    cars = child(base, "audio", "cars")
    engine = child(cars, "Engines") if cars else None
    tuning = {}
    for directory, stem in [(cars, "CarModelTuning"), (engine, "EngineTuning"), (engine, "HarmonicTuning")]:
        if directory:
            path = child(directory, stem + ".zip")
            if path:
                tuning.update({Path(k).name: v for k, v in archive_entries(stack, reader, path).items()})
            tuning.update({Path(k).name: v for k, v in loose_entries(child(directory, stem)).items()})
    tuning.update({Path(k).name: v for k, v in overrides.items() if "/audio/" in k and k.endswith(".xml")})
    banks = loose_entries(child(engine, "soundbanks") if engine else None)
    prefix = "media/audio/cars/engines/soundbanks/"
    banks.update({k.removeprefix(prefix): v for k, v in overrides.items()
                  if k.startswith(prefix) and k.endswith(".fsb")})
    records, missing = [], []
    requests = [name.lower() + suffix for suffix in ("_cmt.xml", "_et.xml")]
    visited = set()
    while requests:
        key = requests.pop(0)
        if key in visited:
            continue
        visited.add(key)
        if key.endswith(".fsb"):
            matches = [entry for path, entry in banks.items() if Path(path).name == key]
            if not matches:
                missing.append(key)
            for entry in matches:
                digest, raw = capture(assets, entry, "audio-bank")
                bank = {"name": key, "provenance": provenance(digest, entry.name, "encoded samples")}
                try:
                    fsb = fsb_parser(raw)
                    end = 48 + fsb.header_bytes + fsb.data_bytes
                    if end > len(raw) or any(s.offset + s.size > end for s in fsb.subsongs):
                        raise ValueError("FSB4 stream exceeds declared bank data")
                    bank["subsongs"] = [
                        {"index": s.index, "name": s.name, "codec": s.codec, "samples": s.samples,
                         "sample_rate_hz": s.rate, "loop_samples": [s.loop_start, s.loop_end],
                         "channels": s.channels, "offset": s.offset, "size": s.size}
                        for s in fsb.subsongs
                    ]
                except (ValueError, struct.error) as error:
                    bank["issue"] = str(error)
                records.append(bank)
            continue
        entry = tuning.get(key)
        if not entry:
            missing.append(key)
            continue
        xml, record = parse_xml(assets, entry, "audio-tuning")
        records.append({"name": key, **record})
        if xml is not None:
            for element in xml.iter():
                for value in [*element.attrib.values(), element.text or ""]:
                    for ref in re.findall(r"[\w.-]+(?:_HT\.xml|\.fsb)\b", value, re.I):
                        requests.append(ref.casefold())
    return {"records": records, "unresolved_names": sorted(set(missing))}


def limits(status, data, *issues):
    return {"status": status, "data": data, "limitations": list(issues)}


# Textures of FM4 cars/Shared.zip that car techniques sample by sampler name (carbin-format.md: grille1/grille2
# and their _alpha variants read Grille1Sampler/Grille2Sampler = grille1_alpha.xds/grille2_alpha.xds).
SHARED_TEXTURES = ("grille1_alpha.xds", "grille2_alpha.xds")
SHARED_PREFIX = "media/cars/shared/"


def shared_entries(stack, reader, base):
    archive = child(base, "cars", "Shared.zip")
    if not archive:
        return {}
    entries = archive_entries(stack, reader, archive)
    return {SHARED_PREFIX + name: entries[name] for name in SHARED_TEXTURES if name in entries}


def add_shared_textures(ir_path, fm4_root, checkout):
    """Add the shared FM4 textures to an IR extracted before they were collected (idempotent)."""
    from .vehicle_ir import Assets, write_json
    base = media_root(fm4_root)
    reader, _, _ = source_tools(checkout)
    ir = json.loads((ir_path / "vehicle.json").read_text(encoding="utf-8"))
    present = {m["name"] for m in ir["raw_members"]}
    assets = Assets(ir_path)
    assets.records = ir["assets"]
    added = []
    with ExitStack() as stack:
        for key, entry in sorted(shared_entries(stack, reader, base).items()):
            if key in present:
                continue
            digest, _ = capture(assets, entry, "vehicle-member")
            ir["raw_members"].append({"name": key, "provenance": provenance(digest, entry.name, "source-native")})
            ir["sections"]["materials"]["data"]["textures"].append(
                {"name": key, "provenance": provenance(digest, entry.name, "encoded texels")})
            added.append(key)
    ir["assets"] = assets.records
    write_json(ir_path / "vehicle.json", ir)
    return added


def add_catalogue_rows(ir_path, table="Combo_Colors", column="Ordinal"):
    """Add a car's rows of `table` to an IR extracted before the table was collected (idempotent).

    The FM4 databases are in the IR already (role `database`, base first, DLC patches after), so the rows come
    from those blobs with the same provenance collect_rows writes. Returns the number of rows added."""
    ir = json.loads((ir_path / "vehicle.json").read_text(encoding="utf-8"))
    tables = ir["sections"]["physics"]["data"]["tables"]
    if tables.get(table):
        return 0
    car_id = ir["vehicle"]["source_id"]
    databases = [digest for digest, record in ir["assets"].items()
                 if any(source["role"] == "database" for source in record["sources"])]
    # Base database first: the order its source entries were captured in (Media/db/gamedb.slt).
    databases.sort(key=lambda d: not any(s["entry"].casefold().endswith("db/gamedb.slt")
                                         for s in ir["assets"][d]["sources"]))
    found = {}
    with tempfile.TemporaryDirectory(prefix="garage-db-") as temp:
        for i, digest in enumerate(databases):
            path = Path(temp) / f"{i}.sqlite"
            path.write_bytes((ir_path / ir["assets"][digest]["path"]).read_bytes())
            with closing(sqlite3.connect(path)) as db:
                db.row_factory = sqlite3.Row
                names = {n for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if table not in names:
                    continue
                for row in query(db, table, column, car_id):
                    found[row[0]] = {"record": json_row(row),
                                     "provenance": provenance(digest, f"{table}:first-column={row[0]}", "source-native")}
    if not found:
        return 0
    tables[table] = list(found.values())
    write_json(ir_path / "vehicle.json", ir)
    return len(found)


def build(base, packs, name, output, checkout, stfs_tool=None):
    base = media_root(base)
    name = media_name(name)
    reader, parser, fsb_parser = source_tools(checkout)
    with ExitStack() as stack:
        assets = Assets(output)
        overrides = {}
        patches = []
        for members in dlc_entries(stack, reader, packs, stfs_tool):
            overrides.update(members)
            patches.extend(v for k, v in sorted(members.items()) if k.endswith("_merge.slt"))
        db_path = child(base, "db", "gamedb.slt")
        temp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="garage-db-")))
        db_sources = [Entry(db_path, "Media/db/gamedb.slt", db_path.read_bytes), *patches]
        databases = []
        car = None
        for i, entry in enumerate(db_sources):
            digest, raw = capture(assets, entry, "database")
            path = temp / f"{i}.sqlite"
            path.write_bytes(raw)
            db = open_db(stack, path)
            databases.append((db, digest))
            row = db.execute("SELECT Id, MediaName FROM Data_Car WHERE MediaName = ? COLLATE NOCASE", (name,)).fetchall()
            if len(row) > 1:
                raise ValueError(f"Ambiguous FM4 database MediaName {name}")
            if row:
                car = row[0]
        if car is None:
            raise ValueError(f"No FM4 Data_Car row for {name}")
        car_id, name = car
        rows = collect_rows(databases, car_id)
        wheel_rows = rows_for(rows, "List_Wheels")
        wheel = wheel_rows[0]["MediaName"] if wheel_rows else None
        collected = {}
        for category, key in (("cars", name), ("wheels", wheel)):
            if not key:
                continue
            media_name(key)
            prefix = f"media/{category}/{key.lower()}/"
            archive = child(base, category, key + ".zip")
            if archive:
                collected.update({prefix + k: v for k, v in archive_entries(stack, reader, archive).items()})
            collected.update({k: v for k, v in overrides.items() if k.startswith(prefix)})
        if not any(k.startswith("media/cars/") for k in collected):
            raise ValueError(f"Missing FM4 car archive for {name}")
        collected.update(shared_entries(stack, reader, base))
        geometry, opaque, xmls, textures, raw_members = [], [], {}, [], []
        for key, entry in sorted(collected.items()):
            digest, raw = capture(assets, entry, "vehicle-member")
            raw_members.append({"name": key, "provenance": provenance(digest, entry.name, "source-native")})
            if key.endswith(".carbin"):
                try:
                    geometry.append(decode_carbin(output, parser, key, raw, digest))
                except (ValueError, OverflowError, struct.error) as error:
                    opaque.append({"name": key, "issue": str(error),
                                   "provenance": provenance(digest, entry.name, "source-native")})
            elif key.endswith(".xml"):
                xmls[key] = parse_xml(assets, entry, "vehicle-xml")[1]
            elif key.endswith((".xds", ".tga")):
                textures.append({"name": key, "provenance": provenance(digest, entry.name, "encoded texels")})
        audio = audio_entries(stack, reader, fsb_parser, base, overrides, name, assets)
        presentation = presentation_assets(stack, reader, base, overrides, rows, assets)
        categories = {
            "geometry": limits("partial" if opaque or any(g["coverage"]["issues"] or
                                g["coverage"]["decoded_sections"] != g["coverage"]["declared_sections"]
                                for g in geometry) else "decoded",
                               {"models": geometry, "opaque_models": opaque},
                               "Native morph tables, tangent frames and packed extra channels remain in the Carbins."),
            "structure": limits("partial", {"wheel_media_name": wheel, "xml": xmls},
                                "Rig, hierarchical pivots, animation controller and locator semantics remain open."),
            "materials": limits("partial", {"textures": textures, "material_binding": "geometry draw material"},
                                "XDS and native shading parameters are preserved for translation."),
            "effects": limits("opaque", {"inputs": "vehicle XML, source textures and audio origin records"},
                              "Physical light positions, presets and FX routing require derivation."),
            "physics": limits("partial", {"tables": rows},
                              "Raw source-native catalogue values and MAXData XML require FH4 unit and field mapping."),
            "audio": limits("partial", audio, "Wave bank, loop and source tuning capture precede FH4 sound synthesis."),
        }
        ir = {"schema_version": VERSION,
              "vehicle": {"source_game": "fm4", "source_id": car_id, "media_name": name},
              "coordinate_system": COORDINATE_SYSTEM,
              "assets": assets.records, "raw_members": raw_members, "presentation": presentation,
              "source_tools": {name: sha256(checkout / f"src/dsfforza/formats/{name}.py")
                               for name in ("carbin", "forzazip", "lzx", "forza_audio")},
              "sections": categories}
        write_json(output / "vehicle.json", ir)
        return ir
