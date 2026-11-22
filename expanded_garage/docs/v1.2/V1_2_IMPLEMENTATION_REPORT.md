# Expanded Garage 1.2.0a1

The common FM4 → Vehicle IR → FH4 backend produces a Suzuki integration preview and a fleet preview for FH4 Steam 1.478.564.0. Both have `installable: false`. The build verified native catalogue, archive and asset structure. First drive and save persistence remain open.

| Source | CarID | ZIP entries | Stock tire family | Catalogue bucket |
| --- | ---: | ---: | --- | --- |
| SUZ_SX4_11 | 12000 | 73 | Modern High Performance | Hot Hatch |
| HON_IntegraR_00 | 12002 | 81 | MaxPerformance | Retro Hot Hatch |
| RUF_CTR2_95 | 12001 | 58 | Modern Ultra High Performance | Retro Supercars |

`NativeTireCompiler` selects shared FH4 friction and model families and preserves original tire dimensions and width upgrades. `NativeDynamicsCompiler` verifies and projects the known stock mechanical fields and reports unresolved FH4-specific fields. `NativeRuntimeRigCompiler` writes a standalone 64-bit Granny skeleton from the same bone graph as the Modelbin and reports the missing animation graph. The Suzuki catalogue has 80 insertions. The fleet has 246. Baseline rows, schema and existing foreign-key results are preserved, including Honda 3477.

The handoff contains Python source, verifier modules and their digests, a Python wheel, research scripts and evidence, compile and wrapped-preview files, and the build registry. The preserved v1 and v1.1 handoffs and original Vehicle IRs stay in their existing locations. The package verifier needs Python 3.12 or later, NumPy and Pillow. The wheel contains `fh4_carbin`, `fh4_modelbin_geometry` and their helper modules. The isolated verification is recorded in `evidence/replay-and-preservation.json`.

| Topic | Document |
| --- | --- |
| Tire rules and dry/wet/snow assets | [Native tire mapping](NATIVE_TIRE_MAPPING.md) |
| Physical conversion and remaining target fields | [Dynamics conversion](DYNAMICS_CONVERSION.md) |
| Native rig and animation dependencies | [GR2 and GSF](GR2_GSF_RUNTIME_CONTRACT.md) |
| Local and chassis-space wheel coordinates | [Wheel transforms](WHEEL_TRANSFORM_CONTRACT.md) |
| Evidence and qualification gates | [Drive-ready validation](DRIVE_READY_VALIDATION.md) |
| Isolated Suzuki owner acceptance | [Suzuki first drive](SUZUKI_FIRST_DRIVE.md) |
| Honda and RUF coverage | [Fleet regression](HONDA_RUF_REGRESSION.md) |
| Rebuild, preflight and rollback | [Installation](INSTALL_AND_ROLLBACK.md) |
| Remaining engineering work | [Next steps](NEXT_STEPS.md) |
