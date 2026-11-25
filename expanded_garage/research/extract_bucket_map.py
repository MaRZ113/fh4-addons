"""FM4 catalogue features -> FH4 CarBucket, learned from the cars present in both games.

For every FM4 Data_Car whose MediaName also has a Data_Car_Buckets row in FH4, store the FM4 features the
converter has for any source car (CarTypeID, FamilyBodyID, Year, PerformanceIndex, CurbWeight,
EnginePlacementID) with the FH4 bucket name. fh4_db_compiler.catalogue_bucket falls back to a weighted
nearest-neighbour vote over these rows (197 cars; leave-one-out 111/197 exact, misses mostly the neighbouring
bucket: retro vs modern hot hatch).

Usage: python -m expanded_garage.research.extract_bucket_map FM4_GAMEDB FH4_GAMEDB FH4_EN_ZIP OUT_JSON
"""

import json
import sqlite3
import sys
from zipfile import ZipFile

from expanded_garage.fh4_dynamics import localized_rows

FIELDS = ("CarTypeID", "FamilyBodyID", "Year", "PerformanceIndex", "CurbWeight", "EnginePlacementID")


def main(fm4_db, fh4_db, en_zip, out):
    fm = sqlite3.connect(fm4_db)
    fm.row_factory = sqlite3.Row
    fh = sqlite3.connect(fh4_db)
    names = {v: k for k, v in localized_rows(fh, ZipFile(en_zip), "CarBuckets", "Id", "Name", leaves_only=True).items()}
    bucket = {m.casefold(): names[b] for m, b in fh.execute(
        "SELECT c.MediaName, k.CarBucket FROM Data_Car c JOIN Data_Car_Buckets k ON k.CarId=c.Id") if b in names}
    rows = [{"media_name": r["MediaName"], **{f: r[f] for f in FIELDS}, "bucket": bucket[r["MediaName"].casefold()]}
            for r in fm.execute(f"SELECT MediaName, {', '.join(FIELDS)} FROM Data_Car")
            if r["MediaName"].casefold() in bucket]
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"format": "fm4-fh4-bucket-map-v1", "fields": FIELDS, "cars": rows}, handle, indent=1)
    print(f"{len(rows)} shared cars -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:])
