"""Dirt and rain masks the car presentation loads by fixed path (FH4 carpresentation11):

- `Textures/Damage/splatterMask.swatchbin`: the dirt mask in the TEXCOORD3 livery/dirt atlas. Native
  (ACU_RSXTypeS_02, 192 of 193 sampled cars): BC1 (TXCH format 0), 256x256 or 512x512, full mip chain,
  TXCH id fifteen zero bytes and 0x01. R marks the sides and ends (vertical panels), G the dirt itself
  (full along the sills and around the wheel arches, fading up the doors, speckled), B a small region.
- `Textures/Wiper/wiper_motion_gradient.swatchbin`: the windscreen wiper sweeps in the pane's TEXCOORD2
  (0..1): R the first blade's swept area, G the second's, each a ramp from the parked position (dark) to
  the end of the sweep (bright). BC5 (format 5), 1024x1024, full mips. 177 of 193 cars ship it.

Converted cars shipped neither, and the owner saw no dirt and no rain drops on the body or the windscreen
(visual-30). FM4 has no such masks (its dirt is a shader on the nodamage atlas, and it has no wiper
geometry), so both are generated from the car's own geometry: the dirt mask by rasterising the stock
LOD0 exterior through FM4's UV0 (which is TEXCOORD3, fh4_model.pack_streams) with the car-space
position and normal of every texel; the wiper ramps by rasterising the windscreen through the
TEXCOORD2 the converter gives panes (fh4_model.glass_channels), with two blades pivoting on its lower edge.
EXPANDED_GARAGE_DIRT=off ships neither (bisection switch).
"""

import os
import struct
import uuid

import numpy as np

from . import fh4_model

SPLATTER = "Textures/Damage/splatterMask.swatchbin"
WIPER = "Textures/Wiper/wiper_motion_gradient.swatchbin"
SPLATTER_SIZE, WIPER_SIZE = 512, 1024
BC1_FORMAT, BC5_FORMAT = 0, 5
NATIVE_ID = bytes(15) + b"\x01"
# Dirt by height above the ground (m): full up to FULL, none above CLEAR; arches add dirt within ARCH of
# the tyre. Calibrated by eye on ACU_RSXTypeS_02's G channel (sills full, mid-door fading, roof clean).
FULL, CLEAR, ARCH = 0.15, 0.60, 0.30
NOISE_CELL = 0.06
WIPER_SWEEP = np.radians(95.0)


def rasterize(uv, attributes, size):
    """Fill a size x size grid from triangles: uv (N, 3, 2) in texture space (v down), attributes
    (N, 3, K). Returns (size, size, K) float32 and a coverage mask. Later triangles win overlaps."""
    out = np.zeros((size, size, attributes.shape[2]), np.float32)
    covered = np.zeros((size, size), bool)
    pixels = uv * size - 0.5
    for tri, values in zip(pixels, attributes):
        lo = np.floor(tri.min(axis=0)).astype(int)
        hi = np.ceil(tri.max(axis=0)).astype(int)
        lo, hi = np.maximum(lo, 0), np.minimum(hi, size - 1)
        if np.any(hi < lo):
            continue
        xs, ys = np.meshgrid(np.arange(lo[0], hi[0] + 1), np.arange(lo[1], hi[1] + 1))
        (x0, y0), (x1, y1), (x2, y2) = tri
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-12:
            continue
        w1 = ((xs - x0) * (y2 - y0) - (x2 - x0) * (ys - y0)) / area
        w2 = ((x1 - x0) * (ys - y0) - (xs - x0) * (y1 - y0)) / area
        w0 = 1 - w1 - w2
        inside = (w0 >= -1e-4) & (w1 >= -1e-4) & (w2 >= -1e-4)
        if not inside.any():
            continue
        weights = np.stack([w0[inside], w1[inside], w2[inside]], axis=1)
        out[ys[inside], xs[inside]] = weights @ values
        covered[ys[inside], xs[inside]] = True
    return out, covered


