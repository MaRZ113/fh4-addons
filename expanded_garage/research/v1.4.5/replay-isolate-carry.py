import importlib.util, struct, json
from pathlib import Path
p=Path('.scratch/expanded-garage-v1.4.5')
s=importlib.util.spec_from_file_location('r',p/'replay-observed.py')
r=importlib.util.module_from_spec(s);s.loader.exec_module(r)
x=r.x
for i in r.rows:
 if i.get('event')!='transition' or not i.get('scene') or i['scene']['scene_state']!=8:continue
 t=i['ns']
 if not (p/f'private-inputs-{t}').is_dir():continue
 if t!=58532095900:continue
 for idx in (-1,1,2,3):
  m=bytearray(r.sample(t,-1))
  if idx in (1,3):
   for j in range(4):struct.pack_into('<I',m,0x1db4+j*0x15e0,0)
  if idx in (2,3):
   struct.pack_into("<4I",m,0x10ed0,0,0,0,0)
  m=bytes(m)
  inputs=[(int(k.stem,16),k.read_bytes()) for k in (p/f'private-inputs-{t}').glob('*.bin')]
  u,status,missing,_=x.execute(0x389ce70,m,(x.P,x.A))
  if status!='ok': print('state failure',status,missing);continue
  packet=bytes(u.mem_read(x.A,0x100))
  u,status,missing,_=x.execute(0x388a170,m,(x.P,x.A+0x400),inputs=inputs,xmm=((x.UC_X86_REG_XMM2,0.005),))
  if status!='ok':print('derivatives failure',status,missing);continue
  derivatives=bytes(u.mem_read(x.A+0x400,0x250))
  updated=bytes(u.mem_read(x.P,len(m)))
  u,status,missing,trace=x.execute(0x38b2850,updated,(x.P,x.A,x.A,x.A+0x400),
   extras=((0x28,struct.pack('<f',0.005)),),inputs=(*inputs,(x.A,packet),(x.A+0x400,derivatives)),visited=x.watch)
  out=bytes(u.mem_read(x.A,0x100))
  print(i['scene']['car_id'], t,idx,status,missing)
  print(' wheel rates',r.fmt(packet,0x80,1),r.fmt(derivatives,0x50,1),[r.fmt(out,0x7c+j*20,2) for j in range(4)])
  print(' chassis vel',r.fmt(out,0x40),'pos',r.fmt(out,0),'ang',r.fmt(out,0x60))
  if status!='ok':continue
  u,status,missing,trace=x.execute(0x38ae7a0,updated,(x.P,x.A),inputs=(*inputs,(x.A,out)),visited=x.watch)
  committed=bytes(u.mem_read(x.P,len(m)))
  print(' commit',status,missing,r.fmt(committed,0x20),r.fmt(committed,0x40),[r.fmt(committed,0x1db0+j*0x15e0,2) for j in range(4)])


