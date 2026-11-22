# Native FH4 tire mapping

`List_UpgradeTireCompound.TireCompoundID` resolves the physical FH4 compound. That row carries road, off-road and snow scales and references the load-dependent friction and affect curves. `TireModelName`, `WetTireModelName` and `SnowTireModelName` are separate scene-resource tokens. Several native records use the same token in all three fields. The physical conditions still differ by the compound and curves.

The named FM4 purpose maps to an observed FH4 asphalt family. The compiler checks road-car class and era, and requires a corroborating FH4 profile that fits both axles within 40 mm of width, 20 percentage points of aspect ratio and 4 inches of rim diameter. For supported unbranded model triplets, stock or upgrade use and distance to the actual sizes decide selection. The resolver follows the target friction-multicurve, friction-curve and affect-curve keys. The 192 paired cars expose multiple native specifications for each relevant FM4 stock compound.

| CarID | Size front and rear | Stock native ID | Dry / wet / snow |
| --- | --- | ---: | --- |
| 12000 | 205/50 R17 and 205/50 R17 | 6 | a / a / a |
| 12002 | 195/55 R15 and 195/55 R15 | 9 | WET_b / WET_b / WET_b |
| 12001 | 245/35 R19 and 285/30 R19 | 8 | WET_b / WET_b / WET_b |

The result preserves tread dimensions in `Data_Car` and the available front/rear width upgrades through the new body ID. Shared tire ZIPs resolve through the target plaintext manifest. The verifier checks the mounted directory range, CRC, 0x1123 offsets, Modelbin entry names and archive SHA-256. For profiles with a single left-side member, the receipt records the absence of a distinct right member. Native mounting behavior on both sides still needs the owner trial.

All 14 emitted stock and upgrade compound rows in the three controls resolve. For other cars with an unknown source family or a profile outside the covered range, the compiler records the source row and reason. An unsupported stock row blocks qualification. Dry/wet/snow road behavior, tire forces and dynamic visuals require the driving test.
