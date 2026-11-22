# Expanded Garage 1.3.0a1 Native Runtime Bridge

The common backend builds Suzuki 12000 and the two fleet controls for FH4 Steam 1.478.564.0. The deliveries pass structural validation and carry `integration-preview` and `installable: false`. No driving or save acceptance has been recorded.

| Vehicle | CarID | ZIP entries | Source-authored clips |
| --- | ---: | ---: | --- |
| Suzuki SX4 Sportback | 12000 | 83 | Fuel, speedometer, tachometer |
| Honda Integra Type-R | 12002 | 90 | Speedometer, tachometer |
| RUF CTR2 | 12001 | 64 | Speedometer, tachometer |

New Granny clips use the FM4 min/mid/max needle geometry. Matching indices establish point correspondence. A rigid fit estimates rotations and a shared pivot and rejects motion outside its residual bounds. Tick records provide the calibration. The generated GSF includes a root state machine, Base animation set, typed parameter and remap nodes, and a subtree sampler. Source clips, nodes, slots and meshes resolve to the corresponding vehicle-owned bones. The two calibration conventions tested from stock data are mph and thousands of RPM. Parameter dispatch remains unverified in FH4.

The independent ForzaTech Studio parser decoded the stock-form roots, all seven authored tracks and the generated rest skeletons. The local Granny middleware reloaded all 13 generated animation containers across the three cars. This checks container and relocation acceptance outside the Python serializer. The middleware reload does not execute the FH4 animation graph.

Steering meshes now bind to their own spindle. The FM4 hardpoint defines the origin and the original wheel geometry defines its plane. The mesh is written in component-local space with the matching Modelbin, Granny and Carbin rest placement. Wheel, steering and instrument attachments are checked against the composed bind matrices. Repeated compilation uses the same importer, without a separate control-car branch.

The original FM4 door geometry is not supplied as a separate moving panel in these control IRs. Hood and trunk parts have named meshes, but their hinge and native event bindings are open. Wipers, driver IK, door articulation and body animation are tracked in the rig receipt. The GSF coverage is the generated gauge channels.

| Qualification | Result |
| --- | --- |
| Structural Validity | Passed for all three control archives and the fleet |
| Drive Readiness | Open pending runtime attachment, physics/contact, and material dependencies |
| Full Vehicle Fidelity | Open pending the remaining accessory animation, lighting, audio, surface effects and damage |

The archive includes the editable compiler and tests, Python wheel, wrapped and plain previews, reproduction recipes, stock comparisons, qualification and independent parser evidence. The historical 3477 and v1.2 handoff remain preserved. The deployment preflight rejected the preview before writing any game file.

- [Wheel and tire contract](RUNTIME_WHEEL_AND_TIRE_CONTRACT.md)
- [Dynamics and material status](DYNAMICS_AND_MATERIALS.md)
- [Build, installation and owner test](INSTALL_TEST_AND_ROLLBACK.md)
