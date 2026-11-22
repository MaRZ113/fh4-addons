import sys, math, os, ctypes
from pathlib import Path
sys.path.insert(0,str(Path('.scratch/expanded-garage-v1.4.4').resolve()))
from native import *
sys.path.insert(0,str(Path.cwd()))
from unicorn import Uc, UcError, UC_ARCH_X86, UC_MODE_64, UC_HOOK_MEM_UNMAPPED, UC_HOOK_CODE
from unicorn.x86_const import *
B=0x7ff602820000; P=0x227190b3010
A=0x7000000000; STACK=0x6000000000; STOP=0x6000100000
FRAME=0xb100
from expanded_garage.research.inspect_megatexture_faults import MiniDump
dump_file=Path(os.environ['LOCALAPPDATA'])/'CrashDumps'/'ForzaHorizon4.exe.30072.dmp'
dump=MiniDump(dump_file.read_bytes(),Path('game/ForzaHorizon4/ForzaHorizon4.exe').read_bytes())
assert dump.base==B
DUMP_PAGES={}
for begin,length,position in dump.ranges:
 for page_start in range(begin & ~0xfff,begin+length,0x1000):
  lo=max(begin,page_start); hi=min(begin+length,page_start+0x1000)
  DUMP_PAGES.setdefault(page_start,[]).append((lo,hi,position+lo-begin))
def sample(n, i=-1):
 b=(root/f'private-focused-{n}.bin').read_bytes()
 k=len(b)+i*FRAME
 return b[k:k+FRAME]