def dilate(image, covered, passes=8):
    """Grow covered texels into empty ones (mips and bilinear filtering read across UV seams)."""
    image, covered = image.copy(), covered.copy()
    for _ in range(passes):
        total = np.zeros_like(image)
        count = np.zeros(covered.shape, np.float32)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            shifted = np.roll(np.roll(covered, dy, 0), dx, 1)
            total += np.roll(np.roll(image * covered[..., None], dy, 0), dx, 1)
            count += shifted
        grow = (~covered) & (count > 0)
        image[grow] = total[grow] / count[grow, None]
        covered |= grow
    return image


def value_noise(points, cell=NOISE_CELL, seed=0):
    """Smooth deterministic noise in [0, 1] over car-space points (trilinear over hashed lattice)."""
    scaled = points / cell
    base = np.floor(scaled).astype(np.int64)
    frac = scaled - base
    frac = frac * frac * (3 - 2 * frac)
    result = np.zeros(len(points))
    for corner in range(8):
        offset = np.array([(corner >> i) & 1 for i in range(3)])
        lattice = base + offset
        h = (lattice[:, 0] * 73856093 ^ lattice[:, 1] * 19349663 ^ lattice[:, 2] * 83492791 ^ seed) & 0xFFFFFF
        h = (h * 2654435761) & 0xFFFFFF
        weight = np.prod(np.where(offset, frac, 1 - frac), axis=1)
        result += weight * (h / 0xFFFFFF)
    return result


def splatter_rgba(draws, wheels, size=SPLATTER_SIZE):
    """(size, size, 4) uint8 dirt mask from exterior draws (FM4 UV0 = TEXCOORD3 atlas) and wheels
    [(centre xyz, radius, width)] in car space (Y up)."""
    uv, attributes = [], []
    for draw in draws:
        tri = draw.indices
        coords = draw.uv0[tri]
        keep = np.all((coords >= 0) & (coords <= 1), axis=(1, 2))   # atlas texels only, no tiled UVs
        if not keep.any():
            continue
        uv.append(coords[keep])
        attributes.append(np.concatenate([draw.positions[tri], draw.normals[tri]], axis=2)[keep])
    if not uv:
        return None
    grid, covered = rasterize(np.concatenate(uv), np.concatenate(attributes), size)
    position, normal = grid[..., :3], grid[..., 3:]
    normal = normal / np.maximum(np.linalg.norm(normal, axis=2, keepdims=True), 1e-6)
    ground = min(c[1] - r for c, r, _ in wheels) if wheels else position[covered][:, 1].min()
    height = position[..., 1] - ground
    dirt = np.clip((CLEAR - height) / (CLEAR - FULL), 0, 1)
    for centre, radius, width in wheels:
        lateral = np.abs(position[..., 0] - centre[0]) - width / 2
        radial = np.hypot(position[..., 1] - centre[1], position[..., 2] - centre[2])
        arch = np.clip((radius + ARCH - radial) / ARCH, 0, 1) * np.clip(1 - lateral / 0.35, 0, 1)
        dirt = np.maximum(dirt, 0.9 * arch)
    up = normal[..., 1]
    dirt *= np.clip(1.25 - up, 0.25, 1)   # upward faces (hood, roof) collect little
    flat = position.reshape(-1, 3)
    noise = value_noise(flat).reshape(size, size)
    fine = value_noise(flat, NOISE_CELL / 4, 1).reshape(size, size)
    dirt *= 0.55 + 0.3 * noise + 0.15 * fine
    sides = np.clip((0.75 - up) / 0.35, 0, 1)
    rgb = np.stack([sides, dirt, np.zeros_like(dirt)], axis=2)
    rgb = dilate(rgb, covered)
    rgba = np.empty((size, size, 4), np.uint8)
    rgba[..., :3] = np.rint(np.clip(rgb, 0, 1) * 255)
    rgba[..., 3] = 255
    return rgba


