# FH4 Steam GameDB research

**Build examined:** 1.478.564.0, x86-64. **Status:** a new car definition was staged and validated in SQLite and in the FH4 GameDB envelope. Game loading, purchase and save persistence remain untested. Research snapshot dated 2026-11-18.

## Local evidence

The supplied tree is `game/ForzaHorizon4`. The PE version resource and `steam_api64.dll` identify the Steam build family. Neither the supplied tree nor the Steam libraries registered on this host had an `appmanifest_1293830.acf`. Ownership, depot provenance and offline launchability of this particular tree remain unverified. Microsoft Store/UWP builds are outside the findings.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `ForzaHorizon4.exe` | 159,507,456 | `b838a79a65c6d1e12f46deaba3584e4c88ebccdfdc4214b4535ece4ba3c0abbe` |
| `media/stripped/gamedbRC.slt` | 21,105,204 | `014a46b4849c0df42bec33a0ef6a490cad0fb9c7eaf02b7220157ca0ad177f7c` |
| Decrypted stock GameDB | 21,094,400 | `5e4a76877b94e66342e7e24cef5a211f0d4a4725fc9fd3be656ead6a8ebc3c1d` |
| H4REM 1.1.0 plaintext GameDB | 21,094,400 | `aae629f3819af1d6174cfa903e874968bd2abc56a78aa1af4168720054f200cf` |

