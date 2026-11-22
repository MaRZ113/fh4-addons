# Wheel transform contract

The Vehicle IR supplies part positions 5/LF, 6/RF, 7/RR and 8/LR. The stored positions are chassis-space meters. Wheel dishes and brakes are serialized in component-local space. The hub's parent is the chassis. The spindle is a hub child with zero rest displacement, so its chassis-space rest position equals the hub rest position.

The verifier checks the four authored points against the FH4 `CarPartPositions` rows, both rig files, numeric Carbin bone bindings and authored initial placements. It checks side conventions and the resulting wheelbase against the original body definition. Suzuki measures 2.507021 m from the authored axle centers. The tire unloaded radius derives from rim radius plus sidewall height, giving 0.3184 m at both Suzuki axles.

Native stock wheel records carry placements and rig transforms simultaneously. In the Golf R example the left-front Carbin placement is approximately (-0.888, 0.170, 1.243) m and the Granny hub bind is approximately (-0.832, 0.212, 1.272) m. These values are both meaningful and can differ. The generated layout records both frames. Measured runtime pose or a native code trace is needed to determine how FH4 composes the matrices under suspension movement.
