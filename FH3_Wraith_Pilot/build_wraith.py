"""Build an isolated Wraith file payload from local FH3 and FH4 data."""

import argparse
from collections import Counter
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(WORKSPACE), str(WORKSPACE / ".scratch/fh3-format"), str(WORKSPACE / "tools")]
from audit_fh3 import Installation, collision_v27_probe, file_hash, mesh_material_contract
from expanded_garage import fh4_archive
from expanded_garage.fh4_db import snapshot
from expanded_garage.fh4_model import BLOB, HEADER
from expanded_garage.fh4_presentation import EN_PATH, NativeLocalization, presentation_mount, strings_part
from expanded_garage.fm4 import collect_rows
from fh4_carbin import Reader
from fh4_modelbin_geometry import blobs

CAR = 'ROL_Wraith_14'
ORDINAL = 2044
COMPARISON = 2041  # Bentley Continental GTS, FH4 GT catalog and W12 audio reference.
DB = 'media/stripped/gamedbRC.slt'
MANIFEST = 'media/zipmanifest.xml'
AUDIO = 'media/audio/cars/CarEngineEventMapping.xml'
EVENTS = 'media/audio/cars/CarEventMapping.xml'
REFERENCES = {
    'aerophysicsid': 'List_AeroPhysics', 'antiswayphysicsid': 'List_AntiSwayPhysics',
    'frontspringdamperphysicsid': 'List_SpringDamperPhysics',
    'rearspringdamperphysicsid': 'List_SpringDamperPhysics',
    'stockwheelid': 'List_Wheels',
}
GRAPHICS = {9: 'List_UpgradeRearWing', 34: 'List_UpgradeCarBodyFrontBumper',
            41: 'List_UpgradeCarBodyChassisStiffness'}


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def widen_mesh(data):
    records = list(blobs(data))
    if not any(r['tag'] == 'Mesh' for r in records):
        return data, 0
    _, _, _, _, header_end, _, count = HEADER.unpack_from(data)
    out = bytearray(data[:header_end])
    upgraded = 0
    ordered = sorted(range(count), key=lambda i: records[i]['offset'])
    last = header_end
    for i in ordered:
        record = records[i]
        at = HEADER.size + BLOB.size * i
        tag, major, minor, metadata_count, metadata, offset, size, mirror = BLOB.unpack_from(data, at)
        if offset < last or any(data[last:offset]):
            raise ValueError('Unexpected source blob layout or padding')
        raw = data[offset:offset + size]
        last = offset + size
        if record['tag'] == 'Mesh':
            ids, body = mesh_material_contract(raw, (major, minor))
            material, = ids
            if mirror != size:
                raise ValueError('Unexpected Mesh length field')
            raw = struct.pack('<4h', -1, material, -1, -1) + body
            minor = 9
            size = mirror = len(raw)
            upgraded += 1
        alignment = 16 if offset % 16 == 0 else 4 if offset % 4 == 0 else 1
        out.extend(bytes(-len(out) % alignment))
        BLOB.pack_into(out, at, tag, major, minor, metadata_count, metadata, len(out), size, mirror)
        out.extend(raw)
    if any(data[last:]):
        raise ValueError('Unexpected source bundle suffix')
    out.extend(bytes(-len(out) % 4))
    struct.pack_into('<I', out, 12, len(out))
    for old, new in zip(records, blobs(out), strict=True):
        first = data[old['offset']:old['offset'] + old['size']]
        second = out[new['offset']:new['offset'] + new['size']]
        if old['tag'] == 'Mesh':
            ids, body = mesh_material_contract(first, old['version'])
            target_ids, target_body = mesh_material_contract(second, new['version'])
            if target_ids != (-1, ids[0], -1, -1) or target_body != body:
                raise ValueError('Mesh readback differs outside the material field')
        elif first != second:
            raise ValueError(f"Unexpected change to {old['tag']}")
    return bytes(out), upgraded


