import importlib.util,struct,math
from pathlib import Path
p=Path('.scratch/expanded-garage-v1.4.5')
s=importlib.util.spec_from_file_location('r',p/'replay-observed.py')
r=importlib.util.module_from_spec(s);s.loader.exec_module(r)
x=r.x
for t,idx in ((17596525500,-1),(58354191800,-1),(58532095900,-1)):
 m=r.sample(t,idx)
 inputs=[(int(k.stem,16),k.read_bytes()) for k in (p/f'private-inputs-{t}').glob('*.bin')]
 sequence=((0x38b9150,(x.P,),0.005),(0x38bb0d0,(x.P,),0.005),(0x38b8b70,(x.P,),0.005),
           (0x38a1480,(x.P,0),0.005),(0x38901a0,(x.P+0x560,),0.005),
           (0x38ad6a0,(x.P,x.A+0x400),0.005),(0x38967c0,(x.P,),0.005),
           (0x38a04e0,(x.P,),0.005),(0x38a4ad0,(x.P,),0.005),
           (0x38a6d30,(x.P+0x560,0),0.005),(0x38bdb80,(x.P,),0.005))
 for a,args,dt in sequence:
  d=struct.unpack_from('<f',m,0x1d70)[0]
  u,status,missing,trace=x.execute(a,m,args,inputs=inputs,xmm=((x.UC_X86_REG_XMM1,dt),(x.UC_X86_REG_XMM2,d if a==0x38b8b70 else dt)),visited=x.watch)
  out=bytes(u.mem_read(x.P,len(m)))
  bad=sum(not math.isfinite(v) for v, in struct.iter_unpack('<f',out))
  print(t,hex(a),status,missing,r.fmt(out,0x24,1),r.fmt(out,0x44,1),r.fmt(out,0x1db0,2),bad)
