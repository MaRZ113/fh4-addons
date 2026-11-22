# Source of the invalid MegaTexture request

The Suzuki presentation focus is a typed `CarPresentation` linked to the loaded `CarScene::Scene` with ordinal 12000 and MediaName `eg_suz_sx4_11`. In the targeted offline observation its basis remains finite while its X, Y and Z translation and its velocity are already NaN. Its paired `ForzaCar` owns a `CCarDynamics` object with NaN in the physical Y coordinate and vertical velocity. Physical X and Z were finite.

The camera position is finite in the captured transition. The presentation update blends toward the focused vehicle before converting the horizontal world coordinates to terrain integers. The focused NaN translation propagates through that arithmetic. The ordinary conversion at `0x3962C7E/0x3962C8C` produces `INT_MIN` before the request reaches MegaTexture. The streamer then accepts this center and wraps signed ring origins. This sequence explains the existing crashes and the black viewer reproduced during the second probe.

The first nonfinite value is localized upstream of camera and render resources, in the vehicle's physical state. The first invalid arithmetic operation inside physics and the exact backend input that triggers it remain unresolved. Engineering acceptance and a corrected deployment are pending.
