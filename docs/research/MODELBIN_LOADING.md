# Modelbin loading in the existing Acura archive

## Controls and isolation

The test uses FH4 Steam 1.478.564.0 and the accepted Phase 2 shared `media/cars/ACU_IntegraR_01.zip`. Both GameDB rows keep the same MediaName, and the Carbin keeps ordinal 368. The asset selected by the stock front bumper of 3477 is the experiment. GameDB, the front bumper upgrade rows, the donor's stock shell and both cars' Race associations are unchanged.

| Step | Only change from the preceding fixture | Expected in the game |
|---|---|---|
| `aero` | Add 3477000 to the existing Race add-on list. Keep 3477000 in the shell list. | 3477 Stock displays shell, splitter and red tow hook. 368 Stock stays original. Both Race choices look normal. |
| `repack` | Re-encode the existing Race Modelbin in the same 97,228-byte DEFLATE span, with an identical decoded payload and CRC. | Same view on both cars. |
| `append` | Append an unreferenced, byte-identical copy as `scene/Exterior/BumperF/bumperF_3477.modelbin`. Update the existing Acura ZIP's registration tuple. | Both cars load and look the same. This qualifies the changed archive directory and manifest. |
| `bind` | Move 3477000 from the original Aero model's list into a new scene model instance pointing to the appended member. Retain the shell and the other Race IDs. | Same view, including the splitter and red tow hook in 3477 Stock. |
| `geometry` | Change only the appended Modelbin's index buffer. Make the tow-hook triangles degenerate in five draw ranges, including their reuse in the corresponding shadow draws. | Only 3477 Stock loses the red hook. Shell and splitter remain. 368 Race and 3477 Race still have their original red hook. |

The original stock shell, side markers and grille remain associated with 368000, 368001, 3477000 and 3477001 throughout. The original Race add-on always contains 368001 and 3477001, with 3477000 present only in `aero`, `repack` and `append`. The extra model instance in `bind` and `geometry` has one ID, 3477000, and reuses the original add-on's model metadata and transform.

## ZIP and manifest

`aero` and `repack` preserve every local-header offset and the Phase 2 directory tuple `(19599118, 23929, 229)`. `append` keeps all 229 original local records at the same offsets, writes the new local header at byte 19,599,118 and moves the directory to byte 19,696,420. Its size including EOCD is 24,027 bytes, with 230 entries. `bind` and `geometry` preserve this second layout. The appended compressed payload occupies `[19599192, 19696420)` in all three variants. This interval is available for a passive ReadFile trace if needed. A broad prefetch of the interval would provide file-I/O evidence with limited attribution to a particular model draw.

`zipmanifest.xml` lists the ZIP, with its central-directory position, length and entry count. The test changes just those three attributes on the existing Acura row and validates the encrypted manifest by decrypting it back. The ZIP entry is registered in the central directory and addressed by the Carbin's `game:\media\cars\acu_integrar_01\scene\exterior\bumperf\bumperf_3477.modelbin` path. It has no separate XML row. `append` can establish that the package and manifest are accepted. `bind` has identical model data, so the `geometry` A/B is what can prove that the payload at the new path reached the renderer.

All fixtures use ZIP32 and method 8. Each member passes CRC readback. The build receipts under `.scratch/phase3_3/fixtures/*/build.json` give both ZIP hashes, the Carbin and target member SHA-256, exact byte spans and manifest identities. The commercial payloads and manifests stay in the local workspace.

## The central extra field `0x1123`

All 229 Phase 2 central records contain tag `0x1123`, length 4. Its little-endian value matches `local_header_offset + 30 + local_filename_length + local_extra_length` for every entry. The first append copied that value from the existing Race Modelbin. The standard central-directory local header offset was correct, while `0x1123` held 1,250,295 where the new payload starts at 19,599,192. Standard ZIP open and CRC only validated the standard fields.

`bind` and `lookup` both crashed while loading 3477. The unreferenced append worked. `lookup` keeps the original four shared-model records and changes just one path, so the crash also occurs without a fifth record. The corrected variants keep the same standard ZIP layout and update only the four bytes starting at decimal ZIP offset 19,720,421. The validator recomputes `0x1123` for all 230 members. The owner confirmed `bind-fixed` loads 3477 with the same visuals. This isolates the original `bind` crash to that field for this artifact. See [TEST_RESULTS.md](TEST_RESULTS.md).

The same read-only check found stale values in historical Phase 3 packages, including 227 of 229 in the relocated full alias and 2 of 229 in the fixed-layout full alias. The entrypoints-only and routed archives checked here have 0 mismatches, so the field alone cannot explain every separate-archive failure. No historical fixture was edited or redeployed.

## FM4 Honda DC2 comparison

The source is FM4 CarID 370, `HON_IntegraR_00`, mesh `bumperFa` from the existing local glTF. The FH4 target below is the painted near-camera draw of `bumperF_a.modelbin`. The FM4 line covers the complete exported bumper with all four materials, so the counts describe different draw scopes.

| Field | FM4 Honda LOD0 | FH4 Acura LODS0 carpaint |
|---|---|---|
| Position bounds X | −0.84040 to +0.84040 | −0.84238 to +0.84238 |
| Position bounds Y | +0.02622 to +0.43949 | +0.00016 to +0.52388 |
| Position bounds Z in source space | −2.25182 to −1.58243 | +1.55284 to +2.21078 |
| Position / triangle counts | 7,180 referenced positions across four draws, 12,373 triangles | 7,861 positions and 13,778 triangles in the painted draw |
| Indices and normals | uint32 triangles, float XYZ normals | uint32 triangle list, split SNORM16 normal in POSITION.w and NORMAL.xy |
| UV0 after mesh decoding | U 0.00287–0.61128, V 0.00302–0.97864 | U 0.00790–0.98023, V 0.09423–0.99853 before material tiling |
| Additional UVs | UV1 present and zero throughout this exported variant | Carpaint layout has UV0–UV4, with additional material-specific streams on other draws |
| Materials | `body`, `bottom`, `matte_colors`, `black` | `carpaint` and separate Acura badge, trim, plastic, frame and other draws |
| LODs | Separate main and LOD0 geometry. Main has 1,954 positions and 3,506 triangles. | Near mesh flags `0x3`. Additional masks `0x4`, `0x8`, `0x10`, `0x20`, `0x40` cover other LODs within the bundle. |

The decoded FH4 Carbin attachment and bumper root bone matrices are identity. The named Honda left headlamp and the Acura left hub both have negative X. Both meshes have Y up and similar metre-scale widths of 1.681 and 1.685. The starting comparison transform is `(x,y,z) → (x,y,-z)`, with the same reflection applied to normals and one triangle winding reversal. Before reflection, 12,373 of 12,373 Honda LOD0 triangles and 13,778 of 13,778 FH4 carpaint triangles agree with their vertex normals. The width ratio is about 0.998, so unit scale is a suitable starting point. The headlight, hood and fender joins still need a fit comparison, and a working paint material needs an explicit UV and tangent mapping. No FH4 Honda Modelbin was created in this phase.

The measurement recipe reads the original ZIP and the previously extracted local glTF without exporting any mesh payload.

```powershell
py tools/fh4_modelbin_geometry.py .scratch/phase3/phase2-baseline/ACU_IntegraR_01.zip `
  --fm4-gltf .scratch/phase3_1/fm4/extracted/hon_dc2_00/car.gltf
```
