# First native load

The owner reported that v1.4 started FH4 and showed Suzuki in Autoshow. Selecting the car caused an access violation before a preview appeared. The first drive is pending.

## Observed fault and compiler correction

Application Event 1000 and the matching Windows minidump record Steam 1.478.564.0, exception `0xc0000005`, module `ForzaHorizon4.exe` and RVA `0x095599AE`. The exception is an invalid read in a wheel mass comparison. The crashing thread's stack contains `SELECT Mass, MassLevel FROM List_Wheels WHERE Id = 12000`. Both mass-level selectors are `INT_MAX`. The five floats at the required table base are 1.0, 0.9, 0.8, 0.7 and 0.6. The address computed by `[rsi+rdi*4+0x774]` matches the exception's fault address. The following instruction divides by a coefficient selected by the second index. The v1.4 Suzuki's selected stock wheel row had a valid FM4-derived mass and SQL NULL in `MassLevel`.

The compiler now assigns mass level 0, the observed unit coefficient, to a new owned stock wheel with an authored FM4 mass and no native relative mass tier. The separate mass value stays intact. Readback rejects NULL, sentinel and out-of-range levels on any imported stock wheel. The dump evidence identifies the wheel reference at this crash site. The post-fix UI and scene path still need owner validation.

The captured code bytes come from the runtime memory stream. On-disk code at the fault RVA differs. The receipt retains the byte window, registers required for the address calculation and SQL on the faulting thread stack. The minidump lacks a confirmed symbolic unwind through this site.

## Data_Car and presentation

The compiler derives mandatory population from column counts in the baseline catalogue. The baseline here contains 783 stock vehicles and the retained Honda 3477. All 148 columns populated in that cohort are now populated for each import. The previous violations were 14 for Suzuki, 14 for Honda and 29 for RUF. Closed cockpit and wheel flags use source body and window groups with native catalogue corroboration. Camera collision flags and the full traction-control tuple have stock support. Puddle behavior and off-road and upgrade wizard ratings use recorded conditional native cohort medians. They are estimates for the target game, pending calibration. Ownership flags describe an ordinary removable vehicle. FM4 family identifiers are translated through the target catalogue. The source ratios, mass and torque remain unchanged.

Honda 3477 and the stock reference cars use a symbolic `game:\Media\Stripped\RC…\Thumbnail_….png` catalogue key. Their mounted RC archives contain `_Big.swatchbin` and `_Small.swatchbin` BC7 textures. The package now emits both owned RC and HiRes archives with matching native dimensions, premultiplied image data, TXCH metadata and mount coordinates. The original FM4 car supplies the pixels. The shared native ZIP path and Big/Small layout are independently verified. The captured crash identifies the mass coefficient read. The absence of visible 2D or 3D preview in that attempt remains a separate observation.

## Scene closure and IDs

The scene, authored Modelbins, stock wheel transforms, local locator graph, selected GR2/GSF clips and v27 physics shell keep their verified v1.4 bytes. The recompiled manifest adds the independent scene and both thumbnail mounts. The package retains known stock library materials, tire families and suspension resources. Existing working Honda 3477 and stock scene mounts serve as comparison controls. The closure report lists stock paths absent from the generated archive, including lights, AO, proxy geometry, paint colors and IK. Their role in post-selection initialization remains open. The recorded load failure is at the wheel mass read. Full scene initialization, graphics and physics dispatch remain runtime observations.

GameDB had progressed to a wheel query with ID 12000. The failing float index was the mass-level sentinel, 2147483647. This establishes that ID 12000 reached the native load path. There is no demonstrated CarID ceiling at this site. Suzuki 12000, RUF 12001, Honda 12002 and the installed Honda 3477 retain their assignments.
