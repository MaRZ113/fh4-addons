"""Build the isolated Phase 3 Acura archive, manifest and 3477 routing database."""

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZipFile
import zlib

from fh4_carbin import Reader, audit
from fh4_gamedb import ROOT, check_integrity, compare, read_only


DONOR = "ACU_IntegraR_01"
ALIAS = "MRZ_IntegraJ_00"
CAR_ID = 3477
BASE_HASHES = (
    "a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c",
    "62bcdeab384e729680fff752a980f541fc46e07a9598a9f6ae4e597471694a37",
    "3063ce9ba39daaebbfdf3bce395cc9a25bf937618c723e090bcd3861f9aa5916",
)
LOCAL_PATH = re.compile(
    rb"game:\\media\\cars\\acu_integrar_01\\([a-z0-9_.\\{}-]+?\.(?:swatchbin|modelbin))",
    re.I,
)
RENAME = {
    f"{DONOR}.carbin", f"{DONOR}.avpins", f"Animations/{DONOR}.gsf", f"UI/{DONOR}.xaml",
}
MATRIX_STEPS = ("routed", "entrypoints", "name", "ordinal", "paths")


def digest(data):
    return sha256(data).hexdigest()


def paths(data):
    return list(LOCAL_PATH.finditer(data))


def retarget_paths(data, names):
    """Change a checked car-local path prefix, keeping all binary field lengths."""
    out = bytearray(data)
    for m in paths(data):
        name = m[1].decode("ascii").replace("\\", "/")
        if name.casefold() not in names:
            raise ValueError(f"Missing source member {name}")
        offset = m.start(1) - len(DONOR) - 1
        out[offset:offset+len(DONOR)] = ALIAS.lower().encode("ascii")
    return bytes(out)


def scene_variant(data, names, change_name=True, change_ordinal=True, change_paths=True):
    reader = Reader(data)
    old = reader.scene()
    if old["ordinal"] != 368 or old["media_name"] != DONOR.lower():
        raise ValueError("Wrong Carbin identity")
    variant = bytearray(data)
    seen = 0
    for offset, text in reader.strings:
        field = text.encode("utf-8")
        if change_name and text == DONOR.lower():
            rewritten = ALIAS.lower().encode("ascii")
        else:
            rewritten = retarget_paths(field, names) if change_paths else field
        seen += len(re.findall(DONOR.encode("ascii"), field, re.I))
        variant[offset:offset+len(field)] = rewritten
    if seen != 159 or seen != len(re.findall(DONOR.encode("ascii"), data, re.I)):
        raise ValueError("Donor name outside the reviewed Carbin fields")
    if change_ordinal:
        struct.pack_into("<I", variant, old["ordinal_offset"], CAR_ID)
    new = Reader(variant).scene()
    if (new["ordinal"] != (CAR_ID if change_ordinal else 368)
            or new["media_name"] != (ALIAS if change_name else DONOR).lower()):
        raise ValueError("Carbin identity readback failed")
    return bytes(variant), new


