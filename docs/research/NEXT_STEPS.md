# Next steps for FH4 Expanded Garage

## First Honda part

1. Use the clone-only binding and ZIP format documented in [MODELBIN_LOADING.md](MODELBIN_LOADING.md) and the runtime controls in [TEST_RESULTS.md](TEST_RESULTS.md). For the first complete shell test, copy the Acura shell as a new member and bind only 3477000 to the copy. Confirm the identical shell before substituting geometry. Preserve the donor, both original Race selections, the Phase 2 asset hashes and the offline test profile backup.
2. Align the FM4 `bumperFa` LOD0 in FH4 car space. Reflect Z, preserve side labels and reverse winding once. Check the adjacent lamp, fender and hood coordinates before fitting. Begin with one independently identifiable surface and a known FH4 paint material. Rebuild tangents and required UV channels where source UVs cannot drive the FH4 material. Validate index bounds, per-LOD draw ranges, transforms and material paths.
3. Stage one independently bound FH4 Modelbin. If its payload exceeds the reserved compressed size, recalculate the affected local offsets, every central `0x1123` field and the manifest directory tuple together. Qualify an identical-payload layout control before deploying geometry. Check both cars in the garage and free roam before preparing any larger body import.

## Separate archive

The Phase 3 Null Car and stalled loading observations still apply to the historical aliases. The `0x1123` audit identifies a packaging defect in several of them. Alias cases with correct fields remain unresolved. A later alias test should validate every payload offset and track the archive's central-directory read, the matching Carbin path and selected Modelbin byte ranges before changing another identity field. This branch is independent of the shared-archive Honda part.