def source_text(game, table, key):
    with ZipFile(game.media / game.physical('stripped/stringtables/en.zip')) as archive:
        raw = archive.read(game.physical(table + '.str'))
    start, secondary, size, pool, count = struct.unpack_from('<5I', raw, 4)
    split = 24 + 8 * count
    if raw[:4] != b'\0\3\2\0' or start != 12 or secondary != split + pool or size != secondary - 24:
        raise ValueError(f'Unexpected FH3 string table layout {table}')
    return dict(strings_part(raw[24:split], raw[split:secondary]))[int(key[2:]) & 0xffffffff]


def db_rows(source, target, locale, game):
    rows = {t: [x['record'] for x in entries] for t, entries in collect_rows([(source, 'fh3')], ORDINAL).items()}
    rows['CarPartPositions'] = [dict(r) for r in source.execute('SELECT * FROM CarPartPositions WHERE Ordinal=?', (ORDINAL,))]
    rows['Data_Car_Buckets'] = [dict(r) for r in source.execute('SELECT * FROM Data_Car_Buckets WHERE CarID=?', (ORDINAL,))]
    rows['List_UpgradeDrivetrain'] = [r for r in rows['List_UpgradeDrivetrain'] if r['IsStock']]
    for table in ('CarsDropParts', 'CarTrackOffsets', 'CarExceptions'):
        column = {'CarsDropParts': 'Ordinal', 'CarTrackOffsets': 'Id', 'CarExceptions': 'CarID'}[table]
        for row in source.execute(f'SELECT * FROM {table} WHERE {column}=?', (ORDINAL,)):
            rows.setdefault(table, []).append(dict(row))
    sway_ids = {r['AntiSwayPhysicsID'] for table in ('List_UpgradeAntiSwayFront', 'List_UpgradeAntiSwayRear') for r in rows[table]}
    rows['List_AntiSwayPhysics'] = [dict(source.execute('SELECT * FROM List_AntiSwayPhysics WHERE AntiSwayPhysicsID=?', (i,)).fetchone()) for i in sorted(sway_ids)]
    make, = rows['List_CarMake']
    car, = rows['Data_Car']
    for field in ('DisplayName', 'Profile'):
        make[field] = locale.encode('List_CarMake', field, 'ROL', source_text(game, 'list_carmake', make[field]))
    for field in ('DisplayName', 'ModelShort'):
        car[field] = locale.encode('Data_Car', field, CAR, source_text(game, 'data_car', car[field]))
    for field in ('IconPath', 'IconPathLarge'):
        make[field] = make[field].replace('\\RC0\\', f'\\RC{ORDINAL}\\')
    car.update(MakeName=make['DisplayName'], Thumbnail=car['Thumbnail'].replace('\\RC0\\', f'\\RC{ORDINAL}\\'),
               ContentId=0, IsPurchased=1, IsInstalled=1, IsDrivable=1, NotAvailableInAutoshow=0)
    columns = {t: [r[1] for r in target.execute(f'PRAGMA table_info({quote(t)})')] for t in rows}
    remap = {}
    for table, references in rows.items():
        pk = columns[table][0]
        for row in references:
            key = next(k for k in row if k.lower() == pk.lower())
            old = row[key]
            if target.execute(f'SELECT 1 FROM {quote(table)} WHERE {quote(pk)}=?', (old,)).fetchone():
                if table not in REFERENCES.values() and table != 'Combo_Colors':
                    raise ValueError(f'Occupied Wraith identity {table}.{pk}={old}')
                new = target.execute(f'SELECT coalesce(max({quote(pk)}), 0) FROM {quote(table)}').fetchone()[0] + 1
                used = {n for (t, _), n in remap.items() if t == table}
                source_ids = {r[key] for r in references}
                while new in used or new in source_ids:
                    new += 1
                remap[table, old] = new
    added_fields = {}
    for table, records in rows.items():
        for row in records:
            by_case = {k.lower(): v for k, v in row.items()}
            pk = columns[table][0]
            old = by_case[pk.lower()]
            by_case[pk.lower()] = remap.get((table, old), old)
            for column, depends in REFERENCES.items():
                if column in by_case:
                    value = by_case[column]
                    by_case[column] = remap.get((depends, value), value)
            missing = [c for c in columns[table] if c.lower() not in by_case]
            if missing:
                reference = native_fields(target, table, row, missing)
                by_case.update({c.lower(): reference[c] for c in missing})
                added_fields.setdefault(table, []).append({'id': old, 'fields': reference})
            row.clear()
            row.update({c: by_case[c.lower()] for c in columns[table]})
    return rows, added_fields, [{'table': t, 'source': k, 'target': n} for (t, k), n in remap.items()]


