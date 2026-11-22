# Passive FH4 file-I/O diagnostics

## Observed mount-only capture

The owner-exported `tools/procmon/Logfile.CSV` has SHA-256 `cb58379cbc368765a7f1e9b735fb2ededdb7d072de70fa9edcc00627d5f6da83`. It contains one FH4 PID, 33268, with 209,817 process events between 01:00:58.6975208 and 01:02:43.2036281 local time. The staged configuration was Phase 2 GameDB (`dc470e01…`), Phase 3 relocated alias manifest (`685060bd…`), and the alias ZIP with 19,610,717 bytes (`b1638555…`). The ZIP size in the trace matches this exact fixture. CarID 3477 still routes to the donor in this configuration.

| Local time / CSV row | OS-level evidence |
|---|---|
| 01:01:04.8681419 / 9500 | `mediapc\zipmanifest.xml` → `PATH NOT FOUND`. |
| 01:01:04.8682614 / 9501 | `media\zipmanifest.xml` → successful open, followed by 1,353 successful reads. |
| 01:01:05.2611700 / 14579 | Early `mediapc\cars\mrz_integraj_00.zip` probe, before opening GameDB. The new name was reached during the manifest interval. |
| 01:01:05.5626296 / 16954 | `media\stripped\gamedbRC.slt` → successful open. |
| 01:01:14.0969855 / 118261 | `media\cars\ACU_IntegraR_01.zip` → successful open, followed by 75 successful reads. |
| 01:01:15.5250330 / 128373 | `media\cars\MRZ_IntegraJ_00.zip` → successful open after the `mediapc` probe failed. |
| 01:01:15.5268154 / 128376 | Alias read `[19582976, 19610717)` covers its central directory at 19586788 and EOCD. |
| 01:01:15.5274549 / 128378 | Alias read `[0, 565248)` follows the `mediapc` probe for `ManufacturerColors.bin`. It covers that member at `[408517, 408698)` and also covers the compressed Carbin at `[4303, 11803)`. The archive closed at 01:01:15.5293207. |

The game reached the alias manifest entry, opened the alias ZIP through `media` and read its directory and an initial block. The initial block is consistent with the color lookup and cannot prove scene parsing. The routed comparison follows.

## Routed alias capture with 3477 selected at startup

`tools/procmon/alias.CSV` SHA-256 `77d6caccaa57f62d71190c7af1f9ae0bf8f03af895923b4dea83e669177aed8a`. PID 27704 has 251,281 FH4 events from 01:09:02.8203927 to 01:11:14.0790778 local time. The active ZIP and manifest match the mount-only capture. The GameDB is the routed SLT, SHA-256 `d6d69fde7e08375ad1ee4e9881314f44e77770602f1d433220d02a2e66ea90f7`. The owner reports a startup stall with 3477 active in the save.

| Local time / CSV row | OS-level evidence |
|---|---|
| 01:09:09.0464583 / 3108 | Successful open of `media\zipmanifest.xml` following the failed `mediapc` probe. |
| 01:09:18.6430906 / 103135 | First successful open of `media\cars\MRZ_IntegraJ_00.zip`. It reads the same directory and initial block as mount-only. |
| 01:09:48.3916536 / 196189 | A second open and the same two read ranges. |
| 01:10:03.9377716 / 227260 | Third successful alias open. Reads the directory and later blocks covering `IKAnchorBones.xml`, `Locators.xml`, `physicsdefinition.bin`, `aovolumetexture.bin` and `Textures/AO/ground_AO.swatchbin`. |
| 01:10:03.9447448 / 227374 | Explicit lookup of `mediapc\cars\mrz_integraj_00\mrz_integraj_00.carbin` → `PATH NOT FOUND`. |

There are three successful alias opens and ten successful alias reads. The loose scene probe follows the earlier read that includes the compressed scene bytes. Procmon cannot determine whether the in-archive scene was parsed or accepted. The working donor also returns `PATH NOT FOUND` on its loose `mediapc` Carbin probes (15 in the mount-only log), so that result alone cannot explain the hang. No per-model alias `.modelbin` path requests appear in this capture. The observed break is between the alias scene request and the stage where the working donor produces model-path probes. This is a trace boundary, with no direct evidence of the native rejection reason.

## Routed alias with one loose Carbin in mediapc

