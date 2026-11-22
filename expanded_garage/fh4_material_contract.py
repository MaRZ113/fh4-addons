"""Classify shared FH4 material bindings by their use in the generated scene."""

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
                 "reverse_light", "reverse_light_lod0", "turn_light"}


def surface_binding(label, component_kind="", component=""):
    name = label.casefold()
    part = component.casefold()
    def bind(member, purpose=MaterialUse.VISIBILITY, alpha=False, fidelity="shared_base", resolved=True,
             compatibility="native_shared_binding"):
        return SurfaceBinding(member+".materialbin", purpose, alpha, resolved, fidelity, compatibility)
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
    if name == "blur_lip":
        return bind("specificuse/wheel_blurlip",MaterialUse.RUNTIME,True)
    if name in {"rim","outer_rim"}:
        return bind("wheelmaterials/aluminum_machined_satin",MaterialUse.RUNTIME)
    if name == "blur_rim":
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
                "reverse_light_lod0","lights_glass"} or name.startswith("detail_glass_"):
        return bind("exterior_glass/gls_clear",MaterialUse.EFFECT,True,"missing_optical_channels")
    if name == "black_glass":
        return bind("specificuse/blackglass",MaterialUse.VISIBILITY,True)
    if name == "defrost_lines":
        return bind("interior_misc/defrostlines" if component_kind=="cockpit" else "exterior_misc/defrostlines",
                    MaterialUse.FIDELITY,True)
    if name in {"glass","window"}:
        # Native glazing by pane: windscreen, rear screen, side windows; cabin side uses interior variants.
        if component_kind == "cockpit":
            member = "interior_glass/gls_windsheild_interior" if part == "glassf" else "interior_glass/gls_window_interior"
        elif part == "glassf":
            member = "exterior_glass/gls_windsheild"
        elif part == "glassr":
            member = "exterior_glass/gls_rearglass"
        elif part.startswith("glass") and not part.endswith(("hl","tl")):
            member = "exterior_glass/gls_window"
        else:
            member = "exterior_glass/gls_clear"
        return bind(member,MaterialUse.VISIBILITY,True)
    if name in {"badge","emblem","wheel_emblem"}:
        return bind("exterior_metal/chrome",MaterialUse.FIDELITY,fidelity="missing_source_emblem_texture")
    if name == "gauge_backing":
        # Native gauge clusters lay the alpha-tested symbols over an opaque plastic face.
        return bind("interior_plastic/plastic")
    if name == "gauge_needle":
        return bind("interior_misc/gauge_needle",MaterialUse.EFFECT,fidelity="missing_source_needle_texture")
    if name in {"gauge_emissive","digital_gauge"}:
        return bind("interior_misc/int_symbols_emissive",MaterialUse.EFFECT,fidelity="missing_instrument_emissive_texture")
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
