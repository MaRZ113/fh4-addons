# Suzuki first drive

Use a separate, offline FH4 Steam 1.478.564.0 copy and a scratch profile with a backup. Close FH4 before deployment. This package targets the captured owner baseline including the current Honda 3477 mount. Preflight checks the executable, database, manifest, English strings and shared stock assets. A baseline mismatch requires rebuilding from the matching local inputs.

From the extracted handoff folder, set `$fh4` to the isolated game directory. Use Python with NumPy and Pillow installed.

```powershell
$package = '.\artifacts\suzuki-package'
python -m expanded_garage preflight --host $fh4 --package $package
python -m expanded_garage install --offline-trial --host $fh4 --package $package
```

1. Expect `offline-trial-eligible` and an empty blocker list. Save the transaction ID printed by install.
2. Launch offline. Find **Suzuki SX4 Sportback** in Autoshow, acquire it and select it in the garage. Confirm the own body and four correctly placed wheels load without Null Car.
3. Enter Free Roam. Start on a flat road, steer both ways, brake to a stop and cross a small grade change. Record wheel placement, road contact and suspension response. Verify an original car and Honda 3477.
4. Return to the garage, exit and restart offline. Reload the acquired Suzuki and check it is still in the garage.
5. Close FH4. Run rollback and keep the journal. Restore the scratch profile from its pretrial backup.

```powershell
python -m expanded_garage rollback --host $fh4 --transaction <id>
```

For a failed attempt, capture the last passed step and the first visible symptom. Missing catalogue entry points to GameDB or presentation. Failure on selection points to initialization. Null Car or configuration with missing models points to mount or scene. Wheels through the road or immobility after load points to wheel positioning or physical initialization. A stationary steering wheel or gauge with otherwise working motion points to runtime binding. Retain the receipt, transaction ID and screenshot or error. These observations narrow the failing stage.

After a successful trial and verified rollback, record owner acceptance. Use the template in the handoff and enter the observed values for the exact installed candidate. The command requires every owner check and an intact rollback.

```powershell
python -m expanded_garage record-runtime --host $fh4 --transaction <id> --evidence .\owner.json
```

## Reproduce

`Build-IntegrationPreview.ps1` builds Suzuki, verifies the three-car fleet and wraps the Suzuki package. Use the preserved v1 registry and Vehicle IRs, the locally decrypted GameDB and manifest matching the intended host, and the host's protected counterparts. The existing [build recipe](../v1.2/INSTALL_AND_ROLLBACK.md) lists the inputs. Omit `-WrapFleet` for the first trial. See `artifacts/evidence/reproduction.json` for exact local input digests. The wheel bundles the scene and geometry verifier modules.
