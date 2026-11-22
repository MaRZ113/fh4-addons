# FM4 DC2 to FH4 Modelbin

## First geometry milestone

Run after the unmodified Acura geometry works from the independent archive. Keep 3477 identity, registration, paint, audio, suspension, engine, performance, Autoshow strings and thumbnail equal to the accepted alias run. Confirm source from FM4 ID 370 and record archive/member hashes. Inspect body and LOD0 scene in carbin-garage. Select the donor stock front bumper primitive(s) by appearance, materials and source part labels. Export one named object and retain the original glTF and transform metadata.

| Check | Procedure |
|---|---|
| Units and axes | Read glTF node transforms. Measure bumper corners, centerline and width against FM4 body landmarks, then against the stock FH4 bumper and front axle in a common view. Derive and record the affine transform and winding explicitly. |
| Position | Match the FH4 Carbin's existing group 34 model transform and anchor. Verify wheel/housing positions and headlamp seam in front, side and top views. A distinct DC2 nose may expose gaps against the Acura shell; record them for full fascia work. |
| Normals and UV | Preserve UV islands, seams, smooth/sharp edges and triangle winding. Compare source and export vertex/triangle counts, normals and material assignment. Recompute tangents only for the selected target material requirements. |
| Materials | Start with a proven FH4 paint material on one mesh. Preserve FM4 UVs. Restore the appropriate XDS-derived textures and target material slots in a later single-variable pass. |
| Runtime payload | Import OBJ or FBX into ForzaTech Studio Modelbin Creator, choose FH4, and use the original FH4 bumper as a format and material reference. Verify bounds, index type, vertex streams and material paths. Use glTF as interchange, an exporter or Blender to produce creator-supported OBJ/FBX. |
| LOD and Carbin | Preserve group 34, its Phase 2 keys and stock/upgrade meanings. Replace only the alias archive member used by the stock bumper. Retain known FH4 fallback LODs initially, document the tested camera distance and evaluate transition, culling and seams. |
| Boot and render | On an isolated offline profile start on 368, switch to 3477, inspect Autoshow/garage/open world, then return to 368. Check near, far and oblique camera views. Save/restart only after both profiles and archives have passed their controls. |

Modelbin Creator and the supported interchange formats are documented by the Forza Mods guide. A creator export is an offline artifact. Verify the exact FH4 game dialect on the target member and probe it in-game before expanding scope. [Modelbin Creator workflow](https://www.forzamods.dev/make-a-mod/modelbin-guides/importing-custom-models-with-forzatech-studio)

## Full car after the first part works

Stage the matched JDM fascia, rear and side panels before mixing full-shell LODs. Map headlamp and indicator states, gauge cluster, cockpit instruments, right-hand-drive seat/steering/passenger transforms, mirrors, driver IK and roof/interior occlusion. Verify wheelbase, rolling radius, brake offsets, caliper positions, wheel animation, original DC2 wheel materials and suspension clearance. Adapt collision and deformable/damage representations in a separate physics pass.

Set the Honda make, 2000 year, official name, localization, gallery thumbnail and factory paint variants as a catalogue/appearance pass after the geometry works. Create appropriate FH4 lights, paint mask, manufacturer colors and animation resources. Delay custom physics and audio until geometry and presentation pass with the original Acura control. In each pass track donor preservation, source hashes, visual comparison and persistence results.
