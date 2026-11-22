"""Native FH4 stock wheel model: one normalised LF rim found by naming convention.

Every native car ships scene/_library/Scene/Wheels/<MediaName>_WheelLF.modelbin.
The Carbin does not reference it; the runtime loads it through the stock
List_Wheels row, mirrors it for the right side and scales it to the rim size.
Measured on 13", 17", 18" and 22" native wheels the geometry is identical in
shape: rim only (the tyre is generated), lip radius 0.14, outer face at X=0
and inner barrel edge at X=1, with a `spindle` bone at the hub mounting plane.
"""

from dataclasses import replace

import numpy as np

from .fh4_model import Bone

RIM_RADIUS = 0.14
TYRE_MARGIN = 1.02


def wheel_path(media_name):
    return f"scene/_library/scene/wheels/{media_name.lower()}_wheellf.modelbin"


def radii(positions):
    return np.hypot(positions[:, 1], positions[:, 2])


def rim_radius(draws):
    """Lip radius from the LODs where the source separates the tyre; else the 98th percentile cut."""
    separated = [d for d in draws if d.lod >= 1 and d.source_material.casefold() not in ("tire", "tyre")]
    if separated and any(d.source_material.casefold() in ("tire", "tyre") for d in draws):
        return float(max(radii(d.positions).max() for d in separated))
    everything = np.concatenate([radii(d.positions) for d in draws])
    raise ValueError(f"Cannot separate the rim from the tyre (max radius {everything.max():.3f} m)")


def rim_only(draw, limit):
    if draw.source_material.casefold() in ("tire", "tyre"):
        return None
    inside = radii(draw.positions) <= limit
    keep = draw.indices[np.all(inside[draw.indices], axis=1)]
    if not len(keep):
        return None
    used, remap = np.unique(keep, return_inverse=True)
    return replace(draw, positions=draw.positions[used], normals=draw.normals[used], uv0=draw.uv0[used],
                   uv1=draw.uv1[used], indices=remap.reshape(-1, 3).astype(draw.indices.dtype))


def normalise(draws):
    """Return (normalised LF draws, bones, receipt). Input draws are LF in the hub frame (outward = -X)."""
    radius = rim_radius(draws)
    limit = radius * TYRE_MARGIN
    kept = [d for d in (rim_only(d, limit) for d in draws) if d is not None]
    if not kept:
        raise ValueError("No rim geometry left after removing the tyre")
    xs = np.concatenate([d.positions[:, 0] for d in kept])
    outer, inner = float(xs.min()), float(xs.max())
    width = inner - outer
    if width <= 1e-3:
        raise ValueError("Degenerate rim width")
    scale = np.array([1 / width, RIM_RADIUS / radius, RIM_RADIUS / radius])
    shift = np.array([-outer / width, 0.0, 0.0])
    result = []
    for draw in kept:
        positions = (draw.positions * scale + shift).astype(np.float32)
        normals = draw.normals / scale
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
        name = "wheel_LODS0" if draw.lod == 0 else f"wheel_LOD{draw.lod}"
        result.append(replace(draw, name=name, positions=positions, normals=normals.astype(np.float32)))
    spindle = float(-outer / width)
    bones = [Bone("<root>", -1), Bone("spindle", 0, (spindle, 0.0, 0.0)), Bone("root", 0)]
    receipt = {"rim_radius_m": radius, "rim_width_m": width, "spindle_x": spindle,
               "removed_tyre_triangles": int(sum(len(d.indices) for d in draws) - sum(len(d.indices) for d in kept)),
               "frame": "native normalised wheel: rim lip radius 0.14, outer face X=0, inner edge X=1"}
    return result, bones, receipt


def check_bounds(low, high):
    if not (low[0] >= -0.02 and high[0] <= 1.02 and max(abs(v) for v in (*low[1:], *high[1:])) <= RIM_RADIUS + 1e-3):
        raise ValueError(f"Wheel model is not in the native normalised frame: {low} {high}")
