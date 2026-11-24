"""FM4 source textures on native FH4 shaders: the car, lamp and wheel atlases and the shared grille tiles.

FM4 binds a texture to a technique by sampler name, per model LOD: LOD0 models read `<atlas>_LOD0.xds`, LOD1-5
models `<atlas>.xds`, a separate layout (Integra trunk emblem: UV 0.19-0.23 in nodamage_LOD0, 0.00-0.87 in
nodamage). One texture for all LODs drew the far models with the wrong art. The near MatI keeps the source
technique name; draws on LOD1-5 get a MatI named `<technique>_lod15` with the far texture (fh4_model.assemble).
"""

import os
import uuid

import numpy as np

from . import fh4_mtpr
from .fh4_material_contract import (ATLAS_PLASTIC_GLOSS, BRAKE_ATLAS_GLOSS, EXTERIOR_ATLAS_GLOSS,
                                    EXTERIOR_ATLAS_MATERIAL, EXTERIOR_ATLAS_PLASTIC, EXTERIOR_STICKER, GRILLE_MESH,
                                    TINTED_LENSES, WHEEL_ATLAS_GLOSS, WHEEL_ATLAS_SURFACES, atlas_source)

FAR = "_lod15"
FOLDER = "textures/exterior/swatches/"
DIFFUSE = 0x810e7224   # CH1 diffuse (badge, plastic_ch1_custom_diffusemap, sticker, int_ch1diff...)
OPACITY = 0x698ca64f   # CH1 opacity mask (sticker, metal_ch1_custom)
LENS_TINT = 0x1925d9bf  # gls_colored tint colour (library red 0.305, 0.003, 0.001)
LENS_TINT_PEAK = 0.305
STICKER_GLOSS = 0.9
GRILLE_SOURCES = {"grille1": "/shared/grille1_alpha.xds", "grille2": "/shared/grille2_alpha.xds"}
ATLASES = {"body": ("/nodamage_lod0.xds", "/nodamage.xds"), "lights": ("/lights_lod0.xds", "/lights.xds"),
           "wheel": ("/wheel_lod0.xds", "/wheel.xds")}


def far_name(label):
    return label + FAR


class Textures:
    """Decoded FM4 textures of the car and the swatchbins written for them."""

    def __init__(self, root, ir, alias):
        from .fh4 import raw_member
        from .fh4_presentation import xds_codec
        self.root, self.ir, self.alias = root, ir, alias.casefold()
        self.raw_member = raw_member
        self.codec = xds_codec(None)
        self.decoded, self.payloads, self.paths = {}, {}, {}

    def pixels(self, suffix):
        if suffix not in self.decoded:
            data = self.raw_member(self.root, self.ir, suffix)
            if data is None or self.codec is None:
                self.decoded[suffix] = None
            else:
                width, height, rgba = self.codec[0](data)
                self.decoded[suffix] = np.frombuffer(bytes(rgba), np.uint8).reshape(height, width, 4)
        return self.decoded[suffix]

    def swatch(self, suffix, role, scale=1.0):
        """Game path of the swatchbin for `suffix`: role 'diff' (opaque RGB, times `scale` in linear light) or
        'opac' (alpha as grey)."""
        key = (suffix, role, scale)
        if key in self.paths:
            return self.paths[key]
        pixels = self.pixels(suffix)
        if pixels is None:
            self.paths[key] = None
            return None
        from .fh4_presentation import swatch_bc7
        image = pixels.copy()
        if role == "opac":
            image[..., :3] = pixels[..., 3:4]
        elif scale != 1.0:
            linear = (pixels[..., :3].astype(np.float32) / 255) ** 2.2 * scale
            image[..., :3] = np.round(255 * np.clip(linear, 0, 1) ** (1 / 2.2)).astype(np.uint8)
        image[..., 3] = 255   # swatch() premultiplies; colour and mask go in separate textures
        stem = suffix.strip("/").replace("/", "_").rsplit(".", 1)[0]
        if scale != 1.0:
            stem += f"_x{int(round(scale * 100)):03d}"
        name = f"{self.alias}_{stem}_{role}_{uuid.uuid5(uuid.NAMESPACE_URL, f'urn:fh4-expanded-garage:{self.alias}:{stem}:{role}')}"
        self.payloads[FOLDER + name + ".swatchbin"] = swatch_bc7(name, image.shape[1], image.shape[0], image.tobytes())
        path = "\\".join(("Game:", "Media", "cars", self.alias, *FOLDER.strip("/").split("/"), "")) + name + ".swatchbin"
        self.paths[key] = path
        return path


def _block(library, member, values, textures):
    from .fh4 import library_mtpr
    body, version = library_mtpr(library, member + ".materialbin")
    patched = fh4_mtpr.patch(body, values, version)
    return tuple(version), fh4_mtpr.set_textures(patched, textures, version)


def lens_tints(grouped, textures):
    """{(kind, component, label): linear tint} from the lamp atlas texels under the lens triangles."""
    tints = {}
    for kind, components in grouped.items():
        for component, draws in components.items():
            for label in {d.source_material.casefold() for d in draws} & TINTED_LENSES:
                if atlas_source(label, kind, component) != "lights":
                    continue
                chosen = [d for d in draws if d.source_material.casefold() == label]
                finest = min(d.lod for d in chosen)
                pixels = textures.pixels(ATLASES["lights"][0 if finest == 0 else 1])
                if pixels is None:
                    continue
                height, width = pixels.shape[:2]
                total, weight = np.zeros(3), 0.0
                for d in chosen:
                    if d.lod != finest:
                        continue
                    centres = d.uv0[d.indices].mean(axis=1)
                    p = d.positions[d.indices]
                    area = np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1) / 2
                    xs = np.clip((centres[:, 0] % 1) * width, 0, width - 1).astype(int)
                    ys = np.clip((centres[:, 1] % 1) * height, 0, height - 1).astype(int)
                    linear = (pixels[ys, xs, :3].astype(float) / 255) ** 2.2
                    total += (linear * area[:, None]).sum(axis=0)
                    weight += area.sum()
                if weight > 0 and total.max() > 1e-6:
                    colour = total / weight
                    tints[(kind, component.casefold(), label)] = tuple(float(round(c, 4)) for c in
                                                                      colour * (LENS_TINT_PEAK / colour.max()))
    return tints


