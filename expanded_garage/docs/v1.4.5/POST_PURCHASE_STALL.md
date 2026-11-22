# Suzuki post-purchase stall

The owner reached the showroom and acquired Suzuki 12000 with the v1.4.5 GameDB. The game remained on the Saving dialog. A verified copy of the offline profile passed SQLite integrity checks and contained a new Suzuki garage row. The game also wrote the base livery payload and header. These observations establish that the garage entry reached disk. The UI did not finish the transition to the next screen.

The captured game process had one continuously active simulation thread. Its stack led from `ForzaSimulation` through RVA `0x274D148` into `OceanWindDirectionManagerImplementation` at RVA `0x365A1F0`. The thread was stopped in the dump at `0x365A271`. The sampled input world vector was finite.

| Item | Observation |
| --- | --- |
| Current heading at manager +0x60 | 19.0° |
| Target heading loaded at 0x365A238 | `0x76E60C00` as binary32, or 2.3329524733950277e33° |
| XMM0 at the loop | 180.0 |
| XMM1 at the loop | 2.3329524733950277e33 |
| XMM4 at the loop | −360.0 |
| Computed float32 value after ADDSS | Same bits as the input, `0x76E60C00` |

The loop subtracts 360 degrees and repeats while the value exceeds 180 degrees. This subtraction cannot change the recorded binary32 value, so the thread cannot leave the loop with that state.

The wind manager has two ordered containers. The registry at +0x40 had ten reachable `oceanwinddirection_Box` entries with finite, ordinary headings. The container read through +0x50 reported eight entries. Both of its extremal links pointed back to its head sentinel, with the nil flag set. Its root still led to five reachable entries with ordinary headings. The code checked the stored count and followed the first link without checking the sentinel. It read four bytes at sentinel +0x40 as a float. Those bytes match the target heading in the thread context exactly.

The immediate stall is thus proven. The event or writer that left the wind container inconsistent is unconfirmed. All 387 recorded Suzuki samples had finite chassis and wheel state, with the eight aerodynamic additions at zero. The deployment manifest changes vehicle and presentation data only. There is no verified wind data correction to apply in the importer.

## Garage reload

After restarting, the owner selected the purchased Suzuki in My Cars. FH4 crashed at RVA `0x0467A51F` with `0xC0000005` (Windows Application record 130524). The game crash report identifies CarID 12000 with car state Ready. The failing thread is a terrain streaming worker. Its direct caller passed a noncanonical value, `0xFF03000100C1D765`, as the tile pointer read at `0x04686AF9`. The loader faulted when it tried to read tile field +0xC4. The recovered return sites are `0x04686B5A → 0x0467D126 → 0x0467FDBA → 0x03325EBA`.

The calling frame contains rectangle `(524097, 523998, 524134, 524035)`. Dividing its lower bounds by 32 gives grid column 16378 and row 16374. For the verified 48 × 48 table layout with origins (−24, −24), the selected index is 803506 and capacity is 2304.

One repeat ran the existing passive observer against the camera, focus object and terrain producer. It produced the same exception address and rectangle (Windows Application record 130527).

| Publication | Camera world position | CarPresentation translation | Producer center |
| --- | --- | --- | --- |
| 643 | (1918.469, 143.632, −4787.314) | (−2757.633, 389.020, −4352.070) | (−703502, −1112271) |
| 644 | (NaN, NaN, NaN) | (−2757.633, 389.020, −4352.070) | (INT_MIN, INT_MIN) |
| 645 | (1599.711, −289.597, −4902.248) | (−2757.633, 389.020, −4352.070) | (−704880, −1112859) |

The focused Suzuki's basis and velocity remain finite across all three samples. At publication 644 the camera position and cached view direction are nonfinite. At 645 the camera is finite again, but the coarsest ring's origins are now (2146713088, 2146305024). The worker computes `(origin - 8192) >> 12` per axis. This gives exactly (524097, 523998), the lower bounds recorded in both crashes. The producer-to-consumer chain is now established for the garage failure. The writer that supplies the invalid camera matrix and its data inputs still need to be identified.

## Resume the offline trial

Retain both profile copies and the v1.4.5 deployment for the controlled comparison. Further camera diagnosis should capture the producer of `CPresentationCamera+0x20..0x5F` during the My Cars exchange. A repeat of the same terrain observation adds little. Check consumption of the five unset target-only `CameraOverrides` fields before choosing a compiler correction. Only road movement, steering, braking and a successful reload can establish the first-drive result.
