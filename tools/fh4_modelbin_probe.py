"""Build sequential, scratch-only Modelbin loading probes for the accepted FH4 Acura ZIP."""

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZipFile
import zlib

from fh4_asset_probe import MEMBER, replace_zip_member
from fh4_carbin import Reader
from fh4_gamedb import ROOT
from fh4_independent_assets import deflate_exact
from fh4_modelbin_geometry import mark_towhook
from fh4_shared_scene_probe import BASE_SHA


ARCHIVE = "ACU_IntegraR_01.zip"
RACE = "scene/Exterior/BumperF/bumperF_race.modelbin"
ALIAS = "scene/Exterior/BumperF/bumperF_3477.modelbin"
CLONE_STOCK = 3477000
CONTROL_LAYOUT = (19599118, 23929, 229)
MANIFEST_SHA = "eb086ab073b2549510d4b99796bb6b781c7cc23b4bb14fff416ede8fa70c2703"
CRYPTO_SHA = "e7de706754590c8e8666d3c0185b0ffa6b523d8a92d56f4938f6b9951d3581a0"
CONTROL_MEMBERSHIPS = {
    "bumperf_a.modelbin": [368000, 368001, 3477000, 3477001],
    "bumperf_race.modelbin": [368001, 3477001],
    "bumperfsidemarkers_a.modelbin": [368000, 368001, 3477000, 3477001],
    "grille_a.modelbin": [368000, 368001, 3477000, 3477001],
}


def digest(data):
    return sha256(data).hexdigest()


def bumper_group(data):
    scene = Reader(data).scene()
    group, = [group for group in scene["groups"] if group["type"] == 34]
    return scene, group


def model_name(row):
    return row["model"]["path"].rsplit("\\", 1)[-1]


def memberships(group):
    return {model_name(row): [entry["id"] for entry in row["ids"]] for row in group["shared"]}


def add_aero(data):
    _, group = bumper_group(data)
    if group["version"] != 3 or memberships(group) != CONTROL_MEMBERSHIPS:
        raise ValueError("Unexpected type-34 Phase 2 preimage")
    race, = [row for row in group["shared"] if model_name(row) == "bumperf_race.modelbin"]
    offset = race["ids"][0]["offset"] - 4
    result = data[:offset] + struct.pack("<Iiii", 3, 368001, CLONE_STOCK, 3477001) + data[offset+12:]
    expected = {**CONTROL_MEMBERSHIPS, "bumperf_race.modelbin": [368001, CLONE_STOCK, 3477001]}
    verify_scene(data, result, expected)
    return result


def verify_scene(before, after, expected):
    old, group = bumper_group(before)
    new, changed = bumper_group(after)
    for key in ("media_name", "ordinal", "skeleton", "fixed"):
        if old[key] != new[key]:
            raise ValueError(f"Changed scene identity or fixed parts: {key}")
    if memberships(changed) != expected:
        raise ValueError("Unexpected type-34 membership")
    for earlier, later in zip(old["groups"], new["groups"], strict=True):
        start = earlier["count_offset"] + 4
        end = earlier["upgrades"][-1]["end"] if earlier["upgrades"] else start
        new_start = later["count_offset"] + 4
        if before[start:end] != after[new_start:new_start+end-start]:
            raise ValueError(f"Changed upgrade records for type {earlier['type']}")
        if earlier is not group:
            assert earlier["type"] == later["type"]
            old_offset = earlier["count_offset"] - 6
            new_offset = later["count_offset"] - 6
            delta = new_offset - old_offset
            groups = old["groups"]
            following = groups[groups.index(earlier)+1:]
            end = following[0]["count_offset"] - 6 if following else len(before)
            if before[old_offset:end] != after[new_offset:end+delta]:
                raise ValueError(f"Changed unrelated group {earlier['type']}")


def describe(data, target):
    with ZipFile(BytesIO(data)) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"CRC failed for {bad}")
        entry = archive.getinfo(target)
        names, extras = struct.unpack_from("<HH", data, entry.header_offset+26)
        return {
            "zip_sha256": digest(data),
            "zip_bytes": len(data),
            "dirstart": archive.start_dir,
            "dirsize": len(data)-archive.start_dir,
            "direntries": len(archive.infolist()),
            "member": target,
            "member_sha256": digest(archive.read(entry)),
            "member_header_offset": entry.header_offset,
            "member_stream_start": entry.header_offset+30+names+extras,
            "member_compressed_size": entry.compress_size,
            "carbin_sha256": digest(archive.read(MEMBER)),
        }