def native_fields(target, table, source, fields):
    owners = {'Data_Car': ('Id', COMPARISON), 'CameraOverrides': ('CarID', COMPARISON),
              'List_UpgradeBrakes': ('Ordinal', COMPARISON), 'List_UpgradeSpringDamper': ('Ordinal', COMPARISON),
              'List_UpgradeDrivetrainDriveline': ('DrivetrainID', 0)}
    if table == 'List_AeroPhysics':
        row = target.execute('SELECT * FROM List_AeroPhysics WHERE AeroPhysicsID=?', (source['AeroPhysicsID'],)).fetchone()
    elif table == 'List_SpringDamperPhysics':
        row = target.execute('SELECT * FROM List_SpringDamperPhysics WHERE SuspensionPhysicsTypeID=? ORDER BY ABS(DefRideHeight-?), SpringDamperPhysicsID LIMIT 1',
                             (source['SuspensionPhysicsTypeID'], source['DefRideHeight'])).fetchone()
    else:
        column, key = owners[table]
        if table == 'List_UpgradeDrivetrainDriveline':
            key, = target.execute('SELECT DrivetrainID FROM List_UpgradeDrivetrain WHERE Ordinal=? AND IsStock=1', (COMPARISON,)).fetchone()
        order = ' ORDER BY ABS(IsStock-?), ABS(Level-?), Id' if 'IsStock' in source else ''
        params = (key, source['IsStock'], source['Level']) if order else (key,)
        row = target.execute(f'SELECT * FROM {quote(table)} WHERE {quote(column)}=?{order} LIMIT 1', params).fetchone()
        if row is None and table == 'CameraOverrides':
            row = target.execute('SELECT * FROM CameraOverrides ORDER BY CarID LIMIT 1').fetchone()
    if row is None:
        raise ValueError(f'No FH4 reference row for {table}')
    return {field: row[field] for field in fields}


def sql_insert(db, rows):
    for table, records in rows.items():
        for row in records:
            columns = ','.join(map(quote, row))
            markers = ','.join('?' for _ in row)
            db.execute(f'INSERT INTO {quote(table)} ({columns}) VALUES ({markers})', tuple(row.values()))


def verify_database_delta(db, baseline, rows):
    db.execute('ATTACH DATABASE ? AS original', (baseline.resolve().as_uri() + '?mode=ro',))
    for table, records in rows.items():
        name = quote(table)
        lost = db.execute(f'SELECT 1 FROM (SELECT * FROM original.{name} EXCEPT SELECT * FROM main.{name}) LIMIT 1').fetchone()
        extra, = db.execute(f'SELECT (SELECT count(*) FROM main.{name})-(SELECT count(*) FROM original.{name})').fetchone()
        if lost or extra != len(records):
            raise ValueError(f'Unexpected catalogue changes to {table}')


def verify_texture_paths(members, fh4):
    local = f'game:/media/cars/{CAR.lower()}/'
    textures = set()
    for folder, suffixes in (('media/cars/_library', ('', '_pri_44', '_pri_45')), ('media/_library', ('',))):
        for suffix in suffixes:
            with ZipFile(fh4 / f'{folder}/Textures{suffix}.zip') as archive:
                textures.update(f'game:/{folder}/textures/{name.lower()}' for name in archive.namelist())
    refs = {match.decode('ascii').lower().replace('\\', '/')
            for name, data in members.items() if name.endswith(('.carbin', '.modelbin'))
            for match in re.findall(rb'game:[\\/][a-z0-9_./\\-]+?\.swatchbin', data, re.I)}
    available = textures | {local + name for name in members}
    missing = refs - available
    if missing:
        raise ValueError(f'Unresolved serialized swatch paths: {sorted(missing)}')
    return len(refs)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def crypto_run(tool, *args):
    result = subprocess.run([tool, *map(str, args)], capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError(result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))


