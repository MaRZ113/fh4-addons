"""Generate a car's aovolumetexture.bin: the ambient-occlusion volume that darkens the ground and
nearby surfaces around the car (the soft shadow under it).

Native layout (reverse-engineered on ACU_RSXTypeS_02, FOR_FiestaST_14, AH_3000_65; magic 'AOVT'):
  u32 magic, u32 version 3, u32 finest dims (128, 64, 128), f32 half-extent (5, 4, 5) m,
  f32 centre (car bounding-box centre), u32 0, u32 vertex count, float4 vertices (triangle list of
  the car's occluder proxy, w 0), then three nested levels, each i32 offset[3], u32 dims[3] and RGBA8
  voxels (x fastest, then y, then z): level 0 covers the box at 1/4 resolution, level 1 the car plus
  ~1.56 m at 1/2, level 2 the car plus ~0.63 m at full resolution, each centred in its grid.
  A is the occlusion (0 in the open, 254 inside the car), RGB the unoccluded direction stored as
  (-x, y, -z), 127-biased.

Our occlusion estimate (hit fraction of 48 rays over 6 m against a solid voxelisation of the proxy,
A = 255 * (1 - exp(-3 * fraction)), three 3-tap blur passes per level) reproduces the native volumes of
FOR_FiestaST_14 and ACU_RSXTypeS_02 from their own proxies at an A RMS error of 12 / 13 / 24 (coarse /
mid / fine), against 16 / 32 / 45 unblurred at 3 m.
"""

import struct

import numpy as np

DIMS = np.array((128, 64, 128))
HALF_EXTENT = np.array((5.0, 4.0, 5.0))
MARGINS = (None, 3.125, 1.25)   # total margin around the car per level (level 0 covers everything)
CELL = 0.03
REACH, STEP, RAYS = 6.0, 0.06, 48
GAIN = 3.0
BLUR_PASSES = 3


def _grow(g):
    out = g.copy()
    for axis in range(3):
        for shift in (1, -1):
            out |= np.roll(g, shift, axis=axis)
    return out


def solid(triangles, cell=CELL, pad=0.12):
    """Boolean occupancy of the closed volume bounded by the triangle soup (n, 3, 3)."""
    points = triangles.reshape(-1, 3)
    low = points.min(0) - pad
    shape = np.ceil((points.max(0) + pad - low) / cell).astype(int) + 1
    surface = np.zeros(shape, bool)
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    longest = np.maximum.reduce([np.linalg.norm(b - a, axis=1), np.linalg.norm(c - a, axis=1),
                                 np.linalg.norm(c - b, axis=1)])
    steps = np.clip(np.ceil(longest / (cell * 0.5)).astype(int), 1, 400)
    for k in np.unique(steps):
        pick = steps == k
        u, v = np.meshgrid(np.linspace(0, 1, k + 1), np.linspace(0, 1, k + 1))
        keep = (u + v) <= 1
        u, v = u[keep], v[keep]
        samples = (a[pick, None] + (b[pick] - a[pick])[:, None] * u[None, :, None]
                   + (c[pick] - a[pick])[:, None] * v[None, :, None]).reshape(-1, 3)
        index = np.floor((samples - low) / cell).astype(int)
        surface[index[:, 0], index[:, 1], index[:, 2]] = True
    # Close small gaps (open LOD seams, window holes) before filling the interior.
    for _ in range(2):
        surface = _grow(surface)
    outside = np.zeros_like(surface)
    for axis in range(3):
        for end in (0, -1):
            outside[(slice(None),) * axis + (end,)] = True
    outside &= ~surface
    while True:
        grown = _grow(outside) & ~surface
        if grown.sum() == outside.sum():
            break
        outside = grown
    filled = ~outside
    for _ in range(2):   # undo the closing dilation
        filled &= ~_grow(~filled)
    return filled, low


def directions(n=RAYS):
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = np.pi * (1 + 5 ** 0.5) * i
    return np.c_[np.cos(theta) * np.sin(phi), np.cos(phi), np.sin(theta) * np.sin(phi)]