def write_probe(root, stage, source, result, member, *, manifest=None, evidence=None):
    report = describe(result, member)
    report["input_zip_sha256"] = digest(source)
    report["stage"] = stage
    report["status"] = "OFFLINE_VALIDATED"
    if evidence:
        report.update(evidence)
    if manifest:
        plain, encrypted = manifest
        report.update(plaintext_manifest_sha256=digest(plain), encrypted_manifest_sha256=digest(encrypted))
    target = root / stage
    if target.exists():
        if (digest((target / ARCHIVE).read_bytes()) != report["zip_sha256"]
                or json.loads((target / "build.json").read_text(encoding="utf-8")) != report
                or manifest and digest((target / "zipmanifest.xml").read_bytes()) != digest(encrypted)):
            raise ValueError(f"Existing fixture differs: {target}")
        return report
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as temp:
        fixture = Path(temp) / stage
        fixture.mkdir()
        (fixture / ARCHIVE).write_bytes(result)
        if manifest:
            (fixture / "zipmanifest.xml").write_bytes(encrypted)
        (fixture / "build.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        fixture.rename(target)
    return report


def build_control(source, output):
    if digest(source) != BASE_SHA:
        raise ValueError("Expected the accepted Phase 2 archive")
    with ZipFile(BytesIO(source)) as archive:
        data = archive.read(MEMBER)
    result, earlier, start, size, count = replace_zip_member(source, add_aero(data), True)
    if earlier != start or (start, size, count) != CONTROL_LAYOUT:
        raise ValueError("Unexpected control ZIP layout")
    return write_probe(output, "aero", source, result, MEMBER)


def replace_fixed(source, contents, name, compressor, layout):
    result, prior, start, size, count = replace_zip_member(
        source, contents, True, member=name, compressor=compressor,
    )
    if prior != start or (start, size, count) != layout or len(result) != len(source):
        raise ValueError(f"Unexpected ZIP layout while editing {name}")
    return result


def build_repack(source, output):
    with ZipFile(BytesIO(source)) as archive:
        model = archive.read(RACE)
    result = replace_fixed(source, model, RACE, deflate_exact, CONTROL_LAYOUT)
    if result == source:
        raise ValueError("Repack fixture must have a distinct compressed stream")
    return write_probe(output, "repack", source, result, RACE)


def append_identical_member(data):
    """Reproduce the initial append, including its historical stale 0x1123 value."""
    with ZipFile(BytesIO(data)) as source:
        entry = source.getinfo(RACE)
        entries = source.infolist()
        local = entry.header_offset
        directory = source.start_dir
        eocd_offset = len(data)-22
        if (source.comment or data[eocd_offset:eocd_offset+4] != b"PK\x05\x06"
                or (directory, len(data)-directory, len(entries)) != CONTROL_LAYOUT
                or any(x.filename.casefold() == ALIAS.casefold() for x in entries)):
            raise ValueError("Unexpected append ZIP preimage")
        old_name, new_name = RACE.encode(), ALIAS.encode()
        if len(old_name) != len(new_name):
            raise ValueError("Expected a same-size entry name")
        name_len, extra_len = struct.unpack_from("<HH", data, local+26)
        end = local+30+name_len+extra_len+entry.compress_size
        local_copy = bytearray(data[local:end])
        if local_copy[30:30+name_len] != old_name or entry.flag_bits:
            raise ValueError("Unexpected source Modelbin local record")
        local_copy[30:30+name_len] = new_name

        pos = directory
        central_copy = None
        for record in entries:
            name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, pos+28)
            end = pos+46+name_len+extra_len+comment_len
            if data[pos:pos+4] != b"PK\x01\x02":
                raise ValueError("Unexpected central directory")
            if record.filename == RACE:
                central_copy = bytearray(data[pos:end])
                if central_copy[46:46+name_len] != old_name:
                    raise ValueError("Unexpected Modelbin central record")
                central_copy[46:46+name_len] = new_name
                struct.pack_into("<I", central_copy, 42, directory)
            pos = end
        if pos != eocd_offset or central_copy is None:
            raise ValueError("Unexpected directory tail")
        eocd = bytearray(data[eocd_offset:])
        struct.pack_into("<HHII", eocd, 8, len(entries)+1, len(entries)+1,
                         eocd_offset-directory+len(central_copy), directory+len(local_copy))
        result = data[:directory] + local_copy + data[directory:eocd_offset] + central_copy + eocd
        with ZipFile(BytesIO(result)) as target:
            if ([(e.filename,e.header_offset,e.CRC,e.compress_size,e.file_size) for e in target.infolist()[:-1]]
                    != [(e.filename,e.header_offset,e.CRC,e.compress_size,e.file_size) for e in entries]
                    or target.namelist()[-1] != ALIAS or target.read(ALIAS) != source.read(RACE)):
                raise ValueError("Appended member readback failed")
        return bytes(result)


