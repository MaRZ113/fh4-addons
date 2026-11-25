"""Classify shared FH4 material bindings by their use in the generated scene."""

import os
from dataclasses import dataclass
from enum import StrEnum


class MaterialUse(StrEnum):
    RUNTIME = "mandatory_runtime"
    VISIBILITY = "required_for_visibility"
    EFFECT = "required_for_functional_effects"
    FIDELITY = "fidelity_only"


@dataclass(frozen=True)
class SurfaceBinding:
    member: str
    use: MaterialUse
    transparent: bool = False
    drive_resolved: bool = True
    fidelity: str = "shared_base"
    trial_compatibility: str = "native_shared_binding"


LAMP_SURFACES = {"head_light", "head_light_lod0", "tail_light", "tail_light_lod0", "brake_light",
                 "reverse_light", "reverse_light_lod0", "turn_light", "hidhead", "fogwhite",
                 "slinlorange", "slinrorange", "taillight2s", "numplate"}


# FM4 cockpit surfaces take their colour (and printed labels, patterns, baked shading) from the car's
# cockpit atlas interior_LOD0.xds through UV0; FM4 techniques only add the finish. A bare FH4 library
# material dropped that colour, and `metal` (gear knob cap) / `reflector` (climate knob rings, dome lens)
# turned into bright metal and chrome. These classes now use the native textured cabin shader
# (int_ch1diffnormglossalpha) with the atlas as diffuse and a per-class gloss (GlossA_floatVal).
COCKPIT_ATLAS_MATERIAL = "interior_plastic/plastic_perforated_004"
COCKPIT_ATLAS_GLOSS = {
    "plastic": 0.35, "bump_plastic": 0.3, "bump_plastic2": 0.3, "interior": 0.35, "solid": 0.3,
    "black": 0.3, "frame": 0.3, "mottled": 0.3, "leather": 0.45, "bump_leather": 0.45,
    "bump_stitching": 0.2, "cloth": 0.1, "headliner": 0.1, "suede": 0.1, "bump_plastic3": 0.3, "rubber": 0.2, "metal": 0.6, "reflector": 0.55,
    # Cabin badges (RUF horn-pad logo, SX4/Integra console script) are drawn in the cockpit atlas; bound to
    # satin chrome the RUF wheel showed a blank chrome plate (owner, visual-52).
    "badge": 0.7,
}
# Cockpit components that are the exterior seen from inside sample the body textures, not the atlas.
COCKPIT_EXTERIOR_PARTS = ("hood", "mirror", "a_pillar", "glass", "cage")


# FM4 exterior emblems and badges draw the car's own logo and lettering (pre-shaded chrome) from
# nodamage_LOD0.xds through UV0; untextured chrome showed featureless blobs. Native badge shader
# badge_ch1difgloss (mfr_emblems/m_002) with its diffuse texture switched on carries that art.
EXTERIOR_ATLAS_MATERIAL = "mfr_emblems/m_002"
EXTERIOR_ATLAS_GLOSS = {"emblem": 0.9, "badge": 0.9}


# FM4 techniques that take their colour from the car's diffuse atlas (baseSampler: nodamage_LOD0.xds on LOD0,
# nodamage.xds on LOD1-5; tools/ForzaHorizon1-ReImagined docs/modding/carbin-format.md, "Samplers of the
# in-race shaders"). Bound to bare library plastics they lost it: SX4 grille back and lower intake grey (atlas
# 12-18, black), Integra intake painted (matte_colors bound to carpaint), RUF/Integra/SX4 discs untextured.
# Native exterior plastic with a CH1 diffuse map, the atlas there, gloss per FM4 class.
EXTERIOR_ATLAS_PLASTIC = "exterior_plastic/plastic_ch1_custom_diffusemap"
ATLAS_PLASTIC_GLOSS = {"plastic": 0.4, "plastic2": 0.4, "frame": 0.45, "matte": 0.15, "matte_colors": 0.15,
                       "misc": 0.5, "engine": 0.4, "dullplastic": 0.2, "cage": 0.4}
