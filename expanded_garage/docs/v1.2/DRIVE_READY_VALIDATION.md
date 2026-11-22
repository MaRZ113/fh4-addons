# Drive-ready validation

| State | Meaning |
| --- | --- |
| `integration-preview` | The build can be inspected offline. Any failed mandatory gate blocks install. |
| `drive-ready-candidate` | All mandatory validators have positive evidence. The owner can explicitly use the isolated local test path. |
| `runtime-accepted` | The owner recorded the trial outcomes, and the installer verified the restored preimages after rollback. |

Full rebuild validation compares the baseline row multiset, schema and FK violations, verifies generated IDs and references, checks CarScene and bone/Modelbin linkage, and checks localization, mount coverage, CRC/0x1123 and native shared-library hashes. It validates v27 shape framing, GR2 relocations and bind pose, physical tire curves and resources, stock tune integrity and source-owned wheel frame consistency. `verify` also supports an archive and catalogue readback without the original install. A full replay takes the original SQLite, plaintext mount and materials library.

The 1.2.0a1 receipts pass archive, catalogue, tire-resource, English artwork, bucket and rest-rig structure checks. The driving gates still require GSF and clips, target-specific mechanical fields, runtime wheel composition, verified collision/contact behavior and resolution of drive-required materials. Lights, audio, damage and other functional work remain tracked separately.

Preflight checks the executable, staged payload hashes, matching protected preimages, native archives and shared material/tire hashes on the offline copy. It independently derives the blockers from the gate states. The preview's rejected install was tested on a scratch fixture with the exact executable and necessary preimages. The guard rejected the preview before any host mutation. The fixture was never launched.
