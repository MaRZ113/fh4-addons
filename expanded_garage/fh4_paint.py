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


PAINT_FOLDERS = ("carcolors/", "carcolors_pbc/")
NOT_PAINT = ("carbon", "liver", "matt")


def paint(member):
    """A plain paint of the colour folders (carcolors/ also holds carbon fibre and livery materials)."""
    return member.startswith(PAINT_FOLDERS) and not any(word in member.rsplit("/", 1)[-1] for word in NOT_PAINT)


@lru_cache(maxsize=1)
def paint_map():
    """fm4-fh4-paint-map-v1.json (research/extract_paint_map.py): FM4 colour -> FH4 materials of the same paint
    on the 195 cars present in both games, and the colour calibration for unseen colours."""
    return json.loads(files(__package__).joinpath("fm4-fh4-paint-map-v1.json").read_text(encoding="utf-8"))


def calibrated(rgb):
    """FM4 swatch RGB as the matching FH4 swatch would be (FM4 stores saturated paints brighter: Ferrari giallo
    modena 254,215,50 in FM4 is 217,183,0 in FH4; fit fh4 = 255 * k * (fm4/255) ** g over 1,120 pairs)."""
    fit = paint_map()["calibration"]
    return tuple(255 * fit["scale"] * (c / 255) ** fit["gamma"] for c in rgb)


def nearest_material(rgb, metallic, available):
    """The FH4 material native cars use for this FM4 colour; otherwise the closest paint (calibrated colour) in
    the paint folders, never one of the opposite finish first.

    visual-52 matched the raw FM4 RGB against the whole palette and required a "_solid" name for solid paints:
    the Integra's Phoenix yellow (FM4 ffe200) became Koenigsegg yolk yellow and then a brighter "yellow",
    while FH4's own ACU_IntegraR_01 uses carcolors/ctr2yellow (owner: paints too bright); liveries and carbon
    fibre were candidates too. EXPANDED_GARAGE_PAINT_MATCH=raw: the visual-52 raw-RGB nearest match."""
    import os
    raw = os.environ.get("EXPANDED_GARAGE_PAINT_MATCH") == "raw"
    known = paint_map()["colors"].get("%02x%02x%02x" % tuple(rgb), {}) if not raw else {}
    for member, _ in sorted(known.items(), key=lambda item: (-item[1], item[0])):
        if member in available and member in palette() and paint(member):
            return member
    target = rgb if raw else calibrated(rgb)
    opposite = "solid" if metallic else "metallic"
    candidates = [(m, e) for m, e in palette().items() if m in available
                  and (raw or paint(m))]
    if not candidates:
        raise ValueError("No native paint material from the palette exists in the shared library")
    # Only the opposite finish is pushed back: most carcolors/ names carry no finish word.
    ranked = sorted(candidates, key=lambda item: (finish(item[0]) == opposite,
                                                  sum((a - b) ** 2 for a, b in zip(item[1]["rgb"], target)),
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