# Brake models: caliper/rotor/hub (FM4 `brake` on RUF, `misc` on Integra) draw the disc and caliper pictures
# of the same atlas (SX4 hub UVs centre on the disc drawn at 0.12, 0.68 of nodamage_LOD0).
BRAKE_ATLAS_GLOSS = {"caliper": 0.55, "rotor": 0.7, "hub": 0.45, "brake": 0.7, "misc": 0.6, "metal": 0.7,
                    "front": 0.55}
# FM4 `emblem` blends the atlas art over the paint by the atlas alpha (Integra "Type R": letters on alpha 255,
# white background on alpha 0 shows the yellow body). Native alpha-tested sticker: diffuse + opacity mask.
EXTERIOR_STICKER = "exterior_misc/sticker_mfr_cutsom"
STRIPE_PAINT = "exterior_misc/carpaint_livery"
# FM4 grille1/grille2 (+_alpha) sample Shared.zip grille1_alpha.xds / grille2_alpha.xds (64x64 tiles, alpha the
# wire): the SX4 diamond mesh. Native alpha-tested metal with the tile as its opacity mask.
GRILLE_MESH = "exterior_metal/metal_ch1_custom"
# FM4 rim techniques sample the wheel archive's atlas (wheel_LOD0.xds / wheel.xds): SX4 dark grey spokes,
# Integra grey with the red H cap. Native wheel MatIs only override colours (wheel_paint carries no diffuse
# texture record), so the rim takes the textured badge shader the emblems already use, at a wheel gloss.
WHEEL_ATLAS = "mfr_emblems/m_002"
WHEEL_ATLAS_GLOSS = 0.8
WHEEL_ATLAS_SURFACES = {"rim", "outer_rim", "wheel_emblem"}
# FM4 lens techniques (detail_glass_*, lights_glass) colour the lens from the lamp atlas (LightsSampler):
# amber indicators, red tail lenses (RUF rear bar, Integra side repeater were clear).
TINTED_LENSES = {"detail_glass_red", "detail_glass_amber", "lights_glass", "lights_gls_taillightst"}
COCKPIT_BODY_PARTS = ("hood", "mirror", "a_pillar", "cage")
MIRROR_SURFACES = {"mirrorleft": "specificuse/mirrorleft", "mirrorright": "specificuse/mirrorright",
                   "mirrormiddle": "specificuse/mirrorcenter", "mirrorcenter": "specificuse/mirrorcenter"}


# MatI label of cabin badges (fh4.make_models renames them, also on the cabin parts drawn in the exterior view).
# MTPR blocks are keyed car-wide by (label, member): sharing `badge` with the exterior stickers gave one of the
# two the other's atlas and mask (owner, visual-56: RUF horn-pad logo in two tones).
CABIN_BADGE = "cabin_badge"


def exterior_like(component_kind, component):
    """Exterior parts, and the cockpit copies of body parts (FM4 keeps hood/mirror LOD0 in the cockpit set)."""
    return component_kind == "exterior" or (component_kind == "cockpit"
                                            and component.casefold().startswith(COCKPIT_BODY_PARTS))


def atlas_source(label, component_kind="", component=""):
    """Which FM4 texture a surface's colour comes from: 'body' (nodamage), 'lights', 'wheel', 'grille1',
    'grille2' or None. EXPANDED_GARAGE_SOURCE_ATLAS=off: the visual-50 untextured bindings."""
    if os.environ.get("EXPANDED_GARAGE_SOURCE_ATLAS") == "off":
        return None
    name = label.casefold()
    if component_kind == "brake" and name in BRAKE_ATLAS_GLOSS:
        return "body"
    if name == CABIN_BADGE:
        return "cabin_badge"
    if (component_kind == "cockpit" and name == "badge" and not component.casefold().startswith(COCKPIT_EXTERIOR_PARTS)
            and os.environ.get("EXPANDED_GARAGE_CABIN_BADGE") != "atlas"):
        return "cabin_badge"
    if component_kind == "wheel" and name in WHEEL_ATLAS_SURFACES:
        return "wheel"
    if not exterior_like(component_kind, component):
        return None
    if name in ATLAS_PLASTIC_GLOSS or name in ("emblem", "badge", "liverybadge", "stripe_badge"):
        return "body"
    if name.startswith(("grille1", "grille2")):
        return name[:7]
    if name in TINTED_LENSES or name == "reflector":
        return "lights"
    return None


