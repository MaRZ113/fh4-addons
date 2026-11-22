import importlib.util,struct
from pathlib import Path
p=Path('.scratch/expanded-garage-v1.4.5')
s=importlib.util.spec_from_file_location('r',p/'replay-observed.py')
r=importlib.util.module_from_spec(s);s.loader.exec_module(r);x=r.x
t=58354191800;m=r.sample(t)
inputs=[(int(k.stem,16),k.read_bytes()) for k in (p/f'private-inputs-{t}').glob('*.bin')]
u,status,missing,_=x.execute(0x38b6350,m,(x.P,),inputs=inputs,xmm=((x.UC_X86_REG_XMM1,0.005),(x.UC_X86_REG_XMM2,.005)))
sp=u.reg_read(x.UC_X86_REG_RSP)
print(status,missing)
for o,q in enumerate(struct.iter_unpack('<Q',bytes(u.mem_read(sp,0x300)))):
 if x.B<q[0]<x.B+0x9b10e00: print(hex(o*8),hex(q[0]-x.B))
print('rcx,rdx',hex(u.reg_read(x.UC_X86_REG_RCX)),hex(u.reg_read(x.UC_X86_REG_RDX)))
