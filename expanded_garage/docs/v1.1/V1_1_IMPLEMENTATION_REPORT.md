# Expanded Garage v1.1 integration preview

Package version `1.1.0a1`. Target FH4 Steam 1.478.564.0.

The common compiler now emits an insert-only SQLite catalogue, an independent CarScene with native upgrade memberships, source-owned locator records and a stock-form v27 collision shell. It also generates English strings, manufacturer swatches, thumbnails, and a staged package that round-trips the native GameDB and manifest envelopes. The installer and rollback were exercised with temporary files.

The primary Suzuki preview, a single catalogue and mount for all three controls, and the wrapped files are under the output of `Build-IntegrationPreview.ps1`. The build starts from the retained v1 Vehicle IRs and a copy of the ID ledger. Phase 4's accepted GameDB and manifest preimages retain Honda 3477.

| Gate | Status |
| --- | --- |
| Structural catalogue and scene integration | Passed on Suzuki, Honda and RUF |
| Shared database and manifest | Passed insert-only and mount checks |
| Owner or runtime acceptance | Pending a candidate with the required native systems |
| First drive and persistence | Unverified |
| Functional completeness | Incomplete |

The current preview carries `installable: false`. The concrete first-drive gaps are in [vehicle systems](VEHICLE_SYSTEMS_STATUS.md). Runtime results for Phase 4 describe Honda 3477 only.
