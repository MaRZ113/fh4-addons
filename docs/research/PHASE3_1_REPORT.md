# FH4 Phase 3.1 — Asset loading and shared scene

**Scope.** Local FH4 1.478.564.0 and the existing Phase 2 IDs 368 and 3477. Phase 3's standalone archive fallback occurred with Acura geometry. The FM4 source entered the workflow only in Phase 3.1.

## Findings

- The shared-scene candidate moves stock bumper ID 3477000 from the existing stock model's shared list to the existing race model's list in type 34. The donor's stock ID and both aftermarket selections keep their original associations. ZIP layout, GameDB, manifest and other members were verified offline. [Shared scene](SHARED_SCENE_TEST.md)
- The identity matrix stages five controls with common routed GameDB, manifest and directory coordinates. It splits filename, scene name, ordinal and path changes. Phase 3's entrypoint-only and full alias runs establish two failed runtime combinations. The intermediate combinations are offline fixtures pending individual runtime checks. [Identity matrix](ASSET_IDENTITY_MATRIX.md)
- Owner-exported Procmon captures cover `mount-only` and a routed startup with 3477 already selected. FH4 tries `mediapc`, then reads `media\zipmanifest.xml` and opens the alias ZIP through `media`. In the routed run it also requests the alias scene path and reads blocks covering several ancillary members. The loose scene lookup returns `PATH NOT FOUND`, as it does for the working donor. Alias Modelbin path probes are absent. Procmon therefore narrows the trace boundary to scene selection or acceptance and leaves the internal rejection unproven. [File I/O diagnostics](FILE_IO_DIAGNOSTICS.md)
- FM4 CarID 370 was extracted with carbin-garage from hash-verified local copies. All 35 car and 12 wheel members matched source CRC and size. The working glTF has 120 car meshes and a separate wheel export. An isolated LOD0 `bumperFa` OBJ retains UV0, normals, source axes, winding and material labels. It has not been compiled or loaded in FH4. [FM4 geometry](FM4_GEOMETRY_STATUS.md)

Keep generated assets, save backups, trace captures and tool executables in local scratch. The source-only handoff includes the scripts and evidence reports.
