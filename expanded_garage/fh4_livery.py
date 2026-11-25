"""Livery masks: the vinyl/decal editor's per-side projection masks (native `LiveryMasks/`).

Every native non-traffic car ships `LiveryMasks/Masks.xml` and six masks (front, back, left, right, top,
wing). The FM4 car archive carries the same set: `liverymasks/masks.xml` with the same schema (six sides,
origin, bounds, axes, scale, rotation) and 1024x1024 RGBA TGAs whose alpha is the side silhouette (RGB is
a coarse rectangle). Native masks (ACU_RSXTypeS_02) are 1024x1024 single-channel BC4 swatches holding that
silhouette, one mip, texture format 3. The XML is in mask-pixel space, so it carries over unchanged.
"""

import io
import os
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

import numpy as np

from . import fh4_model

FOLDER = "LiveryMasks/"
XML = FOLDER + "Masks.xml"
SIDES = ("front", "back", "left", "right", "top", "wing")
SIZE = 1024
BC4_FORMAT = 3
# Native mask TXCH id (ACU_RSXTypeS_02 LiveryMasks/*.swatchbin): fifteen zero bytes and 0x01.
NATIVE_ID = bytes(15) + b"\x01"


def bc4_encode(gray):
    """Encode an (H, W) uint8 image as BC4 UNORM blocks (8-value mode, nearest index)."""
    h, w = gray.shape
    if h % 4 or w % 4:
        raise ValueError("BC4 needs a multiple-of-4 image")
    blocks = gray.reshape(h // 4, 4, w // 4, 4).transpose(0, 2, 1, 3).reshape(-1, 16).astype(np.int32)
    hi, lo = blocks.max(axis=1), blocks.min(axis=1)
    flat = hi == lo
    # r0 > r1 selects the 8-value palette; a flat block uses r0 = r1 + 1 (or r1 = r0 - 1 at 255)
    # so it still decodes exactly through index 0 or 1.
    r0 = np.where(flat & (hi < 255), hi + 1, hi)
    r1 = np.where(flat & (hi == 255), lo - 1, lo)
    palette = np.stack([r0, r1] + [((7 - k) * r0 + k * r1) / 7 for k in range(1, 7)], axis=1)
    index = np.abs(blocks[:, :, None] - palette[:, None, :]).argmin(axis=2).astype(np.uint64)
    bits = np.zeros(len(blocks), dtype=np.uint64)
    for k in range(16):
        bits |= index[:, k] << np.uint64(3 * k)
    out = np.empty((len(blocks), 8), dtype=np.uint8)
    out[:, 0], out[:, 1] = r0, r1
    for i in range(6):
        out[:, 2 + i] = (bits >> np.uint64(8 * i)) & np.uint64(0xFF)
    return out.tobytes()


def bc4_decode(payload, width, height):
    b = np.frombuffer(payload, np.uint8)[:width * height // 2].reshape(-1, 8)
    r0, r1 = b[:, 0].astype(np.float64), b[:, 1].astype(np.float64)
    bits = np.zeros(len(b), dtype=np.uint64)
    for i in range(6):
        bits |= b[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    index = np.stack([(bits >> np.uint64(3 * k)) & np.uint64(7) for k in range(16)], 1).astype(int)
    big = (r0 > r1)[:, None]
    eight = np.stack([r0, r1] + [((7 - k) * r0 + k * r1) / 7 for k in range(1, 7)], 1)
    six = np.stack([r0, r1] + [((5 - k) * r0 + k * r1) / 5 for k in range(1, 5)]
                   + [np.zeros_like(r0), np.full_like(r0, 255)], 1)
    values = np.take_along_axis(np.where(big, eight, six), index, 1)
    return (values.reshape(height // 4, width // 4, 4, 4).transpose(0, 2, 1, 3)
            .reshape(height, width).round().astype(np.uint8))


def mask_swatch(gray):
    """Native-layout LiveryMasks swatch: one BC4 mip, format 3 (header matches ACU_RSXTypeS_02)."""
    height, width = gray.shape
    payload = bc4_encode(gray)
    header = struct.pack("<II16sIIIHBB4I2I3I3I", 56, 80, NATIVE_ID, width, height, 1, 1, 1, 0,
                         0, 0, 0, 0, 64, 68, BC4_FORMAT, 76, 0xffffffff, len(payload), 0, 0xffffffff)
    return fh4_model.bundle([fh4_model.Chunk("TXCB", (0, 0), payload,
                                             (fh4_model.metadata("TXCH", header, 0),))])


def read_mask(data):
    """(width, height, format, payload) of a mask swatch written by mask_swatch or shipped natively."""
    _, _, _, _, header_end, total, count = fh4_model.HEADER.unpack_from(data)
    if total != len(data) or count != 1:
        raise ValueError("Livery mask is not a one-blob bundle")
    tag, _, _, metas, meta_offset, offset, size, _ = fh4_model.BLOB.unpack_from(data, fh4_model.HEADER.size)
    kind, flags, relative = struct.unpack_from("<IHH", data, meta_offset)
    fields = struct.unpack_from("<II16sIIIHBB4I2I3I3I", data, meta_offset + relative)
    width, height, fmt, length = fields[3], fields[4], fields[15], fields[18]
    if tag.to_bytes(4, "big") != b"TXCB" or length != size or size != width * height // 2:
        raise ValueError("Unexpected livery mask layout")
    return width, height, fmt, data[offset:offset + size]


def masks_from_source(files):
    """{member: bytes} from FM4 `liverymasks/*` raw members ({lower-case basename: bytes})."""
    from PIL import Image

    xml = files.get("masks.xml")
    if xml is None:
        return {}
    text = xml.decode("utf-8-sig")
    if "<LiveryMasks" not in text or any(f"<{side.capitalize()} " not in text for side in SIDES):
        raise ValueError("FM4 masks.xml does not describe the six livery sides")
    out = {XML: xml}
    root = ET.fromstring(xml)
    for side in SIDES:
        data = files.get(f"{side}.tga")
        if data is None:
            definition = root.find(side.capitalize())
            if definition is None or definition.get("valid") != "false":
                return {}
            out[f"{FOLDER}{side}.swatchbin"] = mask_swatch(np.zeros((SIZE, SIZE), dtype=np.uint8))
            continue
        image = Image.open(io.BytesIO(data))
        if image.size != (SIZE, SIZE):
            image = image.resize((SIZE, SIZE), Image.Resampling.BILINEAR)
        alpha = np.asarray(image.convert("RGBA"))[..., 3]
        out[f"{FOLDER}{side}.swatchbin"] = mask_swatch(np.ascontiguousarray(alpha))
    return out


def probe_masks(donor):
    """EXPANDED_GARAGE_LIVERY_PROBE=<native car ZIP>: its LiveryMasks byte for byte (bisection)."""
    out = {}
    with ZipFile(donor) as archive:
        for name in archive.namelist():
            if name.casefold().startswith(FOLDER.casefold()):
                base = name.split("/")[-1]
                out[XML if base.casefold() == "masks.xml" else FOLDER + base.casefold()] = archive.read(name)
    return out


def livery_members(raw_files):
    """Archive members for the livery editor. EXPANDED_GARAGE_LIVERY_MASKS=off ships none (bisection)."""
    if os.environ.get("EXPANDED_GARAGE_LIVERY_MASKS") == "off":
        return {}
    probe = os.environ.get("EXPANDED_GARAGE_LIVERY_PROBE")
    if probe:
        return probe_masks(Path(probe))
    return masks_from_source(raw_files)


def verify(read, names):
    """Check the LiveryMasks set in an archive ({lower-case member: real name}); return members used."""
    members = {n for n in names if n.startswith(FOLDER.casefold())}
    if not members:
        return set()
    expected = {XML.casefold()} | {f"{FOLDER}{side}.swatchbin".casefold() for side in SIDES}
    if members != expected:
        raise ValueError(f"Incomplete LiveryMasks set: {sorted(expected ^ members)}")
    root = ET.fromstring(read(names[XML.casefold()]))
    if root.tag != "LiveryMasks" or any(root.find(side.capitalize()) is None for side in SIDES):
        raise ValueError("Masks.xml lacks a livery side")
    for side in SIDES:
        width, height, fmt, payload = read_mask(read(names[f"{FOLDER}{side}.swatchbin".casefold()]))
        if (width, height, fmt) != (SIZE, SIZE, BC4_FORMAT):
            raise ValueError(f"Unexpected livery mask format for {side}")
        if root.find(side.capitalize()).get("valid") != "false" and not bc4_decode(payload, width, height).any():
            raise ValueError(f"Empty livery mask {side}")
    return members
