# End-to-end validation

Run `python -B -m unittest expanded_garage.test_pipeline expanded_garage.test_native_integration -v`. The fixture suite covers ZIP offsets, ID retention, versioned shape and string readback, dependency ordering, clean rollback and an injected failure after manifest publication.

The build verifies ZIP/CRC/0x1123, stock mount preservation, the one updated English row, native scene links, skeleton bounds and streams, upgrade keys and owner IDs, v27 point clouds, added English strings, and the hashes of source-derived UI assets. The database checker compares all baseline row multisets. Layout validity and functional evidence have separate statuses.

- `structurally_verified` records the offline checks performed.
- `unsupported` marks unimplemented behavior.
- `missing_dependency` marks an absent native resource or binding.
- Runtime acceptance requires observed game results and remains open for the integration preview.
