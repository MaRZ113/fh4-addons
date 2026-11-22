# FH4 Phase 2 runtime tests

## Status

| Test | Evidence |
|---|---|
| 3477 listing at 26,000 CR and free-roam load | Owner-confirmed in Phase 1. |
| Missing front geometry, stock wing and aftermarket bumper/wing installation issue | Owner-confirmed report. Pre-patch screenshot shows front geometry absent. |
| Front-only probe | Owner reported save loading stalled. Same save loaded after recovery to stock ZIP and manifest, with the Phase 1 GameDB retained. |
| Mount control, relocated directory | The same save stalled in the owner test. The scene content is byte-identical to stock. |
| Mount control, stock layout | Owner confirmed the save loads. Existing front and wing rendering defects persisted. Aftermarket selection could be applied through the UI but still did not render. |
| Front-only, stock layout | Owner confirmed save loads and front bumper appears. The spoiler remained absent as expected. |
| Wing follow-up, stock layout | Owner confirmed both groups render, including stock and race parts on 3477. Donor 368 remains functional. |
| Separate persisted garage identities | Confirmed on a read-only save copy. |
| Visual and upgrade acceptance | Owner-confirmed for the two affected groups on 3477 and donor functionality. |
| Dual purchase receipts and purposely different tunes after restart | Open. The save establishes independent serialized garage identities, livery references and parts. |

### Save evidence

The game was closed for backup. Seventeen files were copied and verified by SHA-256. Two copied ProfileData files decrypted offline. One contains both IDs and the other contains neither. The relevant profile has an authenticated plaintext with a length-prefixed SQLite tail at 665,528 (867,328 bytes). SQLite `integrity_check` returned `ok`.

| Garage key | CarID | Front bumper | Rear wing | Factory color index | Purchased part rows |
|---:|---:|---:|---:|---:|---:|
| 116 | 368 | 368001 | 368000 | 0 | 37 |
| 627 | 3477 | 3477000 | 3477001 | 1 | 37 |

The profiles record distinct livery references. Neither car has a tune file and all 37 numeric tuning fields compare equal, so independence of deliberately different tunes remains untested. Existing saved rows establish serialized identities and equipment, while a before/after purchase run can verify acquisition paths. [Save inspection script](tools/fh4_save_inspect.py)

## Controlled checks

Record the active variant, exact hashes from [PHASE2_REPORT.md](PHASE2_REPORT.md), the car ID or baseline purchase price, and screenshots from the same camera and settings. Keep the test offline with a backed-up disposable save. Allow each variant its own baseline save copy when testing purchases. Close FH4 before changing the asset pair.

1. **Mount control first.** If the stock retry loads but the control stalls, treat the ZIP/mount path as the current blocker and restore stock. If the control loads, the group edit or its interaction with the active car state becomes a lead. A front-only follow-up can start with an unrelated active car on a disposable profile and separate active-car load from selection in garage.
2. **Front-only, after narrowing the loading failure.** On 3477 check stock front bumper, grille and side markers in garage and free roam. In the Upgrade Shop select the Level 3 front bumper. Record preview, purchase confirmation and debit separately from post-apply rendering. Return to stock and confirm the geometry restores. Repeat stock and Level 3 on 368. The wing is the negative control for this run.
3. **Wing follow-up.** The `bumper-and-wing-fixed-layout` fixture keeps the confirmed front bindings and adds type 9 only relative to the successful front fixture. Check stock wing, Level 3 preview, purchase, install and switch-back on both cars. Track Level 4 separately because its row has `RequiresGraphics=0`. The `rear-wing-fixed-layout` fixture is also available for an isolated wing-only comparison.
4. **Independence.** Buy the 25,000 CR donor and 26,000 CR clone separately. Record receipts and count two garage objects. Apply a different color and compatible tune or upgrade to each through the game UI. Switch between them twice in the garage and in free roam. Exit cleanly, restart offline, and verify both appearances and setups persist. If either intended car is already in the test profile, document its acquisition evidence before evaluating this criterion.
5. **Identity evidence if the UI is ambiguous.** Take a read-only copy of the disposable save after normal purchases and decrypt the copy locally. Run `python tools/fh4_save_inspect.py <decrypted-copy> --id 368 --id 3477`. Cross-check a copy from before the purchases. Restrict diagnostics to offline local file and UI evidence. If only two garage instance keys are available, keep the identity criterion open.
6. **Rollback.** `pwsh -File tools/Set-Fh4AssetProbe.ps1 -Probe stock` restores the verified stock Acura ZIP and manifest while keeping the Phase 1 GameDB. The local originals are in `.scratch/phase2/stock-assets`. If a test save persists part state across variants, restore its disposable baseline before the next comparison.

If boot or car loading breaks, check the deployed ZIP and manifest as a pair and run a mount-control fixture before drawing a selection conclusion. If the affected part still fails with the correct binding, examine ordinal gating, branch-specific model selection, and the difference between upgrade UI eligibility and rendered parts.

## Reproduce local assets

The distributed bundle contains scripts and reports. Use your local stock archive, decoded stock manifest and Phase 1 SQLite input. For the current workspace the stock archive is the deployment backup. The crypto steps use the verified Windows V3.3.5 binary as described in [POC.md](POC.md). Generated binaries stay under ignored `.scratch`.

```powershell
$a = '.scratch/phase2/stock-assets/ACU_IntegraR_01.zip'
$m = '.scratch/phase1/zipmanifest.decoded.xml'
$db = '.scratch/phase1/gamedb.car3477.sqlite'
python tools/fh4_carbin.py $a --db $db --id 368 --id 3477
python tools/fh4_asset_probe.py $a $m $db .scratch/phase2/front-repro --part front-bumper
python tools/fh4_asset_probe.py $a $m $db .scratch/phase2/wing-repro --part rear-wing
python tools/fh4_asset_probe.py $a $m $db .scratch/phase2/mount-repro --part mount-control
python tools/fh4_gamedb.py stage .scratch/phase1/gamedb.stock.sqlite .scratch/phase2/clone-repro.sqlite --donor-id 368 --new-id 3477 --price 26000 --game game/ForzaHorizon4 --carbin $a
```

For each generated directory encrypt `zipmanifest.plain.xml` with `ForzaCryptoTool.exe encrypt <plaintext> --original <stock-encrypted-manifest> -o <directory/zipmanifest.xml>`. Decrypt that output independently and compare it to the generated plaintext. Switch variants using the installer only after verifying the hashes and closing FH4. Its known probe inputs are `.scratch/phase2/front-bumper` and `.scratch/phase2/rear-wing`.

For any `*-fixed-layout` directory, run the same builder with `--preserve-layout` and copy the original encrypted manifest as `zipmanifest.xml`. The builder finds a 7,476-byte deflate stream and preserves the original directory start. For the combined patch use `--part bumper-and-wing --preserve-layout` and output to `.scratch/phase2/bumper-and-wing-fixed-layout`. Installation from the checked workspace uses `pwsh -File tools/Set-Fh4AssetProbe.ps1 -Probe bumper-and-wing-fixed-layout`.
