# Vehicle systems status

| System | Generated or verified | Remaining work |
| --- | --- | --- |
| Powertrain | Independent engine, torque curve and drivetrain rows from FM4. Original mass, ratios and axle dimensions remain inspectable. | Native target-only fields and dynamics under driving loads. |
| Suspension and brakes | Source spring/anti-roll/weight rows and brake geometry. Suspension IDs map by enum name. | Actual contact behavior, stock tire physics and target steering/damping fields. |
| Collision | Native v27 two-cloud stock form derived from the source body envelope. Type-5 clouds carry 16-byte XYZ/margin points. The lower member and its elevated counterpart follow the common observed 0.2 m form. | Validate point-cloud role dispatch at runtime, actual road contacts and multi-shape upgrade assignments. Damage remains open. |
| Wheels and rig | Four own rest positions, local Modelbin geometry, hub/spindle skeleton and selected source locators. | Runtime transform composition, GR2/GSF, control wheel motion/blur, tire model selection and full driver/panel constraints. |
| Materials | Diagnostic v1 shared-material mapping, owned geometry, tangents, normals and primary UV. Fallbacks are declared in the build receipt. | A typed shader/slot translator, source atlas swatches and destination-specific dirt, water, AO, paint and damage channels. |
| Lighting and FX | Authored head/tail hardpoints and candidate surface positions. | Lights and LightPresets writers, projection, glare, separate light events and exhaust. |
| Geometry variants | One source-derived catalogue and CarScene plan for supported body fittings. | Runtime visibility and variant physics; unresolved alternatives retained. |
| Audio | Source banks and tuning retained in IR. | Native mapping, synthesis and separate cabin/external events. |
| Presentation | English v4 string archive, new make IDs, source brand artwork and thumbnails. | Runtime frontend loading, localization for other languages and wheel icons. |

The v27 outer envelope is not a mass transfer. Observed two-cloud stock forms use a zero mass header and the stock inverse-tensor sentinel. Source mechanical values reside separately in GameDB. This establishes serialization, with dynamics and units still requiring controlled validation.
