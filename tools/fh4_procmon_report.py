"""Summarize only FH4 asset-loader events from a Procmon CSV capture."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re


ASSET = re.compile(
    r"(^|\\)(zipmanifest\.xml|acu_integrar_01(?:\.zip)?|mrz_integraj_00(?:\.zip)?)(?:$|\\)",
    re.I,
)
OPS = {"CreateFile", "ReadFile", "QueryOpen", "QueryDirectory", "CreateFileMapping",
       "QueryBasicInformationFile", "QueryStandardInformationFile"}


def summarize(path):
    with path.open("rb") as raw:
        bom = raw.read(2)
    encoding = "utf-16" if bom in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    counts = Counter()
    phase_pids = set()
    with path.open(encoding=encoding, newline="") as source:
        reader = csv.DictReader(source)
        required = {"Process Name", "PID", "Operation", "Path", "Result"}
        if not required <= set(reader.fieldnames or ()):
            raise ValueError("Expected standard English Procmon CSV columns")
        for event in reader:
            if event["Process Name"].casefold() != "forzahorizon4.exe":
                continue
            phase_pids.add(event["PID"])
            normalized = event["Path"].replace("/", "\\").lower()
            if event["Operation"] not in OPS or not ASSET.search(normalized):
                continue
            media = re.search(r"\\(mediapc|media)\\", normalized)
            asset = normalized[media.start()+1:] if media else normalized
            counts[(asset,event["Operation"],event["Result"])] += 1
    return {
        "capture": path.name, "fh4_pids": sorted(phase_pids),
        "operations": [{"path": p, "operation": op, "result": result, "count": n}
                       for (p,op,result),n in sorted(counts.items())],
        "scope": "Host file I/O only. In-archive member lookups may be invisible.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        print(json.dumps([summarize(p) for p in args.csv],indent=2))
    except (OSError, ValueError, UnicodeError) as error:
        parser.exit(1,f"{error}\n")


if __name__ == "__main__":
    main()
