"""FM4 digital dashboards on the native dial shader (a static frame of the car's own dash scene).

FM4 draws `digital_gauge` displays from a render target, not the cabin atlas: the car's
`digitalgauge/dash_<car>.bsg/.fbf/.bgf` is an Anark vector scene (1280x720 presentation, orthographic, y up)
composed at runtime from sprite .xds pieces, shared dashboard textures (Media/cars/Shared.zip dashboards/) and
vector fonts (Media/UI/Fonts.zip). A display shows its part of the frame through the draw's first UV transform,
already applied to the IR's UV0 (driversf-forza research/cockpit.md, "Digital displays").

The scene parser and renderer come from the local driversf-forza checkout (dsfforza.formats.anark,
dsfforza.convert.dash_scene). The frame is rendered at rest (speed, rpm and readouts empty, as
dsfforza.convert.digital_dash._rest_state) and bound like dial faces: diffuse plus the bright marks as the
illumination map. Live values are not driven yet. EXPANDED_GARAGE_DIGITAL_DASH=off: no frame (dark screen).
"""

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import numpy as np

FOLDER = "textures/gauges/swatches/"   # the dial textures folder (fh4.GAUGE_FOLDER), audited by validate_zip
FRAME = (1024, 512)          # presentation 1280x720 resampled; the display UVs are normalised
FM4_MEDIA = Path(r"G:\!Games\Xenia_Canary\Manager\games_extracted\forza_motorsport_4\Media")
DASH_MEDIA_ZIPS = (("cars/Shared.zip", "dashboards/", "shared/dashboards"), ("UI/Fonts.zip", "", "fonts"))


def _driversf():
    from .fh4 import ROOT
    source = ROOT / "tools/driversf-forza-master/src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))


def dash_media():
    """Shared dashboard textures and vector fonts, unpacked once from the FM4 disc into the local cache."""
    media = Path(os.environ.get("EXPANDED_GARAGE_FM4_MEDIA") or FM4_MEDIA)
    target = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "expanded-garage" / "dash_media" / "fm4"
    if (target / "done").exists():
        return target
    if not media.is_dir():
        return None
    _driversf()
    from dsfforza.formats.forzazip import ForzaZip
    for relative, member, sub in DASH_MEDIA_ZIPS:
        with open(media / relative, "rb") as handle:
            archive = ForzaZip(handle)
            for info in archive.entries():
                name = info.filename.replace("\\", "/")
                if info.is_dir() or not name.lower().startswith(member):
                    continue
                out = target / sub / name[len(member):]
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(archive.read(info))
    (target / "done").write_text(json.dumps([z for z, _, _ in DASH_MEDIA_ZIPS]))
    return target


def dash_files(root, ir):
    return {r["name"].rsplit("/", 1)[-1]: root / "blobs" / r["provenance"]["asset"]
            for r in ir.get("raw_members", ()) if "/digitalgauge/" in r["name"].casefold()}


def render_frame(root, ir):
    """RGBA uint8 (FRAME) of the car's dash scene at rest, or None without one."""
    if os.environ.get("EXPANDED_GARAGE_DIGITAL_DASH") == "off":
        return None
    files = dash_files(root, ir)
    if not any(name.casefold().endswith(".bgf") for name in files):
        return None
    media = dash_media()
    if media is None:
        return None
    _driversf()
    from dsfforza.convert import dash_scene, digital_dash
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix="dash-") as tmp:
        folder = Path(tmp)
        for name, path in files.items():
            (folder / name).write_bytes(path.read_bytes())
        dash = dash_scene.Dash(folder, media)
        digital_dash._rest_state(dash, 8000.0)
        image = dash_scene.render(dash, width=1280).convert("RGBA").resize(FRAME, Image.LANCZOS)
    return np.asarray(image, dtype=np.uint8).copy()


def parameters(root, ir, alias, donor, material):
    """({("digital_gauge", material): (version, MTPR)}, {member: swatchbin}) for the dash frame, or ({}, {})."""
    from . import fh4_cache, fh4_mtpr
    from .fh4_presentation import swatch_bc7
    files = dash_files(root, ir)
    if not files:
        return {}, {}
    key = tuple(sorted((n, p.read_bytes()) for n, p in files.items()))
    def compute():
        rendered = render_frame(root, ir)
        return rendered if rendered is not None else np.zeros((1, 1, 4), np.uint8)
    frame = fh4_cache.cached_array("digital-dash", (__name__,), key, compute)
    if frame.shape[0] < 4:
        return {}, {}
    pixels = frame.copy()
    pixels[..., 3] = 255
    luminance = pixels[..., :3].astype(np.float32).mean(axis=2)
    # Same illumination rule as the dial faces (fh4.gauge_texture_parameters): the bright marks glow.
    glow = np.clip((luminance - 60) / 80, 0, 1)
    lit = np.zeros_like(pixels)
    lit[..., :3] = np.where((glow > 0)[..., None], pixels[..., :3], 0)
    lit[..., 3] = np.round(255 * glow).astype(np.uint8)
    folder = "\\".join(("Game:", "Media", "cars", alias.casefold(), *FOLDER.strip("/").split("/"), ""))
    textures, members = {}, {}
    for role, image in (("diff", pixels), ("lite", lit)):
        name = f"{alias.casefold()}_dash_{role}_{uuid.uuid5(uuid.NAMESPACE_URL, f'urn:fh4-expanded-garage:{alias}:dash:{role}')}"
        members[FOLDER + name + ".swatchbin"] = swatch_bc7(name, image.shape[1], image.shape[0], image.tobytes())
        textures[donor["diffuse" if role == "diff" else "illumination"]] = folder + name + ".swatchbin"
    block = fh4_mtpr.set_textures(bytes.fromhex(donor["mtpr"]), textures, tuple(donor["version"]))
    return {("digital_gauge", material): (tuple(donor["version"]), block)}, members
