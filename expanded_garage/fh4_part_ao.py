"""Per-model ambient-occlusion maps (native Textures/AO/<model>_AO.swatchbin).

Every native model has one AO record in the Carbin naming a texture under Textures/AO (ACU_RSXTypeS_02: 113
records, dashPad_a_AO 512x512, doorCardF_a_AO 256x256, hood_a_AO 512x256): single-channel R8 (TXCH format 19)
with a full mip chain, sampled through TEXCOORD2 - the model's own non-overlapping unwrap (vertices inside
the AO charts: dashPad T2 0.68 against T0 0.47 / T3 0.14, doorCard 0.81, hood 0.99). Cabin maps are dark
(dashPad median 9 of 255): the occlusion is baked against the whole car, so the roof and the body shell shade
the cabin and only what sees out through the glass stays light. FM4 has no such maps; without them converted
cabins looked flat and bright (owner, visual-30/31).

This module generates them:
- unwrap(): charts of LOD-0 triangles grown over shared edges while their normals stay within 60 degrees of
  the chart's seed, each projected on its own plane, shelf-packed into the unit square with padding; other LODs
  reuse the chart of the nearest LOD-0 triangle. TEXCOORD2 is written per draw (vertices split per chart).
- bake(): per texel, at the interpolated authored normal, one fixed set of cosine-weighted hemisphere rays
  marched through a voxel shell of the car's opaque geometry (a ray leaving through glass counts half): a
  fine grid near the surface, a dilated one further out. The same rays at every texel keep chart seams
  invisible (per-vertex baking showed the triangles of large faces).
- texture(): R8, format 19, full mip chain, padding dilated so mips do not bleed background.
"""

from collections import defaultdict, deque
from dataclasses import replace
import struct
import uuid

import numpy as np

from . import fh4_model
from .fh4_ao_volume import _grow

AO_FORMAT = 19
CHART_COS = 0.5            # 60 degrees from the chart seed
TEXELS_PER_METRE = 160.0   # target density before the size cap
MAX_SIZE, MIN_SIZE = 1024, 64
BAKE_MAX = 512             # bake resolution cap; larger maps are upsampled
PADDING = 4                # texels between charts at the final size
FINE_CELL, FINE_REACH = 0.015, 0.15
COARSE_CELL = 0.03
# Ray reach calibrated on native cabin maps: at 0.35 m the SX4 seat median is 37 of 255 (ACU seatF 44), the cabin
# 57; at 1.2 m the roof occluded everything (seat median 0).
REACH = 0.35
RAYS = 48
MIN_COSINE = 0.25          # grazing rays skim the surface's own (dilated) voxels
BACKGROUND = 255           # unmapped texels: no darkening for draws without TEXCOORD2
# Response: FM4 cabins already carry the cockpit texel scale (0.25-0.5), so raw visibility read too dark and
# harsh (owner, visual-34). value = 255 * (FLOOR + (1 - FLOOR) * visibility ** GAMMA).
GAMMA, FLOOR = 0.6, 0.15
GLASS_TRANSMISSION = 0.5
# Enclosure: the 0.35 m local term cannot see that the cabin is closed (the headliner read bright, owner
# visual-37). A second march to FAR_REACH on the coarse grid gives the share of light that leaves the car;
# visibility = local * (ENCLOSURE_FLOOR + (1 - ENCLOSURE_FLOOR) * far).
FAR_REACH = 1.5
ENCLOSURE_FLOOR = 0.35


# --- unwrap -------------------------------------------------------------------------------------------------

def _faces(draws):
    """Triangles of the given draws: corners (n,3,3), owning draw index, local triangle index."""
    tris, owner, local = [], [], []
    for i, d in enumerate(draws):
        t = d.positions[d.indices].astype(np.float64)
        tris.append(t)
        owner.append(np.full(len(t), i))
        local.append(np.arange(len(t)))
    if not tris:
        return np.zeros((0, 3, 3)), np.zeros(0, int), np.zeros(0, int)
    return np.concatenate(tris), np.concatenate(owner), np.concatenate(local)


def _normals(tris):
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    area = np.linalg.norm(n, axis=1)
    return n / np.maximum(area, 1e-15)[:, None], area / 2


