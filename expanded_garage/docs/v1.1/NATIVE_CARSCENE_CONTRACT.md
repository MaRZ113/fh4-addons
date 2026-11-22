# Native CarScene contract

The backend compares v5 scene records against the Phase 4 Honda 3477 and stock controls including VW Golf R, Porsche 911 GT2 and Civic Type R. REA independently inventoried the 229-member Phase 4 archive under evidence `ev_e079fe7a5bac4c32e14504f155adebc8975d4ad0ca2f14b23d95ef3f8eb2ed67`. The project reader decodes the scene through EOF and exposes the bone, LOD and upgrade references.

The generated archive names its own Carbin and skeleton. Each Modelbin lives below that car's namespace. The v3 shared variant records bind stock and alternate front and rear bumpers, hoods, side skirts and wings wherever the source parts label resolves a matching mesh. Wings use serialized parent `-1`. Body children bind to the car's stock body ID. The SQLite projection and serializer consume the same variant contract. Unresolved variants stay in Vehicle IR with explicit receipt entries.

Wheel and brake meshes remain in component-local space. Their chassis rest origins come from authored FM4 `CarPartPositions`, with the dimension-based v1 rule available where authored points are absent. The rest origin is represented in the Carbin placement and the hub bind hierarchy, matching the observed stock record shapes. Static evidence cannot determine when runtime composition uses or replaces those representations. A rest/steering/suspension capture is required for that qualification.

The generated rig provides chassis, hubs and spindles plus FH4-schema locator records for supported original hardpoints. Full GR2/GSF controller output, individual movable groups, camera/driver constraints and runtime key resolution remain open.
