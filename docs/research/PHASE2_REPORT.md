# FH4 Phase 2 — Acura clone asset bindings

## Evidence and deployed change

- **Owner-observed Phase 1 runtime:** car 3477 appears in Autoshow at 26,000 CR and drives in free roam using the Acura physics. The owner reports missing front geometry and stock wing, and problems installing aftermarket bumpers and wings. A pre-patch screenshot shows the front geometry symptom.
- **Confirmed in local files:** the Carbin has distinct typed upgrade and shared model bindings. In type 34, the clone lacks both front bumper bindings and uses a different body parent. In type 9, it lacks the bindings for the stock and Level 3 race wing. Rear bumper, hood and skirts are fixed in this scene. See [CARBIN_ID_MAPPING.md](CARBIN_ID_MAPPING.md).
- **Archive controls:** the existing save stalled with the first front variant and with the relocated-directory control, whose unpacked Carbin matches stock. The same save loaded after restoring the stock ZIP and manifest, and again with the fixed-layout control. That control changes only the deflate stream. The loading problem follows the relocated ZIP / manifest pair in these tests. Its precise native constraint remains unknown.
- **Fixed-layout front test:** the owner confirmed that the save loaded and the front bumper appeared while the spoiler remained absent. Relative to the successful layout control, the content change was limited to type 34. This confirms the typed front binding diagnosis under the tested setup.
- **Wing follow-up:** the combined fixed-layout revision was installed after the front result. Relative to the working front fixture, the scene change is limited to type 9. The owner confirmed stock and race parts render on 3477 and donor 368 remains functional. The combined revision is the current installed patch. GameDB, the executable and the encrypted manifest retain their verified hashes.
- **Garage persistence:** the copied profile contains two distinct persisted `Career_Garage` records keyed to 368 and 3477. They have distinct color indices, livery references and equipped parts. The clone already records race wing `3477001` and has 37 rows in the part table linked to its own garage key. The two records store identical values in their 37 tune fields. Purchase receipts and an A/B run with deliberately different tunes are still open.
- **Offline verification:** the parser reached EOF in both outputs. The intended clone group resolves every graphics-required catalogue row. Donor bindings and other typed clone groups match the baseline. All 228 non-target ZIP members retain their local headers and compressed bytes. All 229 members pass CRC. The encrypted manifest round-trips to the matching plaintext. The stock ZIP and manifest were backed up and verified before installation.
- **Remaining scope:** shared media-name reverse lookup still yields two model IDs. The two tested groups work with scene ordinal 368. Other visual upgrades such as the race roll cage, a distinct MediaName and a second archive remain unqualified.

| Variant | ZIP SHA-256 | Encrypted manifest SHA-256 |
|---|---|---|
| Front-only | `de5755392b00d2c9d69e5351214829ab6c0f7c636009eeba4c2775bb96ae43a5` | `7af2c66de2ffeab28d4d327d1405760f2648e24fd9cf1cbecaf125e8b0b92389` |
| Wing-only | `da00c18a42e7099fd3d81a85a9f5bc5f82eae07cb5a550891a3b601b858b47a8` | `496a696d5c78f2032eb9fb795286bc0bbee94ee2221c183fd81285c191e74b77` |
| Mount control | `b723d72a5e297fae932dd8fd5d7694239f24ec8e1db8eb8b2ef14450fb97e42e` | `dbeeee9aad87e0963d694988c8a32a9aed88c3a24dfe7a58d247a6e4292bc430` |
| Front-only, fixed layout | `2facc951664c9309143b1074440e54545a98b5737ecdc0df13e02752575c7f35` | Stock |
| Wing-only, fixed layout | `04dfccff2bf0c75b2d2c2d7fbc0fd27397395eb3dd369ebf1406c5d87bc51205` | Stock |
| Mount control, fixed layout | `bbc0b7b8ba4c66572cf5ced2bb4aa09c80a1d804bc9551f80d4fae81d784b565` | Stock |
| Front and wing, fixed layout | `a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c` | Stock |

Only the target Carbin stream and required ZIP bookkeeping differ. The encrypted `zipmanifest.xml` still describes 229 entries for the Acura ZIP. Its only plaintext difference is the Acura directory start, from 19,599,118 to 19,599,115 for the front fixture, 19,599,107 for the wing fixture or 19,599,086 for the mount control. The unedited encrypted manifest is byte-identical after a crypto round trip. The matching ZIP and manifest must be deployed together.

The fixed-layout versions keep the original ZIP byte count, every local header offset, central-directory coordinates and the stock encrypted manifest. The fixed-layout control uses the stock scene and differs only inside its deflate stream. The fixed-layout front and wing probes also change the Carbin CRC and uncompressed size. They provide a second diagnostic if the relocated-directory control blocks loading.

## Tools and scope

[fh4_gamedb.py](tools/fh4_gamedb.py) accepts source CarID, new CarID, BaseCost and an output SQLite file. It checks direct IDs, content mapping and the derived ID block, checks named links and stock selections, and verifies exact baseline FK violation tuples and an insertion-only delta. With `--game`, it also scans local audio, thumbnails and expansion layouts for the destination. With `--game` or `--carbin`, it audits typed Carbin IDs separately and reports missing bindings for the candidate so a SQLite pass cannot be mistaken for an asset pass. Output is a new file in `.scratch`.

The planner is pinned to the reviewed stock DB and a base-game family of dependency layouts. The original 368 → 3477 command reproduces the prior plaintext SHA-256. Countach 316 → 3478 at a custom cost also generated 100 insertions and passed SQLite and link validation. The tool rejects occupied CarID 3471, occupied derived block 3472 and an unsupported Corrado 309 key layout. Asset and runtime validation for other donors remain separate.

## Separate archive and Phase 3

The current fix tests typed registration while reusing the mounted archive. If independent naming is needed, first use a copied Acura ZIP and one reversible test: register a unique archive path, verify the new directory start, size and count in the mount inventory, then route only 3477 to the alias. Audit archive-local paths, scene header, skeleton, model and AO references, livery and animation resources, and the outbound audio lookup before changing a second input. A distinct name in SQL alone cannot establish that FH4 mounts the archive or resolves the packaged paths. ForzaTech Studio can inspect and edit these fields, and its generic ordinal conversion touches multiple categories in one pass. [Carbin Editor documentation](https://github.com/D3FEKT/ForzaTechStudio/blob/Main/docs/carbin-editor.md)

If modified car archives cannot be loaded, a separate database-only probe can route the clone's stock body selection through donor `CarBodyID=368000`, while keeping its own car definition and body upgrade row. The executable contains a stock-part query template by `CarBodyID` at file offset `0x52e3ab8`. This proposal would share the complete body catalogue and needs an isolated save, since the existing clone's saved front part key is 3477000. Rear-wing choices are scoped by ordinal and need a separate investigation. Neither branch has been qualified in the game.

The next Phase 3 experiment can qualify a unique MediaName with a stock-geometry control. Then introduce imported geometry, LODs, materials and paint, locators, collisions, animation and audio in separate steps. Use assets from locally available source games and retain the donor as the control. Repeat the automotive asset and profile tests when the resource path changes. See [NEXT_STEPS.md](NEXT_STEPS.md).
