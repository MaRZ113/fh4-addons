"""Build scratch-only FH4 Acura upgrade-binding and mount-control fixtures."""

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import struct
import tempfile
from zipfile import ZipFile
import zlib

from fh4_carbin import Reader, audit, read_scene
from fh4_gamedb import ROOT, read_only


ARCHIVE_SHA = "12cccdf370292c07b69eb13a406b88722012f3b87a74dba64a6e1ee53d15b12d"
MANIFEST_SHA = "62bcdeab384e729680fff752a980f541fc46e07a9598a9f6ae4e597471694a37"
DB_SHA = "3063ce9ba39daaebbfdf3bce395cc9a25bf937618c723e090bcd3861f9aa5916"
MEMBER = "ACU_IntegraR_01.carbin"
PARTS = {"front-bumper": (34,), "rear-wing": (9,), "mount-control": (), "bumper-and-wing": (34, 9)}


def add_bindings(data, scene, kind):
    group, = [group for group in scene["groups"] if group["type"] == kind]
    if group["version"] != 3 or not group["shared"]:
        raise ValueError("Expected a v3 shared-model group")
    ids = {368000: 3477000, 368001: 3477001}
    if {node["id"] for node in group["upgrades"]} != ids.keys():
        raise ValueError("Upgrade preimages differ")
    additions = bytearray()
    for node in group["upgrades"]:
        record = bytearray(data[node["offset"]:node["end"]])
        struct.pack_into("<i", record, 4, ids[node["id"]])
        if kind == 34 and node["parent"] == 368000:
            struct.pack_into("<i", record, 8, 3477000)
        elif kind != 9 or node["parent"] != -1:
            raise ValueError("Unexpected parent key")
        additions.extend(record)
    edits = [(group["count_offset"], 4, struct.pack("<I", 4)),
             (group["upgrades"][-1]["end"], 0, additions)]
    for shared in group["shared"]:
        refs = shared["ids"]
        aliases = [ids[ref["id"]] for ref in refs]
        edits.extend([(refs[0]["offset"]-4, 4, struct.pack("<I", len(refs) + len(aliases))),
                      (refs[-1]["offset"]+4, 0, struct.pack(f"<{len(aliases)}i", *aliases))])
    result = bytearray(data)
    for offset, length, contents in sorted(edits, reverse=True):
        result[offset:offset+length] = contents
    return bytes(result)


def deflate(contents, original_size=None):
    if original_size is None:
        compressor = zlib.compressobj(level=9, wbits=-15)
        return compressor.compress(contents) + compressor.flush()
    for split in range(40, len(contents), 67):
        compressor = zlib.compressobj(level=9, wbits=-15)
        zipped = (compressor.compress(contents[:split]) + compressor.flush(zlib.Z_BLOCK)
                  + compressor.compress(contents[split:]) + compressor.flush())
        if len(zipped) == original_size:
            return zipped
    raise ValueError("No exact-size deflate stream found by the bounded search")


def replace_zip_member(original, contents, preserve_layout=False, *, member=MEMBER, compressor=deflate):
    """Replace one ZIP32 deflate stream, preserving other member bytes and metadata."""
    with ZipFile(BytesIO(original)) as old:
        entries = old.infolist()
        directory = old.start_dir
        item = old.getinfo(member)
        eocd = len(original)-22
        if (old.comment or item.compress_type != 8 or original[eocd:eocd+4] != b"PK\x05\x06"
                or any(entry.flag_bits or entry.extract_version >= 45 for entry in entries)):
            raise ValueError("Unsupported ZIP layout")
        local = item.header_offset
        if original[local:local+4] != b"PK\x03\x04":
            raise ValueError("Missing local header")
        names, extras = struct.unpack_from("<HH", original, local+26)
        start = local+30+names+extras
        end = start+item.compress_size
        zipped = compressor(contents, item.compress_size if preserve_layout else None)
        shift = len(zipped)-item.compress_size
        fields = (zlib.crc32(contents), len(zipped), len(contents))
        result = bytearray(original[:start] + zipped + original[end:])
        struct.pack_into("<III", result, local+14, *fields)

        pos = directory+shift
        for entry in entries:
            if result[pos:pos+4] != b"PK\x01\x02":
                raise ValueError("Missing central-directory entry")
            name_len, extra_len, comment_len = struct.unpack_from("<HHH", result, pos+28)
            name = result[pos+46:pos+46+name_len].decode("cp437")
            if name != entry.filename:
                raise ValueError("Central-directory order mismatch")
            if entry.filename == member:
                struct.pack_into("<III", result, pos+16, *fields)
            elif entry.header_offset >= end:
                struct.pack_into("<I", result, pos+42, entry.header_offset+shift)
            pos += 46+name_len+extra_len+comment_len
        if pos != eocd+shift:
            raise ValueError("Unexpected central-directory tail")
        struct.pack_into("<I", result, pos+16, directory+shift)

        with ZipFile(BytesIO(result)) as new:
            if new.namelist() != old.namelist() or new.read(member) != contents:
                raise ValueError("ZIP readback failed")
            for entry in entries:
                if entry.filename == member:
                    continue
                new_entry = new.getinfo(entry.filename)
                # All non-target local headers and compressed payloads must match.
                offset = entry.header_offset
                name_len, extra_len = struct.unpack_from("<HH", original, offset+26)
                length = 30+name_len+extra_len+entry.compress_size
                new_offset = new_entry.header_offset
                if original[offset:offset+length] != result[new_offset:new_offset+length]:
                    raise ValueError(f"Non-target member changed: {entry.filename}")
            bad = new.testzip()
            if bad is not None:
                raise ValueError(f"CRC failed for {bad}")
        return bytes(result), directory, directory+shift, len(original)-directory, len(entries)


