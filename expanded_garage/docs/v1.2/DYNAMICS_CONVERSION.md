# Dynamics conversion

| Area | Conversion and readback |
| --- | --- |
| Transmission | Preserve original reverse, forward ratios and final drive. Verify the reverse sign, active ratio count and unused slots. Both catalogues use the count including reverse. The 192 pairs agree on counts and 178 final drives are numerically identical. |
| Shift system | Decode the FH4 enum using English strings. Explicit FM4 CVT or paddle data select those categories. A clutch-equipped discrete gearbox and a non-paddle driver rig yield a provisional Stick ID for the controls. The source flag does not distinguish every manual and non-paddle automatic. This inference remains a gate. |
| Steering | Preserve the source angle limits, rate and sensitivity envelope. The cohort uses 105 for both turning and countersteer in all 192 FH4 pairs. The compiler applies that shared rate policy to the observed FM4 road-car defaults and writes the same result to the stock spring upgrade. Other limits retain source values. |
| Suspension | Preserve original rest, travel, rate bounds, camber, toe, caster and suspension type. Resolve the suspension type by its native name. Project a single FM4 damping rate to equal low and high slopes. Use the observed 0.2 breakpoint for that linear representation. FH4 rate and clamp response require confirmation. |
| Torque and mass | Verify torque curve, estimated power from torque and RPM, positive mass, load distribution, final drive, differential split, clutch limits and references. Convert brake size from inches to millimeters. |

The field register in `native-catalogue-systems.json` covers gaps in driveline flex, progressive springs, bump stops, beam stiffness, brake rotor geometry and torque clamps, traction control, FFB, fuel consumption and aerodynamic offsets. Native stock frequency summaries in `evidence/physics-and-tire-cohort.json` serve as research evidence. Unresolved values keep the mandatory dynamics gate closed.

The v27 collision is a stock-form two-cloud envelope derived from the new car's exterior. Suspension and contact integration still require a road test.
