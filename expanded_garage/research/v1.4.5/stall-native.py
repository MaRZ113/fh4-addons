import sys,struct
from pathlib import Path
sys.path.insert(0,str(Path('.scratch/expanded-garage-v1.4.4').resolve()))
from native import *
p=Path('.scratch/expanded-garage-v1.4.5')
pages={int(x.stem.rsplit('-',1)[1],16):x.read_bytes() for x in p.glob('private-stall-code-*.bin')}
def body(a,z):
 page=a&~0xfff
 b=bytearray()
 for x in range(page,z,0x1000):
  b+=pages.get(x,code(x,0x1000))
 return bytes(b[a-page:z-page])
for a in (0x365a271,0x274d14d,0x30ee2cd,0x2b73592,0x30615e5,0x27a2220,0x3055c50):
 s,z=bnd(a)
 print(hex(a),hex(s),hex(z))
 lines=[f'{i.address:08x} {i.bytes.hex():22} {i.mnemonic:10} {i.op_str}' for i in cs.disasm(body(s,z),s)]
 (p/f'stall-native-{s:08x}.txt').write_text('\n'.join(lines)+'\n')
 j=min(range(len(lines)),key=lambda i:abs(a-int(lines[i].split()[0],16))) if lines else 0
 print('\n'.join(lines[max(0,j-5):j+5]))