def append_xml(base, ending, entry):
    if base.count(ending) != 1:
        raise ValueError(f'Unexpected XML closing element {ending!r}')
    out = base.replace(ending, b'    ' + entry + b'\r\n' + ending)
    if len(ET.fromstring(out)) != len(ET.fromstring(base)) + 1:
        raise ValueError('XML append did not round-trip')
    return out


def build(args):
    out = args.output.resolve()
    if not out.is_relative_to(Path(__file__).resolve().parent) or out.exists():
        raise ValueError('Expected a new output directory in the isolated Wraith pilot')
    work, files = out / 'work', out / 'files'
    work.mkdir(parents=True)
    fh3 = Installation(args.fh3, True)
    fh4, crypto = args.fh4.resolve(strict=True), args.crypto.resolve(strict=True)
    preimages = {name: file_hash(fh4 / name) for name in (DB, MANIFEST, EN_PATH, AUDIO, EVENTS)}
    database = work / 'fh4-base.sqlite'
    manifest = work / 'fh4-base.xml'
    crypto_run(crypto, 'decrypt', fh4 / DB, '-o', database)
    crypto_run(crypto, 'decrypt', fh4 / MANIFEST, '-o', manifest)
    with closing(snapshot(database)) as target, closing(snapshot(args.fh3_db)) as source:
        locale = NativeLocalization(target, fh4 / EN_PATH)
        rows, fields, remap = db_rows(source, target, locale, fh3)
        english = locale.write(files)
        staged = work / 'wraith.sqlite'
        with closing(sqlite3.connect(staged.resolve().as_uri(), uri=True)) as db:
            target.backup(db)
            with db:
                sql_insert(db, rows)
            if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise ValueError('FH4 database failed integrity check')
            verify_database_delta(db, database, rows)
    car_zip = fh3.car_path(CAR)
    members, converted = {}, Counter()
    with ZipFile(car_zip) as archive:
        for name, entry in fh3.members(archive).items():
            raw = archive.read(entry)
            if name.endswith('.modelbin'):
                raw, count = widen_mesh(raw)
                converted['Mesh'] += count
            if name == 'physicsdefinition.bin':
                raw = bytes(collision_v27_probe(raw))
            members[name] = raw
    reader = Reader(members[CAR.lower() + '.carbin'])
    scene = reader.scene()
    if scene['ordinal'] != ORDINAL:
        raise ValueError('Carbin identity mismatch')
    for group in scene['groups']:
        ids = {r['Id'] for r in rows[GRAPHICS[group['type']]]}
        used = {item['id'] for item in group['upgrades']} | {i['id'] for part in group['shared'] for i in part['ids']}
        if not used <= ids:
            raise ValueError(f'Unresolved graphics group {group["type"]}: {used - ids}')
    for _, value in reader.strings:
        prefix = f'game:\\media\\cars\\{CAR.lower()}\\'
        if value.lower().startswith(prefix) and value.lower().removeprefix(prefix).replace('\\', '/') not in members:
            raise ValueError(f'Unresolved scene asset {value}')
    texture_paths = verify_texture_paths(members, fh4)
    payload, directory = fh4_archive.pack(members)
    write(files / f'media/cars/{CAR}.zip', payload)
    mounted = fh4_archive.add_mount(manifest.read_bytes(), CAR, directory)
    art = []
    with ZipFile(fh3.media / fh3.physical('stripped/rc0.zip')) as archive:
        original = fh3.members(archive)
        variants = ((False, ('Manu_Logo_ROL.swatchbin', 'L_Manu_Logo_ROL.swatchbin', f'Thumbnail_{ORDINAL}_Small.swatchbin')),
                    (True, (f'Thumbnail_{ORDINAL}_Big.swatchbin',)))
        for hires, names in variants:
            content = {name: archive.read(original[name.lower()]) for name in names}
            relative = f'media/stripped/RC{ORDINAL}{"HiRes" if hires else ""}.zip'
            data, layout = fh4_archive.pack(content)
            write(files / relative, data)
            art.append({'kind': 'thumbnail_archive', 'hires': hires, 'path': relative, 'directory': vars(layout)})
    mounted = presentation_mount(mounted, {'english': english, 'art': art})
    write(work / 'wraith-manifest.xml', mounted)
    for relative, plain, control in ((DB, staged, database), (MANIFEST, work / 'wraith-manifest.xml', manifest)):
        wrapped = files / relative
        wrapped.parent.mkdir(parents=True, exist_ok=True)
        crypto_run(crypto, 'encrypt', plain, '--original', fh4 / relative, '-o', wrapped)
        check = work / (control.name + '.roundtrip')
        crypto_run(crypto, 'decrypt', wrapped, '-o', check)
        if file_hash(plain) != file_hash(check):
            raise ValueError(f'Wrapped {relative} failed roundtrip')
    write(files / AUDIO, append_xml((fh4 / AUDIO).read_bytes(), b'</Engines>',
                                   b'<Engine mediaName="ROL_Wraith_14" engineName="BEN_ContinentalGTS_13" />'))
    existing = ET.parse(fh4 / EVENTS).getroot()
    event, = [e for e in existing if e.get('ordinal') == str(COMPARISON)]
    event.attrib.update(friendlyName=CAR, ordinal=str(ORDINAL))
    write(files / EVENTS, append_xml((fh4 / EVENTS).read_bytes(), b'</CarEvents>',
                                    ET.tostring(event, encoding='ascii', xml_declaration=False)))
    source_bank = fh3.media / fh3.physical(f'audio/fmodopus/x64/PG_{CAR}.bank')
    write(out / 'source-only' / f'PG_{CAR}.bank', source_bank.read_bytes())
    if any(file_hash(fh4 / name) != digest for name, digest in preimages.items()):
        raise ValueError('FH4 inputs changed while building')
    if any((fh4 / p.relative_to(files)).exists() for p in files.rglob('*')
           if p.is_file() and p.relative_to(files).as_posix() not in preimages):
        raise ValueError('A new Wraith file path is already occupied in FH4')
    receipt = {'state': 'offline candidate', 'source_archive': file_hash(car_zip), 'car_id': ORDINAL,
               'archive_members': len(members), 'converted_records': dict(converted), 'record_counts': {t: len(r) for t, r in rows.items()},
               'serialized_swatch_paths_resolved': texture_paths,
               'native_fields': fields, 'id_remap': remap, 'files': [
                   {'path': p.relative_to(files).as_posix(), 'sha256': file_hash(p), 'preimage': preimages.get(p.relative_to(files).as_posix())}
                   for p in sorted(files.rglob('*')) if p.is_file()]}
    write(out / 'receipt.json', (json.dumps(receipt, indent=2) + '\n').encode())
    print(f'{len(members)} members, {converted["Mesh"]} Mesh records, {len(receipt["files"])} files in {files}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('output', type=Path)
    ap.add_argument('--fh3', type=Path, default=Path(r'G:\!Games\Forza Horizon 3'))
    ap.add_argument('--fh4', type=Path, default=WORKSPACE / 'game/ForzaHorizon4')
    ap.add_argument('--fh3-db', type=Path, default=WORKSPACE / '.scratch/fh3-format/fh3.sqlite')
    ap.add_argument('--crypto', type=Path, default=WORKSPACE / '.scratch/phase1/ForzaCryptoTool.exe')
    build(ap.parse_args())


if __name__ == '__main__':
    main()
