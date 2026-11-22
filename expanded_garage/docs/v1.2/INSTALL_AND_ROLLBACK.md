# Build, preflight and rollback

The handoff includes a Python wheel and editable source. Use the included `expanded-garage.cmd` or `python -m expanded_garage` from the extracted source root. The wheel installs the `expanded-garage` entry point and the verification modules. NumPy and Pillow must be available in the Python environment.

Use `Build-IntegrationPreview.ps1` for the preserved control IRs and ID ledger. Keep the original v1 location, or point `-V1` at a directory with `registry.sqlite` and `control-cars/<MediaName>/ir`. The script copies the registry and verifies both outputs. Add `-WrapFleet` to stage both protected bundle variants.

```powershell
.\expanded_garage\Build-IntegrationPreview.ps1 -Output C:/Garage/v1.2-rebuild `
  -Fh4Database <decrypted-phase4-sqlite> -Fh4Manifest <decrypted-baseline-xml> `
  -Fh4Materials <stock-Materials.zip> -Fh4Strings <stock-EN.zip> `
  -OriginalDatabase <protected-gamedbRC.slt> -OriginalManifest <protected-zipmanifest.xml> `
  -Crypto <local-ForzaCryptoTool.exe> -V1 <preserved-v1-root> -WrapFleet
```

Use `import --source fm4 --car SUZ_SX4_11 --target fh4` with `--fm4-root`, the target paths and a new output directory to rebuild from local FM4. Supply `--source-tools` for the local XDS checkout if it is outside the default tools directory. A raw RUF July DLC import uses `--dlc` and the local STFS extractor specified by `--stfs-tool`. The preserved IRs can be built directly with `compile` and `compile-fleet`.

For the tested Phase 4 baseline, the protected DB SHA-256 is `d6d69fde7e08375ad1ee4e9881314f44e77770602f1d433220d02a2e66ea90f7` and the protected manifest SHA-256 is `f18533e6dfa5c12f06645464310755777e1bd787ea6bc503b2821a4272c9322f`. Use matching local shared assets and the pinned envelope tool. The prepared files are verified by decrypting the new wrappers and comparing against the compilation.

```powershell
expanded-garage preflight --host <isolated-offline-fh4> --package <staged-package>
expanded-garage install --offline-test --host <isolated-offline-fh4> --package <drive-ready-package>
expanded-garage rollback --host <isolated-offline-fh4> --transaction <id>
expanded-garage record-runtime --host <isolated-offline-fh4> --transaction <id> --evidence <owner.json>
```

The deployed catalogue and manifest are whole-file transactions. Use a fleet transaction for several imports. Rollback verifies all current paths and backups before restoring preimages. It stops on conflicting modifications. Installer journals under `.expanded-garage` track recovery and acceptance. Keep game saves separate from the file transaction. Use a scratch profile and restore its pre-trial copy after rollback if it had acquired an imported car.

Owner evidence uses `{"observer":"owner","cars":{"12000":{...}}}`. Each tested car supplies the six boolean observations in `ACCEPTANCE_CHECKS` in `fh4_installer.py`. The acceptance command verifies the observations and restored files and ties the record to the exact validation SHA.
