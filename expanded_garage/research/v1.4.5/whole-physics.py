import sys
from pathlib import Path
sys.path.insert(0,str(Path('.scratch/expanded-garage-v1.4.4').resolve()))
from native import *
out=Path('.scratch/expanded-garage-v1.4.5')
def pt(a,b):
 return [y for y in entries if a<=y[0]<b]
def unwind(x):
 s,e,u=x
 flags,n = disk(u,4)[0]>>3,disk(u,4)[2]
 return struct.unpack('<3I',disk(u+((n*2+4+3)&~3),12)) if flags&4 else None
def whole(a):
 s,z=bnd(a); x=next((p for p in entries if p[:2]==(s,z)),None)
 if not x:return (s,z)
 p=x
 while j:=unwind(p): p=j
 s=p[0]; z=max([p[1]]+[p[1] for p in pt(s,s+0x4000) if (h:=unwind(p)) is not None and h[0]==s])
 return s,z
for a in (0x388d6e0,0x388d969,0x3892c70,0x388b6ff,0x388d718,0x388f940,0x38602e0,0x38ad6a0,0x38989d0,0x38894c0,0x38aaf00,0x388ff10,0x388a170,0x38b6350,0x38910e0,0x388f0f0,0x388ba80,0x37f4170,0x3864c5a,0x38bdb80,0x38bfc86,0x38a5920,0x38a6d30,0x38ae440,0x3887780,0x38a8f00):
 s,z=whole(a)
 print(hex(a),hex(s),hex(z))
 (out/f'whole-{s:08x}.txt').write_text('\n'.join(render(s,z))+'\n')
targets=set((0x388d6e0,0x3892c70,0x38602e0,0x38621d0,0x3890620,0x388f940,0x38ad6a0,0x388b6ff,0x38910e0,0x388ff10,0x3888590,0x388a170,0x388b530,0x38a29c0,0x38a38c0,0x388f0f0))
for s,z,u in pt(0x3000000,0x3910000):
 raw=code(s,z-s)
 if not raw:continue
 for m in re.finditer(b'\xe8',raw):
  i=m.start()
  if i+5>len(raw) or (a:=s+i+5+struct.unpack_from('<i',raw,i+1)[0]) not in targets:continue
  print(f'call {s+i:08x} -> {a:08x} owner {whole(s)[0]:08x}')





