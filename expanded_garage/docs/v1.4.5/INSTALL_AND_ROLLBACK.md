# Suzuki Offline Trial v1.4.5

Use the isolated FH4 Steam 1.478.564.0 host and a backed-up offline profile. Close FH4 before changing or rolling back the installed files. The latest trial replaces the previous Suzuki package and requires the same original GameDB, manifest, localisation and shared resources. Roll back the previous trial before running preflight.

From the handoff root, set `$fh4` to the isolated host path and run the source package with Python 3.12 or later, NumPy and Pillow.

```powershell
$trial = '.\artifacts\suzuki-package'
python -m expanded_garage preflight --host $fh4 --package $trial
python -m expanded_garage install --host $fh4 --package $trial --offline-trial
```

Keep the printed transaction ID. The installed GameDB SHA-256 for this trial is `c563448444865db27ca7ec5293e7ac465d35b392bc631f87b2b0d53c7bc91506`. The Suzuki archive is identical to the prior independent scene. The corrected aerodynamics reside in the new GameDB.

1. Start FH4 offline. Select Suzuki SX4 Sportback in Autoshow.
2. Check that the 3D body and wheels appear. Rotate the camera, return to the catalogue and reopen Suzuki.
3. If the showroom passes, acquire Suzuki and load it onto a flat road. Check the throttle, steering and brakes.
4. Return to the garage and restart FH4 if checking persistence. Record the last completed step and any visible failure.
5. Close FH4 before rollback.

```powershell
python -m expanded_garage rollback --host $fh4 --transaction <id>
```

The installation and rollback rehearsal restores the archived preimage of each changed game file. If the test added the car to a scratch profile, preserve the trial save separately and use the matching pretrial profile with the restored game files.

The first offline trial saved the Suzuki garage entry before a simulation thread stalled. For that profile, continue from the garage after restarting the game. See [post-purchase stall](POST_PURCHASE_STALL.md) for the capture and follow-up procedure.

## Rebuild

The preserved v1 registry and three Vehicle IRs are the source inputs. Run the existing common recipe with the local, hash-pinned resources.

```powershell
$media = 'game/ForzaHorizon4/media'
$baseline = '.scratch/expanded-garage-v1.4.1/offline-file-fixture/media'
& .\expanded_garage\Build-IntegrationPreview.ps1 `
  -Output '.scratch/expanded-garage-v1.4.5/rebuild' `
  -V1 '.\inputs\v1' `
  -Fh4Database '.scratch/phase7_1/camera-owner-test/baseline.sqlite' `
  -Fh4Manifest '.scratch/expanded-garage-v1.4/owner-baseline/zipmanifest.plain.xml' `
  -Fh4Materials "$media/cars/_library/Materials.zip" `
  -Fh4Strings "$baseline/stripped/stringtables/EN.zip" `
  -OriginalDatabase "$baseline/stripped/gamedbRC.slt" `
  -OriginalManifest "$baseline/zipmanifest.xml" `
  -Crypto '.scratch/phase1/ForzaCryptoTool.exe'
```

The solo Suzuki package is the offline trial. The fleet build validates the compiler against Suzuki 12000, RUF 12001 and Honda 12002 together. Its receipt lists the remaining fleet deployment blockers. The previous Honda 3477 uses its independent archive.
