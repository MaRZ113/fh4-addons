# Vehicle IR v1

A bundle is a directory headed by `vehicle.json`. The schema defines the manifest shape. Paths beneath the bundle use forward slashes. File identity is SHA-256 of the full file. JSON stores finite numbers and UTF-8 text.

## Coordinates and geometry

The chassis frame uses +X for vehicle right, +Y for up, +Z for forward, and meters. Component-local geometry uses the same axis convention. Wheel and brake model roles remain axle-local until a vehicle placement is supplied. The FM4 adapter reflects mesh Z and reverses triangle winding. Positions and normals have already undergone that transform when a backend reads them.

Each `geometry.models[]` entry references its original byte stream and a corresponding `geometry/<raw digest>.npz` file. NPZ members are NumPy arrays without pickle.

| Array | Type | Shape | Meaning |
| --- | --- | --- | --- |
| position | little-endian float32 | N × 3 | Vertex position in meters |
| normal | little-endian float32 | N × 3 | Unit surface direction |
| uv0, uv1 | little-endian float32 | N × 2 | Dimensionless input UV before the draw transform |
| native_extra | unsigned byte | N × B | Preserved, source-specific packed attributes |
| triangles | little-endian uint32 | M × 3 | Oriented, zero-based triangle indices in the selected pool |

Each draw stores its material label, source LOD, original topology identifier and two affine UV transforms. The eight coefficients are `[offsetU, scaleU, offsetV, scaleV]` for each channel. LOD 0 denotes the near tier in the FM4 adapter. Section bounds refer to the offset-local source encoding, with basis reflection applied. A section offset is already included in the position arrays. Alternate models, gauge poses and wheel blur remain present even when the preview backend omits them.

## Semantic sections

| Section | FM4 adapter payload in this version | Remaining schema work |
| --- | --- | --- |
| geometry | Components, LOD, triangles, UVs, normals, source extra channel and opaque Carbins | Canonical weights, authored tangent frames, colors and morph targets |
| structure | Car-attribute XML, model roles and stock wheel MediaName | Bind poses, named relationship graph, hinge axes, animation channels, locators, cameras and Forzavista |
| materials | Draw material labels and native texture resources | Target-independent texture slots, texture sampling and shader feature contracts |
| effects | Source material labels, XML and audio origin inputs | Authored light functions, directions, timing, projections and exhaust emitters |
| physics | Related catalogue records in original scalar units and preserved MAXData | Canonical powertrain and suspension schema, chassis rigid body and collision/deformation topology |
| audio | Car/engine/harmonic tuning XML and indexed FSB4 subsongs when readable | Game-neutral signal/event/control model and decoded source sample tracks |

A section status describes decoding coverage. `decoded` means the implemented representation was read successfully. `partial` records known omissions, `opaque` retains source artifacts with unresolved semantics, and `unavailable` marks absent input. A reader must preserve these distinctions.

## Provenance

`assets[sha256]` records the exact `blobs/<sha256>` bytes, size, and one or more source container/member locations. Paths in source receipts are descriptive and are never used to dereference a bundle. STFS members have both package-relative and archive-relative parts of the locator. The renderer consumes only the relative content-addressed blobs and NPZ assets.

A `provenance` value carries the source digest, field or record locator, units, and ordered transforms. Source-native DB rows and XML declare `source-native` where the original unit has yet to be established. Complete raw FM4 vehicle files and input SQLite snapshots travel with the bundle. This permits new decoders to recover further attributes without repeating the original extraction.

IR revisions that change axes, units, binary arrays, or field meaning require a version increase. Format-schema success alone does not establish any emission contract, so the compiler checks hashes before resolving arrays and the target verifies its own formats after serialization.
