# Wheel and tire contract

`WheelTransformCompiler` uses original PartPositions and the same axle dimensions used by the tire compiler. The hub rest translation is chassis-local. The spindle starts at its hub origin with zero parent-local displacement. Wheel and brake meshes retain component-local coordinates. The render placement, source GameDB references, GR2 bounds and tire size each share an audited source coordinate. Unloaded contact is calculated from the hub Y minus the tire radius.

The stock cohort covers five FH4 cars and all 20 corners. Model placement versus hub translation differs by 0.075–0.213 m. Several DB part positions also differ from both serialized frames. The comparison establishes that the fields represent distinct inputs. Additive runtime composition and suspension displacement remain unobserved. No wheel coordinate was zeroed as a workaround.

The pinned PE's on-disk bytes at the scene-update address differ from the previously observed mapped executable code. REA opened the pinned PE and its requested function analysis reached the 300-second deadline. Local Capstone and PE unwind readback established the address mapping, but the on-disk sequence did not provide a usable body. The historical Phase 4 mapped-code evidence identifies the spindle and wheel blur paths. Its trace does not provide the four live world-space wheel transforms.

Stock dry-profile counts include 207 cars with `a`, 25 with `c`, and two with `drag`; all three tokens have only the left Modelbin in the supplied stock mounts. For `b`, `WET_b` and `WET_c`, the stock resource lookup also finds a distinct right member. The proof lists archive hashes and manifest ranges. Observed one-sided stock packages establish supported serialized layouts. The right-hand render path and force response still require a driving observation.

The next minimum native check is four live hubs and spindles at rest and during suspension deflection. Compare joint and display values independently with the generated rest and contact coordinates. Confirm the native wheel path and braking before qualifying the first-drive package.
