# Regression results

| Car | CarID | ZIP members | Current evidence |
|---|---:|---:|---|
| Suzuki SX4 Sportback | 12000 | 83 | Common backend rebuilt. ZIP, mounted thumbnail, catalogue and model-path audits passed. Native 3D selection failed again. |
| RUF CTR2 | 12001 | 64 | Common backend rebuilt. ZIP, insert-only catalogue and model-path audits passed. Runtime untested. |
| Honda Integra Type-R | 12002 | 90 | Common backend rebuilt. ZIP, insert-only catalogue and model-path audits passed. Runtime untested. |
| Phase 4 Honda | 3477 | 234 | Existing archive hash verified. It opened the 3D viewer in the ordinary offline run. |

All generated vehicles used `Build-IntegrationPreview.ps1` and the preserved v1 IRs. The rebuild and regular verify steps ran for solo Suzuki and the fleet. The staged solo metadata still carries the v1.4.1 static classification. Runtime evidence supersedes that classification for future trial planning. Stage outputs were compared with v1.4.1 by content hash; no new installable artifact is enclosed. The full test run and input/output hashes are in the handoff evidence.

The installed package was checked against its eight journal digests, and the protected Honda, native Acura and executable were checked separately. No build or rollback operation was applied to the live offline game directory in this investigation. The existing v1.4.1 fixture rollback receipt is included as prior evidence.

Use the local v1 IRs and the original wrapped files from a separate baseline directory. In the investigation the original wrapped inputs came from the rolled-back v1.4.1 fixture. The compilation recipe and expected checksums are below. The recipe is for reproduction on disk; it performs no installation.

```powershell
$hostMedia = 'game/ForzaHorizon4/media'
$baseline = '.scratch/expanded-garage-v1.4.1/offline-file-fixture/media'
& .\expanded_garage\Build-IntegrationPreview.ps1 `
  -Output '.scratch/expanded-garage-v1.4.2/reproduction' `
  -Fh4Database '.scratch/phase7_1/camera-owner-test/baseline.sqlite' `
  -Fh4Manifest '.scratch/expanded-garage-v1.4/owner-baseline/zipmanifest.plain.xml' `
  -Fh4Materials "$hostMedia/cars/_library/Materials.zip" `
  -Fh4Strings "$baseline/stripped/stringtables/EN.zip" `
  -OriginalDatabase "$baseline/stripped/gamedbRC.slt" `
  -OriginalManifest "$baseline/zipmanifest.xml" `
  -Crypto '.scratch/phase1/ForzaCryptoTool.exe'
```

Compare the resulting hashes with `evidence/reproducibility.json`. The prior v1.4.1 staged archives reproduce byte for byte.
