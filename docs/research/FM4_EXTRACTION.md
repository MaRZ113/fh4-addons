# FM4 Honda Integra Type-R 2000 source

The locally extracted FM4 tree on G: was inspected read-only. The on-disc SQLite has 370 / 2000 / `HON_IntegraR_00`, make 16, stock wheel 115 and stock engine 124. It has separate rows 368 / 2001 / Acura and 369 / 2002 / `HON_IntegraR_02` (DC5). Source build identity includes `default.xex` SHA-256 `fea11e87ff8434e51ed8e683023ea17281dec02a5ed8fa226b1c73726cf15128`. Direct ISO extraction was unnecessary because the two matching archives are on disk.

| Source relative path | SHA-256 | Observed content |
|---|---|---|
| `Media/cars/HON_IntegraR_00.zip` | `e67d5676b7990a5db179b13b17b2bb0d643641822609af278115407e8d81c69e` | 35 members, 34 method 21. Base, LOD0 and cockpit Carbins; individual rotor and caliper Carbins; XDS materials, damage and lights textures; livery masks; `carattribs.xml`, `physics/maxdata.xml` and build metadata. |
| `Media/wheels/HON_IntegraR_00.zip` | `92c3c43145db7a88cc06c573cab8decad5e91517299dc98fa51fb1783ac0384c` | 12 members, 11 method 21. Wheel Carbin, wheel XDS textures and wheel/tire metadata. |
| `Media/db/gamedb.slt` | `8b31670cc770b349e8b3994c9f9fc346c52b444211d8925a00d985a5f0362942` | Plain SQLite. Source identity, stock parts, engine, wheel and physics catalogue. |

The ZIP inventory records declared sizes and CRCs. It leaves payload decode, named mesh groups, per-mesh LODs, material bindings, rig and driver placement unverified. The source tree contains B18C5-named FSB banks. Resolve stock engine 124 to actual runtime events before assigning any bank to the JDM DC2. Use the working Acura physics and audio for the geometry tests.

## Read-only inventory and planned extraction

```powershell
$fm4 = 'G:\!Games\Xenia_Canary\Manager\games_extracted\forza_motorsport_4'
python tools/fm4_integra_inventory.py $fm4
```

After the independent FH4 archive works, copy the two Honda ZIPs into `.scratch/phase3/fm4-input` and verify both digests. Keep the original names while separating car and wheel inputs by directory. Use the existing carbin-garage executable with the FM4 profile and explicit workspace output. Treat ZIP method 21 as LZX and verify decoded sizes and CRCs. The standard Python ZIP reader is used here for directory inventory only.

```powershell
tools/carbin-garage-exe/carbin-garage.exe import .scratch/phase3/fm4-input/cars/HON_IntegraR_00.zip --profile fm4 --out .scratch/phase3/fm4-working --slug hon_dc2_00
```

Audit the reported glTF, binary buffers and textures against the archived first-level and LOD0/cockpit entries. Inspect per-primitive source labels, material bindings, joint or locator metadata and source-to-glTF unit/axis transforms. Confirm which source provides the front bumper instead of assuming a filename. Inspect the wheel ZIP separately with a format-aware reader or the original official converter where its wheel import accepts this layout. Retain rig/animation observations and unresolved fields separately from mesh export.

carbin-garage documents FM4-to-glTF/PNG and ports to FH1. Forza Studio 4.0 documents direct FM4 resource viewing and export and is a useful independent cross-check for models, wheels and XDS. ForzaTech Studio is the target-side viewer and writer. Doliman100's modern extraction tools focus on later ForzaTech generations and supply research on target layouts and wheel geometry. [carbin-garage](https://github.com/ItsNotPaths/carbin-garage), [Forza Studio 4.0 announcement](https://codeescape.com/2012/05/forza-studio-4-0-forza-motorsport-resource-extraction-tool/), [ForzaTech Studio](https://github.com/D3FEKT/ForzaTechStudio), [extraction tools](https://github.com/Doliman100/ForzaTech-extraction-tools)
