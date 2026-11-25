"""Extract FM4's cockpit texel scales from the shared shader settings.

FM4 draws cabin trims with cockpit_<technique> shaders that multiply the cockpit atlas texel (linear light)
by a per-technique scale from Media/cars/Shared.zip ShaderSettings/Normal.xml, overridden per car by its
own ShaderSettings.xml: cockpit_plastic CockpitPlasticPaintScale 0.5, cockpit_mottled 0.35, cloth/rubber/
solid/reflector PaintScale 0.5, metal 0.002, bump_* CockpitPaintScale 0.3-0.5. Showing the atlas unscaled
made converted cabins far brighter than FM4 (owner, visual-29).

Writes expanded_garage/fm4-cockpit-shading-v1.json: {technique without the cockpit_ prefix: scale}.

    python -m expanded_garage.research.extract_cockpit_shading <FM4 Media> [--source-tools <driversf checkout>]
"""

import argparse
import json
from pathlib import Path
import sys

# Parameter that scales the texel, by priority (cockpit-specific first).
SCALE_KEYS = ("CockpitPaintScale", "CockpitPlasticPaintScale", "PaintScale")


def texel_scales(settings):
    out = {}
    for technique, params in sorted(settings.items()):
        if not technique.startswith("cockpit_"):
            continue
        for key in SCALE_KEYS:
            if key in params:
                out[technique[len("cockpit_"):]] = {"scale": params[key], "parameter": key}
                break
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path)
    parser.add_argument("--source-tools", type=Path,
                        default=Path(__file__).resolve().parents[2] / "tools/driversf-forza-master")
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parents[1] / "fm4-cockpit-shading-v1.json")
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_tools / "src"))
    from dsfforza.formats.forzazip import ForzaZip
    from dsfforza.convert.forza_materials import parse_shader_settings

    archive = ForzaZip(str(args.media / "cars" / "Shared.zip"))
    entry = next(i for i in archive.zip.infolist() if i.filename.casefold() == "shadersettings/normal.xml")
    scales = texel_scales(parse_shader_settings(archive.read(entry).decode("utf-8", "replace")))
    args.out.write_text(json.dumps({
        "format": "fm4-cockpit-shading-v1",
        "source": "FM4 Media/cars/Shared.zip ShaderSettings/Normal.xml",
        "meaning": "cockpit atlas texel multiplier in linear light; car ShaderSettings.xml overrides",
        "priority": list(SCALE_KEYS),
        "techniques": scales,
    }, indent=1) + "\n", encoding="utf-8")
    print(f"{len(scales)} cockpit techniques")


if __name__ == "__main__":
    main()