def occlusion(points, occupancy, low, cell=CELL, chunk=20000):
    """(hit fraction, unoccluded direction, inside) per point."""
    shape = np.array(occupancy.shape)
    rays = directions()
    distances = np.arange(STEP, REACH + 1e-9, STEP)
    hit_fraction = np.zeros(len(points))
    bent = np.zeros((len(points), 3))
    inside = np.zeros(len(points), bool)

    def lookup(q):
        index = np.floor((q - low) / cell).astype(int)
        ok = np.all((index >= 0) & (index < shape), axis=1)
        out = np.zeros(len(q), bool)
        out[ok] = occupancy[index[ok, 0], index[ok, 1], index[ok, 2]]
        return out

    for start in range(0, len(points), chunk):
        p = points[start:start + chunk]
        inside[start:start + chunk] = lookup(p)
        for ray in rays:
            hit = np.zeros(len(p), bool)
            for distance in distances:
                hit |= lookup(p + ray * distance)
            hit_fraction[start:start + chunk] += hit
            bent[start:start + chunk] += np.outer(~hit, ray)
    hit_fraction /= len(rays)
    norm = np.linalg.norm(bent, axis=1, keepdims=True)
    return hit_fraction, np.where(norm > 1e-6, bent / np.maximum(norm, 1e-9), 0.0), inside


def tyre(center, radius, width, segments=16):
    """Closed cylinder along X as triangles."""
    angles = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    ring = np.c_[np.zeros(segments), radius * np.cos(angles), radius * np.sin(angles)]
    left, right = ring + (-width / 2, 0, 0) + center, ring + (width / 2, 0, 0) + center
    tris = []
    for i in range(segments):
        j = (i + 1) % segments
        tris += [(left[i], left[j], right[i]), (left[j], right[j], right[i]),
                 (np.asarray(center) - (width / 2, 0, 0), left[j], left[i]),
                 (np.asarray(center) + (width / 2, 0, 0), right[i], right[j])]
    return np.asarray(tris, float)


