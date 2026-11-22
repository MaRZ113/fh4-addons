"""Extract the canonical GUID of each named car locator from the native roster.

Native Locators.xml files reuse one GUID per locator name across cars
(carLocator_headlightL is 533329f7-... on 398 of 400 sampled cars). Systems
that address a locator, such as the HomeSpace presentation shots with
RotAnimType="TargetPart" RotAnimPartToTarget="headlightL", depend on it.
A name is canonical when one GUID covers at least SHARE of the cars that carry
it and at least MIN_CARS cars carry it.

Writes expanded_garage/fh4-locator-guids-v1.json.

    python -m expanded_garage.research.extract_locator_guids <FH4 media/cars>
"""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

SHARE = 0.9
MIN_CARS = 20


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cars", type=Path)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fh4-locator-guids-v1.json")
    args = parser.parse_args()
    seen = defaultdict(Counter)
    cars = 0
    for path in sorted(args.cars.glob("*.zip")):
        try:
            with ZipFile(path) as z:
                names = {n.lower(): n for n in z.namelist()}
                if "locators.xml" not in names:
                    continue
                text = z.read(names["locators.xml"]).decode("utf-8", "replace")
        except (BadZipFile, NotImplementedError):
            continue
        cars += 1
        for block in re.findall(r"<Locator .*?</Locator>", text, re.S):
            name = re.search(r'<Name value="([^"]*)"', block)[1]
            guid = re.search(r'<GUID value="([^"]*)"', block)[1].lower()
            seen[name][guid] += 1
    canonical = {}
    for name, counts in sorted(seen.items()):
        guid, hits = counts.most_common(1)[0]
        total = sum(counts.values())
        if total >= MIN_CARS and hits / total >= SHARE:
            canonical[name] = {"guid": guid, "cars": total, "share": round(hits / total, 4)}
    guids = Counter(v["guid"] for v in canonical.values())
    shared = sorted(g for g, n in guids.items() if n > 1)
    # One GUID under two spellings (carLocator_quarterPaneRF / quarterPanelRF) is one locator renamed.
    record = {"format": "fh4-locator-guids-v1", "cars": cars, "share": SHARE, "min_cars": MIN_CARS,
              "shared_guids": {g: sorted(n for n, v in canonical.items() if v["guid"] == g) for g in shared},
              "locators": canonical}
    args.out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"{args.out}: {len(canonical)} canonical locator GUIDs over {cars} cars")


if __name__ == "__main__":
    main()
