# Steam 1.478.564.0 car dependencies

## Root identity and content

`Data_Car.Id` is `INTEGER PRIMARY KEY`. `ModelId` is a separate indexed integer. All 783 stock rows use the same number in both columns. `Data_Car` has no column named `Ordinal`. The schema uses `Ordinal`, `CarId` or `CarOrdinal` in its consumers. `ContentId` on `Data_Car` is a different field with stock values 0, 53 and 99. `ContentOffersMapping.ContentId` is polymorphic. For `ContentType=1` its 119 records all resolve to existing car IDs, and `OfferId` references `ContentOffers.Id`. Base-game donor 368 has no type-1 offer mapping. Its type-1 `OnDiscContent` row is `Id=4, ContentId=368, ReleaseOrder=0`. The candidate copies this release metadata using a new row ID 700.

| Dependency | Donor 368 / candidate 3477 |
|---|---|
| Identity and presentation | Both use `ACU_IntegraR_01`, the same localized name token, and the donor thumbnail path. Candidate price is 26,000 CR as a test marker. Donor price is 25,000 CR. |
| Purchase-related DB state | Donor fields `ContentId=0`, `IsInstalled=1`, `IsPurchased=1`, `IsDrivable=1`, `NotAvailableInAutoshow=0`, `VisibleOnlyIfOwned=0` are inherited. `Data_Car.IsPurchased` is distinct from the player's garage ownership. |
| Category and display | Clone `Data_Car_Buckets` (bucket 24, non-hero), `CarRarities`, `CameraOverrides`, `CarExceptions` and the donor's `EventBlueprintFlyerBackgrounds` row. Share make, class, type and wheel lookup records. |
| Factory colors and attach positions | `Combo_Colors` adds 2 rows; `CarPartPositions` adds 10 rows. Both remap the ordinal and their derived IDs. |
| Body | `List_UpgradeCarBody.Ordinal` resolves the stock body. Both its record ID and `CarBodyID` move from 368000 to 3477000. Clone `Data_CarBody` and 10 `List_UpgradeCarBody*` catalogues keyed by that body ID. The donor has no `Data_CarBody_DummyAxle` row. |
| Suspension, brakes and aero | Clone `List_UpgradeSpringDamper` and its 10 physics rows, front/rear `List_UpgradeAntiSway*` and their 6 physics rows, `List_UpgradeBrakes`, and `List_UpgradeRearWing` and its 3 aero rows. Repoint the cloned references to the cloned child IDs. The front bumper still references shared aero IDs 1 and 4. |
| Powertrain, rims and tires | Clone ordinal-specific `List_UpgradeEngine`, `List_UpgradeDrivetrain`, front/rear rim and tire compound choices. Reference existing engine, drivetrain and tire compound records. Stock engine 122 has `MediaName=ACU_IntegraR_01`; drivetrain 121 and tire compound 6 are shared. |
| Garage and persistence | The shipped `NewProfile_Career_Garage` template is empty. Its `Id` is the PK for the garage row and `CarId` is a separate field. `NewProfile_Career_PurchasedParts.GarageId` and the executable garage queries indicate instance-level part state. The shipped `ForzaProfile.sch` contains counters including `CarsInGarage`, `GarageValue` and `GaragePartsValue`. Actual Steam profile serialization, keys and limits for a new ID remain unverified. |

For donor-owned derived keys the tested remap is `368000 + suffix → 3477000 + suffix`, with the suffix preserved. It applies to the reviewed rows and columns. For example rear wing aero IDs `368901..368903` become `3477901..3477903`. Independent engine, drivetrain, manufacturer and other global keys retain their own namespace. The stage inserts the 34 reviewed table groups in [the script](tools/fh4_gamedb.py). It leaves gameplay roles such as event rewards, barn finds, rentals, traffic, presets and existing profile templates outside the candidate's registration.

The stock DB declares 140 FK entries. Twelve tables declare references to `Data_Car.Id`: `CarInvalidDefaultParts`, `CarListFilters`, `CarPartPositions`, `CarRarities`, `CarsDropParts`, `EliminatorUpgradePresetPackages`, `List_AntiSwayPhysics`, `List_UpgradeBrakes`, `List_UpgradeMotor`, `List_UpgradeRearWing`, `UnobtainableCars`, `UpgradePresetPackages`. Several important relationships lack a declared FK, including the body upgrade linkage and suspension/aero links. Stock `foreign_key_check` already reports 2,941 violations across four tables. The candidate preserves the exact same violation tuples. Its explicit link checks additionally cover the remapped stock branches and shared lookup targets.

