# One controlled Suzuki retry

The current v1.4.1 payload at CarID 12000 is the known crashing comparison. `regression/` contains static diagnostics and checksums. There is no replacement installation package in this handoff. One ordinary instrumented repeat has already reproduced the failure and changed the crash signature within the same MegaTexture operation.

## Capture needed before another launch

Keep the executable and installed transaction hashes pinned. The required additions are the grid and ring heap pages, the tile-record page if the parser reached `0x467FE60`, the native stack, and the active streaming task or request. A per-executable full user-mode Windows crash dump, collected during an ordinary isolated offline session, is the next way to obtain those heap pages. Use a private dump directory with sufficient space and restore any per-executable capture settings afterwards. The account used for the repeat lacks the administrator rights needed to configure system-wide WER LocalDumps. The attempted native debugger attach in this environment terminated the game on the title screen at a different RVA and was abandoned.

Run `python -B -m expanded_garage.research.inspect_megatexture_faults --exe <local FH4 executable> --dump <local full dump> --out <private JSON>` on the captured dump. The known grid fields are `grid+0x60` through `+0x77`, ring fields are `ring+0x90/+0x94` and `+0xD0` through `+0xDC`, and the tile buffer is `tile+0xD8`. In WinDbg after `.ecxr`, the owner fault uses RSI for the grid and RDI for the ring. Inspect `dq @rsi+60 L1`, `dd @rsi+68 L4`, `dd @rdi+90 L2`, `dd @rdi+d0 L4`, and the corresponding `!address` records. At the repeat fault use the saved parent RSI/RDI from the parser report.

If the heap confirms which ring contains `INT_MIN`, identify the input producer for it. The precise update is at `0x4685D76` and the downstream writer is at `0x468124B/0x4681252`. A follow-up observation needs the calling thread and RDX input at that update, plus XMM operands before `0x4680FE5/E9` and `0x4680FFD/0x4681003`, with the last resolved resource or viewport identity. A crash-time memory snapshot may recover the heap and still lack the producer's original floating-point operands.

## Acceptance after a proven correction

On the same isolated offline host, install only the newly verified Suzuki package after the baseline is restored. Verify the catalogue thumbnail, select Suzuki once, and require a rendered 3D vehicle with all four wheels and a return to Autoshow. A successful 3D viewer test establishes first native load. Acquisition, Free Roam, driving, saving and reloading are later acceptance steps.

To roll back the existing v1.4.1 transaction with the game closed, run `python -B -m expanded_garage rollback --host <offline game root> --transaction 4339e5089d56469a990197ce3d01f3f5`. This is the observed local journal ID. The installer verifies its preimages. The game updated five of 18 local save files during the investigation without a purchase. The before-image is saved privately and was not written back automatically.
