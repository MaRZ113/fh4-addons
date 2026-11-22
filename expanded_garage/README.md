# Expanded Garage

The FM4 adapter and FH4 compiler run through one command. The current version is recorded in `pyproject.toml`. The latest offline trial procedure is [v1.4.5 install and rollback](docs/v1.4.5/INSTALL_AND_ROLLBACK.md); the audit and the plan for the next release are in [v1.4.6/AUDIT_AND_PLAN.md](docs/v1.4.6/AUDIT_AND_PLAN.md). Earlier reports stay under `docs/<version>/`.

## Build a candidate

Use Python 3.12+ with NumPy, Pillow and the local `tools/driversf-forza-master` source checkout. Run from the workspace with `python -m expanded_garage` or `.\expanded-garage.cmd`. Installing the project also provides an `expanded-garage` executable. For fresh extraction outside the workspace, pass `--source-tools` with the checkout path. The [integration recipe](Build-IntegrationPreview.ps1) assembles Suzuki, the shared fleet and staged wrappers while working from a copy of the v1 ID ledger. Supply `--fh4-strings` for translated enum and bucket resolution and English localization.

```powershell
$fm4 = 'G:\!Games\Xenia_Canary\Manager\games_extracted\forza_motorsport_4'
$registry = '.scratch/expanded-garage-v1.3/registry.sqlite'
if (!(Test-Path -LiteralPath $registry)) {
    New-Item -ItemType Directory -Force (Split-Path $registry) | Out-Null
    Copy-Item -LiteralPath '.scratch/expanded-garage/registry.sqlite' -Destination $registry
}
$target = @(
    '--target', 'fh4',
    '--fh4-db', '.scratch/reproduce/stock.sqlite',
    '--fh4-manifest', '.scratch/reproduce/zipmanifest.xml',
    '--fh4-materials', 'game/ForzaHorizon4/media/cars/_library/Materials.zip',
    '--fh4-strings', 'game/ForzaHorizon4/media/stripped/stringtables/EN.zip',
    '--registry', $registry
)
python -m expanded_garage import --source fm4 --car SUZ_SX4_11 --fm4-root $fm4 @target --out .scratch/expanded-garage/suzuki-new
python -m expanded_garage verify .scratch/expanded-garage/suzuki-new/fh4
```

Use the same command for `HON_IntegraR_00`. For `RUF_CTR2_95`, add `--dlc .scratch/phase7_1/july` to mount the extracted July pack. A raw July STFS file works with `--dlc <file> --stfs-tool .scratch/phase7_1/extract-stfs/Extract-STFS.ps1`. Additional local packs can be passed in mount order with repeated `--dlc`. A direct DLC payload ZIP supplies its own members only. Use the complete extracted pack or STFS when it also supplies loose audio and separate strings.

To run all three with the same options, use [Build-ControlCars.ps1](Build-ControlCars.ps1). The script fails on an existing output. Use the same ID ledger for subsequent builds and backup copies of it alongside related saves. The default experimental range is 12000–19999. Allocation skips occupied car, model and derived body IDs in the supplied SQLite baseline. Retrying compilation keeps a reservation. First allocation order determines the IDs. Runtime save-facing compatibility of the range requires an integrated run.

## Outputs

| Location | Contents |
| --- | --- |
| `ir/vehicle.json`, `ir/blobs`, `ir/geometry` | Portable decoded geometry, local source records and source hashes. See [Vehicle IR v1](VEHICLE_IR.md) and the [schema](vehicle-ir-v1.schema.json). |
| `fh4/EG_*.zip`, `fh4/zipmanifest.xml` | Native render archive with central 0x1123 and a one-row mount addition staged against the plaintext baseline. |
| `fh4/build.json` | Identity, archive hash, exact library material dependencies, generated-rig and reverse-engineering limits. |
| `fh4/catalogue.json` | FH4 schema crosswalk, planned identity keys and manufacturer match. |
| `fh4/gamedb.sqlite`, `fh4/database-graph.json` | Staged SQLite and its insert-only proof, typed ownership and unresolved fields. |
| `fh4/media/`, `fh4/presentation.json`, `fh4/validation.json` | Source art and optional English archive, plus measured support and first-drive blockers. |
| `fh4/physics-input.json` | Stock mechanical measurements, original collision spheres and approximate geometry-derived convex envelope. |
| `fh4/native-catalogue-systems.json` | Tire mappings, bucket, stock dynamics, inference and unresolved target fields. |

`extract` produces only the IR. `compile --ir <directory>` accepts a saved v1 IR and the same target arguments. As an independent research command, `compare-physics --fm4-db <sqlite> --fh4-db <sqlite> --out <new.json>` computes held-out errors against unchanged source values for variables measured on common models. Fits are recorded for review.

## Installation and rollback

File deployment is gated on target GameDB insertion, the mandatory native asset graph and wrapped DB and manifest round-trips. The current previews carry `installable: false`. See [the preflight, rollback, and runtime acceptance commands](docs/v1.2/INSTALL_AND_ROLLBACK.md).

The deployment contract requires a closed, isolated host and one hash-pinned transaction containing the approved car ZIP, wrapped GameDB, wrapped ZIP manifest and additional owned resources. Archive the exact preimage and the offline test profile before deployment. On rollback restore the preimage of the GameDB, manifest, and any replaced shared entries, then remove only transaction-owned new files after checking their hashes. Preserve the identity ledger and restore a preinstallation profile where the vehicle was owned. The existing Phase 4 switcher demonstrates these operations for its own fixed fixtures. Its format and hashes are specific to those fixtures.

## Validation

```powershell
# from the repository root
$env:PYTHONPATH = "$PWD;$PWD	ools"
python -m unittest discover -t . -s expanded_garage -p 'test*.py' -v
```

The verifier replays the native archive offsets, parses the scene to EOF, verifies the identity and scene-local skeleton paths, and checks every generated mesh's local bone, layout and indexable vertex streams. It verifies the manifest row, including directory geometry. The build validates named material dependencies against the supplied common material library and records the library SHA-256.

The original v1 control recipe produced builds under `.scratch/expanded-garage/control-cars`. Each v1 package passed the archive and model checks. Recompiling them with the original source reproduced every file in their `fh4` directories byte-for-byte.

| Control | Test ledger ID | ZIP members | Source driver geometry | Manufacturer lookup |
| --- | ---: | ---: | --- | --- |
| `HON_IntegraR_00` | 12002 | 57 | Right | Honda 16 |
| `SUZ_SX4_11` | 12000 | 61 | Left | Suzuki requires registration |
| `RUF_CTR2_95` | 12001 | 53 | Left | RUF requires registration |

The RUF run used the original local July STFS and resolved the loose intake bank. All three source audio inventories resolved four FSB4 banks. The geometry and audio observations refer to decoded source evidence and static target verification.
