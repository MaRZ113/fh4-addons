# Backend and diagnostic changes

The compiler output and installer policy remain those of v1.4.1. The three rebuilts reproduce the failing Suzuki payload and the two structural control payloads. This handoff contains neither a speculative vehicle data change nor a new installable trial.

- `research/inspect_megatexture_faults.py` reproduces both observed accesses from pinned local dumps. It validates code, saved registers, caller frames, integer transforms and capacity, and reports grid/ring and virtual region data when the dump captured them. It understands MemoryList and Memory64List.
- `research/audit_scene_load_resources.py` reads the retail Carbin scenes and emitted Modelbin headers, reports comparisons without assigning mandatory status from frequency and checks referenced material file names in the shared archive.
- `research/watch_native_load_files.py` samples archive handles without attaching or modifying the target. Its output is limited to the specified game ZIPs.
- `test_native_load.py` tests both recovered rectangles and the edge arithmetic of the native domain.

The evidence narrows future compiler changes to the producer input that yields invalid MegaTexture ring coordinates. An input proven wrong should be corrected in its owning generator, with a regression that tests the native field contract. Model variants, CarID thresholds, geometry UV clamping, and arbitrary optional assets have no demonstrated remedy for the observed fault.
