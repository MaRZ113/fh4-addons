# Rolls-Royce Wraith pilot for Forza Horizon 4

`candidate-06/files` contains eight files relative to the FH4 game root. The candidate uses CarID `2044` and media name `ROL_Wraith_14`. The build reads the installed FH3 and the current local FH4 snapshot. It writes only to the pilot folder. Neither game was started.

## Package contents

- The 246-member Wraith archive preserves the original Carbin, rigs, animations, paint resources, LODs, damage morphs, cockpit, wheels and brake assets. The build widens 1,704 Mesh 1.8 records and verifies that the rest of each draw record and all other bundle payloads remain byte-identical. The v27 collision uses the donor's original points.
- The database adds 126 records across 49 tables. All original rows in those tables were checked against the input database. The original eight-speed RWD powertrain, engine and tuning rows carry over. The FH3 AWD swap is omitted. FH4-specific fields use native reference rows recorded in `receipt.json`.
- The native manifest, English strings, thumbnails and manufacturer icons accompany the car. The database and manifest passed encrypted roundtrip checks. All 139 explicit serialized swatch paths scanned in the scene and models resolve in the archives.
- The driving audio mapping uses FH4's Bentley Continental GT W12 and the native Luxury Forzavista events. The original FH3 Wraith bank is available in `candidate-06/source-only` for audio work. Keep it outside the FH4 payload until event and bank compatibility have been checked.

## Applying later

Review `candidate-06/receipt.json` before applying. Its `preimage` values are the SHA-256 hashes expected for each replacement. A null value means the corresponding path is expected to be absent. The database, manifest, localisation and XML maps are full snapshots. If the target has changed, regenerate the candidate against that snapshot or merge the additions. The receipt records the payload SHA-256 hashes for verification. The `work` directory holds plaintext staging files for inspection.

Build a fresh candidate from the project root with PowerShell:

```powershell
python -B FH3_Wraith_Pilot/build_wraith.py FH3_Wraith_Pilot/candidate-next
```

The first in-game checks are catalog visibility, purchase, garage loading, cockpit rendering, rear-hinged door motion, paint and livery, wheel clearance, steering, braking, shifts, collision and save persistence. Skinning, ambient occlusion, driver rig, effects and sound behavior need runtime review. The package is an offline candidate.