def imported_names():
 rva,length=struct.unpack_from('<II',data,opt+120)
 names={}
 for lookup,_,_,dll,iat in struct.iter_unpack('<5I',disk(rva,length//20*20)):
  if not iat:break
  while q:=struct.unpack('<Q',disk(lookup,8))[0]:
   if q>>63==0:names[iat]=disk(q+2,128).partition(b'\0')[0].decode('ascii',errors='replace')
   lookup+=8;iat+=8
 return names
NAMES=imported_names(); UCRT=ctypes.CDLL('ucrtbase.dll')

def execute(a, blob, args, extras=(), xmm=(), inputs=(), constants=(), visited=None):
 u=Uc(UC_ARCH_X86,UC_MODE_64); mapped=set(); accesses=[]; coverage=[]
 current=0
 def put(start, raw):
  start_page=start & ~0xfff; last_page=(start+len(raw)+0xfff)&~0xfff
  for p in range(start_page,last_page,0x1000):
   if p not in mapped: u.mem_map(p,0x1000); mapped.add(p)
  u.mem_write(start,raw)
 def page(_u, access, ad, size, value, _):
  s=ad&~0xfff; r=s-B
  data=code(r,0x1000)
  if data:
   put(s,data); return True
  for name,v,sa,b,o,*rest in sec:
   if name.rstrip(b'\0') in (b'.rdata',b'.pdata') and sa<=r<sa+b:
    data=disk(r,0x1000)
    if len(data)==0x1000:
     put(s,data);return True
  if any(lo<=ad and ad+size<=hi for lo,hi,_ in DUMP_PAGES.get(s,())):
   for lo,hi,fileoff in DUMP_PAGES[s]:put(lo,dump.raw[fileoff:fileoff+hi-lo])
   return True
  accesses.append((hex(current-B),hex(ad),size))
  return False
 def step(_u, at, z, _):
  nonlocal current
  current=at
  if at==B+0x2c76c20:
   # The emulator already maps an explicit stack. The target helper probes
   # Windows guard pages and preserves the allocation size passed in RAX.
   sp=u.reg_read(UC_X86_REG_RSP)
   ret,=struct.unpack('<Q',u.mem_read(sp,8))
   u.reg_write(UC_X86_REG_RSP,sp+8)
   u.reg_write(UC_X86_REG_RIP,ret)
   return
  if z==6 and (head:=code(at-B,6))[:2] in (b'\xff\x25',b'\xff\x15'):
   name=NAMES.get(at-B+6+struct.unpack_from('<i',head,2)[0])
   done=False
   if name in ('cosf','sinf','atanf','atan2f','expf','logf','acosf','asinf','powf','fmodf','tanf','sqrtf'):
    f=getattr(UCRT,name); f.restype=ctypes.c_float
    n=2 if name in ('atan2f','powf','fmodf') else 1
    f.argtypes=[ctypes.c_float]*n
    vals=[struct.unpack('<f',struct.pack('<I',u.reg_read(r)&0xffffffff))[0] for r in (UC_X86_REG_XMM0,UC_X86_REG_XMM1)][:n]
    ans=f(*vals)
    u.reg_write(UC_X86_REG_XMM0,struct.unpack('<I',struct.pack('<f',ans))[0]); done=True
   elif name in ('_finite','_isnan'):
    number=struct.unpack('<d',struct.pack('<Q',u.reg_read(UC_X86_REG_XMM0)&0xffffffffffffffff))[0]
    u.reg_write(UC_X86_REG_RAX,int(math.isfinite(number) if name=='_finite' else math.isnan(number))); done=True
   elif name in ('memset','memcpy','memmove'):
    dest=u.reg_read(UC_X86_REG_RCX); source=u.reg_read(UC_X86_REG_RDX)
    n=u.reg_read(UC_X86_REG_R8)
    if n>2**20:raise ValueError('Excessively large replay transfer')
    u.mem_write(dest, bytes([source&0xff])*n if name=='memset' else bytes(u.mem_read(source,n)))
    u.reg_write(UC_X86_REG_RAX,dest); done=True
   if done and head[:2]==b'\xff\x15':
    u.reg_write(UC_X86_REG_RIP,at+z)
   elif done:
    sp=u.reg_read(UC_X86_REG_RSP)
    ret,=struct.unpack('<Q',u.mem_read(sp,8))
    u.reg_write(UC_X86_REG_RSP,sp+8)
    u.reg_write(UC_X86_REG_RIP,ret)
  if visited is not None:
   val=visited.get(at-B)
   if val:
    keys=(0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15)
    regs={j:struct.unpack('<4f',u.reg_read(UC_X86_REG_XMM0+j).to_bytes(16,'little')) for j in keys}
    sp=u.reg_read(UC_X86_REG_RSP)
    stack={}
    try:
     stack={k:struct.unpack('<4f',u.mem_read(sp+k,16)) for k in (0x30,0x60,0x70,0x80,0x90)}
    except UcError:pass
    coverage.append((hex(at-B),val,regs,stack))
 put(P,blob)
 put(A,bytes(0x3000))
 put(STACK,bytes(0x50000))
 for addr,raw in inputs:
  put(addr,raw)
 for addr,raw in constants:
  put(B+addr,raw)
 sp=STACK+0x45008
 put(sp,struct.pack('<Q',STOP))
 for i,g in enumerate((UC_X86_REG_RCX,UC_X86_REG_RDX,UC_X86_REG_R8,UC_X86_REG_R9)):
  if i<len(args):u.reg_write(g,args[i])
 for off,s in extras: put(sp+off,s)
 for reg,num in xmm:u.reg_write(reg,struct.unpack('<I',struct.pack('<f',num))[0])
 u.reg_write(UC_X86_REG_RSP,sp)
 u.hook_add(UC_HOOK_MEM_UNMAPPED,page)
 u.hook_add(UC_HOOK_CODE,step)
 try: u.emu_start(B+a,STOP,count=120_000); status='ok' if u.reg_read(UC_X86_REG_RIP)==STOP else 'instruction limit'
 except UcError as e: status=str(e)
 return u,status,accesses,coverage

def vec(m,k,l=4):
 return [float(v) for v in struct.unpack_from('<'+'f'*l,m,k)]
def words(m,k,l=4): return struct.unpack_from('<'+'I'*l,m,k)
watch={v:k for v,k in [(0x38b2c43,'linear velocity'),(0x38b2e77,'wheel-relative rate'),(0x38b2f07,'wheel coordinate'),(0x3890c8e,'wheel divisor'),(0x3890a4b,'bumpstop')]}
if __name__=='__main__':
 for title,m in [('Honda',sample(1211800)),('Suzuki-staging',sample(29620439200,-2)),('Suzuki-placed',sample(29644310400,-2))]:
  print(title)
  for a,args,extras,xmm in (
      (0x389ce70,(P,A),(),()),(0x3890620,(P,A+0x200,A+0x220,A+0x600),((0x28,struct.pack('<Q',P+0x1db0)),),()),
      (0x388ba80,(P,A+0x200,A+0x220,P+0x1db0),((0x28,struct.pack('<f',0.005)),),()),
      (0x388a170,(P,A+0x600),(),((UC_X86_REG_XMM2,0.005),))):
   u,status,missing,trace=execute(a,m,args,extras,xmm,visited=watch)
   b=bytes(u.mem_read(A,0x1000))
   print(hex(a),status,missing,trace,vec(b,0),vec(b,0x200),vec(b,0x220),vec(b,0x600),vec(b,0x620))