The probe adds only `mediapc\cars\MRZ_IntegraJ_00\MRZ_IntegraJ_00.carbin`, 45,204 bytes, SHA-256 `02122240cf5cf5e86f2e94d19e6794330fafab4452e44d1c62da365d92309a90`. The file matches the member in the alias ZIP. The routed GameDB, ZIP and manifest retain their comparison hashes. This test differs from the earlier loose test under `media`.

`tools/procmon/alias-loose.CSV` SHA-256 `3c8cf3151e8d615cbc61269ba671ee3790dc964407f4bb7a08dea4b60a7314f5`. PID 29668 has 140,648 events at 01:19:02.0723820–01:20:46.6578799 local. At CSV rows 2787–2801, FH4 opens and enumerates `mediapc`, `mediapc\cars` and the new alias directory. The capture has no `CreateFile` or `ReadFile` for the loose Carbin and no path containing `.carbin` at all. `mediapc` activity across the capture changes substantially (81 path rows versus 44,340 in the preceding alias capture), so the missing loose-file probe is a visible change in host I/O. Its internal cause is unverified.

The alias ZIP has three successful opens and ten successful reads. Their ordered offset/length pairs are identical to `alias.CSV`. The last open is at 01:20:01.1256600 (CSV row 125119) and the last alias read is at 01:20:01.2547856 (row 125468). There are still no per-model alias `.modelbin` paths. This run did not demonstrate use of the supplied loose scene or advance the observed model-load boundary. Procmon cannot tell whether the scene inside the ZIP was parsed. The log does not establish the on-screen outcome.

`pwsh -File tools/Set-Fh4LooseCarbinProbe.ps1 -Mode restore` removes the verified loose file and the empty directories created for this probe. It leaves the other Phase 3 files at their current hashes.

## Earlier capture attempts

The local x64 Procmon executable is `tools/procmon/Procmon64.exe`. Its Authenticode verification returned Valid with Microsoft Corporation as signer. The first elevation attempt was cancelled by Windows. The owner later launched Procmon and exported the capture above. The prior WPR FileIO attempt returned access denied. [Microsoft Process Monitor](https://learn.microsoft.com/en-us/sysinternals/downloads/procmon)

Phase 3's raw PE literals include `game:\media\zipmanifest.xml`, `game:\mediapc\zipmanifest.xml`, `game:\media\cars\%s.zip` and `game:\media\cars\%s\%s.carbin`. Phase 3's runtime observations establish the old donor can load under the edited manifest and that the alias fell back or stalled. They do not establish which manifest or archive FH4 opened. See [ASSET_MOUNTING.md](ASSET_MOUNTING.md).

## Capture sequence

1. Close FH4. Start approved Procmon and enable File System activity. Add an Include filter for Process Name `ForzaHorizon4.exe`. Keep the capture before launch. Use separate logs and record the start time and selected in-game ID. If you narrow path filters, include both manifest paths, the Phase 2 Acura ZIP, the alias ZIP, its loose directory and the adjacent car directory. Use backing files under `.scratch/phase3_1/io`.
2. Start Phase 2 offline from the test profile. Choose 368 and 3477. Exit cleanly and save the filtered capture to PML and CSV as `phase2`.
3. Close FH4, deploy `mount-only` and repeat the same two selections. This Phase 3 installer mode keeps the Phase 2 GameDB and adds the alias ZIP and manifest entry. It tests whether adding an unselected archive affects ordinary loads. An untouched alias during this run can be normal lazy mounting.
4. Start a fresh capture before launching after the routed alias deployment. Trigger 368 and then 3477 via the same path as the baseline. Record garage fallback and Autoshow behavior separately. Revert to Phase 2 with the checked installer and verify the original hashes.
5. Inspect successful `CreateFile` and `ReadFile` separately from attempted opens and metadata queries. A positive alias ZIP read shows the OS opened that container. A Carbin member lookup within ZIP may be invisible to Procmon. A negative alias read is meaningful only with the full startup and trigger interval captured for the correct PID.

```powershell
python tools/fh4_procmon_report.py .scratch/phase3_1/io/phase2.csv .scratch/phase3_1/io/mount-only.csv .scratch/phase3_1/io/alias.csv
```

The report tool filters exact FH4 paths and operation results. Keep raw captures local, since discarded events can contain unrelated activity. No protected-code hooks or network logging are required for this plan.