def _adjacency(tris):
    """CSR neighbours over shared (welded) edges."""
    corners = np.rint(tris.reshape(-1, 3) / 1e-4).astype(np.int64)
    _, ids = np.unique(corners, axis=0, return_inverse=True)
    ids = ids.reshape(-1, 3)
    edges = np.concatenate([ids[:, [0, 1]], ids[:, [1, 2]], ids[:, [2, 0]]])
    edges.sort(axis=1)
    face = np.tile(np.arange(len(ids)), 3)
    key = edges[:, 0] * (ids.max() + 1) + edges[:, 1]
    order = np.argsort(key, kind="stable")
    key, face = key[order], face[order]
    same = np.flatnonzero(key[1:] == key[:-1])
    a, b = face[same], face[same + 1]
    pairs = np.concatenate([np.c_[a, b], np.c_[b, a]])
    pairs = pairs[pairs[:, 0] != pairs[:, 1]]
    pairs = pairs[np.lexsort((pairs[:, 1], pairs[:, 0]))]
    start = np.searchsorted(pairs[:, 0], np.arange(len(ids) + 1))
    return start, pairs[:, 1]


def _grow_charts(tris):
    normals, area = _normals(tris)
    start, neighbour = _adjacency(tris)
    chart = np.full(len(tris), -1)
    seeds = []
    for seed in np.argsort(-area):
        if chart[seed] >= 0:
            continue
        cid = len(seeds)
        seeds.append(normals[seed])
        chart[seed] = cid
        queue = deque([seed])
        while queue:
            f = queue.popleft()
            for g in neighbour[start[f]:start[f + 1]]:
                if chart[g] < 0 and normals[g] @ normals[seed] > CHART_COS:
                    chart[g] = cid
                    queue.append(g)
    return chart, normals, area


def _frames(tris, chart, normals, area, count):
    frames = []
    for cid in range(count):
        pick = chart == cid
        n = (normals[pick] * area[pick, None]).sum(axis=0)
        if np.linalg.norm(n) < 1e-9:
            n = normals[pick][0] if np.linalg.norm(normals[pick][0]) > 0.5 else np.array([0.0, 0.0, 1.0])
        n = n / np.linalg.norm(n)
        helper = np.eye(3)[np.argmin(np.abs(n))]
        u = np.cross(n, helper)
        u /= np.linalg.norm(u)
        frames.append((u, np.cross(n, u)))
    return frames


def _project(points, frame):
    u, v = frame
    return np.stack([points @ u, points @ v], axis=-1)


def _pack(sizes, side, pad):
    """Shelf packing of (w, h) texel rectangles into side x side; returns offsets or None."""
    order = np.argsort(-sizes[:, 1], kind="stable")
    offsets = np.zeros_like(sizes)
    x = y = shelf = 0.0
    for i in order:
        w, h = sizes[i] + pad
        if w > side:
            return None
        if x + w > side:
            x, y, shelf = 0.0, y + shelf, 0.0
        if y + h > side:
            return None
        offsets[i] = (x + pad / 2, y + pad / 2)
        x += w
        shelf = max(shelf, h)
    return offsets


