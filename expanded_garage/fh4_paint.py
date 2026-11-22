"""Manufacturer colours: ManufacturerColors.bin and Combo_Colors from the source paint list.

The FH4 "Manufacturer colors" tab reads the car archive's ManufacturerColors.bin
(a Grub 1.1 bundle with one MNCL 1.1 blob). Each record carries the swatch
colour as sRGB/255 floats and a shared library paint material. FM4 stores the
stock colours as Combo_Colors rows (RGB, Metallic). Each one is matched to the
nearest native paint material of the same finish from the palette collected
over all native archives (fh4-paint-palette-v1.json).
"""

from functools import lru_cache
from importlib.resources import files
import json
import struct

from .fh4_model import Chunk, bundle

LIBRARY_PREFIX = "Game:\\Media\\cars\\_library\\materials\\"
# Record header used by FOR_FiestaST_14 and most native archives.
RECORD_HEADER = struct.pack("<BI", 1, 0x1D)


@lru_cache(maxsize=1)
def palette():
    record = json.loads(files(__package__).joinpath("fh4-paint-palette-v1.json").read_text(encoding="utf-8"))
    return record["materials"]


def finish(member):
    name = member.rsplit("/", 1)[-1]
    if "_met" in name or "metallic" in name or "pearl" in name or "mica" in name:
        return "metallic"
    if "_solid" in name or "solid" in name:
        return "solid"
    return None


def nearest_material(rgb, metallic, available):
    """Closest palette material by RGB distance; same finish first, any finish otherwise."""
    wanted = "metallic" if metallic else "solid"
    candidates = [(m, e) for m, e in palette().items() if m in available]
    if not candidates:
        raise ValueError("No native paint material from the palette exists in the shared library")
    ranked = sorted(candidates, key=lambda item: (finish(item[0]) != wanted,
                                                  sum((a - b) ** 2 for a, b in zip(item[1]["rgb"], rgb)),
                                                  -item[1]["cars"], item[0]))
    return ranked[0][0]


def unpack_rgb(value):
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def manufacturer_colors(colors, available):
    """colors: Combo_Colors rows (Sequence, RGB, Metallic). Returns (bytes, receipt)."""
    rows = sorted(colors, key=lambda r: r["Sequence"])
    if not 0 < len(rows) < 256:
        raise ValueError("Manufacturer colour count must be 1..255")
    payload = bytearray([len(rows)])
    receipt = []
    for row in rows:
        rgb = unpack_rgb(int(row["RGB"]))
        member = nearest_material(rgb, bool(row["Metallic"]), available)
        path = (LIBRARY_PREFIX + member.replace("/", "\\")).encode("ascii")
        if len(path) > 255:
            raise ValueError(f"Paint material path too long: {member}")
        payload += RECORD_HEADER + struct.pack("<3f", *(c / 255 for c in rgb)) + bytes([len(path)]) + path
        receipt.append({"sequence": row["Sequence"], "rgb": list(rgb), "metallic": bool(row["Metallic"]),
                        "material": member, "material_rgb": palette()[member]["rgb"]})
    return bundle([Chunk("MNCL", (1, 1), bytes(payload))]), receipt


def read_manufacturer_colors(data):
    """Parse a generated file back into [(rgb floats, library member)]."""
    if data[:4] != b"burG" or data[20:24] != b"LCNM":
        raise ValueError("Unexpected ManufacturerColors bundle")
    offset, size = struct.unpack_from("<II", data, 20 + 12)
    payload = data[offset:offset + size]
    count, o, result = payload[0], 1, []
    for _ in range(count):
        if payload[o:o + 5] != RECORD_HEADER:
            raise ValueError("Unexpected ManufacturerColors record header")
        rgb = struct.unpack_from("<3f", payload, o + 5)
        length = payload[o + 17]
        path = payload[o + 18:o + 18 + length].decode("ascii")
        if not path.startswith(LIBRARY_PREFIX) or not all(0 <= v <= 1 for v in rgb):
            raise ValueError("Invalid ManufacturerColors record")
        result.append((rgb, path[len(LIBRARY_PREFIX):].replace("\\", "/").lower()))
        o += 18 + length
    if o != len(payload):
        raise ValueError("Trailing ManufacturerColors bytes")
    return result
