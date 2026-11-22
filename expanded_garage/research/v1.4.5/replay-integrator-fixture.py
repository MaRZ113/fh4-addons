import importlib.util,struct
from pathlib import Path
p=Path('.scratch/expanded-garage-v1.4.5/replay-wheel-state.py')
spec=importlib.util.spec_from_file_location('replay',p)
x=importlib.util.module_from_spec(spec);spec.loader.exec_module(x)
for title,m in [('Honda',x.sample(1211800)),('Suzuki staging',x.sample(29620439200,-2)),('Suzuki placed',x.sample(29644310400,-2))]:
 u,status,missing,_=x.execute(0x389ce70,m,(x.P,x.A))
 assert status=='ok'
 packet=bytes(u.mem_read(x.A,0x100))
 for mode in ('keep','zero-Rates'):
  s=bytearray(packet)
  if mode=='zero-Rates':
   for j in range(4):struct.pack_into('<I',s,0x80+j*20,0)
  u,status,missing,trace=x.execute(0x38b2850,m,(x.P,x.A+0x800,x.A,x.A+0x400),
   extras=((0x28,struct.pack('<f',0.005)),),inputs=((x.A,bytes(s)),),visited=x.watch)
  out=bytes(u.mem_read(x.A+0x800,0x100))
  print(title,mode,status,missing,[x.words(out,0x7c+j*20,2) for j in range(4)],x.words(out,0,4),x.words(out,0x40,4))
