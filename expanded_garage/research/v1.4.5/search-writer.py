import sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path('.scratch/expanded-garage-v1.4.4').resolve()))
from native import *
out=defaultdict(list)
for x,z,u in entries:
 if not 0x3850000<=x<0x38d0000:continue
 for ins in cs.disasm(code(x,z-x),x):
  dst=ins.operands[0] if ins.operands else None
  if dst and dst.type==X86_OP_MEM and dst.mem.base not in (X86_REG_RBP,X86_REG_RSP):
   if (dst.mem.disp in (0x1db4,0x1db0,0x1ed4,0x10ed0,0x10970) or dst.mem.disp==4 and ins.mnemonic in ('movss','vmovss','addss','subss')):
    out[x].append(f'{ins.address:08x}: {ins.mnemonic} {ins.op_str}')
  for op in ins.operands:
   if op.type==X86_OP_MEM and op.mem.base==X86_REG_RIP and ins.mnemonic in ('movaps','movups','movss','mov','andps','orps','subps','xorps','movdqa','movdqu','mulps','divps','mulss','divss'):
    a=ins.address+ins.size+op.mem.disp
    bits=disk(a,4)
    if bits in (b'\x00\x00\xc0\x7f',b'\x00\x00\xc0\xff'):
     out[x].append(f'{ins.address:08x}: {ins.mnemonic} {ins.op_str} ; constant {a:08x} {bits.hex()}')
for func,lines in out.items():
 print(f'{func:08x}',*lines,sep='\n ')