# Native exterior COLOR0 (carpaint weather block, carpaint_standard CarLightScenario PS reads v6.x, v6.y, v6.w):
# the dust layer is scaled by (1 - 2R) and so only shows where R < 0.5; the splatter by G^1.7; B unread, A 255.
# Owner's reference (FOR_FiestaST_14 off road, visual-46): splatter only around the wheels, dust over most of the
# body fading smoothly towards the window line. So G is the wheel-arch term over a faint base, and R rises
# slowly from ~0 at the sills to 0.5 around the waist (dust thinning out up to it, none above).
DIRT_HEIGHTS = (0.0, 0.4, 0.7, 1.0)
DIRT_LEVELS = (0.25, 0.15, 0.05, 0.0)
EXPOSURE_HEIGHTS = (0.0, 0.3, 0.6, 0.9, 1.15, 1.35, 1.6)
EXPOSURE_LEVELS = (0.02, 0.08, 0.18, 0.3, 0.42, 0.52, 0.6)
ARCH_SPLATTER = 0.9


def vertex_colors(positions, normals, wheels, ground):
    """(N, 4) uint8 COLOR0 for exterior vertices from car-space positions (Y up) and wheels
    [(centre xyz, radius, width)]; `ground` is the contact height."""
    height = positions[:, 1] - ground
    dirt = np.interp(height, DIRT_HEIGHTS, DIRT_LEVELS)
    for centre, radius, width in wheels:
        lateral = np.abs(positions[:, 0] - centre[0]) - width / 2
        radial = np.hypot(positions[:, 1] - centre[1], positions[:, 2] - centre[2])
        arch = np.clip((radius + ARCH - radial) / ARCH, 0, 1) * np.clip(1 - lateral / 0.35, 0, 1)
        dirt = np.maximum(dirt, ARCH_SPLATTER * arch)
    exposure = np.clip(np.interp(height, EXPOSURE_HEIGHTS, EXPOSURE_LEVELS), 0, 1)
    out = np.empty((len(positions), 4), np.uint8)
    out[:, 0] = np.rint(exposure * 255)
    out[:, 1] = np.rint(dirt * 255)
    out[:, 2] = np.rint(dirt * 0.85 * 255)
    out[:, 3] = 255
    return out


def wiper_rgba(draws, driver_side, size=WIPER_SIZE):
    """(size, size, 4) uint8 wiper sweep ramps over the windscreen's TEXCOORD2 (fh4_model.glass_channels)."""
    uv, attributes = [], []
    for draw in draws:
        channels = fh4_model.glass_channels(draw.positions, draw.uv1)
        tex2 = channels[1][draw.indices]
        uv.append(tex2)
        attributes.append(draw.positions[draw.indices])
    if not uv:
        return None
    grid, covered = rasterize(np.concatenate(uv), np.concatenate(attributes), size)
    points = np.concatenate(attributes).reshape(-1, 3)
    bottom = points[:, 1].min()
    left, right = points[:, 0].min(), points[:, 0].max()
    width = right - left
    # Distance up the pane from its lower edge (the slope, not the height) and across it.
    low = points[points[:, 1] < bottom + 0.05]
    base_z = np.median(low[:, 2])
    up = np.hypot(grid[..., 1] - bottom, grid[..., 2] - base_z)
    reach = np.hypot(points[:, 1] - bottom, points[:, 2] - base_z).max()
    # Tandem blades park along the lower edge pointing away from the driver (pivots at 15% and 55%
    # of the width from the driver's side) and sweep up to near vertical.
    driver_left = driver_side == "L"
    toward = 1.0 if driver_left else -1.0
    edge = left if driver_left else right
    out = np.zeros((size, size, 2), np.float32)
    for channel, (fraction, length) in enumerate(((0.15, 0.95), (0.55, 0.85))):
        pivot = edge + toward * fraction * width
        across = (grid[..., 0] - pivot) * toward
        angle = np.arctan2(up, across)
        radius = np.hypot(up, across)
        swept = (radius <= length * reach) & (radius >= 0.1 * reach) & (angle >= 0) & (angle <= WIPER_SWEEP)
        out[..., channel] = np.where(swept & covered, angle / WIPER_SWEEP, 0)
    rgba = np.zeros((size, size, 4), np.uint8)
    rgba[..., :2] = np.rint(out * 255)
    rgba[..., 3] = 255
    return rgba


