# FM4 Honda DC2 geometry extraction

The fixture is Honda Integra Type-R 2000 (DC2), CarID 370 in FM4, `HON_IntegraR_00`. The game copy on G: was read only. The car ZIP, wheel ZIP and GameDB were copied into `.scratch/phase3_1/fm4/input` and SHA-256 checked against [FM4_EXTRACTION.md](FM4_EXTRACTION.md). The carbin-garage 0.0.1 executable has SHA-256 `e572fe49744a93e32cdc10dacb83f507566ea078fa77bde5505ded404468af4f`. Its FM4 import decoded the method-21 LZX entries. All 35 car members and 12 wheel members matched central-directory CRC and size when compared to extracted originals.

| Part | Source mesh names / glTF indices |
|---|---|
| Stock front bumper candidate | `bumperFa` main 45, LOD0 88. The source tool identifies it as a front group, its coordinates are forward of the axle, and it has the default `a` suffix. FM4 GameDB lists stock front row 370000 separately from upgrades. The exact runtime stock row-to-mesh association has not been observed in FM4. |
| Front lamps | `headlightL` and `headlightR`, main 38/39 and LOD0 80/81. |
| Hood | `hooda`, main 37 and cockpit 111. No standalone hood in the exported LOD0 mesh list. |
| Nearby alternatives | `bumperFrameF` plus `bumperFk`, `bumperFs`, `bumperFq`, `bumperFj`, `bumperFf`, `bumperFb` and `bumperFrace`. Keep separate from the stock candidate. |

The source car glTF has 120 meshes across main, LOD0, cockpit and brake/rotor variants. The wheel ZIP exports a separate wheel mesh. The car glTF SHA-256 is `cc8d8741898d7a6cd3cfc5e5036eb51160a6247d9cac14af33f4c968afd82a70`, its buffer is `6831e49f5b614f5b177b5d354c5b6c74dd42ac0f32a76832213a37f3184b3a5e`. The extracted `maxdata.xml` gives wheelbase 2.545032 and outer front/rear tracks 1.699206 and 1.698979 in the source's metre-scale coordinates. The exported front is at negative Z, with lateral variation on X and up on Y. FH4 target alignment still needs direct comparison with its stock Modelbin.

The isolated LOD0 OBJ uses identity transform and preserves source triangle winding, UV0 and normals. It has 7,180 exported vertices and 12,373 triangles; X spans approximately ±0.8404, Y [0.0262, 0.4395], Z [-2.2518, -1.5824]. UV0 spans U [0.00287, 0.61128] and V [0.00302, 0.97864]. Normal lengths span approximately [0.99992, 1.00009]. Source material labels have 9,064 `body`, 118 `bottom`, 2,975 `matte_colors` and 216 `black` triangles. MTL values are neutral placeholders. Keep the original glTF for UV1 and original material/texture metadata. No FH4 Modelbin or material conversion has been performed.

The local OBJ is `.scratch/phase3_1/fm4/bumperFa_lod0.obj`, SHA-256 `62254e01186da0f2d6b1b92134b8da54605ae1b241174eef6fbf362f2eb5fd2e`. It and the other extracted assets are excluded from the handoff.

```powershell
$p = '.scratch/phase3_1/fm4'
tools/carbin-garage-exe/carbin-garage.exe import "$p/input/Media/cars/HON_IntegraR_00.zip" --profile fm4 --out "$p/repro" --slug hon_dc2_00 --all-variants
python tools/fm4_bumper_obj.py "$p/repro/hon_dc2_00/car.gltf" "$p/repro/front.obj"
```

[carbin-garage](https://github.com/ItsNotPaths/carbin-garage)
