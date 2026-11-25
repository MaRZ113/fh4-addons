"""Native FH4 stock wheel model: one normalised LF rim found by naming convention.

Every native car ships scene/_library/Scene/Wheels/<MediaName>_WheelLF.modelbin.
The Carbin references it four times in part group 43 (one entry per corner on the
spindle bone, the right side turned 180 degrees about Y); the runtime scales it to
the rim size.
Measured on 13", 17", 18" and 22" native wheels the geometry is identical in
shape: rim only (the tyre is generated), lip radius 0.14, outer face at X=0
and inner barrel edge at X=1, with a `spindle` bone at the hub mounting plane.

X is metric over the face: spokes, nuts and the lip front lie within 0.1 m of the outer face
(ACU_RSXTypeS_02: spokes to 0.036 m, spindle at 0.092 m; FOR_FiestaST_14: spindle 0.074 m). Only
the barrel's inner edge sits at X=1, and the width morph pulls it back towards X=0.1. Stretching the
whole source width onto 0..1 deepened the dish about fivefold.
"""

from dataclasses import replace

import numpy as np

from .fh4_model import WHEEL_FACE_DEPTH, Bone

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
# Chrome wheels name them chrome_blur_* (SAL_S281_06: drawn as solid chrome discs, owner visual-61).
BLUR_NAMES = {"blur_rim": "blurRim", "blur_lip": "blurLip", "chrome_blur_rim": "blurRim", "chrome_blur_lip": "blurLip"}


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


def normalise(draws, mount_x=None):
    """Return (normalised LF draws, bones, receipt). Input draws are LF in the hub frame (outward = -X).

    mount_x: hub-frame X of the brake mounting plane (outer face of the LF rotor hat). Native wheels put the
    `spindle` bone there: ACU_RSXTypeS_02 spindle 0.092 and rotor face 0.092 behind the rim face, Fiesta
    0.074/0.084, 911 GT2 0.087. The FM4 hub origin is the wheel centre, not that plane; on a wide rim
    (RUF CTR2, 0.25 m) the centre lies past the metric face zone and stretched to 0.263, and the discs showed
    that deep inside the wheel (owner, visual-53). Without rotors the hub centre is kept.
    """
    radius = rim_radii(draws)
    kept = [d for d in (rim_only(d, radius[d.lod] * TYRE_MARGIN) for d in draws if d.lod in radius) if d is not None]
    if not kept:
        raise ValueError("No rim geometry left after removing the tyre")
    xs = np.concatenate([d.positions[:, 0] for d in kept])
    outer, inner = float(xs.min()), float(xs.max())
    width = inner - outer
    if width <= 1e-3:
        raise ValueError("Degenerate rim width")
    # Face zone (within WHEEL_FACE_DEPTH of the outer face) keeps metres; the barrel beyond it is
    # stretched linearly so the inner edge lands on X=1 (barrel_scale > 1 when the rim is narrower than 1 m).
    barrel_scale = (1 - WHEEL_FACE_DEPTH) / max(width - WHEEL_FACE_DEPTH, 1e-6)
    result = []
    for draw in kept:
        depth = draw.positions[:, 0].astype(np.float64) - outer
        barrel = depth > WHEEL_FACE_DEPTH
        x = np.where(barrel, WHEEL_FACE_DEPTH + (depth - WHEEL_FACE_DEPTH) * barrel_scale, depth)
        radial = RIM_RADIUS / radius[draw.lod]
        positions = np.column_stack([x, draw.positions[:, 1:3] * radial]).astype(np.float32)
        scale = np.column_stack([np.where(barrel, barrel_scale, 1.0), np.full((len(x), 2), radial)])
        normals = draw.normals / scale
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
        result.append(replace(draw, name=mesh_name(draw), positions=positions, normals=normals.astype(np.float32)))
    spindle = float(-outer)
    if mount_x is not None and outer < mount_x < inner:
        spindle = float(mount_x - outer)
    if spindle > WHEEL_FACE_DEPTH:
        spindle = WHEEL_FACE_DEPTH + (spindle - WHEEL_FACE_DEPTH) * barrel_scale
    bones = [Bone("<root>", -1), Bone("spindle", 0, (spindle, 0.0, 0.0)), Bone("root", 0)]
    receipt = {"rim_radius_m": min(radius.values()), "lod_rim_radius_m": {str(k): v for k, v in sorted(radius.items())},
               "rim_width_m": width, "spindle_x": spindle, "outer_face_m": outer,
               "removed_tyre_triangles": int(sum(len(d.indices) for d in draws) - sum(len(d.indices) for d in kept)),
               "barrel_scale": barrel_scale,
               "frame": "native normalised wheel: rim lip radius 0.14 on every LOD, outer face X=0 (metric to 0.1 m), inner edge X=1"}
    return result, bones, receipt


def check_bounds(low, high):
    if not (low[0] >= -0.02 and high[0] <= 1.02 and max(abs(v) for v in (*low[1:], *high[1:])) <= RIM_RADIUS + 1e-3):
        raise ValueError(f"Wheel model is not in the native normalised frame: {low} {high}")
