# FH4 new car slot experiment

## Offline result

**SQLite and envelope verified. Owner observed Autoshow and free-roam loading.** The candidate adds the 2001 Acura Integra Type R as `Id=ModelId=3477` and preserves all 783 original car definitions, including donor `368`. It uses the donor's media name, thumbnail, localized labels, stock powertrain and donor packages. It has its own body and upgrade keys, transposed physics rows and a base-game release row. The candidate price is 26,000 CR, chosen to identify the 25,000 CR donor and the candidate separately in Autoshow. See [Phase 2](PHASE2_REPORT.md) for the visual defects and asset probes.

| Check | Observed result |
|---|---|
| Stock backup | Encrypted DB copied and SHA-256 compared to the game file and H4REM backup. |
| Candidate delta | 784 total `Data_Car` rows. 110 inserts across 34 tables, including the release mapping. Full-table multiset comparison found zero removed or updated rows and identical stored schema. |
| SQLite integrity | Both `integrity_check` results were `ok`. The exact 2,941 baseline FK violation tuples were preserved. Explicit checks resolved the cloned body, suspension, aero and shared lookup links and verified each cloned upgrade catalogue's stock choice. |
| Derived identities | Both IDs survive the shipped SQL drivability predicate. The media-name reverse query returns both IDs, which could affect native lookup. |
| Stock crypto control | Encrypting the exact stock plaintext using the shipped SLT as the format input reproduced the shipped SLT byte for byte. |
| Candidate crypto control | The FH4 CLI authenticated all blocks of the staged SLT. An independent decrypt invocation recovered byte-identical staged SQLite, and `sqlite3 -readonly` verified it again. |
| Runtime | Owner reported 3477 in Autoshow at 26,000 CR and drivable in free roam. Phase 2 confirmed separate persisted garage IDs and restored the two affected visual groups. Independent purchase receipts and different custom tunes remain open. |

The reports and scripts are the handoff. All generated SQLite files, encrypted SLTs, asset inventories and CLI logs are local under `.scratch/phase1`, which is excluded from version control. See [RESEARCH.md](RESEARCH.md) for the source identity and [DEPENDENCIES.md](DEPENDENCIES.md) for the row and asset evidence.

| Current local candidate | SHA-256 |
|---|---|
| `gamedb.car3477.sqlite` and `gamedb.car3477.readback.sqlite` | `3063ce9ba39daaebbfdf3bce395cc9a25bf937618c723e090bcd3861f9aa5916` |
| `gamedb.car3477.encrypted.slt` | `dc470e013aa8a67dd64f51e5f4813a95e1c7f80bf4a0f2a03c5d26b1545291a8` |

## Reproduce in scratch

