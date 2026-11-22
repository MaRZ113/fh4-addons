# Phase 3.3 test results

Build 1.478.564.0. The designated offline test profile was copied before the first deployment and all 17 files passed SHA-256 comparison. The live save was used by the game and never rewritten by the tools. The installed ZIP, manifest and GameDB were checked on each deployment.

| Fixture | Evidence | Result |
|---|---|---|
| Phase 2 | Accepted Phase 2 and 3.2 runtime reference. Three asset hashes rechecked before Phase 3.3. | Baseline |
| `aero` | Screen captures show installed Stock on 3477 with painted shell, splitter and red hook, and a normal 368 Stock. Race previews for both are normal. 3477 also loaded into free roam. | Runtime passed |
| `repack` | Owner confirmed normal loading and unchanged visuals. The same Modelbin SHA-256 and CRC were verified after decompressing the changed stream. | Runtime passed |
| `append` | Owner confirmed normal loading, original 368 and the same result on 3477. The added member was unreferenced. | Runtime passed as a mount-only control |
| `bind` | 3477 crashed while loading. Windows Application Error 1000 at 09:55:46 UTC+10, exception `0xC0000005`, fault RVA `0x03A8AFC4`. FH4's own report identifies CarID 3477 and state `Loading`. | Runtime failed |
| `lookup` | Changing the path in the original shared-model record also crashed, without increasing the type-34 record count. Event 1000 at 09:59:06 UTC+10 has the same exception and RVA. | Runtime failed |
| `bind-fixed` | The diagnostic correction changes only the new entry's 4-byte `0x1123` value. The owner confirmed that 3477 loads and looks the same. | Runtime passed |
| `geometry-fixed` | 3477 Stock lacks the red hook and retains the shell and splitter. The Race preview restores the red hook. The same Stock geometry appears in free roam. The owner confirmed stable loading and normal appearance on 368 with this same fixture. | Runtime passed |

First-run local capture suffixes `ip9WQog22I` and `y8nU0WOgJo` show 3477 Stock and Race, `g4Z0uyTlLS` and `90xYTY3GSm` show 368 Stock and Race, and `NNrpe1bw3e` shows the clone in free roam. The handoff includes timestamp and digest receipts for observed captures and curated crash fields. The capture images, save files and raw crash reports remain in the local workspace.

`geometry-fixed` frame suffixes are `TOldmEQsNM` for installed 3477 Stock, `FOSbWkoMFB` for 3477 Race preview and `oq0KQBXlhr` for 3477 in free roam. These frames came from one observed launch. The normal 368 appearance in this fixture is owner-confirmed. A separate post-restart visual run was not recorded.

The uncorrected `geometry` was only built for byte-for-byte reproduction. The control fixtures `duplicate`, `lookup-fixed` and `append-fixed` can be generated locally. Their report labels describe offline validation unless the runtime table records a launch.

## Rollback

After FH4 exited, `-Mode restore` returned the installed ZIP and manifest to the original Phase 2 hashes. The encrypted GameDB also matches its Phase 2 SHA-256. See `.scratch/phase3_3/restore-hashes.json`. The validated candidate remains in the local fixture directory.
