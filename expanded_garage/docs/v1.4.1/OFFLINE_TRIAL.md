# Retry Suzuki selection

Use the captured Steam 1.478.564.0 baseline with Honda 3477 and a backed-up scratch save. Close FH4. Use the v1.4.1 CLI from this handoff. It accepts the audited wheel and catalogue contract. An earlier installed trial can be restored with its transaction ID and rollback.

```powershell
$package = '.\artifacts\suzuki-package'
python -m expanded_garage preflight --host $fh4 --package $package
python -m expanded_garage install --offline-trial --host $fh4 --package $package
```

Set `$fh4` to the isolated offline game directory. Keep the printed transaction ID. Start FH4 offline and navigate to Suzuki in Autoshow. Record whether the 2D icon appears and whether selecting the car opens a stable 3D preview. Check a stock car and Honda 3477 in the same session. If Suzuki loads, continue with the [first drive, save and restart checks](../v1.4/INSTALL_TRIAL_AND_ROLLBACK.md).

If a crash recurs, keep the Event 1000 fields and the PID-matched Windows minidump from that exact attempt. The previous reference point is `ForzaHorizon4.exe+0x095599AE`, an access violation at the wheel mass index read. A repeat at that address requires comparing the actual opened GameDB and both coefficient indices with the new receipt. A different address needs the new exception context. For request ordering in a repeat, start a local ProcMon capture immediately before selection and filter to the FH4 process and the two `RC12000` archives plus `EG_SUZ_SX4_11.zip`. Note whether the RC archives and the car archive are opened. Whole-file ZIP access can precede any particular member read.

Close FH4 and restore the deployment. Restore the scratch save from its backup when done.

```powershell
python -m expanded_garage rollback --host $fh4 --transaction <id>
```

Use `record-runtime` only after the full first-drive and restart scenario succeeds and rollback is verified. A successful preview alone qualifies selection. Build again from the preserved Vehicle IR and exact baseline using [the v1.4 build recipe](../v1.4/INSTALL_TRIAL_AND_ROLLBACK.md). Keep the v1.4.1 output directory separate from older handoffs.
