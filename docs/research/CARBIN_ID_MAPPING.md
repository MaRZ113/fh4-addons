# Acura Integra Type R asset bindings

Inputs were the stock `ACU_IntegraR_01.zip` and the Phase 1 GameDB with both 368 and 3477. REA identified the archive and retained its inventory as `ev_fed85fed39f5f7f6c0234a255c6929f3554f5c11cd985b91a587813d6a51c01b`. The included reader parsed all 44,988 bytes of the original scene (SHA-256 `5b50a7533f4354e249b1b54fbbb2725eef6eb69a8ce63c24f2f8479dc823f3f9`) using the Horizon v5 layout. It found six fixed groups, three upgrade groups and 97 render models, all model record version 16. [ForzaTech Studio Carbin Editor](https://github.com/D3FEKT/ForzaTechStudio/blob/Main/docs/carbin-editor.md), [reference parser](https://github.com/D3FEKT/ForzaTechStudio/blob/Main/ForzaTools.Bundles/CarbinParser.cs)

The little-endian scene ordinal at file offset `0x13` is 368 and the scene media name is `acu_integrar_01`. Both car definitions select that archive by media name. For version-3 upgrade groups the scene has typed upgrade records with stock flag, level, ID and parent key, followed by shared model records that explicitly enumerate upgrade IDs. IDs such as 368000 recur across part types. Always resolve them in their typed group.

| Type / DB catalogue | Stock donor / candidate | Aftermarket donor / candidate | Observed scene |
|---|---|---|---|
| 34 / `List_UpgradeCarBodyFrontBumper` | 368000 / 3477000 | Level 3: 368001 / 3477001 | Original ID fields at `0xa67e` and `0xa6ab`. Both parent fields are 368000. The clone catalogue uses parent 3477000. The shared lists bind `bumperf_a`, `bumperfsidemarkers_a` and `grille_a` to both donor IDs and `bumperf_race` to donor level 3. |
| 35 / `List_UpgradeCarBodyRearBumper` | 368000 / 3477000 | No aftermarket rows | Fixed group with `bumperr_a`, plate and plate light. |
| 36 / `List_UpgradeCarBodyHood` | 368000 / 3477000 | No aftermarket rows | Fixed group with `hood_a` and two hood liner models. |
| 37 / `List_UpgradeCarBodySideSkirt` | 368000 / 3477000 | No aftermarket rows | Fixed group with `skirtl_a`. |
| 9 / `List_UpgradeRearWing` | 368000 / 3477000 | Level 3: 368001 / 3477001. Level 4: 368002 / 3477002. | Original ID fields at `0xa2e4` and `0xa311`. Both parents are -1. `wing_a` binds to 368000 and `wing_race` to 368001. Level 4 has `RequiresGraphics=0` and lacks a dedicated scene record. |
| 41 / `List_UpgradeCarBodyChassisStiffness` | 368000 / 3477000, `RequiresGraphics=0` | Level 3 graphics: 368003 / 3477003 | Parent 368000. `rollcage_a` explicitly references the donor's level 3 ID. This is a separate future test. |

In the original scene the 97 AO maps have `PartId=-1` and `PartType=0xffffffff`. The seven droppable model IDs occupy a different namespace. The probes add a copy of each reviewed upgrade record in exactly one group and append clone IDs to its shared model lists. Each donor record and each original shared ID is preserved. Header ordinal, media name, paths, transform, draw groups, LOD flags, AO and drop fields are unaffected. Other groups are copied byte-for-byte as part of the same scene.

The SQL audit verifies type, ID, level, stock flag, applicable body parent and graphics path. Fixed stock groups are treated separately. The controlled game tests restored both affected groups with their typed entries and shared model references. The owner's control run could apply aftermarket parts while the meshes were still absent, and the copied save records a selected clone race wing. This identifies a visual selection problem for the tested upgrade paths.
