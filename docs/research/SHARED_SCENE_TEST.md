# Separate bumper geometry in the shared Acura scene

The checked Phase 2 `ACU_IntegraR_01.zip` has SHA-256 `a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c`. The probe ZIP is `bdb0327206640931eafe22d847f0dc5dc31a4b0f5a8f9193e2cfd83edbcaab2b` and its Carbin is `4fdaa67f3599355da4813c1a29863598e34306fed088f1b2ead6c6d758a67697`. REA archive inventory is `ev_2468a6c9318cca7dc0db4b75ed7ede0e3de4c23034e4840b2f54e1d08d51313d`.

| Type 34 model | Phase 2 membership | Probe membership |
|---|---|---|
| `bumperf_a.modelbin` | 368000, 368001, 3477000, 3477001 | 368000, 368001, 3477001 |
| `bumperf_race.modelbin` | 368001, 3477001 | 368001, 3477001, 3477000 |
| Grille and side markers | Existing bindings | Preserved |

The probe moves the same four-byte reference between two existing fields and adjusts both counts. The total Carbin size is unchanged. Every other ZIP member and all offsets are preserved via the Phase 2 exact-size DEFLATE writer. All 229 CRCs passed. The donor references and clone race selection are preserved. Existing `bumperf_race.modelbin` data is used byte for byte. There are no new ZIP members or GameDB edits.

In Phase 3.1, the probe was installed and Phase 2 was restored before the separate file-I/O baseline attempt. Phase 3.2 ran the gameplay test below.

```powershell
python tools/fh4_shared_scene_probe.py .scratch/phase3/phase2-baseline/ACU_IntegraR_01.zip .scratch/phase3_1/shared-geometry/ACU_IntegraR_01.zip
pwsh -File tools/Set-Fh4SharedScene.ps1 -Mode bumper
pwsh -File tools/Set-Fh4SharedScene.ps1 -Mode restore
```

Test both cars with the actual stock front option selected. Compare the same angles in garage and free roam. Confirm the Race choice still works for both. Exit, restart offline and repeat with the same probe ZIP.

## Phase 3.2 run

The previous alias and loose Carbin were removed. The Phase 2 reference was verified before switching to the existing shared-scene probe.

| Asset | Phase 2 reference SHA-256 | Installed for the test |
|---|---|---|
| GameDB | `dc470e013aa8a67dd64f51e5f4813a95e1c7f80bf4a0f2a03c5d26b1545291a8` | Same |
| ZIP manifest | `eb086ab073b2549510d4b99796bb6b781c7cc23b4bb14fff416ede8fa70c2703` | Same |
| Acura ZIP | `a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c` | `bdb0327206640931eafe22d847f0dc5dc31a4b0f5a8f9193e2cfd83edbcaab2b` |

The pre-test profile backup has 17 verified files. Its read-only garage inspection gave Race on both cars, 368001 and 3477001.

### First launch

The full-bumper success criterion failed. Both Stock selections render differently, and the clone loads into free roam, but Stock on 3477 shows the Forza Aero splitter and red tow hook with the painted bumper shell missing. The owner confirmed the 3477 Stock symptom.

| Car and selection | Observed | Local ShareX frame suffix |
|---|---|---|
| 368 Stock | Original shell in upgrade preview | `0q0Jrv7G8D` |
| 368 Race | Shell, splitter and tow hook in upgrade preview | `ux2ZqlO3N0` |
| 3477 Stock | Splitter and tow hook with the shell missing | `Ul3a1Tg0VY` |
| 3477 Race | Shell and Forza Aero together | `yRKckpxx6W` |
| 3477 Stock, free roam | The same missing shell outside the upgrade shop | `woq0hlKGvM` |

### After restart

The original test process was PID 34716 at 09:18:49, and the restarted process was PID 31024 at 09:23:23 on 2026-11-19 (UTC+10). The owner confirmed the defect persisted. ShareX frames show Stock marked **Installed** on both cars. Race was examined in preview on the second launch.

| Car | Installed Stock | Race preview |
|---|---|---|
| 368 | Original shell visible, `n7qutlHkXe` | Shell and Forza Aero, `D9kQj366OP` |
| 3477 | Splitter and tow hook, shell missing, `WQsFlKiyDO` | Shell and Forza Aero, `MV3lP2ezC6` |

A read-only inspection of the copied post-restart profile passed SQLite integrity checks. Garage IDs 116 and 627 retain their separate CarIDs and Stock bumper selections, 368000 and 3477000. The source frame timestamps and SHA-256 hashes are in the local `.scratch/phase3_2/screenshot-hashes.json`.

### Type 34 diagnosis

The probe retains the four upgrade records with their original parents and levels. The 3477000 record stays at Carbin offset `0xa73a` with parent 3477000, Stock and level 0. Only its two shared model memberships change. The baseline shell association at `0xa7a0` is removed from `bumperf_a.modelbin`, and the ID appears at `0xa9e2` in the probe's `bumperf_race.modelbin` list. Side-marker and grille bindings remain intact. Both ZIP members are byte-identical to Phase 2. REA inventory reference `ev_4abbdcacce37ddf8ab00e707ea9b08553616d7bd728633f209935e2e0cf3a35d` covers both members.

| Modelbin | SHA-256 | Bundle directory evidence |
|---|---|---|
| `bumperF_a.modelbin` | `65ea139c750f266a6bfbab943e119cff5d720465e735090b05996f434ae0dcd1` | 58 Mesh blobs and 9 materials, including `carpaint` and the Acura badge |
| `bumperF_race.modelbin` | `6cdd50c6c0e5bc773a51836e0a20ef75e38b5c7eb5ec8cdcc7da9878e5e9f62f` | 26 Mesh blobs and 3 materials, `aluminum_rgh`, `carbonfiber_001` and `towhook` |

The Phase 2 Race selection resolves **both** models. The probe resolved the race add-on for 3477000 and removed the model that supplies the shell. The matching symptoms in the upgrade shop and free roam show that type 34 can select a different set of existing visible parts per ID. An independently selectable complete shell remains untested. The narrow follow-up control would keep 3477000 on the stock shell and add it to the race add-on, while leaving the other three upgrade IDs untouched. The single-Modelbin replacement and FM4 tests remain pending.

### Rollback

After FH4 exited, `Set-Fh4SharedScene.ps1 -Mode restore` reinstated the Phase 2 ZIP. All three installed SHA-256 hashes match the Phase 2 values above. The local verification record is `.scratch/phase3_2/restore-hashes.json`. The test profile retains the Stock selections written during gameplay, and the pre-test backup remains available.
