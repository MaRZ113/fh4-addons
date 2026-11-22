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
    if name in {"rim","inner_rim","outer_rim","wheel_black"}:
        return bind("wheelmaterials/aluminum_machined_satin",MaterialUse.RUNTIME)
    if name in {"caliper","brake","rotor","hub","metal"}:
        return bind("brakes/metal",MaterialUse.RUNTIME)
    if name in {"head_light","reverse_light","tail_light","brake_light","turn_light","head_light_lod0",
                "tail_light_lod0","reverse_light_lod0","lights_glass"} or name.startswith("detail_glass_"):
        return bind("exterior_glass/gls_clear",MaterialUse.EFFECT,True,"missing_optical_channels")
    if name in {"glass","window","black_glass","defrost_lines"}:
        return bind("exterior_glass/gls_limotint" if name=="black_glass" else "exterior_glass/gls_clear",
                    MaterialUse.FIDELITY if name=="defrost_lines" else MaterialUse.VISIBILITY, True)
    if name in {"badge","emblem","wheel_emblem"}:
        return bind("exterior_metal/chrome",MaterialUse.FIDELITY,fidelity="missing_source_emblem_texture")
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
