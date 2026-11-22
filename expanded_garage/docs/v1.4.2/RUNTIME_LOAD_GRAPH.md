# Observed runtime load sequence

1. The existing v1.4.1 installation loaded the Autoshow, separate Suzuki manufacturer, pricing and its 2D thumbnail. Both new thumbnail archives were already held by the game before selection.
2. In the same offline process the stock Acura RSX Type-S 422 and independent Honda 3477 opened the 3D vehicle viewer. Both could return to Autoshow. The six comparison archives cover road-hatch, RWD, AWD and the Phase 4 proxy. Only those two controls were selected in this run.
3. Selecting Suzuki changed `EG_SUZ_SX4_11.zip` from unopened to held at `08:16:48.888008Z`. An archive handle establishes file access, with ZIP member ordering unobserved. The game was on `EmptySceneWithSpinner`, track 555 UK. The repeat had player car 3091 ready; the owner dump had 3477 ready. The selected Suzuki never rendered in the 3D viewer.
4. MegaTexture received an invalid rectangle. Windows recorded event 130413 with access violation `0xC0000005` at `0x467FE60`; the process ended by `08:16:57.763526Z`. The owner event 130402 previously reported the same worker path at `0x46827E7`.

| Return site | Recovered action |
|---|---|
| `0x3325EBA` | Native thread trampoline returns from the indirect worker callback. |
| `0x467FDBA` | Worker wrapper wakes and calls `0x467CFB0`, which drains the source and texture request queues before budgeted processing. |
| `0x467D531` | Budgeted update submits the queued rectangle to `0x46823C0`. |
| `0x46824DE` | Region dispatch calls `0x4684550` to handle ring seams. |
| `0x4684608` | Seam processing recurses; its tail jump at `0x4684654` enters `0x4682650` to translate ring coordinates and look up the grid. |
| `0x468284F` | Repeat only. The grid path calls the source-name parser at `0x467FDE0`. |

The producer path to observe starts at `0x4685D76`, a call importing an update input through RDX to `0x4680BC0`. This path calculates ring origins at the conversions around `0x4680FE5` and `0x4680FFD`, followed by the `0x4681070` call and writes at `0x468124B/0x4681252`. These are measured code paths; the worker-origin relationship was derived from field accesses. No producer stack frame was captured at the time of either exception.

The order of actual Carbin, Modelbin, GR2/GSF, material and physics reads is unknown. The worker stack contains no proved connection to those individual vehicle resources. The owner's dump also contains a Suzuki audio path in memory outside the fault stack. Its request and failure status were not observed.
