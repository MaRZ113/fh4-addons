# RUF CTR2 compatibility

The shared compiler consumes the retained July DLC Vehicle IR as `EG_RUF_CTR2_95`, CarID 12001, with independent make ID 20091. It preserves own RWD type, mounting direction, camshaft/torque/gear records, rear-engine source metadata and original wheel and body geometry. The English make and car strings use the same native serializer as Suzuki.

Verify rear placement, axle behavior and cockpit layout during runtime qualification. The DLC intake and companion source audio banks remain in the IR and await translation to the native FH4 audio system.
