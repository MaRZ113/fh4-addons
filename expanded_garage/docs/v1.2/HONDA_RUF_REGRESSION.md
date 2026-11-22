# Honda and RUF regression

| Car | Independent ID | Source structure verified | Test coverage |
| --- | ---: | --- | --- |
| Honda Integra Type-R 2000 | 12002 | Right-hand steering and driver geometry, source-specific hub positions, 195/55 R15 on both axles | 81 ZIP members, native rest rig, Retro Hot Hatch bucket, stock tires and five forward ratios |
| RUF CTR2 | 12001 | July DLC assets, rear engine, rear drive, 245/35 R19 front and 285/30 R19 rear | 58 ZIP members, native rest rig, Retro Supercars bucket, stock tires and six forward ratios |

The same code also builds Suzuki 12000 into the shared fleet SQLite and manifest. The baseline preservation audit includes the old Honda 3477 and all its related rows. The new Honda has its own ID and save identity. Runtime behavior, RHD driver animation and RUF audio still need qualification.