def cockpit_atlas_surface(label, component_kind, component):
    return (component_kind == "cockpit" and label.casefold() in COCKPIT_ATLAS_GLOSS
            and not component.casefold().startswith(COCKPIT_EXTERIOR_PARTS))


def surface_binding(label, component_kind="", component=""):
    name = label.casefold()
    part = component.casefold()
    def bind(member, purpose=MaterialUse.VISIBILITY, alpha=False, fidelity="shared_base", resolved=True,
             compatibility="native_shared_binding"):
        return SurfaceBinding(member+".materialbin", purpose, alpha, resolved, fidelity, compatibility)
    if atlas_source(label, component_kind, component) == "cabin_badge":
        # FM4 cabin `badge` (base x env, no normal map) reads as a raised outline: the RUF horn-pad logo is
        # the pad colour with a light rim (owner, visual-53: a solid grey decal with the opaque atlas shader).
        # Alpha-tested sticker whose mask is only the inner rim of the atlas alpha (fh4_source_atlas, role
        # 'edge'); thin script (SX4/Integra console) is narrower than the rim and stays whole.
        # EXPANDED_GARAGE_CABIN_BADGE=atlas: the visual-53 opaque binding.
        # visual-57: the sticker shader bevels its opacity mask (raised-badge look): on a thin outline the bevel
        # lit the horizontal strokes white and the slanted ones dark (owner, visual-56). Opaque textured cabin
        # shader instead, with the logo filled by the surrounding pad colour and only the rim kept
        # (fh4_source_atlas role 'outline').
        return bind(COCKPIT_ATLAS_MATERIAL, MaterialUse.FIDELITY, fidelity="source_cockpit_badge_outline")
    if cockpit_atlas_surface(label, component_kind, component):
        return bind(COCKPIT_ATLAS_MATERIAL, fidelity="source_cockpit_atlas")
    source = atlas_source(label, component_kind, component)
    if source == "body" and name == "stripe_badge":
        return bind(STRIPE_PAINT, MaterialUse.FIDELITY, True, fidelity="source_factory_stripe_mask")
    if source == "body" and name in ("emblem", "badge", "liverybadge"):
        # Integra "Type R" side and rear stickers are `badge` draws: the atlas alpha is the lettering (owner,
        # visual-51: white surround with the opaque badge shader).
        return bind(EXTERIOR_STICKER, MaterialUse.FIDELITY, True, fidelity="source_exterior_atlas_alpha")
    if source == "body" and component_kind == "brake":
        return bind(EXTERIOR_ATLAS_PLASTIC, MaterialUse.RUNTIME, fidelity="source_brake_atlas")
    if source == "body":
        return bind(EXTERIOR_ATLAS_PLASTIC, MaterialUse.FIDELITY, fidelity="source_exterior_atlas")
    if source in ("grille1", "grille2"):
        return bind(GRILLE_MESH, MaterialUse.FIDELITY, True, fidelity="source_grille_mask")
    if source == "wheel":
        return bind(WHEEL_ATLAS, MaterialUse.RUNTIME, fidelity="source_wheel_atlas")
    if source == "lights" and name in TINTED_LENSES:
        return bind("exterior_glass/gls_colored", MaterialUse.EFFECT, True, "source_lamp_atlas_tint")
    if (exterior_like(component_kind, component) and name in ("textured_reflector", "reflector")
            and os.environ.get("EXPANDED_GARAGE_LAMP_TEXTURE") != "off"):
        # FM4 textured_reflector draws the lamp atlas (lights_LOD0.xds) through UV0: red bumper reflectors and
        # tail lamp backs, amber head lamp backs (SX4). Plain chrome showed them white (owner, showroom shots).
        # Badge shader with its diffuse on, pointed at the lamp atlas (fh4.lamp_texture_parameters).
        return bind(EXTERIOR_ATLAS_MATERIAL, MaterialUse.FIDELITY, fidelity="source_lamp_atlas")
    if exterior_like(component_kind, component) and name in EXTERIOR_ATLAS_GLOSS:
        return bind(EXTERIOR_ATLAS_MATERIAL, MaterialUse.FIDELITY, fidelity="source_exterior_atlas")
    if name in {"body","body_2","body_3","paint","matte_colors","race_kit_red"}:
        return bind("exterior_misc/carpaint",MaterialUse.RUNTIME)
    if name in {"tire","tyre"}:
        return bind("genericassetmaterials/tiresidewall_a",MaterialUse.RUNTIME,fidelity="source_tread_texture_missing")
    # Native stock wheels (sampled LF wheel MatIs): inner_rim paint_matteblack (27 of 40), black backfacinggeo
    # (22 of 31), blur_lip specificuse/wheel_blur or wheel_blurlip (48 of 49), both translucent (bucket 28).
    if name == "inner_rim":
        return bind("wheelmaterials/paint_matteblack",MaterialUse.RUNTIME)
    if name == "wheel_black":
        return bind("specificuse/backfacinggeo",MaterialUse.RUNTIME)
    if name in ("blur_lip", "chrome_blur_lip"):
        return bind("specificuse/wheel_blurlip",MaterialUse.RUNTIME,True)
    if name in {"rim","outer_rim"}:
        return bind("wheelmaterials/aluminum_machined_satin",MaterialUse.RUNTIME)
    if name in ("blur_rim", "chrome_blur_rim"):
        # Native blurRim meshes: specificuse/wheel_blur, translucent (bucket 28), shown by the game at speed.
        return bind("specificuse/wheel_blur",MaterialUse.RUNTIME,True)
    if name in {"caliper","brake","rotor","hub","metal"}:
        return bind("brakes/metal",MaterialUse.RUNTIME)
    if name in LAMP_SURFACES:
        # FM4 lamp techniques glow through a native lit reflector whose MatI carries the light function
        # (fh4_model.lamp_parameters: head 71, tail+brake 69, reverse 1, turn 70).
        return bind("exterior_lights/lights_smooth_ch1",MaterialUse.EFFECT,fidelity="missing_lamp_glow_mask")
    if name in {"tail_light","tail_light_lod0","brake_light","detail_glass_red"}:
        # Red lens: native gls_colored with the donor's red tint parameters.
        return bind("exterior_glass/gls_colored",MaterialUse.EFFECT,True,"native_red_lens_tint")
    if name in {"head_light","reverse_light","turn_light","head_light_lod0",
                "reverse_light_lod0","lights_glass","lights_gls_oldhead"} or name.startswith("detail_glass_"):
        return bind("exterior_glass/gls_clear",MaterialUse.EFFECT,True,"missing_optical_channels")
    if name == "black_glass":
        # FM4 paints window surrounds and pillar appliques with black_glass; native blackglass keeps its gloss
        # (owner, visual-33: the opaque plastic read matte; the showroom-only pillar polygons stay open).
        # EXPANDED_GARAGE_BLACK_GLASS=opaque: opaque plastic_mgl off the panes (experiment).
        variant = os.environ.get("EXPANDED_GARAGE_BLACK_GLASS", "gloss")
        if variant == "opaque" and not part.startswith("glass"):
            return bind("exterior_plastic/plastic_mgl",MaterialUse.FIDELITY,fidelity="opaque_black_glass_trim")
        # FM4 draws black_glass with its glass technique: a black surface with the pane's reflections. The
        # native frit shader (below) has none and read matte next to FM4 on every build since visual-33
        # (owner, comparison shots). paint_glossblack is the native opaque black under a clear coat
        # (carpaint_standard). Rejected in visual-29 for "facets" on the pillar that visual-33 traced to the
        # pane shadows, now gone. EXPANDED_GARAGE_BLACK_GLASS=native: the visual-40 native frit.
        if variant == "gloss" and not part.startswith("glass"):
            return bind("wheelmaterials/paint_glossblack",MaterialUse.FIDELITY,fidelity="glossy_black_glass_trim")
        # On pane models (and with =native): native blackGlass, opaque (bucket 17) on the int_simplediffuse shader: no reflection of its own.
        # Natives get their gloss from the pane modelled ~2 mm over the frit; FM4 cars have no such pane,
        # and a generated one showed its outline (owner, visual-39), so the frit stays plain native.
        # Translucent, it showed the sunlit cabin through and read grey (owner, visual-33..38).
        # EXPANDED_GARAGE_BLACK_GLASS=translucent: the visual-27 translucent frit (bisection switch).
        return bind("specificuse/blackglass",MaterialUse.VISIBILITY,
                    os.environ.get("EXPANDED_GARAGE_BLACK_GLASS") == "translucent")
    if name == "defrost_lines":
        return bind("interior_misc/defrostlines" if component_kind=="cockpit" else "exterior_misc/defrostlines",
                    MaterialUse.FIDELITY,True)
    if name in {"glass","window"} and component_kind == "cockpit" and not part.startswith("glass"):
        # Cabin glass off the windows is the instrument cover (SX4 `interior`): natives use gls_clear (13 of 17
        # sampled gauge-cluster glasses); the window shader drew road dust over the dials (owner, visual-42).
        return bind("interior_glass/gls_clear",MaterialUse.VISIBILITY,True)
    if name in {"glass","window"}:
        # Native glazing by pane: windscreen, rear screen, side windows; cabin side uses interior variants.
        if component_kind == "cockpit":
            member = "interior_glass/gls_windsheild_interior" if part == "glassf" else "interior_glass/gls_window_interior"
        elif part == "glassf":
            member = "exterior_glass/gls_windsheild"
        elif part == "glassr" and os.environ.get("EXPANDED_GARAGE_REAR_GLASS") == "native":
            member = "exterior_glass/gls_rearglass"
        elif part == "glassr":
            # FM4 draws every pane with one `window` technique (one tint); gls_rearglass carries no tint
            # parameter and the rear screen read lighter than the side glass (owner, visual-30).
            member = "exterior_glass/gls_window"
        elif part.startswith("glass") and not part.endswith(("hl","tl")):
            member = "exterior_glass/gls_window"
        else:
            member = "exterior_glass/gls_clear"
        return bind(member,MaterialUse.VISIBILITY,True)
    if (component_kind == "cockpit" and not part.startswith(COCKPIT_EXTERIOR_PARTS)
            and ("chrome" in name or name == "badge")):
        # Native cabins use interior_metal/chrome (68 of the chrome/metal MatIs in 30 sampled interiors);
        # the exterior chrome shader mirrored the showroom light strips as black bands (owner, visual-27).
        # FM4 cockpit_chrome reflects dimly (CubeScale 0.3, ChromeAmbientScale 0.02): satin, not mirror.
        return bind("interior_metal/chrome_satin")
    if name in {"badge","emblem","wheel_emblem"}:
        return bind("exterior_metal/chrome",MaterialUse.FIDELITY,fidelity="missing_source_emblem_texture")
    if name == "gauge_backing":
        # Native gauge clusters lay the alpha-tested symbols over an opaque plastic face.
        return bind("interior_plastic/plastic")
    if name == "gauge_needle":
        return bind("interior_misc/gauge_needle",MaterialUse.EFFECT,fidelity="missing_source_needle_texture")
    if name == "gauge_emissive":
        # Dial faces: native int_gauges with the car's own dial texture (fh4.gauge_texture_parameters).
        return bind("interior_misc/int_gauges",MaterialUse.EFFECT,fidelity="source_dial_texture")
    if name == "digital_gauge_unlit":
        return bind("interior_plastic/plastic",fidelity="digital_dash_without_scene")
    if name == "digital_gauge":
        # int_symbols_emissive without a texture showed the game's Forza-logo placeholder (owner, visual-61: Prius,
        # Leaf, PTG BMW). FM4 digital dashes are not in the cabin atlas: digitalgauge/dash_<car>.bgf/.bsg/.fbf is a
        # vector scene composed at runtime from sprite .xds pieces into the screen's own 0..1 UV space. A static frame
        # of it goes on the dial shader (fh4_digital_dash); cars without a dash scene get a dark display
        # (fh4.make_models). EXPANDED_GARAGE_DIGITAL_GAUGE=symbols: the old binding.
        if os.environ.get("EXPANDED_GARAGE_DIGITAL_GAUGE") != "symbols":
            return bind("interior_misc/int_gauges",MaterialUse.EFFECT,fidelity="source_digital_dash_frame")
        return bind("interior_misc/int_symbols_emissive",MaterialUse.EFFECT,fidelity="missing_instrument_emissive_texture")
    if name in MIRROR_SURFACES and os.environ.get("EXPANDED_GARAGE_MIRRORS") != "chrome":
        # FM4 mirrorLeft/Right/Middle are the mirror glass. Bound to chrome they showed the blurred environment
        # cube, not the view behind (owner, visual-52). Native mirror materials draw the rear-view render target
        # through UV0 spread 0..1 over the glass (fh4.mirror_uv).
        return bind(MIRROR_SURFACES[name], MaterialUse.EFFECT, fidelity="native_mirror_target")
    if "chrome" in name or name.startswith("mirror") or "reflector" in name:
        return bind("exterior_metal/chrome")
    if "leather" in name:
        return bind("interior_leather/leather_mgl")
    if name in {"cloth","headliner","carpet","bump_stitching"}:
        return bind("interior_fabric/fabric_001")
    if "carbon" in name:
        return bind("exterior_misc/carbonfiber_flat")
    if name in {"frame","bottom","undercarriage","bumper_frame"}:
        return bind("specificuse/undercarriage")
    if name in {"rubber","rubber_trim","weatherstrip"}:
        return bind("exterior_misc/rubbertrim")
    if name.startswith("grille"):
        return bind("exterior_metal/metal_radiator",fidelity="missing_grille_mask")
    if name in {"black","interior","plastic","plastic2","bump_plastic","bump_plastic2"}:
        return bind("interior_plastic/plastic" if component_kind=="cockpit" else "exterior_plastic/plastic_smooth")
    if name in {"wood","mottled"}:
        return bind("interior_plastic/plastic_mgl",MaterialUse.FIDELITY,fidelity="missing_finish")
    # FM4 catch-all names lose the surface subtype. Apply the native base pass only
    # where the component provides positive evidence about its visible surface.
    if name == "solid" and component_kind == "cockpit":
        if part in {"seatl","seatr"}:
            return bind("interior_fabric/fabric_001",fidelity="source_seat_finish_unresolved",
                        compatibility="trial-compatible")
        if part == "interior":
            return bind("interior_plastic/plastic",fidelity="source_cabin_surface_mix_unresolved",
                        compatibility="trial-compatible")
    if name == "misc" and component_kind == "exterior":
        if part.startswith(("body","hood","trunk","bumper","tailgate")):
            return bind("exterior_misc/carpaint",fidelity="source_mixed_panel_finish_unresolved",
                        compatibility="trial-compatible")
        if part.startswith("exhaust"):
            return bind("exterior_metal/chrome",fidelity="source_exhaust_finish_unresolved",
                        compatibility="trial-compatible")
    if name == "misc" and component_kind == "cockpit" and part.startswith("hood"):
        return bind("exterior_misc/carpaint",fidelity="source_cockpit_hood_finish_unresolved",
                    compatibility="trial-compatible")
    if name == "misc" and component_kind == "brake" and part.startswith(("caliper","rotor")):
        return bind("brakes/metal",MaterialUse.RUNTIME,fidelity="source_brake_finish_unresolved",
                    compatibility="trial-compatible")
    # Explicit opaque preview binding with no drive qualification for an unclassified visible surface.
    return bind("exterior_plastic/plastic_smooth",resolved=False,fidelity="unclassified_source_surface",
                compatibility="unclassified")