def swatch(rgba, texconv_format, txch_format, block_bytes):
    """Native-layout swatch (one TXCB, mip chain, TXCH id NATIVE_ID) encoded by texconv."""
    import subprocess
    import tempfile
    from pathlib import Path
    from PIL import Image
    from .fh4_presentation import mip_header, texconv

    tool = texconv()
    if tool is None:
        raise ValueError("texconv is required for the dirt and wiper masks")
    height, width = rgba.shape[:2]
    with tempfile.TemporaryDirectory(prefix="mask-") as tmp:
        source = Path(tmp) / "mask.png"
        Image.fromarray(rgba, "RGBA").save(source)
        subprocess.run([str(tool), "-nologo", "-y", "-nogpu", "-dx10", "-f", texconv_format, "-m", "0",
                        "-o", tmp, str(source)], check=True, capture_output=True)
        dds = (Path(tmp) / "mask.dds").read_bytes()
    if dds[84:88] != b"DX10":
        raise ValueError("texconv did not return a DX10 texture")
    payload = dds[148:]
    mips, w, h = [], width, height
    while True:
        mips.append(max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block_bytes)
        if w == 1 and h == 1:
            break
        w, h = max(1, w // 2), max(1, h // 2)
    if sum(mips) != len(payload):
        raise ValueError("Mask mip chain size mismatch")
    header = mip_header(NATIVE_ID, width, height, txch_format, mips)
    return fh4_model.bundle([fh4_model.Chunk("TXCB", (0, 0), payload, (fh4_model.metadata("TXCH", header, 0),))])


def members(exterior, windscreen, wheels, driver_side):
    """{archive member: swatchbin} for the car: the dirt mask and, with a windscreen, the wiper ramps."""
    if os.environ.get("EXPANDED_GARAGE_DIRT") == "off":
        return {}
    from . import fh4_cache
    modules = (__name__,)
    out = {}
    wheel_rows = [(tuple(map(float, c)), float(r), float(w)) for c, r, w in wheels]
    key = [(d.positions, d.normals, d.uv0, d.indices) for d in exterior]
    out[SPLATTER] = fh4_cache.cached_bytes(
        "dirt-splatter", modules, (key, wheel_rows),
        lambda: _encode(splatter_rgba(exterior, wheel_rows), "BC1_UNORM", BC1_FORMAT, 8))
    if windscreen:
        key = [(d.positions, d.uv1, d.indices) for d in windscreen]
        out[WIPER] = fh4_cache.cached_bytes(
            "dirt-wiper", modules, (key, driver_side),
            lambda: _encode(wiper_rgba(windscreen, driver_side), "BC5_UNORM", BC5_FORMAT, 16))
    return {name: data for name, data in out.items() if data}


def _encode(rgba, texconv_format, txch_format, block_bytes):
    return b"" if rgba is None else swatch(rgba, texconv_format, txch_format, block_bytes)


def read(data):
    """(width, height, TXCH format, mip count) of a mask swatch."""
    _, _, _, _, _, total, count = fh4_model.HEADER.unpack_from(data)
    if total != len(data) or count != 1:
        raise ValueError("Mask swatch is not a one-blob bundle")
    tag, _, _, _, meta_offset, offset, size, _ = fh4_model.BLOB.unpack_from(data, fh4_model.HEADER.size)
    _, _, relative = struct.unpack_from("<IHH", data, meta_offset)
    fields = struct.unpack_from("<II16sIIII4I2II", data, meta_offset + relative)
    if tag.to_bytes(4, "big") != b"TXCB" or fields[2] != NATIVE_ID:
        raise ValueError("Unexpected mask swatch layout")
    return fields[3], fields[4], fields[13], fields[6] >> 16


def verify(read_member, names):
    """Check the generated dirt/wiper masks in an archive ({lower-case member: name}); return those used."""
    used = set()
    for member, fmt in ((SPLATTER, BC1_FORMAT), (WIPER, BC5_FORMAT)):
        key = member.casefold()
        if key not in names:
            continue
        width, height, txch_format, mips = read(read_member(names[key]))
        full = int(np.log2(max(width, height))) + 1
        if txch_format != fmt or width != height or mips != full:
            raise ValueError(f"Unexpected {member} layout: {width}x{height} format {txch_format}, {mips} mips")
        used.add(key)
    return used
