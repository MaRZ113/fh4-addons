# Native GameDB compiler

`DatabaseGraph` records the target table, primary key, owner, field values, conversion rule and edges to owned or shared keys. It orders inserts, checks target key occupancy, then verifies each staged value. `CarPartPositions.ID` is a logical key because the reviewed schema declares no primary key for that table. Each catalogue ID namespace is validated separately.

The graph creates an independent vehicle, body, engine, drivetrain and stock wheel, on-disc placement, rarity, camera offsets, source hardpoint rows, stock parts and supported geometry variants. Torque curves, aero, spring and anti-roll definitions get new keys. Typed dependencies include the make and wheel from the car, body and drivetrain owners, and deferred physics definitions. The unchanged-table audit compares row multisets against the baseline and verifies the schema and existing SQLite foreign-key findings.

FM4 positions use the imported authoring frame. Brake diameters convert from inches. Suspension type IDs map by target name. Factory engine and drivetype IDs are checked by semantic label. FM4 performance index and target class projection are recorded as provisional. Target-only columns and missing tyre, bucket and wheel icon bindings appear in `database-graph.json`. The current projection will need additional target semantics before a drive test.

Existing makes link by manufacturer code. Suzuki and RUF get independent make IDs 20075 and 20091. English game strings use the stable table namespace observed in stock plus allocated 32-bit entries, preserving existing text and symbol records. Source logos are encoded as premultiplied BC3 swatches. Source thumbnails are converted to PNG. Native frontend rendering and other locales remain unverified.
