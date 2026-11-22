# MegaTexture fault objects

`inspect_megatexture_faults.py` verifies the pinned executable and module identity, instruction bytes, minidump exception context, saved stack values and return sites. Its output for both real dumps is in `evidence/megatexture-faults.json`.

The native constructor at `0x4672DD0` installs the `Concurrency::concurrent_queue<MegaTextureSourceStreamingRequest>` and `Concurrency::concurrent_queue<MegaTextureStreamingRequest>` vtables at object offsets `+0x08` and `+0x30`. The same constructed object sets the tile divisor at `+0x00` to 1023, the X/Y origins at `+0x68/+0x6c` to −24, and width/height at `+0x70/+0x74` to 48. It stores the `0x4800`-byte allocation pointer at `+0x60` and initializes 2304 slots. That object layout and the code that uses it identify the table as a MegaTexture streaming grid. The module bytes came from ordinary local process reads of the hash-pinned build. The fault instruction bytes were also captured in the respective dumps.

| Measurement | Owner dump `11648` | Local offline repeat `32600` |
|---|---|---|
| Thread | `10376` | `30392` |
| Fault RVA | `0x46827E7` | `0x467FE60` |
| Operation | `mov r13, [rax + rcx*8]` | `movsxd rax, dword ptr [r12]` |
| Fault address | `0x12e5ceaf938` | `0x10` |
| Grid object | `RSI = 0x12d3bc1ad80` | Parent RSI `0x292f0ba1290` |
| Table pointer | `RAX = 0x12e5c8cbe80` | Uncaptured |
| First tile `(column, row)` | `(-16401, 16399)` | `(-16401, -16401)` |
| Linear index | `771927` in RCX | `−802473` reconstructed from the parent frame and constructor |

At `0x4682650`, integer inputs are formed from the ring origins and a wrapped rectangle. The function shifts each coordinate right by seven and uses a sign-corrected division by 1023, equivalent to floor division. The original stack holds raw `(xmin, ymin, xmax, ymax)` as `(-2147483648, 2147379968, -2147419648, 2147380224)`. The repeat holds `(-2147483648, -2147483648, -2147389696, -2147483136)`. Applying the transforms gives the independently stored tile bounds in both frames. The linear index is `(row - originY) * width + column - originX`.

In the repeat, `0x468284A` passes the selected object to `0x467FDE0`. The callee reads `[object+0xD8]`, adds `0x10` to it, checks a positive list count and reads a signed resource-name length. R12 was `0x10` at that first read and the saved parent R13 agrees with the object passed to the parser. `0x467F080` stores its incoming data pointer at the same member and is another caller of the name parser. The parser normalizes the names, including removal of `_underwater` in a branch. The null buffer is inferred directly from the executed instruction path. Its heap member is outside both captured MemoryList ranges; the actual heap allocation and virtual protection were unavailable because neither dump includes `MemoryInfoList`.
