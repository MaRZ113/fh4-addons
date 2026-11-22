# FH4 Expanded Garage — Phase 3.3

Steam build 1.478.564.0. This phase tests an additional Modelbin in the working shared Acura archive. CarIDs 368 and 3477 keep their Phase 2 GameDB entries and MediaName. The original stock and Race assets are byte-identical to Phase 2.

**Runtime proof achieved.** Under `geometry-fixed`, the installed Stock option on 3477 retained the shell and splitter but lost the red tow hook. Its Race preview still had the hook. The clone loaded into free roam with the altered geometry and the owner confirmed normal appearance on 368. The Phase 2 ZIP, manifest and GameDB hashes were verified after rollback. This result qualifies an independently bound payload inside the shared ZIP. Separate archive mounting and the FM4 geometry conversion remain open.

## Results

- The additive type-34 control passed in the game. Stock on 3477 shows the painted shell and Forza Aero together. Stock on 368 stays original and both Race previews display correctly.
- Re-encoding the Race Modelbin in place and appending an unreferenced duplicate both passed runtime controls. These runs establish that the original Modelbin still loads and that the expanded ZIP and its matching manifest mount.
- When the scene referenced the new filename, the first `bind` fixture and the path-only `lookup` control both crashed during 3477 loading at `0x03A8AFC4`. `bind-fixed` passed after correcting the stale `0x1123` payload offset. The working and failing `bind` files differ by exactly four bytes with the manifest, Carbin and model data held constant.
- The altered Modelbin differs in five indexed tow-hook draw ranges. The Carbin, all original Modelbins and the matching manifest are byte-identical between `bind-fixed` and `geometry-fixed`. This isolates the visible result to the clone-only payload. See [TEST_RESULTS.md](TEST_RESULTS.md).
- The existing FM4 Honda DC2 extract was measured against the FH4 Acura shell. The comparison covers position and basis, topology, normals, UVs, materials and LODs. No game assets were produced from the Honda geometry here. See [MODELBIN_LOADING.md](MODELBIN_LOADING.md).

## Reproduction and rollback

Run from the project root with Python and the local, hash-verified Phase 2 backups and crypto tool. The full command builds the historical controls and the corrected fixtures in scratch. The installer verifies the selected ZIP and matching encrypted manifest before using them, and the first installation verifies a backup of the designated offline save.

```powershell
$phase2 = '.scratch/phase3/phase2-baseline'
py tools/fh4_modelbin_probe.py "$phase2/ACU_IntegraR_01.zip" .scratch/phase3_3/fixtures `
  --crypto .scratch/phase1/ForzaCryptoTool.exe --manifest "$phase2/zipmanifest.xml"
pwsh -File tools/Set-Fh4ModelbinProbe.ps1 -Mode aero -SaveDirectory '<offline test profile>'
# Close FH4 before every switch.
# Use repack, append-fixed, bind-fixed, then geometry-fixed for a clean replay.
pwsh -File tools/Set-Fh4ModelbinProbe.ps1 -Mode restore
```

The handoff contains the scripts, a per-variant patch recipe and SHA-256 receipts, measurement data and redacted crash evidence. Regenerate installable fixtures from the pinned local inputs. Proprietary archives, models, manifests, saves, screenshots, executables and external repositories are excluded. The GameDB and manifest are needed in their Phase 2 form for installation and rollback. The measurement tool additionally accepts the local FM4 glTF and its sibling buffer.

The reviewed builder ran on Python 3.15.0 with zlib-ng 1.3.1. Different compressors may produce different byte streams or fail an exact-size fit. Build receipts give the outputs from the tested environment.

## Evidence index

- [Modelbin experiment and extraction comparison](MODELBIN_LOADING.md)
- [Runtime results and rollback](TEST_RESULTS.md)
- [Next experiments](NEXT_STEPS.md)
- Fixture SHA-256 and recipe records under `.scratch/phase3_3`.
- REA baseline inventory `ev_57543810985a26d011cf4182ed49ac774a43acf5bbe47dbfbd0aa0b2a60952c3`. The initial 230-entry geometry ZIP inventory `ev_86c629c09a8c0bd04329003f0eac7262195ed955b6e1a174b5e1ac873c4f7fab` proves standard archive structure and CRC coverage. The project validator covers the proprietary extra field separately.
