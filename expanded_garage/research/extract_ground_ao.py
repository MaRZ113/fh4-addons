"""Extract the native ground-AO texture container as a template.

Native cars (78 of 79 sampled) ship Textures/AO/ground_AO.swatchbin: a 512x512 single-channel 8-bit
texture with a full mip chain (349525 payload bytes, zero GUID), seen from above, covering 10 x 10 m
around the AO volume centre (row 0 at +z). It is the soft contact shadow the ground shows under a car.
The generator fills only the payload; this keeps the bytes around it.

Writes expanded_garage/fh4-ground-ao-v1.json.

    python -m expanded_garage.research.extract_ground_ao <FH4 media/cars> [--donor ACU_RSXTypeS_02]
"""

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

from .. import fh4_model

SIZE = 512
PAYLOAD = sum((SIZE >> level) ** 2 for level in range(10))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cars", type=Path)
    parser.add_argument("--donor", default="ACU_RSXTypeS_02")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fh4-ground-ao-v1.json")
    args = parser.parse_args()
    archive = ZipFile(args.cars / f"{args.donor}.zip")
    member = next(n for n in archive.namelist() if n.lower().endswith("ground_ao.swatchbin"))
    data = archive.read(member)
    _, _, _, _, _, offset, size, _ = fh4_model.BLOB.unpack_from(data, fh4_model.HEADER.size)
    if size != PAYLOAD:
        raise SystemExit(f"Unexpected ground AO payload {size}, expected {PAYLOAD}")
    record = {"format": "fh4-ground-ao-v1", "donor": args.donor, "member": member, "size": SIZE,
              "prefix": data[:offset].hex(), "suffix": data[offset + size:].hex(), "payload": size}
    args.out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"{args.out}: template from {args.donor}/{member} ({offset} + {size} + {len(data) - offset - size} bytes)")


if __name__ == "__main__":
    main()