def unwrap(draws):
    """Draws with TEXCOORD2 (`uv2`) for one model, and the map size. Transparent draws are left alone."""
    opaque = [i for i, d in enumerate(draws) if not d.transparent]
    if not opaque:
        return list(draws), 0
    finest = min(draws[i].lod for i in opaque)
    base = [i for i in opaque if draws[i].lod == finest]
    tris, owner, local = _faces([draws[i] for i in base])
    chart, normals, area = _grow_charts(tris)
    count = int(chart.max()) + 1
    frames = _frames(tris, chart, normals, area, count)
    flat = np.stack([_project(tris[k], frames[chart[k]]) for k in range(len(tris))])   # (n,3,2) metres
    low = np.full((count, 2), np.inf)
    high = np.full((count, 2), -np.inf)
    np.minimum.at(low, chart, flat.min(axis=1))
    np.maximum.at(high, chart, flat.max(axis=1))
    extent = high - low
    total = float(area.sum())
    side = int(min(MAX_SIZE, max(MIN_SIZE, 2 ** np.ceil(np.log2(np.sqrt(total) * TEXELS_PER_METRE * 1.4)))))
    # Largest density that packs: binary search on texels per metre.
    lo_d, hi_d, best = 1.0, TEXELS_PER_METRE * 4, None
    for _ in range(24):
        mid = (lo_d + hi_d) / 2
        offsets = _pack(np.maximum(extent * mid, 1.0), side, PADDING)
        if offsets is None:
            hi_d = mid
        else:
            lo_d, best = mid, (mid, offsets)
    if best is None:
        raise ValueError("AO charts do not fit the map")
    density, offsets = best
    frame_data = {"low": low, "density": density, "offsets": offsets, "side": side, "frames": frames}

    # Chart of every triangle of every opaque draw: own chart on the finest LOD, nearest finest otherwise.
    centres = tris.mean(axis=1)
    grid = defaultdict(list)
    cell = 0.05
    for k, c in enumerate(np.floor(centres / cell).astype(int)):
        grid[tuple(c)].append(k)
    chart_of = {}
    for slot, i in enumerate(base):
        chart_of[i] = chart[owner == slot]
    for i in opaque:
        if i in chart_of:
            continue
        d = draws[i]
        t = d.positions[d.indices].astype(np.float64)
        tn, _ = _normals(t)
        assigned = np.zeros(len(t), int)
        for k, c in enumerate(t.mean(axis=1)):
            key = np.floor(c / cell).astype(int)
            found = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        found += grid.get((key[0] + dx, key[1] + dy, key[2] + dz), [])
            if not found:
                assigned[k] = int(chart[np.argmin(np.linalg.norm(centres - c, axis=1))])
                continue
            found = np.asarray(found)
            score = np.linalg.norm(centres[found] - c, axis=1) + 0.05 * (normals[found] @ tn[k] < 0.3)
            assigned[k] = int(chart[found[np.argmin(score)]])
        chart_of[i] = assigned

    out = list(draws)
    for i in opaque:
        out[i] = _with_uv2(draws[i], chart_of[i], frame_data)
    return out, side


def _with_uv2(draw, tri_chart, data):
    tri = draw.indices.reshape(-1, 3)
    keys = tri.astype(np.int64) * (len(data["frames"]) + 1) + tri_chart[:, None]
    unique, inverse = np.unique(keys.ravel(), return_inverse=True)
    vertex, vchart = unique // (len(data["frames"]) + 1), unique % (len(data["frames"]) + 1)
    p = draw.positions[vertex].astype(np.float64)
    uv = np.empty((len(vertex), 2))
    for cid in np.unique(vchart):
        rows = vchart == cid
        flat = _project(p[rows], data["frames"][cid])
        uv[rows] = ((flat - data["low"][cid]) * data["density"] + data["offsets"][cid]) / data["side"]
    return replace(draw, positions=draw.positions[vertex], normals=draw.normals[vertex], uv0=draw.uv0[vertex],
                   uv1=draw.uv1[vertex], indices=inverse.reshape(-1, 3), uv2=uv.astype(np.float32))


# --- occluders ----------------------------------------------------------------------------------------------

def shell(triangles, cell, low, shape):
    """Boolean surface voxels of a triangle soup on a fixed grid."""
    grid = np.zeros(shape, bool)
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
        ok = np.all((index >= 0) & (index < shape), axis=1)
        grid[index[ok, 0], index[ok, 1], index[ok, 2]] = True
    return grid


