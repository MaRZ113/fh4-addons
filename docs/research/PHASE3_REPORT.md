# FH4 Expanded Garage — Phase 3

## Result

The Phase 2 build and the owner-designated test save were backed up and verified before deployment. The approved test target was the existing local FH4 copy. The donor archive and executable matched their accepted hashes throughout. The current investigation concerns the local 1.478.564.0 build and environment.

The staged candidate routes only CarID 3477 to `MRZ_IntegraJ_00` and supplies a separate archive carrying the Acura geometry. SQLite, authenticated framing, archive CRC, path references and manifest coordinates passed offline validation. **Gate A did not pass in the game.** The full relocated alias stalled when changing to 3477 after the save loaded on 368. The entrypoint-only alias returned Null Car in the Upgrade Shop. The fixed-layout full alias also returned Null Car. Adding the loose copy yielded Null Car and the owner reported indefinite loading when selecting 3477 in Autoshow. These tests establish the fallback and stall under their respective inputs. They leave the precise native rejection unresolved.

Restored Phase 2 and verified the encrypted SLT, manifest, donor ZIP and executable hashes. The alias ZIP is absent from the game tree, and the verified loose tree was moved back to scratch. The owner's screen capture of Null Car with the Integra catalogue label is local evidence, SHA-256 `becda64c8b7968df96dc5a96fbbb780c37fca9e8fc6aee294ac7770f118028c7`. It is excluded from the handoff.

The working Phase 2 files and 17 save files are under `.scratch/phase3/phase2-baseline`. The installer verifies that baseline for rollback. It never writes to the donor archive, FM4 tree, EXE or saves. The owner tested on the existing designated offline profile with a local backup. No Steam licence or cross-environment compatibility claim follows from this setup.

| Input or generated output | SHA-256 |
|---|---|
| FH4 executable | `b838a79a65c6d1e12f46deaba3584e4c88ebccdfdc4214b4535ece4ba3c0abbe` |
| Phase 2 Acura ZIP | `a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c` |
| Phase 2 encrypted GameDB | `dc470e013aa8a67dd64f51e5f4813a95e1c7f80bf4a0f2a03c5d26b1545291a8` |
| Phase 2 encrypted manifest | `eb086ab073b2549510d4b99796bb6b781c7cc23b4bb14fff416ede8fa70c2703` |
| Alias plaintext GameDB | `0890eb6aa972574859b60ee05922994befbad51e75d046f7e560deefb4eb9a0c` |
| Alias encrypted GameDB | `d6d69fde7e08375ad1ee4e9881314f44e77770602f1d433220d02a2e66ea90f7` |
| Full alias ZIP, relocated | `b1638555b113cb025fa950e44ea962244af7dacf28c794a9e95182560df65e44` |
| Full alias encrypted manifest, relocated | `685060bd016bcf7daf217ff6ad663d3984b58432071fd379cd42c5ca09a2e967` |
| Entrypoint-only ZIP | `5ff272bbd9b3f36c9b70cb6c5467d28a57cc0150a358e4c4b9ed5164ddb6be6e` |
| Full alias ZIP, fixed layout | `a22761af42114e6a5c7956755b92c614f179ae0fa155d8962292a81a5307a210` |
| Encrypted manifest shared by entrypoint-only and fixed-layout alias | `f18533e6dfa5c12f06645464310755777e1bd787ea6bc503b2821a4272c9322f` |

## FM4 source

Read-only inventory identifies the correct 2000 DC2 as FM4 ID 370, `HON_IntegraR_00`. The 2002 DC5 is ID 369. Car and wheel archives and the source database are present. FM4 geometry import and the FH4 Modelbin experiment are gated on successful independent mounting. [FM4 inventory and extraction procedure](FM4_EXTRACTION.md), [conversion pipeline](CONVERSION_PIPELINE.md)

The handoff contains only code and reports. Generated game assets, encrypted and decrypted databases, saves, backups and third-party tool binaries stay local in `.scratch`.
