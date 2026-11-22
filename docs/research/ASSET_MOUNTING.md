# FH4 independent car asset mounting

## Observed format and native paths

The phase uses the x64 1.478.564.0 executable identified in [PHASE3_REPORT.md](PHASE3_REPORT.md). The following offsets refer to original executable file bytes. These literals identify candidate routes, while xrefs and runtime file access are needed to prove their selection.

| File offset | Literal |
|---|---|
| `0x52cd118` | `game:\media\cars\%s.zip` |
| `0x52d0778` | `game:\media\cars\%s\UI\%s.xaml` |
| `0x54ff850` | `game:\media\cars\%s\%s.carbin` |
| `0x54ff870` | `game:\media\cars\%s\Locators.xml` |
| `0x5506f50` | `zipmount.xml` near a DLC archive path |
| `0x5506f78` / `0x5506f98` | `game:\media\zipmanifest.xml` / `game:\mediapc\zipmanifest.xml` |
| `0x5507000`–`0x5507028` | `mountas`, `dirstart`, `dirsize`, `direntries` |

The stock decrypted `ZipFiles` manifest contains 2,368 registry entries. The Acura entry uses priority 80 and coordinates `19599118 / 23929 / 229`. `dirsize` equals the central directory through the end-of-central-directory record. No stock `mountas` attributes were found in this document. Native strings alone leave optional attribute syntax, load order and the active file source unknown.

The builder appends one alias record with priority 80 and actual ZIP coordinates, then verifies every existing manifest entry is identical. The relocated alias uses `19586788 / 23929 / 229`; the fixed variants use `19599118 / 23929 / 229`. The encrypted manifest stays at 356,436 bytes and independent decryption reproduces the edited plaintext. REA retained the relocated alias ZIP inventory as `ev_8553a1b1d6154d86ba8d34968d7100668089cd60912755d6ac50b7375057b879`.

## Identity and dependencies

`MRZ_IntegraJ_00` has the same 15-byte length as `ACU_IntegraR_01`. The builder edits a copied Phase 2 asset and leaves the live donor ZIP intact. It verifies all 159 donor tokens in the Carbin occur within parsed string fields. It changes the header name and ordinal only for the full alias, plus exact car-local path prefixes to known ZIP members. The scene retains the working type 34 and 9 bindings. Four entrypoint filenames change (Carbin, AvPins, GSF and XAML). Some packaged member basenames retain Acura's token and resolve inside the new namespace. Shared wheel AO references remain in `_library`.

The full alias also rewrites checked private path prefixes in `Lights.bin` and 13 Modelbins and updates the front-end XAML class name. It keeps model, vertex and index bytes and texture payloads. Build metadata and GSF build provenance stay intact. Audio, names, year, producer, showroom thumbnail, price, physics and save keys remain at their Phase 2 values. Alias-specific Forzavista or additional animation routing remains unverified.

The fixed layout variant keeps all 229 local header offsets, the directory coordinates and archive size. In the members with tight compression budgets, verified method-8 payloads can use a bounded local ZIP extra field to fill the original record span. The separate entrypoint-only control retains all compressed payloads and changes four equal-length member names only. It yields a Null Car in the owner test. Full alias with matching scene name and ordinal yields the same fallback with fixed layout. Adding all 229 files loose under the stock-style alias path also gives Null Car, with a stall in Autoshow. This tests only that loose namespace and installed input pair. It leaves virtual mount resolution, scene acceptance and another native lookup as candidates.

The namespace test is independent only after the alias is visibly correct and the registered archive is confirmed as its source. Next, capture a short passive file-I/O trace targeted to `ForzaHorizon4.exe` during one stock selection and one alias selection. Compare actual reads of `zipmanifest.xml`, the two ZIP paths and the alias scene. If the alias ZIP opens, identify the first failed resource or rejected scene. If it never opens, trace runtime registration and the active mount manifest. The built-in WPR FileIO attempt returned Windows error `0x80070005` (access denied), with no trace started. A bounded scan for direct RIP-relative references to the listed literals in stock `.text` produced no matches with its selected instruction forms. Static call paths therefore remain unverified. Runtime evidence for another title does not establish FH4's load order. [FH1 archive modding reference](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/blob/main/docs/modding.md)