class Occluders:
    def __init__(self, triangles, glass=None):
        points = triangles.reshape(-1, 3)
        self.low = points.min(axis=0) - FAR_REACH
        span = points.max(axis=0) + FAR_REACH - self.low
        self.fine_shape = np.ceil(span / FINE_CELL).astype(int) + 1
        self.coarse_shape = np.ceil(span / COARSE_CELL).astype(int) + 1
        self.fine = shell(triangles, FINE_CELL, self.low, self.fine_shape)
        self.coarse = _grow(shell(triangles, COARSE_CELL, self.low, self.coarse_shape))
        self.glass_fine = self.glass_coarse = None
        if glass is not None and len(glass):
            self.glass_fine = shell(glass, FINE_CELL, self.low, self.fine_shape)
            self.glass_coarse = _grow(shell(glass, COARSE_CELL, self.low, self.coarse_shape))

    @staticmethod
    def _lookup(grid, shape, cell, low, q):
        index = np.floor((q - low) / cell).astype(int)
        ok = np.all((index >= 0) & (index < shape), axis=-1)
        out = np.zeros(q.shape[:-1], bool)
        out[ok] = grid[index[ok][:, 0], index[ok][:, 1], index[ok][:, 2]]
        return out

    def visibility(self, points, normals, chunk=4096):
        i = np.arange(RAYS * 2) + 0.5
        phi = np.arccos(1 - 2 * i / (RAYS * 2))
        theta = np.pi * (1 + 5 ** 0.5) * i
        dirs = np.c_[np.cos(theta) * np.sin(phi), np.cos(phi), np.sin(theta) * np.sin(phi)]
        fine_steps = np.arange(2 * FINE_CELL, FINE_REACH, FINE_CELL)
        coarse_steps = np.arange(FINE_REACH, REACH, COARSE_CELL)
        far_steps = np.arange(REACH, FAR_REACH, 2 * COARSE_CELL)
        out = np.ones(len(points))
        for s in range(0, len(points), chunk):
            p = points[s:s + chunk] + normals[s:s + chunk] * (1.5 * FINE_CELL)
            weight = normals[s:s + chunk] @ dirs.T                                  # (n, D)
            weight = np.where(weight > MIN_COSINE, weight, 0.0)
            hit = np.zeros(weight.shape, bool)
            through = np.zeros(weight.shape, bool)
            for t in fine_steps:
                q = p[:, None, :] + dirs[None] * t
                hit |= self._lookup(self.fine, self.fine_shape, FINE_CELL, self.low, q)
                if self.glass_fine is not None:
                    through |= self._lookup(self.glass_fine, self.fine_shape, FINE_CELL, self.low, q)
            for t in coarse_steps:
                q = p[:, None, :] + dirs[None] * t
                hit |= self._lookup(self.coarse, self.coarse_shape, COARSE_CELL, self.low, q)
                if self.glass_coarse is not None:
                    through |= self._lookup(self.glass_coarse, self.coarse_shape, COARSE_CELL, self.low, q)
            # A ray leaving through glass counts as partly open (native pillar trims stay dark, ACU median 15).
            open_ = np.where(hit, 0.0, np.where(through, GLASS_TRANSMISSION, 1.0))
            far_hit, far_through = hit.copy(), through.copy()
            for t in far_steps:
                q = p[:, None, :] + dirs[None] * t
                far_hit |= self._lookup(self.coarse, self.coarse_shape, COARSE_CELL, self.low, q)
                if self.glass_coarse is not None:
                    far_through |= self._lookup(self.glass_coarse, self.coarse_shape, COARSE_CELL, self.low, q)
            far_open = np.where(far_hit, 0.0, np.where(far_through, GLASS_TRANSMISSION, 1.0))
            total = weight.sum(axis=1)
            local = np.where(total > 0, (weight * open_).sum(axis=1) / np.maximum(total, 1e-9), 1.0)
            far = np.where(total > 0, (weight * far_open).sum(axis=1) / np.maximum(total, 1e-9), 1.0)
            out[s:s + chunk] = local * (ENCLOSURE_FLOOR + (1 - ENCLOSURE_FLOOR) * far)
        return out


# --- bake ---------------------------------------------------------------------------------------------------

