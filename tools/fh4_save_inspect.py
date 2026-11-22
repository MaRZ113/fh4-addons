"""Inspect car identities in a decrypted copy of an FH4 ProfileData file."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3


def inspect(path, ids):
    data = path.read_bytes()
    magic = b"SQLite format 3\0"
    offset = data.rfind(magic)
    if offset < 4:
        raise ValueError("Embedded SQLite header missing")
    length = int.from_bytes(data[offset-4:offset], "little")
    page_size = int.from_bytes(data[offset+16:offset+18], "big")
    if page_size == 1:
        page_size = 65536
    if (length < 512 or length != len(data)-offset or page_size not in
            (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536) or length % page_size):
        raise ValueError("Unexpected length-prefixed SQLite tail")
    placeholders = ",".join("?" for _ in ids)
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    try:
        db.deserialize(data[offset:])
        db.execute("PRAGMA query_only=ON")
        if [row[0] for row in db.execute("PRAGMA integrity_check")] != ["ok"]:
            raise ValueError("Embedded SQLite integrity check failed")
        rows = db.execute(
            "SELECT Id AS GarageId, CarId, FrontBumper, RearWing, DefaultManufacturerColorIndex,"
            " length(LiveryFileName)>0 AS HasLiveryFile, length(TuneFileName)>0 AS HasTuneFile,"
            " (SELECT count(*) FROM Career_PurchasedParts p WHERE p.GarageId=g.Id) AS PartRows"
            f" FROM Career_Garage g WHERE CarId IN ({placeholders}) ORDER BY CarId,GarageId", ids,
        )
        return {"input_sha256": sha256(data).hexdigest(), "sqlite_offset": offset,
                "sqlite_length": length, "integrity_check": "ok", "garage": [dict(r) for r in rows]}
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plaintext_profile_copy", type=Path)
    parser.add_argument("--id", type=int, required=True, action="append")
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.plaintext_profile_copy, args.id), indent=2))
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
