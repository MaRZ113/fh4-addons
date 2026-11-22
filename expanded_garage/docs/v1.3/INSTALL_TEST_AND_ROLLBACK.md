# Reproduce, preflight and test

Run `Build-IntegrationPreview.ps1` from the extracted source tree. The same command compiles the independent Suzuki and fleet. See the [complete command and required local inputs](../v1.2/INSTALL_AND_ROLLBACK.md). The v1 ID ledger reserves Suzuki 12000, RUF 12001 and Honda 12002. Use a copy of the original ledger with the preserved Vehicle IRs. The script stages the protected manifests and databases when supplied their matching originals. Use `-WrapFleet` to produce the fleet deployment preview as well.

```powershell
python -B -m unittest discover -s . -p 'test*.py' -v
expanded-garage verify <compiled-suzuki-or-fleet>
expanded-garage preflight --host <isolated-offline-FH4> --package <staged-package>
```

The real-asset tests use the retained Vehicle IR roots and the stock GameDB, manifest and library present at the standard workspace paths. The handoff includes a Python wheel with all verifier readers and shift profile data. Repeat the full verification with the matching baseline GameDB, plaintext manifest and material library. Evidence in the handoff shows verification through a wheel installed outside the source tree.

The v1.3 previews remain blocked for installation. When a future build passes drive readiness, use one coherent Suzuki trial.

1. Keep a closed Steam 1.478.564.0 offline copy and an independent saved profile backup. Record the preflight result.
2. Use `expanded-garage install --offline-test --host <offline-FH4> --package <qualified-package>` and retain the journal transaction ID.
3. Acquire the Suzuki, select it in the garage and enter Free Roam. Verify visible wheel placement and suspension travel. Test steering in both directions, throttle and braking on a road. Verify an original car and Honda 3477.
4. Return to the garage and restart offline. Confirm the Suzuki persists and loads. Close FH4 and run `expanded-garage rollback --host <offline-FH4> --transaction <id>`. Restore the isolated profile copy when appropriate.
5. After successful rollback and gameplay, use `record-runtime` with the owner evidence format in the [installer guide](../v1.2/INSTALL_AND_ROLLBACK.md). Keep the hash of the exact tested package.

Current transactions were exercised with isolated file fixtures. The actual preview was rejected before creating a deployment journal.