def rasterise(draws, size, bright=None):
    """Covered texels with world points and interpolated authored normals (oriented like the face), and the
    texels to leave unshaded (`bright`: {id(draw): per-triangle bool}). The finest LOD is rasterised first.
    The same ray set serves every texel, so points shared by two charts get the same value: no seams."""
    bright = bright or {}
    covered = np.zeros((size, size), bool)
    keep = np.zeros((size, size), bool)
    points = np.zeros((size, size, 3))
    norms = np.zeros((size, size, 3))
    for d in sorted((d for d in draws if d.uv2 is not None), key=lambda d: d.lod):
        tri = d.indices.reshape(-1, 3)
        uv = d.uv2.astype(np.float64) * size - 0.5
        p = d.positions.astype(np.float64)
        fn, _ = _normals(p[tri])
        vnorm = d.normals.astype(np.float64)
        vnorm = vnorm / np.maximum(np.linalg.norm(vnorm, axis=1, keepdims=True), 1e-12)
        fn *= np.where(np.sum(fn * vnorm[tri].sum(axis=1), axis=1) < 0, -1.0, 1.0)[:, None]
        flags = bright.get(id(d))
        valid = np.all(np.isfinite(uv), axis=1)
        for k, (a, b, c) in enumerate(tri):
            if not (valid[a] and valid[b] and valid[c]) or np.linalg.norm(fn[k]) < 0.5:
                continue
            ua, ub, uc = uv[a], uv[b], uv[c]
            x0, y0 = np.floor(np.minimum(np.minimum(ua, ub), uc)).astype(int)
            x1, y1 = np.ceil(np.maximum(np.maximum(ua, ub), uc)).astype(int)
            x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, size - 1), min(y1, size - 1)
            if x1 < x0 or y1 < y0:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
            q = np.stack([gx.ravel(), gy.ravel()], axis=1).astype(np.float64)
            v0, v1, v2 = ub - ua, uc - ua, q - ua
            den = v0[0] * v1[1] - v1[0] * v0[1]
            if abs(den) < 1e-12:
                continue
            w1 = (v2[:, 0] * v1[1] - v1[0] * v2[:, 1]) / den
            w2 = (v0[0] * v2[:, 1] - v2[:, 0] * v0[1]) / den
            w0 = 1 - w1 - w2
            inside = (w0 >= -0.02) & (w1 >= -0.02) & (w2 >= -0.02)
            if not inside.any():
                continue
            qx, qy = q[inside, 0].astype(int), q[inside, 1].astype(int)
            free = ~covered[qy, qx]
            qx, qy = qx[free], qy[free]
            weights = np.clip(np.stack([w0, w1, w2], 1)[inside][free], 0, 1)
            weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-9)
            points[qy, qx] = weights @ p[[a, b, c]]
            vn = weights @ vnorm[[a, b, c]]
            vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-9)
            vn *= np.where(vn @ fn[k] < 0, -1.0, 1.0)[:, None]
            norms[qy, qx] = vn
            covered[qy, qx] = True
            if flags is not None and flags[k]:
                keep[qy, qx] = True
    return covered, points[covered], norms[covered], keep[covered]


def dilate(image, covered, rounds):
    image, covered = image.copy(), covered.copy()
    for _ in range(rounds):
        total = np.zeros_like(image, dtype=np.float64)
        count = np.zeros_like(image, dtype=np.float64)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                shifted = np.roll(np.roll(image, dy, 0), dx, 1)
                ok = np.roll(np.roll(covered, dy, 0), dx, 1)
                total += np.where(ok, shifted, 0)
                count += ok
        grow = ~covered & (count > 0)
        image[grow] = total[grow] / count[grow]
        covered |= grow
    return image, covered


def bake(draws, occluders, side, bright=None):
    size = min(side, BAKE_MAX)
    covered, points, normals, keep = rasterise(draws, size, bright)
    image = np.full((size, size), float(BACKGROUND))
    if covered.any():
        visibility = np.clip(occluders.visibility(points, normals), 0, 1)
        value = 255.0 * (FLOOR + (1 - FLOOR) * visibility ** GAMMA)
        value[keep] = BACKGROUND
        image[covered] = value
    image, _ = dilate(image, covered, PADDING + 2)
    if size != side:
        from PIL import Image
        image = np.asarray(Image.fromarray(image.astype(np.float32), mode="F").resize((side, side), Image.BILINEAR))
    return np.clip(np.rint(image), 0, 255).astype(np.uint8)


def texture(name, image):
    """Native AO swatch: R8 (format 19) with a full mip chain."""
    from PIL import Image
    from .fh4_presentation import mip_header
    levels, current = [], Image.fromarray(image)
    while True:
        levels.append(np.asarray(current, dtype=np.uint8).tobytes())
        if current.size == (1, 1):
            break
        current = current.resize((max(1, current.size[0] // 2), max(1, current.size[1] // 2)), Image.BOX)
    payload = b"".join(levels)
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, "urn:fh4-expanded-garage:ao:" + name.casefold())
    header = mip_header(identifier.bytes_le, image.shape[1], image.shape[0], AO_FORMAT, [len(x) for x in levels])
    return fh4_model.bundle([fh4_model.Chunk("TXCB", (0, 0), payload, (fh4_model.metadata("TXCH", header, 0),))])


def occluder_triangles(groups, glass=False):
    """Finest-LOD triangles of every component: opaque ones, or with `glass` the translucent panes."""
    out = []
    for draws in groups:
        opaque = [d for d in draws if d.transparent == glass]
        if not opaque:
            continue
        finest = min(d.lod for d in opaque)
        out += [d.positions[d.indices].astype(np.float64) for d in opaque if d.lod == finest]
    return np.concatenate(out) if out else np.zeros((0, 3, 3))