# FM4 rim techniques multiply the wheel atlas texel by PaintScale (Shared.zip ShaderSettings/Normal.xml: rim 0.75,
# outer_rim 1.0), overridden by the wheel archive's shadersettings.xml (SX4: rim 0.5). Unscaled the rims read pure
# white next to FM4's grey (owner, visual-51). Same mechanism as the cockpit texel scales (fh4.cockpit_texel_scales).
WHEEL_PAINT_SCALE = {"rim": 0.75, "outer_rim": 1.0, "wheel_emblem": 1.0}


def wheel_paint_scales(root, ir):
    import re
    scales = dict(WHEEL_PAINT_SCALE)
    for member in ir.get("raw_members", ()):
        name = member["name"].casefold()
        if name.startswith("media/wheels/") and name.endswith("/shadersettings.xml"):
            text = (root / "blobs" / member["provenance"]["asset"]).read_bytes().decode("utf-8", "replace")
            for technique in list(scales):
                section = re.search(rf"<\s*{technique}\s*>(.*?)<\s*/\s*{technique}\s*>", text, re.S | re.I)
                value = section and re.search(r'<\s*PaintScale\s+value\s*=\s*"([^"]+)"', section.group(1))
                if value and np.isfinite(float(value.group(1))):
                    scales[technique] = float(value.group(1))
    return scales


def parameters(root, ir, alias, library, grouped):
    """(car-wide {(MatI name, library member): (version, MTPR)}, {(kind, component): {...}} per-component
    lens tints, {archive member: swatchbin}) for every surface bound to an FM4 source texture."""
    if library is None or os.environ.get("EXPANDED_GARAGE_SOURCE_ATLAS") == "off":
        return {}, {}, {}
    textures = Textures(root, ir, alias)
    wanted = set()
    for kind, components in grouped.items():
        for component, draws in components.items():
            for d in draws:
                source = atlas_source(d.source_material, kind, component)
                if source is not None:
                    wanted.add((d.source_material, d.target_material, source, d.lod > 0))
    wheel_scales = wheel_paint_scales(root, ir)
    out = {}
    for label, member, source, far in sorted(wanted):
        name = label.casefold()
        library_member = member[:-len(".materialbin")]
        if source in ATLASES:
            suffix = ATLASES[source][1 if far else 0]
            if textures.pixels(suffix) is None:
                suffix = ATLASES[source][0]
        else:
            suffix = GRILLE_SOURCES[source]
        scale = wheel_scales.get(name, 1.0) if source == "wheel" else 1.0
        diffuse = textures.swatch(suffix, "diff", scale)
        if diffuse is None:
            continue
        key = (far_name(label) if far else label, member)
        if library_member == EXTERIOR_ATLAS_PLASTIC:
            gloss = ATLAS_PLASTIC_GLOSS.get(name) or BRAKE_ATLAS_GLOSS.get(name, 0.5)
            out[key] = _block(library, library_member, {
                "CH1DiffColTextureSwitchBool": 1, "CH1NormalMapSwitchBool": 0, "CH1LocalAOSwitchBool": 0,
                "DiffuseColorGroupColorParam": (1.0, 1.0, 1.0, 1.0), "GlossA_floatVal": gloss}, {DIFFUSE: diffuse})
        elif library_member == EXTERIOR_STICKER:
            out[key] = _block(library, library_member, {
                "CH1DiffColTextureSwitchBool": 1, "CH1OpacityMaskSwitchBool": 1,
                "DiffuseColorGroupColorParam": (1.0, 1.0, 1.0, 1.0), "GlossA_floatVal": STICKER_GLOSS},
                {DIFFUSE: diffuse, OPACITY: textures.swatch(suffix, "opac")})
        elif library_member == GRILLE_MESH:
            out[key] = _block(library, library_member, {"CH1OpacityMaskSwitchBool": 1},
                              {OPACITY: textures.swatch(suffix, "opac")})
        elif library_member == EXTERIOR_ATLAS_MATERIAL:
            gloss = WHEEL_ATLAS_GLOSS if name in WHEEL_ATLAS_SURFACES and source == "wheel" else (
                EXTERIOR_ATLAS_GLOSS.get(name, 0.85))
            out[key] = _block(library, library_member, {
                "CH1DiffColTextureSwitchBool": 1, "CH1OpacityMaskSwitchBool": 0,
                "DiffuseColorGroupColorParam": (1.0, 1.0, 1.0, 1.0), "GlossA_floatVal": gloss}, {DIFFUSE: diffuse})
    per_component = {}
    from .fh4 import library_mtpr
    body, version = library_mtpr(library, "exterior_glass/gls_colored.materialbin")
    for (kind, component, label), tint in lens_tints(grouped, textures).items():
        block = fh4_mtpr.patch(body, {LENS_TINT: (*tint, 1.0)}, version)
        original = next(d.source_material for d in grouped[kind][next(k for k in grouped[kind] if k.casefold() == component)]
                        if d.source_material.casefold() == label)
        per_component.setdefault((kind, component), {})[(original, "exterior_glass/gls_colored.materialbin")] = (
            tuple(version), block)
    return out, per_component, textures.payloads

