import importlib.util, struct, math
from pathlib import Path
p=Path('.scratch/expanded-garage-v1.4.5')
s=importlib.util.spec_from_file_location('r',p/'replay-observed.py')
r=importlib.util.module_from_spec(s);s.loader.exec_module(r);x=r.x
def step(stage,m,actual,dt):
 u,status,missing,_=x.execute(0x389ce70,m,(x.P,x.A))
 if status!='ok':return 'pack',status,missing,m
 packet=bytes(u.mem_read(x.A,0x100))
 u,status,missing,_=x.execute(0x388a170,m,(x.P,x.A+0x400),inputs=actual,xmm=((x.UC_X86_REG_XMM2,dt),))
 if status!='ok':return 'forces',status,missing,m
 deriv=bytes(u.mem_read(x.A+0x400,0x250)); m=bytes(u.mem_read(x.P,len(m)))
 print(' frame',stage,'v',x.vec(packet,0x40,3),'ang',x.vec(packet,0x50,3),'forces',x.vec(deriv,0,3),'rot.forces',x.vec(deriv,0x10,3),
       'tube',x.vec(deriv,0x20,3),'rate', [x.vec(packet,0x80+i*20,2) for i in range(4)],
       'inputs',[x.vec(deriv,0x4c+i*0x54,3) for i in range(4)])
 u,status,missing,_=x.execute(0x38b2850,m,(x.P,x.A,x.A,x.A+0x400),extras=((0x28,struct.pack('<f',dt)),),
      inputs=(*actual,(x.A,packet),(x.A+0x400,deriv)))
 if status!='ok':return 'integrate',status,missing,m
 result=bytes(u.mem_read(x.A,0x100))
 u,status,missing,_=x.execute(0x38ae7a0,m,(x.P,x.A),inputs=(*actual,(x.A,result)))
 return 'commit',status,missing,bytes(u.mem_read(x.P,len(m)))
for t in (17596525500,58354191800):
 m=r.sample(t)
 actual=[(int(k.stem,16),k.read_bytes()) for k in (p/f'private-inputs-{t}').glob('*.bin')]
 for dt in (0.005,):
  b=m
  for j in range(8):
   kind,status,missing,b=step(j,b,actual,dt)
   if status!='ok' or not all(math.isfinite(struct.unpack_from('<f',b,offset)[0]) for offset in (0x24,0x44,0x1db4,0x3394,0x4974,0x5f54)):break
  print(t,dt,j+1,status,missing,r.fmt(b,0x20),r.fmt(b,0x40),r.fmt(b,0x1db0,2))

