import importlib.util,struct
from pathlib import Path
p=Path('.scratch/expanded-garage-v1.4.5')
s=importlib.util.spec_from_file_location('r',p/'replay-observed.py')
r=importlib.util.module_from_spec(s);s.loader.exec_module(r)
x=r.x
for t,idx in ((709900,-1),(17596525500,-1),(58354191800,-1),(58532095900,-2),(58532095900,-1)):
 m=r.sample(t,idx)
 inputs=[(int(k.stem,16),k.read_bytes()) for k in (p/f'private-inputs-{t}').glob('*.bin')]
 for a in (0x38b9150,0x38bb0d0,0x38b8b70,0x38b6350):
  d=struct.unpack_from('<f',m,0x1d70)[0]
  u,status,missing,trace=x.execute(a,m,(x.P,0),inputs=inputs,xmm=((x.UC_X86_REG_XMM1,0.005),(x.UC_X86_REG_XMM2,d if a==0x38b8b70 else 0.005)),visited=x.watch)
  out=bytes(u.mem_read(x.P,len(m)))
  print(t,idx,hex(a),status,missing,r.fmt(out,0x20),r.fmt(out,0x40),r.fmt(out,0x1db0,2),trace[:7])
