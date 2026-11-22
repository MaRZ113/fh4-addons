# Backend status

The import backend has no production code change in this handoff. An asset field or compiler transformation that explains the first invalid vertical physics value is still missing. The only changes are passive diagnostics, exact arithmetic replay, minidump decoding for the additional grid fault, and tests.

The verified first-input boundary is the world position and velocity produced by `CCarDynamics`, before publication to the foreground car. Compare the initial physical placement and first suspension/solver update for this same actor against a working stock actor. Once the first invalid operation is tied to its input, fix that input's shared compiler and enforce its units and domain at serialization and readback.

The native readback confirms that Suzuki's two suspension families are loaded and its observed spring values are finite. The staged spring data has legacy values that merit targeted comparison. Suzuki uses a max stretch of 0.5 m and bump-stop stiffness of 200 in the data table. Stock rows in the same articulated families peak at 0.1624 m stretch and use zero stiffness. Positive stiffness with zero BumpStopHeight also occurs in other native families. These comparisons do not establish the invalid calculation or a replacement rule.
