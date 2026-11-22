"""Inventory the FM4 DC2's on-disc database and method-21 ZIP directories."""

import argparse
from collections import Counter
from hashlib import file_digest
import json
from pathlib import Path
import sqlite3
from zipfile import BadZipFile, ZipFile


MEDIA = "HON_IntegraR_00"


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def inventory(root):
    media = root / "Media"
    database = media / "db/gamedb.slt"
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        rows = db.execute(
            "SELECT Id,Year,MediaName,MakeID,StockWheelID FROM Data_Car WHERE MediaName=?",
            (MEDIA,),
        ).fetchall()
        if len(rows) != 1 or rows[0][:3] != (370, 2000, MEDIA):
            raise ValueError("Target database row does not identify the FM4 2000 DC2")
        car = dict(zip(("id", "year", "media_name", "make_id", "wheel_id"), rows[0]))
        engine = db.execute(
            "SELECT EngineID FROM List_UpgradeEngine WHERE Ordinal=? AND IsStock=1",
            (car["id"],),
        ).fetchall()
        if len(engine) != 1:
            raise ValueError("Stock engine mapping is ambiguous")
        car["stock_engine_id"] = engine[0][0]
    archives = {}
    for group in ("cars", "wheels"):
        relative = f"Media/{group}/{MEDIA}.zip"
        path = root / relative
        with ZipFile(path) as carrier:
            members = [{"name": z.filename, "method": z.compress_type, "size": z.file_size,
                        "crc32": f"{z.CRC:08x}"} for z in carrier.infolist()]
            archives[group] = {"relative_path": relative, "sha256": digest(path),
                               "dirstart": carrier.start_dir,
                               "methods": Counter(z["method"] for z in members), "members": members}
    return {"car": car, "db_sha256": digest(database), "archives": archives,
            "verification_scope": "ZIP central-directory inventory; LZX payloads not decoded"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fm4_root", type=Path)
    try:
        print(json.dumps(inventory(parser.parse_args().fm4_root), indent=2))
    except (OSError, ValueError, BadZipFile, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