def build(archive, manifest, db_path, output, part, preserve_layout=False):
    archive_data, xml = archive.read_bytes(), manifest.read_bytes()
    if (sha256(archive_data).hexdigest() != ARCHIVE_SHA or sha256(xml).hexdigest() != MANIFEST_SHA
            or sha256(db_path.read_bytes()).hexdigest() != DB_SHA):
        raise ValueError("Inputs must match the reviewed stock ZIP, manifest and Phase 1 SQLite")
    output = output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / ".scratch"):
        raise ValueError("Use a new output directory inside .scratch")
    original, before = read_scene(archive, MEMBER)
    kinds = PARTS[part]
    modified, after = original, before
    for kind in kinds:
        modified = add_bindings(modified, after, kind)
        after = Reader(modified).scene()
    with read_only(db_path) as db:
        for car_id in (368, 3477):
            base = audit(db, car_id, before)["catalogs"]
            result = audit(db, car_id, after)["catalogs"]
            for key in result:
                # Compare semantic bindings since byte offsets move downstream.
                model_sets = lambda group: [(r["id"], r["models"], r["fixed_stock"]) for r in group["rows"]]
                if car_id == 368 or key not in kinds:
                    if model_sets(base[key]) != model_sets(result[key]):
                        raise ValueError(f"Unexpected binding change for car {car_id}, part {key}")
                elif result[key]["unmatched_graphics_ids"]:
                    raise ValueError("Candidate bindings still missing")

    patched, old_start, new_start, size, count = replace_zip_member(
        archive_data, modified, preserve_layout,
    )
    entry = (rb'(<Zip [^>]*path="game:\\media\\cars\\acu_integrar_01\.zip"[^>]*dirstart=")'
             + str(old_start).encode() + rb'(" dirsize="' + str(size).encode()
             + rb'" direntries="' + str(count).encode() + rb'"[^>]*>)')
    new_xml, matches = re.subn(entry, lambda m: m[1] + str(new_start).encode() + m[2], xml)
    if matches != 1:
        raise ValueError("Expected one matching manifest preimage")
    report = {
        "part": part, "status": "OFFLINE_ASSET_VALIDATED", "preserve_layout": preserve_layout,
        "zip_sha256": sha256(patched).hexdigest(), "plaintext_manifest_sha256": sha256(new_xml).hexdigest(),
        "carbin_sha256": sha256(modified).hexdigest(), "old_directory_start": old_start,
        "new_directory_start": new_start, "directory_size": size, "member_count": count,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        staging = Path(directory) / "patch"
        staging.mkdir()
        (staging / "ACU_IntegraR_01.zip").write_bytes(patched)
        (staging / "zipmanifest.plain.xml").write_bytes(new_xml)
        (staging / "build.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        staging.rename(output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive", "manifest", "db", "output"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--part", choices=PARTS, required=True)
    parser.add_argument("--preserve-layout", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.archive, args.manifest, args.db, args.output, args.part,
                               args.preserve_layout), indent=2))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