def check_payload_offsets(data):
    """Validate Forza's central extra field against the physical payload of every entry."""
    with ZipFile(BytesIO(data)) as archive:
        for entry in archive.infolist():
            name_size, extra_size = struct.unpack_from("<HH",data,entry.header_offset+26)
            payload_start = entry.header_offset+30+name_size+extra_size
            if len(entry.extra) != 8:
                raise ValueError(f"Unexpected central extra field layout: {entry.filename}")
            extra_tag, extra_len, value = struct.unpack("<HHI",entry.extra)
            if (extra_tag, extra_len, value) != (0x1123,4,payload_start):
                raise ValueError(f"Bad 0x1123 payload offset: {entry.filename}: {value} vs {payload_start}")


def correct_payload_offset(data):
    """Change only the appended central record's proprietary data-offset field."""
    with ZipFile(BytesIO(data)) as archive:
        entry = archive.getinfo(ALIAS)
        source = archive.getinfo(RACE)
        pos = archive.start_dir
        for item in archive.infolist()[:-1]:
            pos += 46+len(item.filename.encode("ascii"))+len(item.extra)+len(item.comment)
        start = pos+46+len(ALIAS.encode("ascii"))
        if (archive.infolist()[-1] != entry or data[pos:pos+4] != b"PK\x01\x02"
                or data[start:start+8] != entry.extra or entry.extra != source.extra
                or struct.unpack("<HH",entry.extra[:4]) != (0x1123,4)):
            raise ValueError("Expected the reviewed stale 0x1123 copy")
        name_size, extra_size = struct.unpack_from("<HH",data,entry.header_offset+26)
        physical = entry.header_offset+30+name_size+extra_size
    result = bytearray(data)
    struct.pack_into("<I",result,start+4,physical)
    check_payload_offsets(result)
    return bytes(result), {"central_extra_field": "0x1123",
                           "central_extra_value_file_offset": start+4,
                           "old_value": int.from_bytes(entry.extra[4:],"little"),
                           "new_value": physical}


def attach_manifest(crypto, baseline, layout):
    if digest(crypto.read_bytes()) != CRYPTO_SHA or digest(baseline.read_bytes()) != MANIFEST_SHA:
        raise ValueError("Unexpected local crypto executable or Phase 2 manifest")
    with tempfile.TemporaryDirectory(dir=ROOT / ".scratch") as temp:
        folder = Path(temp)
        original, changed, encoded, readback = (folder / name for name in
                                                ("base.xml", "changed.xml", "zipmanifest.xml", "roundtrip.xml"))
        command = str(crypto.resolve())
        subprocess.run([command, "decrypt", str(baseline.resolve()), "-o", str(original)],
                       check=True, capture_output=True)
        xml = original.read_bytes()
        prefix = b'<Zip version="1" path="game:\\media\\cars\\acu_integrar_01.zip" priority="80" '
        def row(values):
            start, size, count = values
            return prefix + f'dirstart="{start}" dirsize="{size}" direntries="{count}" />'.encode("ascii")
        if xml.count(row(CONTROL_LAYOUT)) != 1:
            raise ValueError("Unexpected manifest preimage")
        new = xml.replace(row(CONTROL_LAYOUT), row(layout))
        ET.fromstring(new)
        changed.write_bytes(new)
        subprocess.run([command, "encrypt", str(changed), "-o", str(encoded),
                        "--original", str(baseline.resolve())], check=True, capture_output=True)
        subprocess.run([command, "decrypt", str(encoded), "-o", str(readback)],
                       check=True, capture_output=True)
        if readback.read_bytes() != new:
            raise ValueError("Manifest crypto round-trip mismatch")
        return new, encoded.read_bytes()


def route_existing_model(carbin):
    original = b"bumperf_race.modelbin"
    alias = ALIAS.rsplit("/", 1)[-1].lower().encode()
    if len(alias) != len(original) or carbin.count(original) != 1:
        raise ValueError("Unexpected existing model path")
    changed = carbin.replace(original, alias)
    expected = {name: ids for name, ids in memberships(bumper_group(carbin)[1]).items()
                if name != original.decode()}
    expected[alias.decode()] = [368001, CLONE_STOCK, 3477001]
    verify_scene(carbin, changed, expected)
    return changed


