"""Extract a native Autovista points-of-interest file (<media>.avpins) and its fit hardpoints.

Writes expanded_garage/fh4-autovista-v1.json with the donor's avpins text and the
donor locator positions used to map root-frame positions onto the target car.

    python -m expanded_garage.research.extract_autovista <media/cars/DONOR.zip>
"""

import argparse
import hashlib
import json
import re
from pathlib import Path
from zipfile import ZipFile

FIT = ("carLocator_headlightL", "carLocator_headlightR", "carLocator_taillightL",
       "carLocator_taillightR", "carLocator_steeringWheel")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("donor", type=Path)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fh4-autovista-v1.json")
    args = parser.parse_args()
    media = args.donor.stem
    with ZipFile(args.donor) as z:
        names = {n.lower(): n for n in z.namelist()}
        avpins = z.read(names[f"{media.lower()}.avpins"]).decode("utf-8-sig")
        locators = z.read(names["locators.xml"]).decode("utf-8", "replace")
    points = {}
    for block in re.findall(r"<Locator .*?</Locator>", locators, re.S):
        name = re.search(r'<Name value="([^"]*)"', block)[1]
        if name in FIT:
            m = dict(re.findall(r'value\._(\d\d)="([^"]*)"', block))
            points[name] = [float(m["41"]), float(m["42"]), float(m["43"])]
    missing = [n for n in FIT if n not in points]
    if missing:
        raise SystemExit(f"Donor lacks fit locators {missing}")
    record = {"format": "fh4-autovista-v1", "donor": media,
              "donor_sha256": hashlib.sha256(args.donor.read_bytes()).hexdigest(),
              "driver_side": "L" if points["carLocator_steeringWheel"][0] < 0 else "R",
              "fit_points": points, "avpins": avpins}
    args.out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"{args.out}: {len(avpins)} chars from {media}, driver side {record['driver_side']}")


if __name__ == "__main__":
    main()
