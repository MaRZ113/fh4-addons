# Suzuki SX4 Sportback first-drive trial

`EG_SUZ_SX4_11`, CarID 12000. The delivered Suzuki archive and wrapped files are an integration preview. Run the read-only preflight to inspect the gates. Use the procedure below when a new build returns `drive-ready-candidate`.

1. Use a closed, isolated offline copy of the supported FH4 version. Copy the offline test profile before testing. Keep the installation and profile used for ordinary play outside this procedure.
2. Run preflight and install with `--offline-test` using [the installer commands](INSTALL_AND_ROLLBACK.md). Keep the returned transaction ID.
3. Start FH4 offline. Locate Suzuki in the catalogue, acquire the SX4 and confirm ownership in the garage. Switch to the SX4 and enter Free Roam. Check the visible car and all four wheels.
4. On a flat road, stop, start, turn both ways and brake. Check suspension motion over a gentle transition. Return to the garage, exit, restart offline and reload the same owned car. Check a stock car and the pre-existing Honda 3477.
5. Close FH4, roll back using the transaction ID, and verify the restored preimages. Restore the isolated pre-trial profile if necessary. Once all observations pass, record them with `record-runtime`.

Record the first failing step and the exact build receipt if any check fails. The current preview has no owner gameplay result.