Run from `C:\Forza_Research` with Python 3.11 or later, PowerShell and the pinned Windows CLI binary. Use fresh output names if repeating against existing files. The official V3.3.5 Windows executable has SHA-256 `e7de706754590c8e8666d3c0185b0ffa6b523d8a92d56f4938f6b9951d3581a0`. Place it under the indicated scratch directory after verifying its digest. The following PowerShell was structured for the supplied workspace path, which contains no spaces. [Pinned release](https://github.com/DVS-code/Forza-Crypto-Tool/releases/tag/V3.3.5)

```powershell
$ErrorActionPreference = 'Stop'
$root = (New-Item -ItemType Directory -Force '.scratch/reproduce').FullName
$crypto = '.scratch/phase1/ForzaCryptoTool.exe'
$digest = 'e7de706754590c8e8666d3c0185b0ffa6b523d8a92d56f4938f6b9951d3581a0'
if ((Get-FileHash -LiteralPath $crypto).Hash -ne $digest) { throw 'Crypto binary mismatch' }

$game = (Resolve-Path 'game/ForzaHorizon4').Path
$stock = "$root/stock.slt"
$original = "$game/media/stripped/gamedbRC.slt"
[IO.File]::Copy((Resolve-Path $original).Path, $stock, $false)
if ((Get-FileHash $stock).Hash -ne (Get-FileHash $original).Hash) { throw 'Backup mismatch' }

function Invoke-Fh4Crypto([string[]]$cli) {
    $process = Start-Process -FilePath $crypto -ArgumentList $cli -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Crypto CLI exit $($process.ExitCode)" }
}

Invoke-Fh4Crypto @('decrypt', $stock, '-o', "$root/stock.sqlite")
Invoke-Fh4Crypto @('decrypt', "$game/media/zipmanifest.xml", '-o', "$root/zipmanifest.xml")
python tools/fh4_gamedb.py inspect "$root/stock.sqlite" --id 368
python tools/fh4_gamedb.py inspect "$root/stock.sqlite" --id 3477
python tools/fh4_gamedb.py assets $game --id 3477 --manifest "$root/zipmanifest.xml"
python tools/fh4_gamedb.py stage "$root/stock.sqlite" "$root/car3477.sqlite"
if ($LASTEXITCODE -ne 0) { throw 'Stage failed' }

Invoke-Fh4Crypto @('encrypt', "$root/stock.sqlite", '--original', $stock, '-o', "$root/stock-roundtrip.slt")
if ((Get-FileHash $stock).Hash -ne (Get-FileHash "$root/stock-roundtrip.slt").Hash) {
    throw 'Stock round trip mismatch'
}
Invoke-Fh4Crypto @('encrypt', "$root/car3477.sqlite", '--original', $stock, '-o', "$root/car3477.slt")
Invoke-Fh4Crypto @('decrypt', "$root/car3477.slt", '-o', "$root/car3477-readback.sqlite")
if ((Get-FileHash "$root/car3477.sqlite").Hash -ne (Get-FileHash "$root/car3477-readback.sqlite").Hash) {
    throw 'Candidate round trip mismatch'
}
python tools/fh4_gamedb.py compare "$root/stock.sqlite" "$root/car3477-readback.sqlite"
```

The command block was rerun in a fresh scratch directory and reproduced both candidate hashes. The tested Python SQLite build was 3.53.4. Another SQLite version may lay out pages differently. Recheck logical row deltas and compare the readback against that run's own candidate.

## Local deployment

With owner approval, the candidate was installed at `game/ForzaHorizon4/media/stripped/gamedbRC.slt`. The game process was closed and the stock database was backed up before replacement. The deployed SHA-256 matches the candidate above, and the executable still matches the SHA-256 in [RESEARCH.md](RESEARCH.md). The rollback copy is `.scratch/phase1/gamedb.preinstall-3477.stock.slt`, verified against the stock SHA-256 in [RESEARCH.md](RESEARCH.md). This operation only deployed GameDB. The runtime and visual follow-up is tracked in [RUNTIME_TESTS.md](RUNTIME_TESTS.md).

## Runtime validation plan

1. **Qualify the environment.** Locate a licensed Steam install with app manifest 1293830, confirm the executable and stock GameDB hashes, and establish that the stock game boots offline to Autoshow. Use a disposable local profile with sufficient credits and Steam Cloud disabled for the test environment. Verify isolation and record the path of the actual launched process. Stop at this gate if offline launch or profile isolation fails. The supplied tree contains the necessary analysis files. An offline-launchable Steam install and isolated profile are the outstanding runtime prerequisites. [Steam edition save FAQ](https://support.forza.net/hc/en-us/articles/18522401574163-Steam-Save-FAQ-for-Forza-Horizon-4)
2. **Establish the baseline.** With the stock SLT, record the donor's Autoshow entry, price, thumbnail, purchase flow, garage loading and a short free-roam drive. Record baseline profile and file hashes. Keep a separate rollback copy of the stock SLT and an untouched copy of the disposable test profile.
3. **Change one test input.** After approval, install only `gamedb.car3477.encrypted.slt` as `media/stripped/gamedbRC.slt` in the verified test installation. Check the deployed path and digest. Boot offline with the stock executable and collect local file-open evidence for the candidate DB if necessary. This test probes whether the stock loader accepts a modified authenticated envelope.
4. **Test registration and ownership.** In Autoshow, record separate listings for 25,000 and 26,000 CR under equivalent conditions. Purchase both via the normal UI. Record the balances, two garage entries, and which definition each purchase resolves to. Apply a distinctive color or stock-compatible setup to one through gameplay and verify independence. Test exterior, cockpit, camera, audio, thumbnail and free-roam driving with both.
5. **Test persistence.** Exit cleanly and relaunch offline with the same candidate and disposable profile. Verify both entries and their independent state and drive both again. Require ID evidence of 368 and 3477 using local diagnostics or read-only inspection of a copy of the game's own test save. If the UI and local evidence identify only garage objects, mark the slot test inconclusive.
6. **Restore and classify.** Exit the game, restore the stock SLT in the test installation from its verified backup and verify the hash. Keep the candidate-generated profile isolated. Record the first failing checkpoint with logs, the deployed SHA-256, game build, and asset-path evidence. If the candidate reaches Autoshow and collapses to a single identity on selection, focus on the reverse media-name lookup. If it never becomes eligible, trace installation/release predicates and catalogue construction. If it loads and lacks Forzavista events, test a separate ordinal-mapping change only after isolating that failure.

On a separate later run, an encrypted price-only diagnostic in a scratch clone of the stock DB could distinguish edited-envelope acceptance from new-row registration failures. Its result would be a loader control. If evidence points to a maximum ordinal, qualify and test an unused ID within the observed stock range with the same dependency graph. A fallback involving a distinct media name would require evidence for how FH4 mounts the donor ZIP under a second name and resolves its internal paths. These are conditional experiments after the first observed runtime result.