def levels(low, high, centre):
    """[(resolution, offset, dims)] for the three nested levels."""
    size = high - low
    result = []
    for level, margin in enumerate(MARGINS):
        resolution = DIMS // (2 ** (2 - level))
        if margin is None:
            result.append((resolution, np.zeros(3, int), resolution))
            continue
        voxel = 2 * HALF_EXTENT / resolution
        dims = np.ceil((size + margin) / voxel).astype(int)
        dims = np.minimum(dims + dims % 2, resolution)
        result.append((resolution, (resolution - dims) // 2, dims))
    return result


def _blur(field, passes):
    """Separable 3-tap box blur over the first three (z, y, x) axes, edges clamped."""
    for _ in range(passes):
        for axis in range(3):
            pad = [(1, 1) if a == axis else (0, 0) for a in range(field.ndim)]
            padded = np.pad(field, pad, mode="edge")
            n = field.shape[axis]
            field = sum(np.take(padded, range(k, k + n), axis=axis) for k in range(3)) / 3
    return field


def build(triangles):
    """aovolumetexture.bin bytes for an occluder proxy given as triangles (n, 3, 3) in the car frame."""
    triangles = np.asarray(triangles, float)
    low, high = triangles.reshape(-1, 3).min(0), triangles.reshape(-1, 3).max(0)
    centre = (low + high) / 2
    occupancy, grid_low = solid(triangles)
    vertices = np.c_[triangles.reshape(-1, 3), np.zeros(len(triangles) * 3)].astype("<f4")
    out = bytearray(struct.pack("<4sIIII3f3fII", b"TVOA", 3, *DIMS, *HALF_EXTENT, *centre, 0, len(vertices)))
    out += vertices.tobytes()
    for resolution, offset, dims in levels(low, high, centre):
        voxel = 2 * HALF_EXTENT / resolution
        k, j, i = np.meshgrid(np.arange(dims[2]), np.arange(dims[1]), np.arange(dims[0]), indexing="ij")
        index = np.stack([i, j, k], -1).reshape(-1, 3) + offset
        points = centre - HALF_EXTENT + (index + 0.5) * voxel
        fraction, bent, inside = occlusion(points, occupancy, grid_low)
        alpha = np.where(inside, 254.0, 255 * (1 - np.exp(-GAIN * fraction)))
        bent = np.where(inside[:, None], 0.0, bent)
        # Native volumes are smooth across the car surface; a raw voxel step reads as a hard shadow edge.
        field = _blur(np.c_[bent, alpha].reshape(dims[2], dims[1], dims[0], 4), BLUR_PASSES).reshape(-1, 4)
        rgb = 127 + 127 * field[:, :3] * (-1, 1, -1)
        voxels = np.round(np.c_[rgb, field[:, 3]]).clip(0, 254).astype(np.uint8)
        out += struct.pack("<3i3I", *offset, *dims) + voxels.tobytes()
    return bytes(out)


def read(data):
    """Parsed volume: header fields, proxy vertices and [(offset, dims, voxels[z, y, x, 4])]."""
    magic, version, *rest = struct.unpack_from("<4sIIII3f3fII", data)
    if magic != b"TVOA" or version != 3:
        raise ValueError("Not an AOVT v3 volume")
    dims, extent, centre, _, count = rest[0:3], rest[3:6], rest[6:9], rest[9], rest[10]
    offset = 52 + 16 * count
    vertices = np.frombuffer(data, "<f4", 4 * count, 52).reshape(count, 4)
    blocks = []
    while offset < len(data):
        *origin, dx, dy, dz = struct.unpack_from("<3i3I", data, offset)
        size = dx * dy * dz * 4
        blocks.append((tuple(origin), (dx, dy, dz),
                       np.frombuffer(data, np.uint8, size, offset + 24).reshape(dz, dy, dx, 4)))
        offset += 24 + size
    if offset != len(data) or len(blocks) != 3:
        raise ValueError("Truncated AOVT volume")
    return dict(dims=tuple(dims), extent=tuple(extent), centre=tuple(centre), vertices=vertices, blocks=blocks)


GROUND_SIZE, GROUND_SAMPLES, GROUND_REACH, GROUND_RAYS = 512, 128, 6.0, 640
GROUND_MEMBER = "Textures/AO/ground_AO.swatchbin"


def ground_occlusion(triangles, samples=GROUND_SAMPLES):
    """Cosine-weighted upper-hemisphere occlusion of the ground under the car, [z, x] with row 0 at +z,
    over the 10 x 10 m square around the volume centre. Native ground_AO = 255 * (1 - this) (on the
    native ACU proxy: correlation 0.998, RMS error 5 of 255)."""
    triangles = np.asarray(triangles, float)
    points = triangles.reshape(-1, 3)
    centre = (points.min(0) + points.max(0)) / 2
    ground = points[:, 1].min() + 0.02
    occupancy, low = solid(triangles)
    shape = np.array(occupancy.shape)
    rays = directions(GROUND_RAYS)
    rays = rays[rays[:, 1] > 0.05]
    axis = (np.arange(samples) + 0.5) / samples * 2 * HALF_EXTENT[0] - HALF_EXTENT[0]
    zz, xx = np.meshgrid(axis[::-1], axis, indexing="ij")
    p = np.c_[centre[0] + xx.ravel(), np.full(xx.size, ground), centre[2] + zz.ravel()]
    result = np.zeros(len(p))
    for ray in rays:
        hit = np.zeros(len(p), bool)
        for distance in np.arange(STEP, GROUND_REACH, STEP):
            index = np.floor((p + ray * distance - low) / CELL).astype(int)
            ok = np.all((index >= 0) & (index < shape), axis=1)
            found = np.zeros(len(p), bool)
            found[ok] = occupancy[index[ok, 0], index[ok, 1], index[ok, 2]]
            hit |= found
        result += hit * ray[1]
    image = (result / rays[:, 1].sum()).reshape(samples, samples)
    # Two 3x3 box passes hide the residual ray banding (native falloff is smooth).
    for _ in range(2):
        padded = np.pad(image, 1, mode="edge")
        image = sum(padded[i:i + samples, j:j + samples] for i in range(3) for j in range(3)) / 9
    return image


def _upsample(image, size):
    """Bilinear resize of a square array to size x size (cell-centred)."""
    n = image.shape[0]
    pos = (np.arange(size) + 0.5) * n / size - 0.5
    i0 = np.clip(np.floor(pos).astype(int), 0, n - 1)
    i1 = np.clip(i0 + 1, 0, n - 1)
    t = np.clip(pos - i0, 0, 1)
    rows = image[i0] * (1 - t)[:, None] + image[i1] * t[:, None]
    return rows[:, i0] * (1 - t)[None, :] + rows[:, i1] * t[None, :]


def ground_texture(triangles, template):
    """Textures/AO/ground_AO.swatchbin bytes: native container (fh4-ground-ao-v1.json) with our R8 mips."""
    level = np.clip(255 * (1 - _upsample(ground_occlusion(triangles), GROUND_SIZE)), 0, 255)
    chain = []
    while True:
        chain.append(np.round(level).astype(np.uint8).tobytes())
        if level.shape[0] == 1:
            break
        level = level.reshape(level.shape[0] // 2, 2, level.shape[1] // 2, 2).mean((1, 3))
    payload = b"".join(chain)
    if len(payload) != template["payload"]:
        raise ValueError("Ground AO mip chain does not fit the native container")
    return bytes.fromhex(template["prefix"]) + payload + bytes.fromhex(template["suffix"])
