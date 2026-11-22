# Scene resource implications

The affected live object has a resolved Suzuki `CarScene::Scene` pointer and a scene state byte of 8. The state label is unknown. Its `CarPresentation` also has a renderer pointer and a driver model. The 3D view remained black once the focus translation became nonfinite.

No runtime attribution connects the six open resources to the physical position fault. `AOVolumeTexture.bin` and `ProxyLOD.modelbin` remain renderer work. `IKAnchorBones.xml` and the light files remain driver and lighting work. The black-view run reached paint selection UI, which gives no evidence that the missing vehicle-specific `ManufacturerColors.bin` caused the physics state. The existing work on each resource and its consumer is documented in v1.4.2 `NATIVE_SCENE_CLOSURE.md`.

Implement the resource writers once their field and activation contracts are confirmed. Keep the independent source geometry and Vehicle IR identities.
