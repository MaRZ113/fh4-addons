"""FM4 manufacturer colour (Combo_Colors.RGB) -> FH4 paint material, learned from cars in both games.

For every FM4 car whose MediaName has an FH4 archive, each FM4 colour is paired with the nearest of that car's
own FH4 ManufacturerColors materials (palette swatch RGB, distance below 70). Votes per exact FM4 RGB give the
material FH4 chose for the same paint (FM4 0xFFE200 on ACU_IntegraR_01 -> carcolors/ctr2yellow). A joint fit
fh4 = 255 * k * (fm4 / 255) ** g over all pairs gives the calibration used for colours never seen.

Usage: python -m expanded_garage.research.extract_paint_map FM4_GAMEDB FH4_CARS_DIR OUT_JSON
"""

import json
import os
import re
import sqlite3
import sys
import zipfile
from collections import Counter, defaultdict

import numpy as np

from expanded_garage import fh4_paint

MAX_DISTANCE = 70


def main(fm4_db, cars_dir, out):
    db = sqlite3.connect(fm4_db)
    palette = fh4_paint.palette()
    votes, pairs, cars = defaultdict(Counter), [], 0
    for car_id, media in db.execute("SELECT Id, MediaName FROM Data_Car"):
        path = os.path.join(cars_dir, media + ".zip")
        if not os.path.exists(path):
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                names = {n.lower(): n for n in archive.namelist()}
                data = archive.read(names["manufacturercolors.bin"])
        except (KeyError, zipfile.BadZipFile):
            continue
        members = [m.decode().replace("\\", "/").lower()
                   for m in re.findall(rb"materials.([\x20-\x7e]+?\.materialbin)", data)]
        options = [(tuple(palette[m]["rgb"]), m) for m in members if m in palette]
        if not options:
            continue
        cars += 1
        for (value,) in db.execute("SELECT RGB FROM Combo_Colors WHERE Ordinal=?", (car_id,)):
            rgb = fh4_paint.unpack_rgb(value)
            target, member = min(options, key=lambda o: sum((a - b) ** 2 for a, b in zip(o[0], rgb)))
            if sum((a - b) ** 2 for a, b in zip(target, rgb)) ** 0.5 < MAX_DISTANCE:
                votes[f"{value:06x}"][member] += 1
                pairs.append((rgb, target))
    a = np.array([p[0] for p in pairs], float)
    b = np.array([p[1] for p in pairs], float)
    best = min(((float(np.median(np.abs(255 * k * (a / 255) ** g - b))), float(g), float(k))
                for g in np.arange(0.8, 1.61, 0.05) for k in np.arange(0.8, 1.06, 0.01)))
    record = {"format": "fm4-fh4-paint-map-v1", "cars": cars, "pairs": len(pairs),
              "calibration": {"gamma": round(best[1], 3), "scale": round(best[2], 3),
                              "median_abs_error": round(best[0], 2)},
              "colors": {rgb: dict(counter.most_common()) for rgb, counter in sorted(votes.items())}}
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=1, sort_keys=True)
    print(cars, "cars,", len(pairs), "pairs,", len(votes), "colours; calibration", record["calibration"])


if __name__ == "__main__":
    main(*sys.argv[1:4])
