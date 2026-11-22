# Installation and rollback

`prepare-package` checks the exact wrapped source assets against their decrypted baseline hashes, wraps the generated GameDB and manifest, then decrypts both staged wrappers independently and compares the bytes. It records native paths and SHA-256 for both preimage and payload. Current Suzuki output is a structural preview with `installable: false` and is rejected by the deployment command.

For a candidate with a qualified validation receipt, use an explicitly selected closed offline copy. Back up the test profile, keep the registry, and use the installer as follows.

```powershell
python -m expanded_garage install --host C:/path/to/isolated/FH4 --package C:/path/to/qualified/package
python -m expanded_garage rollback --host C:/path/to/isolated/FH4 --transaction <transaction-id>
```

The installer validates the executable and all preimages, checks for competing mount and car paths, stores original hashes and file backups, and publishes the GameDB last. It can recover an interrupted transaction via its journal in `.expanded-garage`. A file with an unrecognized hash stops rollback. Package removal is transaction-scoped. For several cars, build one fleet transaction with a shared catalogue and manifest.

For the owner test, confirm Suzuki in Autoshow, inspect all four wheels, drive and brake in Free Roam, return to the garage, restart and load the same owned car. Check an original car and Honda 3477. Close the host before rollback. When testing a save that owned Suzuki, restore the isolated pre-test profile deliberately. The installer operates on vehicle files and leaves profile management to that independent test step.