## Shipped executable evidence

These null-terminated ASCII literals are from `.rdata` of the PE identified in [RESEARCH.md](RESEARCH.md). Addresses are RVAs. For these literals the RVA is the file offset plus `0x1800`.

| File offset / RVA | Evidence |
|---|---|
| `0x4c98ef0 / 0x4c9a6f0` | `SELECT MediaName FROM Data_Car WHERE Id=%d AND IsCarInstalled(%d)`. Evidence of a forward resource-name lookup gated by installation state. The native function implementation remains unknown. |
| `0x4cb2dd8 / 0x4cb45d8` | `SELECT ModelId FROM Data_Car WHERE UPPER(MediaName) = @model`. The stock candidate-name lookup yields 368; the staged DB yields 368 and 3477. Its runtime callers and handling of multiple matches remain unknown. This is the main resource-aliasing uncertainty. |
| `0x4d073c0 / 0x4d08bc0` | A query joins `Data_Car.Id` to `Data_Car_Buckets.CarId` and `List_UpgradeEngine.Ordinal`, and pulls `Data_Engine.MediaName` as the engine audio name. |
| `0x4faa180 / 0x4fab980`; `0x4faa530 / 0x4fabd30` | SQL fragments join type-1 offers, category and tire/preset rows, and provide an Autoshow availability predicate. |
| `0x4fadce0 / 0x4faf4e0` | A catalogue query joins owned `CareerGarage.CarId` to `Data_Car.ModelId`, uses `IsCarDLC`, and generates large and small thumbnail names from the DB path. |
| `0x4faabb0 / 0x4fac3b0` | A garage query joins `Garage.ModelId` to `Data_Car.Id`. This supports matching both new IDs before trying variants. |
| `0x5362320 / 0x5363b20` | `CREATE VIEW Drivable_Data_Car` selects car rows whose ID is absent from `UnobtainableCars.Ordinal`. Replaying the predicate in SQLite includes 368 and 3477. Other PE SQL uses `IsCarInstalled` and `IsCarVisibleAndReleased` on top of this view. |

## Resource and collision checks

- The decrypted base `media/zipmanifest.xml` is a version-3 ZIP mount inventory with 2,368 entries. It includes `game:\media\cars\acu_integrar_01.zip`; the manifest declares directory start 19,599,118 and 229 entries, both matching the shipped donor ZIP. The donor archive contains the Carbin, 96 Modelbins, 89 Swatchbins, animations, locators and `physicsdefinition.bin`. The candidate uses the same media name and archives. The need for additional native registration remains a runtime question.
- Stock `CarEventMapping.xml` directly registers ordinal 368 with friendly name `ACU_IntegraR_01` and its Forzavista event. The PE contains that XML's resource path at file offset `0x552e360`. The prototype keeps the audio XML stock, so ordinal-specific Forzavista for 3477 needs its own runtime check. The donor also has a named audio modulation XML, `PG_ACU_IntegraR_01.bank`, and uses a stock engine definition with the same media name. `CarEngineEventMapping.xml` includes that engine name as a target for other cars.
- The donor DB thumbnail points to `RC0\Thumbnail_368.png`. `RC0.zip` and `RC0HiRes.zip` contain `Thumbnail_368_Big.swatchbin` and `Thumbnail_368_Small.swatchbin`. The archive structure and PE thumbnail-generator calls corroborate the indirection. The candidate uses the same thumbnail field and its English name uses the donor token and existing localization archive. Preview/background path selection for the new ID remains untested.
- The audit scanned direct SQL car ID columns and type-1 content maps, integer values in ID-named columns for 3477000–3477999, 885 distinct stock audio ordinals, 88 base or DLC RC archives (756 distinct thumbnail IDs), eight installed expansion layouts and three published FH4 lists. ID 3477 and the derived block had no hits within their scanned scopes. 3472–3474 have occupied aero offsets; 3475–3476 have occupied drivetrain part offsets. The checked local expansion folders are `MicrosoftExpansion1` and `MicrosoftExpansion2`. External entitlements or additional unknown native registries remain outside this evidence.
