# Suzuki SX4 Sportback integration

Source `SUZ_SX4_11`, target `EG_SUZ_SX4_11`, CarID 12000 and new Suzuki manufacturer 20075. The same builder as the other controls creates its own geometry, catalogue graph, native localization, logo swatches, wheel models and v27 collision envelope. The source describes a left-hand-drive vehicle.

The primary package stages the wrapped GameDB and mount, English archive, vehicle ZIP and three derived artwork files. `validation.json` records the remaining first-drive dependencies. A static verify pass and envelope readback are complete. Autoshow display, spawn, movement, garage persistence, and actual rollback on the game host await runtime qualification.
