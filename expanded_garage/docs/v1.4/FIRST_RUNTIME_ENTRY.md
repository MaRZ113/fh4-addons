# Expanded Garage 1.4.0a1

The standalone Suzuki SX4 Sportback `EG_SUZ_SX4_11` (12000) passes the static offline trial checks. Its scene archive contains 83 entries. The other two controls compile with the same backend and are available in the fleet regression build. The staged install contains only Suzuki.

| Decision | Suzuki | Evidence |
| --- | --- | --- |
| Structural validity | Passed | Insert-only GameDB audit, manifest and archive replay, model and rig dependencies |
| Offline trial eligibility | Passed | Stock tires, compound curves, four wheel bindings, steering geometry, v27 shell, GSF and native suspension readback |
| Runtime accepted | Pending | Owner acquisition, load, drive, save, restart and verified rollback |
| Full vehicle fidelity | Pending | See the remaining systems below |

## Dynamics and materials

FM4 supplies weight and distribution, torque curve, drive layout, transmission ratios, steering inputs, spring and damper rates, track and hardpoints. The conversion resolves the 27 previously outstanding Suzuki drive fields. It uses bounded medians of comparable native stock cars for the handling scalars. Aero caps and FFB use shared stock values. Stock bump-stop height and progressive settings are selected together by axle and family. Anti-roll beam stiffness uses same-family axle measurements. Brake torque clamps and non-spinning mass use native brake-type evidence. Driveline flex uses a repeated stiffness/damping tuple and same-family spring and torque statistics. Each projected field records its matched rows or support count. These are operating estimates for an offline drive, with physical calibration still open.

The FM4 legacy suspension enum does not identify FH4 kinematics. A manufacturer specification matched through the existing source vehicle traits identifies the front strut and rear torsion beam. The matching FH4 road suspension families are resolved through the baseline stock catalogue. Native XML readback checks the spindle, chassis, upright, suspension travel and front kingpin. Host preflight pins the original protected XML bytes. [Suzuki suspension specification](https://xr793.org/wp-content/uploads/2022/08/2010-Suzuki-SX4-Hatchback-CN.pdf)

Six Suzuki surfaces previously lacked a drive-qualified binding. The material compiler classifies them by role and FM4 material label. It uses fabric for seats, a general cabin surface for the cockpit, paint for body closures and metal for exhaust. These are marked `trial-compatible` in the receipt. Every serialized Modelbin material resolves in the host library. Actual surface finish, source textures and functional channels remain separate.

## Native scene and physics boundary

The static audit reloads the owned GR2 skeleton and the named GSF with its accessory clips. It verifies four wheel Modelbins bound to their respective spindles, the steering wheel component bound to its steering spindle, and matching Carbin, Modelbin, GR2 and FM4 part-position rest frames. The v27 readback checks header fields, point cloud roles, bounds, radius and clearance above the four unloaded tire contact points. Stock tire families and physical curves resolve through the native catalogue. Left-only tire resources carry evidence of the same pattern in stock packages.

On Steam 1.478.564.0, Phase 4 traces show scene initialization at RVA `0x3620D50` building an animation GSF path. The scene update routine at `0x362FC20` references Granny assets, spindles and wheel blur separately. The GSF shipped here binds gauges. Wheel rotation, steering motion and suspension response still require live confirmation. The wheel, collision and animation entries remain `unverified_runtime` until owner gameplay.

## Regression and remaining coverage

Honda 12002 retains the RHD source geometry. Its double-wishbone source specification is recorded, with a compatible shared FH4 axle mapping still open. RUF 12001 retains rear engine placement, RWD and the source July DLC parts. Its source suspension topology and rear-engine road dynamics mappings need stronger evidence before qualification. Both archives and their insert-only fleet database pass the structural audit. [Honda DC2 suspension specification](https://www.honda.co.jp/sportscar/sportscar/integra_type_r/)

The trial still needs confirmation of native suspension/contact forces, wheel placement under load, steering and tire behavior. Remaining fidelity work includes shader channels, dirt, water, AO, lights and exhaust, dynamic wheel blur, camera and driver animation, closures, damage, sound, tuning calibration and non-English localization. The evidence records uncovered assistance, aero transients and other target-only fields separately.
