"""Move only the clone's stock bumper reference between existing Phase 2 models."""

import argparse
from hashlib import sha256
from pathlib import Path
import struct

from fh4_asset_probe import replace_zip_member
from fh4_carbin import Reader, read_scene
from fh4_gamedb import ROOT


BASE_SHA = "a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c"


def bumper_lists(scene):
    group, = [g for g in scene["groups"] if g["type"] == 34]
    return {Path(entry["model"]["path"]).name: entry["ids"] for entry in group["shared"]}


def move_stock_reference(data, scene):
    lists = bumper_lists(scene)
    stock = lists["bumperf_a.modelbin"]
    race = lists["bumperf_race.modelbin"]
    if [x["id"] for x in stock] != [368000, 368001, 3477000, 3477001]:
        raise ValueError("Stock bumper list differs from Phase 2")
    if [x["id"] for x in race] != [368001, 3477001]:
        raise ValueError("Race bumper list differs from Phase 2")
    edits = [
        (stock[0]["offset"]-4, 4, struct.pack("<I", 3)),
        (stock[2]["offset"], 4, b""),
        (race[0]["offset"]-4, 4, struct.pack("<I", 3)),
        (race[-1]["offset"]+4, 0, struct.pack("<i", 3477000)),
    ]
    changed = bytearray(data)
    for offset, length, value in sorted(edits, reverse=True):
        changed[offset:offset+length] = value
    after = Reader(changed).scene()
    before_refs = {k: [r["id"] for r in v] for k, v in lists.items()}
    before_refs["bumperf_a.modelbin"].remove(3477000)
    before_refs["bumperf_race.modelbin"].append(3477000)
    if before_refs != {k: [r["id"] for r in v] for k, v in bumper_lists(after).items()}:
        raise ValueError("Unexpected bumper binding delta")
    if len(changed) != len(data):
        raise ValueError("Unexpected scene size")
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        if output.exists() or not output.is_relative_to(ROOT / ".scratch"):
            raise ValueError("Use a new .scratch output")
        source = args.archive.read_bytes()
        if sha256(source).hexdigest() != BASE_SHA:
            raise ValueError("Expected the accepted Phase 2 shared archive")
        data, scene = read_scene(args.archive)
        changed = move_stock_reference(data, scene)
        zip_data, old, new, size, count = replace_zip_member(source, changed, preserve_layout=True)
        if old != new or (new, size, count) != (19599118, 23929, 229):
            raise ValueError("Unexpected mount layout")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            stream.write(zip_data)
        print(f"ZIP SHA-256 {sha256(zip_data).hexdigest()}")
        print(f"Carbin SHA-256 {sha256(changed).hexdigest()}")
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