def deflate_exact(data, size):
    """Pad a byte-aligned non-final stream with valid empty blocks to the exact ZIP size."""
    for level in (9, 8, 7, 6, 5, 4, 3, 2, 1):
        codec = zlib.compressobj(level, wbits=-15)
        exact = codec.compress(data) + codec.flush()
        if len(exact) == size:
            return exact
        for split in (None, min(777, len(data)), min(1514, len(data)), len(data)//2):
            codec = zlib.compressobj(level, wbits=-15)
            if split is None:
                prefix = codec.compress(data) + codec.flush(zlib.Z_SYNC_FLUSH)
            else:
                prefix = (codec.compress(data[:split]) + codec.flush(zlib.Z_BLOCK)
                          + codec.compress(data[split:]) + codec.flush(zlib.Z_SYNC_FLUSH))
            available = size-len(prefix)
            for blocks, tail_size in ((1, 2), (2, 3), (3, 4), (4, 5)):
                if available < tail_size or (available-tail_size) % 5:
                    continue
                tail_bits = 0
                for i in range(blocks):
                    tail_bits |= (2 + (i == blocks-1)) << (i*10)
                tail = b"\0\0\0\xff\xff"*((available-tail_size)//5)
                payload = prefix + tail + tail_bits.to_bytes(tail_size, "little")
                check = zlib.decompressobj(-15)
                if check.decompress(payload) != data or not check.eof or check.unused_data:
                    raise ValueError("Exact-size DEFLATE readback failed")
                return payload
    raise ValueError(f"No {size}-byte DEFLATE fit for {len(data)} bytes")


def compact_deflate(data):
    """Use a high-compression method-8 encoder for members with a tight layout budget."""
    seven_zip = shutil.which("7z")
    if seven_zip is None:
        raise ValueError("7z is required for the fixed-layout alias")
    with tempfile.TemporaryDirectory(dir=ROOT / ".scratch") as path:
        root = Path(path)
        (root / "member.bin").write_bytes(data)
        subprocess.run([seven_zip, "a", "-tzip", "-mm=Deflate", "-mx=9", "-mfb=258",
                        "-mpass=15", "compact.zip", "member.bin"], cwd=root,
                       capture_output=True, check=True)
        blob = (root / "compact.zip").read_bytes()
        with ZipFile(BytesIO(blob)) as archive:
            info = archive.getinfo("member.bin")
            if info.compress_type != 8 or archive.read(info) != data:
                raise ValueError("7z did not produce a valid deflate stream")
            name_len, extra_len = struct.unpack_from("<HH", blob, info.header_offset+26)
            start = info.header_offset+30+name_len+extra_len
            return blob[start:start+info.compress_size]


def archive_variant(source, entrypoint_only=False, preserve_layout=False, matrix_step=None):
    """Build a ZIP32 using the original local/central records and raw unchanged payloads."""
    result = bytearray()
    central = []
    edits = []
    step = MATRIX_STEPS.index(matrix_step) if matrix_step else (1 if entrypoint_only else 4)
    rename = RENAME if step >= 1 else set()
    with ZipFile(BytesIO(source)) as old:
        infos = old.infolist()
        names = {info.filename.casefold() for info in infos}
        if len(infos) != 229 or len(names) != len(infos) or any(i.flag_bits for i in infos):
            raise ValueError("Unexpected Phase 2 ZIP layout")
        pos = old.start_dir
        for info in infos:
            local = info.header_offset
            if source[local:local+4] != b"PK\x03\x04" or source[pos:pos+4] != b"PK\x01\x02":
                raise ValueError("Invalid donor ZIP32 headers")
            if preserve_layout and len(result) != local:
                raise ValueError("A local header moved in the fixed-layout variant")
            name_len, extra_len = struct.unpack_from("<HH", source, local+26)
            c_name, c_extra, c_comment = struct.unpack_from("<HHH", source, pos+28)
            start = local+30+name_len+extra_len
            local_header = bytearray(source[local:start])
            central_header = bytearray(source[pos:pos+46+c_name+c_extra+c_comment])
            pos += len(central_header)
            name = info.filename
            new_name = name.replace(DONOR, ALIAS) if name in rename else name
            if (name.encode() != local_header[30:30+name_len]
                    or name.encode() != central_header[46:46+c_name]):
                raise ValueError(f"ZIP name mismatch: {name}")
            local_header[30:30+name_len] = new_name.encode()
            central_header[46:46+c_name] = new_name.encode()
            old_data = old.read(info)
            new_data = old_data
            if name == f"{DONOR}.carbin":
                if step < 2:
                    scene = Reader(old_data).scene()
                else:
                    new_data, scene = scene_variant(old_data, names, change_ordinal=step >= 3,
                                                    change_paths=step >= 4)
            elif step < 4:
                pass
            elif name == "Lights.bin" or name.lower().endswith(".modelbin"):
                new_data = retarget_paths(old_data, names)
            elif name == f"UI/{DONOR}.xaml":
                old_class = f'x:Class="ForzaUI.{DONOR}"'.encode()
                if old_data.count(old_class) != 1:
                    raise ValueError("XAML class preimage mismatch")
                new_data = old_data.replace(old_class, f'x:Class="ForzaUI.{ALIAS}"'.encode())
            elif paths(old_data):
                raise ValueError(f"Unreviewed runtime path in {name}")
            payload = source[start:start+info.compress_size]
            if old_data != new_data:
                if info.compress_type != 8 or len(old_data) != len(new_data):
                    raise ValueError(f"Unsupported member change: {name}")
                if preserve_layout:
                    try:
                        payload = deflate_exact(new_data, info.compress_size)
                    except ValueError:
                        payload = compact_deflate(new_data)
                        padding = info.compress_size-len(payload)
                        if padding < 4:
                            raise ValueError(f"Compact member cannot fit donor layout: {name}")
                        # A well-formed local ZIP extra field absorbs the size difference.
                        local_header.extend(struct.pack("<HH", 0xF3A0, padding-4) + bytes(padding-4))
                        struct.pack_into("<H", local_header, 28, extra_len+padding)
                else:
                    codec = zlib.compressobj(9, wbits=-15)
                    payload = codec.compress(new_data) + codec.flush()
                values = zlib.crc32(new_data), len(payload), len(new_data)
                struct.pack_into("<III", local_header, 14, *values)
                struct.pack_into("<III", central_header, 16, *values)
            struct.pack_into("<I", central_header, 42, len(result))
            result.extend(local_header)
            result.extend(payload)
            central.append(central_header)
            if name != new_name or old_data != new_data:
                edits.append({"original": name, "alias": new_name,
                              "before_sha256": digest(old_data), "after_sha256": digest(new_data)})
        if source[pos:pos+4] != b"PK\x05\x06" or len(source)-pos != 22:
            raise ValueError("Invalid donor EOCD")
        eocd = bytearray(source[pos:])
        directory = len(result)
        central_blob = b"".join(central)
        struct.pack_into("<II", eocd, 12, len(central_blob), directory)
        result.extend(central_blob)
        result.extend(eocd)
        expected = {e["alias"]: e["after_sha256"] for e in edits}
        for info in infos:
            if info.filename not in rename:
                expected.setdefault(info.filename, digest(old.read(info)))
    with ZipFile(BytesIO(result)) as new:
        if len(new.infolist()) != 229 or new.testzip() is not None:
            raise ValueError("Alias archive failed CRC")
        if expected != {name: digest(new.read(name)) for name in new.namelist()}:
            raise ValueError("Alias member readback mismatch")
        alias_names = {name.casefold() for name in new.namelist()}
        prefix = f"game:\\media\\cars\\{ALIAS.lower()}\\".encode()
        for name in new.namelist():
            data = new.read(name)
            # Audit all rewritten path fields by mapping back to the reviewed parser pattern.
            donor_prefix = f"game:\\media\\cars\\{DONOR.lower()}\\".encode()
            as_donor = re.sub(re.escape(prefix), lambda _: donor_prefix, data, flags=re.I)
            for m in paths(as_donor):
                rel = m[1].decode().replace("\\", "/").casefold()
                if rel not in alias_names:
                    raise ValueError(f"Aliased path did not resolve: {name}: {rel}")
    if (entrypoint_only or preserve_layout) and (directory != 19599118 or len(result) != len(source)):
        raise ValueError("Entrypoint control changed the ZIP layout")
    return bytes(result), edits, scene, (directory, len(result)-directory, len(central))


def add_manifest(data, directory):
    stock = ET.fromstring(data)
    donor_path = f"game:\\media\\cars\\{DONOR.lower()}.zip"
    alias_path = f"game:\\media\\cars\\{ALIAS.lower()}.zip"
    donor, = [e for e in stock if e.get("path") == donor_path]
    if (donor.get("dirstart"), donor.get("dirsize"), donor.get("direntries")) != ("19599118", "23929", "229"):
        raise ValueError("Donor manifest preimage mismatch")
    if any(e.get("path", "").casefold() == alias_path for e in stock):
        raise ValueError("Alias is already registered")
    start, size, count = directory
    entry = (f'  <Zip version="{donor.attrib["version"]}" path="{alias_path}" '
             f'priority="{donor.attrib["priority"]}" dirstart="{start}" '
             f'dirsize="{size}" direntries="{count}" />').encode()
    closing = b"</ZipFiles>"
    if data.count(closing) != 1:
        raise ValueError("Unexpected manifest framing")
    result = data.replace(closing, entry + (b"\r\n" if b"\r\n" in data else b"\n") + closing)
    parsed = ET.fromstring(result)
    if ([e.attrib for e in parsed][:-1] != [e.attrib for e in stock]
            or parsed[-1].attrib["path"] != alias_path or len(parsed) != len(stock)+1):
        raise ValueError("Manifest readback mismatch")
    return result


def route_db(source, output, scene):
    shutil.copyfile(source, output)
    db = sqlite3.connect(output)
    try:
        with db:
            db.execute("PRAGMA foreign_keys=ON")
            if db.execute("SELECT 1 FROM Data_Car WHERE upper(MediaName)=upper(?)", (ALIAS,)).fetchone():
                raise ValueError("Alias MediaName already exists")
            updated = db.execute(
                "UPDATE Data_Car SET MediaName=? WHERE Id=? AND ModelId=? AND MediaName=?",
                (ALIAS, CAR_ID, CAR_ID, DONOR),
            ).rowcount
            if updated != 1:
                raise ValueError("Missing Phase 2 clone row")
    finally:
        db.close()
    with read_only(source) as before, read_only(output) as after:
        delta = compare(before, after)
        if (check_integrity(before) != check_integrity(after)
                or delta["changed_tables"] != {"Data_Car": {"added": 1, "removed": 1}}
                or delta["added_car_ids"] or delta["removed_car_ids"]):
            raise ValueError("Unexpected GameDB delta")
        for row in before.execute("SELECT * FROM Data_Car ORDER BY Id"):
            new = dict(after.execute("SELECT * FROM Data_Car WHERE Id=?", (row["Id"],)).fetchone())
            expected = dict(row)
            if row["Id"] == CAR_ID:
                expected["MediaName"] = ALIAS
            if expected != new:
                raise ValueError(f"Unexpected Data_Car change for {row['Id']}")
        result = audit(after, CAR_ID, scene)
        if (result["media_matches"] != (scene["media_name"] == ALIAS.lower())
                or result["scene_ordinal_matches"] != (scene["ordinal"] == CAR_ID) or any(
            result["catalogs"][group]["unmatched_graphics_ids"] for group in (9, 34)
        )):
            raise ValueError("Alias lost reviewed scene bindings")


def build(donor, manifest, stock_db, output, entrypoint_only=False, preserve_layout=False,
          matrix_step=None):
    output = output.resolve()
    if not output.is_relative_to(ROOT / ".scratch") or output.exists():
        raise ValueError("Use a new directory under .scratch")
    inputs = [donor.read_bytes(), manifest.read_bytes(), stock_db.read_bytes()]
    if tuple(map(digest, inputs)) != BASE_HASHES or len(DONOR) != len(ALIAS):
        raise ValueError("Expected the exact Phase 2 input assets")
    archive, edits, scene, coords = archive_variant(inputs[0], entrypoint_only, preserve_layout,
                                                   matrix_step)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        trial = Path(temporary) / "alias"
        trial.mkdir()
        (trial / f"{ALIAS}.zip").write_bytes(archive)
        registry = add_manifest(inputs[1], coords)
        (trial / "zipmanifest.plain.xml").write_bytes(registry)
        db = trial / "gamedb.alias.sqlite"
        route_db(stock_db, db, scene)
        report = {
            "status": "OFFLINE_ALIAS_VALIDATED", "media_name": ALIAS,
            "entrypoint_only": entrypoint_only,
            "preserve_layout": preserve_layout,
            "matrix_step": matrix_step,
            "inputs_sha256": dict(zip(("phase2_zip", "stock_manifest_plaintext", "phase2_db"), BASE_HASHES)),
            "archive_sha256": digest(archive), "archive_coordinates": coords,
            "manifest_sha256": digest(registry), "db_sha256": digest(db.read_bytes()),
            "alias_ordinal": scene["ordinal"], "member_changes": edits,
        }
        (trial / "build.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        trial.rename(output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("donor", "manifest", "phase2_db", "output"):
        parser.add_argument(arg, type=Path)
    parser.add_argument("--entrypoint-only", action="store_true")
    parser.add_argument("--preserve-layout", action="store_true")
    parser.add_argument("--matrix-step", choices=MATRIX_STEPS)
    args = parser.parse_args()
    if args.matrix_step and (args.entrypoint_only or not args.preserve_layout):
        parser.error("--matrix-step requires --preserve-layout and replaces --entrypoint-only")
    try:
        print(json.dumps(build(args.donor, args.manifest, args.phase2_db, args.output,
                               args.entrypoint_only, args.preserve_layout, args.matrix_step), indent=2))
    except (OSError, ValueError, sqlite3.Error, ET.ParseError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
