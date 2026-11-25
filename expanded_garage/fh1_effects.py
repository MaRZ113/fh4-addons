"""Convert source GTX1 surface data for the FH4 pilot."""

import numpy as np

from . import fh4_cache, fh4_dirt, fh4_source_atlas




def factory_stripes(root, ir, alias, exterior, rows):
    """Estimate a factory stripe swatch from the opaque texels of each original paint combination."""
    samples, areas = [], []
    for draws in exterior.values():
        for draw in draws:
            if draw.lod != 0 or draw.source_material.casefold() != "stripe_badge":
                continue
            p = draw.positions[draw.indices]
            areas.append(np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1))
            samples.append(draw.uv0[draw.indices].mean(axis=1))
    if not samples:
        return {}
    uv = np.concatenate(samples) % 1
    weight = np.concatenate(areas)
    images = fh4_source_atlas.Textures(root, ir, alias)
    stripes = {}
    for row in rows:
        stem, extension = row["BaseTexture"].casefold().rsplit(".", 1)
        if extension != "tga" or not stem.startswith("nodamage"):
            raise ValueError(f"Unexpected manufacturer colour atlas {row['BaseTexture']}")
        pixels = images.pixels(f"/{stem}_lod0.xds")
        if pixels is None:
            raise ValueError(f"Missing GTX1 paint combination atlas {stem}")
        height, width, _ = pixels.shape
        rgba = pixels[(uv[:, 1] * height).astype(int), (uv[:, 0] * width).astype(int)]
        covered = rgba[:, 3] >= 128
        colour = np.average(rgba[covered, :3], weights=weight[covered], axis=0)
        stripes[row["Sequence"]] = tuple(np.rint(colour).astype(int).tolist())
    return stripes


def impact_mask(root, ir, alias):
    """Estimate a TEXCOORD3 impact mask from FH1's matching diffuse and damage atlases."""
    images = fh4_source_atlas.Textures(root, ir, alias)
    pristine = images.pixels("/nodamage_lod0.xds")
    damaged = images.pixels("/damage_lod0.xds")
    if pristine is None or damaged is None or pristine.shape != damaged.shape:
        raise ValueError("The GTX1 pilot needs paired pristine and damaged LOD0 textures")

    def encode():
        difference = np.abs(pristine.astype(np.int16) - damaged.astype(np.int16))
        intensity = np.clip((difference.max(axis=2) - 8) * 3, 0, 255).astype(np.uint8)
        rgba = np.empty_like(pristine)
        rgba[..., :3] = intensity[..., None]
        rgba[..., 3] = 255
        # Stock Ford GT uses a 2048-square BC1 chain (TXCH format 0), including the final 1x1 mip.
        return fh4_dirt.swatch(rgba, "BC1_UNORM", fh4_dirt.BC1_FORMAT, 8)

    payload = fh4_cache.cached_bytes("fh1-impact-mask", (__name__, fh4_dirt.__name__),
                                     (pristine, damaged), encode)
    return {"Textures/Damage/impactMask.swatchbin": payload}
