# FH4 alias identity matrix

All five matrix steps have matching encrypted equivalents for one existing routed GameDB and one existing appended manifest. Their plaintext hashes are `0890eb6aa972574859b60ee05922994befbad51e75d046f7e560deefb4eb9a0c` and `a4cb627db22cbc578d5c67ddce550a92ba5e2037a18768a7a1963a3b641943bb`. Their ZIP size, all 229 local-header offsets and directory coordinates (19599118, 23929, 229) stay fixed. Byte-level compression and CRC metadata can change when payload fields change. The following deltas refer to decoded fields.

| Step | Difference from previous comparable step | ZIP SHA-256 | Runtime evidence |
|---|---|---|---|
| Fixed mount control | Add the alias-path ZIP and one manifest entry to Phase 2, leaving the Phase 2 DB. Use the `routed` ZIP bytes for this control. | `a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c` | Pending |
| `routed` | Change only DB `3477.MediaName`. Archive bytes still match the Phase 2 donor. | `a4156136c82ad6caef67de233370127ff5130c2840f0f6c6539362d413e88f7c` | Pending |
| `entrypoints` | Rename four entrypoint members within the alias ZIP. Member payloads, internal name, ordinal and paths match the previous row. | `5ff272bbd9b3f36c9b70cb6c5467d28a57cc0150a358e4c4b9ed5164ddb6be6e` | Phase 3 Null Car |
| `name` | Change only parsed Carbin MediaName string. Ordinal stays 368. | `d06d926f5b014a521a828203317d4975d923e65ade952f37f48d3bbdb812e881` | Pending |
| `ordinal` | Change only parsed Carbin ordinal to 3477. | `750500ba21584b708151e761a6c5fd6c0257048a9e68cdb4e15e0ccfc405583b` | Pending |
| `paths` | Retarget checked car-local paths in Carbin, Lights and Modelbins and the associated XAML class. | `a22761af42114e6a5c7956755b92c614f179ae0fa155d8962292a81a5307a210` | Phase 3 Null Car |

The original Phase 3 installer mode `mount-only` is a separate relocated-layout control. It has full alias ZIP SHA-256 `b1638555b113cb025fa950e44ea962244af7dacf28c794a9e95182560df65e44`, alias manifest SHA-256 `685060bd016bcf7daf217ff6ad663d3984b58432071fd379cd42c5ca09a2e967` and Phase 2 SLT SHA-256 `dc470e013aa8a67dd64f51e5f4813a95e1c7f80bf4a0f2a03c5d26b1545291a8`. Procmon confirms the alias ZIP opens and its directory is read in this run. This result applies to the relocated control. [Capture results](FILE_IO_DIAGNOSTICS.md)

```powershell
$a = '.scratch/phase3/phase2-baseline/ACU_IntegraR_01.zip'
$m = '.scratch/phase1/zipmanifest.decoded.xml'
$d = '.scratch/phase1/gamedb.car3477.sqlite'
foreach ($step in @('routed','entrypoints','name','ordinal','paths')) {
    python tools/fh4_independent_assets.py $a $m $d ".scratch/phase3_1/identity/$step" --preserve-layout --matrix-step $step
    if ($LASTEXITCODE) { throw "Fixture $step failed" }
}
```

These are local staging fixtures. Record a verified installed hash before using a runtime result. The staged name and ordinal changes cannot determine which stage rejects the alias without gameplay tests or file-I/O evidence.