def bind_clone(carbin, *, use_existing_path=False):
    _, group = bumper_group(carbin)
    rows = group["shared"]
    _, race, markers, _ = rows
    count = group["upgrades"][-1]["end"]
    start = race["ids"][0]["offset"]-4
    model_offset = race["ids"][-1]["offset"]+4
    end = markers["ids"][0]["offset"]-4
    model = carbin[model_offset:end]
    old_name = b"bumperf_race.modelbin"
    new_name = ALIAS.rsplit("/", 1)[-1].lower().encode()
    if (struct.unpack_from("<I",carbin,count)[0] != 4 or model.count(old_name) != 1
            or [item["id"] for item in race["ids"]] != [368001, CLONE_STOCK, 3477001]):
        raise ValueError("Unexpected clone association preimage")
    alias = model if use_existing_path else model.replace(old_name, new_name)
    new = (carbin[:count] + struct.pack("<I", 5) + carbin[count+4:start]
           + struct.pack("<Iii",2,368001,3477001) + model
           + struct.pack("<Ii",1,CLONE_STOCK) + alias + carbin[end:])
    if use_existing_path:
        _after_scene, after = bumper_group(new)
        if ([model_name(row) for row in after["shared"]].count(old_name.decode()) != 2
                or [[ref["id"] for ref in row["ids"]] for row in after["shared"]
                    if model_name(row) == old_name.decode()] != [[368001,3477001],[CLONE_STOCK]]):
            raise ValueError("Expected two exclusive memberships under the original path")
        verify_scene(carbin, new, {**CONTROL_MEMBERSHIPS, old_name.decode(): [CLONE_STOCK]})
    else:
        verify_scene(carbin, new, {**CONTROL_MEMBERSHIPS, new_name.decode(): [CLONE_STOCK]})
    return new


def bind_stream(data, size):
    # This block boundary fits the reviewed Carbin with the stock compressed byte budget.
    def encode(split):
        codec = zlib.compressobj(9, wbits=-15)
        return (codec.compress(data[:split]) + codec.flush(zlib.Z_BLOCK)
                + codec.compress(data[split:]) + codec.flush())

    for split in (3287, *range(40, len(data), 17)):
        result = encode(split)
        if len(result) == size:
            return result
    raise ValueError("No fixed-size bind Carbin stream found")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--crypto", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        if not output.is_relative_to(ROOT / ".scratch"):
            raise ValueError("Use a scratch output directory")
        base = args.baseline.read_bytes()
        check_payload_offsets(base)
        print(json.dumps(build_control(base, output), indent=2))
        control = (output / "aero" / ARCHIVE).read_bytes()
        print(json.dumps(build_repack(control, output), indent=2))
        if args.crypto or args.manifest:
            if not args.crypto or not args.manifest:
                raise ValueError("Supply both --crypto and --manifest")
            repack = (output / "repack" / ARCHIVE).read_bytes()
            appended = append_identical_member(repack)
            info = describe(appended, ALIAS)
            layout = (info["dirstart"], info["dirsize"], info["direntries"])
            manifest = attach_manifest(args.crypto, args.manifest, layout)
            print(json.dumps(write_probe(output, "append", repack, appended, ALIAS,
                                         manifest=manifest), indent=2))
            with ZipFile(BytesIO(appended)) as archive:
                carbin = archive.read(MEMBER)
            bound = replace_fixed(appended, bind_clone(carbin), MEMBER, bind_stream, layout)
            print(json.dumps(write_probe(output, "bind", appended, bound, ALIAS,
                                         manifest=manifest), indent=2))
            with ZipFile(BytesIO(bound)) as archive:
                model = archive.read(ALIAS)
            marked, draws = mark_towhook(model)
            geometry = replace_fixed(bound, marked, ALIAS, deflate_exact, layout)
            print(json.dumps(write_probe(output, "geometry", bound, geometry, ALIAS,
                                         manifest=manifest,
                                         evidence={"marked_towhook_draws": draws}), indent=2))
            lookup = replace_fixed(appended, route_existing_model(carbin), MEMBER, bind_stream, layout)
            print(json.dumps(write_probe(output, "lookup", appended, lookup, ALIAS,
                                         manifest=manifest), indent=2))
            duplicate = replace_fixed(appended, bind_clone(carbin, use_existing_path=True),
                                      MEMBER, bind_stream, layout)
            print(json.dumps(write_probe(output, "duplicate", appended, duplicate, RACE,
                                         manifest=manifest), indent=2))
            for stage, previous in (("append-fixed", appended), ("lookup-fixed", lookup),
                                    ("bind-fixed", bound), ("geometry-fixed", geometry)):
                fixed, correction = correct_payload_offset(previous)
                if stage == "geometry-fixed":
                    correction["marked_towhook_draws"] = draws
                print(json.dumps(write_probe(output, stage, previous, fixed, ALIAS,
                                             manifest=manifest, evidence=correction), indent=2))
    except (OSError, ValueError, struct.error, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