The stock SLT matches the packaged H4REM backup byte for byte. `media/DB/gamedb_temp.slt` is a zero-byte file in the supplied tree. The main SLT decrypts to a `SQLite format 3` database with 262 tables, 492 indexes and 783 `Data_Car` rows. It uses a 36-byte TransformIT header and 161 blocks of `0x20000` payload bytes plus 16 MAC bytes per block. The decryptor reported all MACs valid. Re-encrypting the stock plaintext with the stock input as the original reproduced the entire encrypted file byte for byte. [Doliman100 container description](https://github.com/Doliman100/ForzaTech-crypto-tool#tfit-file-structure), [DVS release](https://github.com/DVS-code/Forza-Crypto-Tool/releases/tag/V3.3.5)

REA independently identified the PE architecture and SHA-256 (`ev_cee7056858eb0a4d7bd96e7732af94723bfed8f3bbadb147298588f5d39fd461`) and inspected the stock SQLite table (`ev_952542a133640574cf2918094181854152d6f34b2c5739035a2ed4f23c25b6e7`). File offsets and SQL fragments in [DEPENDENCIES.md](DEPENDENCIES.md) were checked against the shipped PE bytes. They establish the presence of SQL text and resource paths. Active call paths, query order and runtime capacity remain unknown.

## Tools and applicability

| Source | Result for this Steam build |
|---|---|
| [Doliman100 ForzaTech crypto](https://github.com/Doliman100/ForzaTech-crypto-tool/tree/2aa621fb028837452171d76fbdb6b18e9d78706d) | Documents FH4 GameDB decryption and format. The supplied public source is missing the key and table directories noted by its author. The local build attempt stopped on missing includes. |
| [DVS ForzaCryptoTool V3.3.5](https://github.com/DVS-code/Forza-Crypto-Tool/releases/tag/V3.3.5) | Windows release binary matched the GitHub SHA-256. Its local FH4 CLI decrypted the stock GameDB, manifest and shipped profile schema and performed the stock and candidate GameDB round trips. The public repository exposes documentation and release binaries. Validation is against the pinned binary and actual outputs. |
| [Nenkai ForzaTools](https://github.com/Nenkai/ForzaTools), [DB Load Patcher 1.0.4](https://github.com/Nenkai/ForzaTools/releases/tag/fh4-db-1.0.4) | Nenkai documents loading plaintext `media/stripped/gamedbRC.slt` through this .NET 8 launcher. The supplied launcher matches the official release executable SHA-256 `62daaddd64f0f970284346e2f5a6ef6675d34c2fb0fe4fd52c25bf187f1b9431`. Its source edits a GameDB-encryption check in memory after unpacking. Its built-in probe tests only the last two bytes of the seven-byte instruction. The on-disk target bytes at file offset `0x305c7de` differ from its expected unpacked instruction. The launcher was inspected without execution. The first proposed launch uses the encrypted candidate and stock executable. |
| [ForzaTech extraction tools](https://github.com/Doliman100/ForzaTech-extraction-tools) | Describes Carbin/Modelbin, localization and GameDB-assisted wheel alignment. Its documented FH4 environment is older Steam build 1.466.445.0. It provides asset inspection and conversion evidence. |
| [ForzaTech Studio](https://github.com/D3FEKT/ForzaTechStudio), [V0.9.2.2](https://github.com/D3FEKT/ForzaTechStudio/releases/tag/V0.9.2.2) | Asset editor for scene, geometry, texture, collision and supporting formats. Its SQLite asset-index service builds its own catalogue. The reviewed sources and model-swapping workflow address asset authoring and replacement. [Creator's model-swapping guide](https://www.forzamods.dev/make-a-mod/carbin-guides/model-swapping-with-forzatech-studio) |
| [addidotlol FH4 Car ID List](https://github.com/addidotlol/FH4-Car-ID-List) | The supplied CSV has 748 rows and 745 distinct IDs. The author marks it out of date. It is useful as an additional search input. |
| [gradbradice FH4 catalogue](https://github.com/gradbradice/forza-car-db/tree/ad1097ed37cf04a838f47252210530c8774d4f82/fh4) | All 783 IDs in `cars.json` match the stock GameDB ID set exactly. Useful for names and catalogue cross-checks. |
| [Tinase-nau FH4 car IDs](https://github.com/Tinase-nau/FH4-car-IDs) | All 885 IDs match the local `CarEventMapping.xml` ordinal set. The audio set has 134 IDs outside `Data_Car` and the DB has 32 IDs outside that audio set. Both sources matter for collision checks. |
| [H4REM 1.1.0](https://www.nexusmods.com/forzahorizon4/mods/37?tab=description) | The author reports edited GameDB loading on Steam 1.478.564.0 via Patcher 1.0.4. The supplied mod changes data in 55 tables, including 782 existing `Data_Car` rows, with identical stored schema and an identical set of 783 car IDs. Local comparison used complete row multisets. |

## Public implementation and limits

The open-source and public discussions reviewed through 2026-11-18 yielded no reproducible Steam FH4 demonstration covering a newly allocated car ID, simultaneous donor retention, normal purchase, and persistence after a restart. H4REM changes existing car definitions. AIO exposes existing traffic, story and rare cars, and Car Swapper substitutes existing IDs. An FH4 community discussion about adding models has no tested procedure. Recent same-game donor merge tooling lists FH2/FH5/FH6/FM8, and the separate FM4 Xbox 360 cloner targets a different title. The public search covers indexed GitHub, Nexus, Forza Mods documentation and relevant public discussions. [AIO feature list](https://github.com/candiath/AIO-forzaMods), [Car Swapper](https://github.com/visibou/fh4carswapper), [FH4 discussion](https://www.reddit.com/r/ForzaModding/comments/1qcnq42/addingimporting_car_models_from_other_instalments/), [Forza Mod Tool releases](https://github.com/HTTPJXRDN/Forza-Mod-Tool/releases), [FM4 editor](https://github.com/soulbrix/FM4CarDatabaseEditor)

- The observed stock `Data_Car.Id` range is 247–3471 and there are 783 distinct IDs. All stock rows satisfy `Id = ModelId` and all have distinct `MediaName`. SQLite accepts the extra ID and a shared `MediaName`. The shipped PE contains a reverse lookup by `MediaName` that returns two rows for the staged candidate. Application handling of the two results is an open question.
- Base and DLC content records, audio ordinals and derived part keys have different scopes. For example, the direct IDs 3472–3476 can be absent while their thousand-key ranges are occupied. ID 3477 clears the scoped local and catalogue scans documented in [DEPENDENCIES.md](DEPENDENCIES.md).
- The integer width, sentinels, upper ID bound, maximum car count, native registry allocation and resource path registration limits remain unknown. There is a stock SQLite constraint on identity and evidence of native eligibility functions. A successful SQLite insert or authenticated envelope cannot establish native table or save capacity.
