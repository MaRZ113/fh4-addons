# Phase 3 deployment and runtime gates

## Target and rollback

Use the designated offline test profile and the approved local copy. Start with 368 selected and allow the game to write its normal test save. Close FH4 before each switch. The checked installer snapshots the Phase 2 ZIP, manifest and SLT, plus a hash-verified backup of the designated test save on first deployment. Record candidate hashes with [PHASE3_REPORT.md](PHASE3_REPORT.md). Deploy a matching archive and manifest pair. Keep the original donor archive hash constant.

```powershell
pwsh -File tools/Set-Fh4IndependentAssets.ps1 -Mode restore
```

Restore switches the GameDB and manifest to the Phase 2 hashes, removes the verified generated alias ZIP, and moves a verified loose test tree into scratch. Profile files are handled through the game's normal write path or copied for diagnosis. Resolve save state separately from asset rollback.

## Ordered tests

1. Phase 2 baseline. Verify 368, 3477 and both upgrade groups from the established shared scene. Record screenshots and persisted IDs.
2. Run exact alias data and archive after stock vehicle load. A stall or Null Car ends that test at the loader gate. Compare the entrypoint-only and fixed-layout controls. Return to the Phase 2 baseline or one narrow follow-up variant between tests.
3. Confirm actual alias asset access, correct rendering, both vehicle identities, paint, stock and race parts, preview, free roam and save/restart. A passive file open trace plus the path rewrites can establish the resource source. Keep a trace to the exact archive/scene and stamp the tested hashes. A successful loose-file control would validate the scene path in a different input mode and leaves ZIP acceptance open.
4. Only after gate 3, import the FM4 front bumper in one stock group-34 member on the independent 3477. Keep control appearance and performance for 368. Check scale, camera distances, dimensions, orientation, seams, normals, UVs and lighting. Repeat with the unchanged alias as the control.
5. Approve a full Honda conversion after the previous geometry checkpoint works in FH4. See [CONVERSION_PIPELINE.md](CONVERSION_PIPELINE.md).

## Build in scratch

```powershell
$p = '.scratch/phase3/phase2-baseline/ACU_IntegraR_01.zip'
$m = '.scratch/phase1/zipmanifest.decoded.xml'
$d = '.scratch/phase1/gamedb.car3477.sqlite'
python tools/fh4_independent_assets.py $p $m $d .scratch/phase3/alias-repro
python tools/fh4_independent_assets.py $p $m $d .scratch/phase3/entrypoint-repro --entrypoint-only
python tools/fh4_independent_assets.py $p $m $d .scratch/phase3/layout-repro --preserve-layout
```

The fixed-layout builder uses 7-Zip for the two tight members, and Python's zlib for other deflate streams. Verify generated hashes if tool versions differ. The installer recognizes the Phase 3 hashes and expected directories `alias`, `entrypoint` and `fixed-layout`.

Encrypt `gamedb.alias.sqlite` as `gamedbRC.slt` using the backed up Phase 2 SLT as `--original`; encrypt `zipmanifest.plain.xml` as `zipmanifest.xml` using the backed up encrypted manifest as `--original`. Use the pinned offline FH4 CryptoTool from [POC.md](POC.md). Decrypt both results independently and require hash parity before deployment.
