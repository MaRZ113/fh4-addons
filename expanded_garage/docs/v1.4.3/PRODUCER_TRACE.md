# Producer trace

| Native evidence | Operation |
|---|---|
| `ForzaCar+0x7D10` and `+0x7EF8` | Pointers to the dynamics interface and the deferred presentation interface. The latter resolves to the focused `CarPresentation`. |
| `0x2706526`, `0x397D770`, `0x3940110`, `0x3A3A5B0` | Publisher, enqueue, callback and final copy for the velocity setter. Dynamics getter `0x389D640` reads complete `CCarDynamics+0x20`. |
| `CPresentationCamera+0x2F0` | Focus object. The live Suzuki focus identifies scene 12000 through `CarPresentation+0x6D8`. |
| `0x3962A2C`, `0x38FBAE0` | Obtain the presentation camera matrix from `CPresentationCamera+0x20`. |
| `0x3962AA8`, `0x3A18780` | Obtain the focused car matrix. This reads `CarPresentation+0xA0..0xDF`, optionally followed by a velocity-derived translation adjustment. |
| `0x3962C16..0x3962C2A` | Blend camera and focused car world position. The blend cannot make a NaN difference finite, including a zero blend weight. |
| `0x3962C75..0x3962C94` | Convert scaled X and Z to signed terrain-center integers and place them at the start of the input block. |
| `0x3962F44 → 0x4685D76 → 0x4680BC0` | `TrackPresentation11` passes the block to MegaTexture through R8, then RDX. The callee copies it to manager `+0x90..0xBF`. |
| `0x468101A → 0x4681070 → 0x468124B/0x4681252` | Align centers, compute ring origins and write their new values. |
| `0x4682650 → 0x46827E7` | Convert the wrapped rectangle to tile coordinates and index the 48×48 table. |

PID 26756 crashed at `0x46827F4` after the original grid lookup selected index `−769673`. The selected record pointer was `0xffff`. The fault ring address matches the live passive capture. The first ring origin changed from around `−706228` to `2147482624` after its input became `INT_MIN`.

PID 20028 provided the focused car matrix, velocity and matching dynamics object. It displayed a black 3D viewer and kept running. A later access violation during the return to the car catalogue was at `0x287A713`, a different instruction. No causal relationship to that later exception has been established.
