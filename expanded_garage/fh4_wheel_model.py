"""Native FH4 stock wheel model: one normalised LF rim found by naming convention.

Every native car ships scene/_library/Scene/Wheels/<MediaName>_WheelLF.modelbin.
The Carbin references it four times in part group 43 (one entry per corner on the
spindle bone, the right side turned 180 degrees about Y); the runtime scales it to
the rim size.
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


def is_tyre(draw):
    return draw.source_material.casefold() in ("tire", "tyre")


def rim_radii(draws):
    """Lip radius per LOD: the outermost non-tyre vertex of that LOD.

    FM4 wheels are not one scale across LODs. LOD1-5 carry the rim at its true size with the tyre around
    it (a 17" sample: lip 0.236 m, tyre to 0.317 m); LOD0 has no tyre and its rim is stretched radially to
    the tyre's outer radius (lip and wheel_black at 0.317 m, emblem 0.037 m against 0.028 m on LOD1).
    One radius for every LOD therefore cut the LOD0 lip and barrel away and shrank its spokes.
    """
    if not any(is_tyre(d) for d in draws):
        everything = np.concatenate([radii(d.positions) for d in draws])
        raise ValueError(f"Cannot separate the rim from the tyre (max radius {everything.max():.3f} m)")
    result = {}
    for draw in draws:
        if not is_tyre(draw):
            result[draw.lod] = max(result.get(draw.lod, 0.0), float(radii(draw.positions).max()))
    if not result or min(result.values()) <= 1e-3:
        raise ValueError("No rim geometry left after removing the tyre")
    return result


# FM4 speed-blur meshes map to the native blur meshes the game shows in motion instead of wheel_*:
# blurRim (specificuse/wheel_blur) and blurLip (specificuse/wheel_blurlip), both translucent.
BLUR_NAMES = {"blur_rim": "blurRim", "blur_lip": "blurLip"}


def mesh_name(draw):
    stem = BLUR_NAMES.get(draw.source_material.casefold(), "wheel")
    return f"{stem}_LODS0" if draw.lod == 0 else f"{stem}_LOD{draw.lod}"


def rim_only(draw, limit):
    if is_tyre(draw):
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
    radius = rim_radii(draws)
    kept = [d for d in (rim_only(d, radius[d.lod] * TYRE_MARGIN) for d in draws if d.lod in radius) if d is not None]
    if not kept:
        raise ValueError("No rim geometry left after removing the tyre")
    xs = np.concatenate([d.positions[:, 0] for d in kept])
    outer, inner = float(xs.min()), float(xs.max())
    width = inner - outer
    if width <= 1e-3:
        raise ValueError("Degenerate rim width")
    shift = np.array([-outer / width, 0.0, 0.0])
    result = []
    for draw in kept:
        scale = np.array([1 / width, RIM_RADIUS / radius[draw.lod], RIM_RADIUS / radius[draw.lod]])
        positions = (draw.positions * scale + shift).astype(np.float32)
        normals = draw.normals / scale
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
        result.append(replace(draw, name=mesh_name(draw), positions=positions, normals=normals.astype(np.float32)))
    spindle = float(-outer / width)
    bones = [Bone("<root>", -1), Bone("spindle", 0, (spindle, 0.0, 0.0)), Bone("root", 0)]
    receipt = {"rim_radius_m": min(radius.values()), "lod_rim_radius_m": {str(k): v for k, v in sorted(radius.items())},
               "rim_width_m": width, "spindle_x": spindle, "outer_face_m": outer,
               "removed_tyre_triangles": int(sum(len(d.indices) for d in draws) - sum(len(d.indices) for d in kept)),
               "frame": "native normalised wheel: rim lip radius 0.14 on every LOD, outer face X=0, inner edge X=1"}
    return result, bones, receipt


def check_bounds(low, high):
    if not (low[0] >= -0.02 and high[0] <= 1.02 and max(abs(v) for v in (*low[1:], *high[1:])) <= RIM_RADIUS + 1e-3):
        raise ValueError(f"Wheel model is not in the native normalised frame: {low} {high}")
