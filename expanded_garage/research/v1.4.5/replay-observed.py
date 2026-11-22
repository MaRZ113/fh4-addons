import json, importlib.util, struct, math
from pathlib import Path
p=Path('.scratch/expanded-garage-v1.4.5')
spec=importlib.util.spec_from_file_location('replay',p/'replay-wheel-state.py')
x=importlib.util.module_from_spec(spec);spec.loader.exec_module(x)
log=p/'focused.jsonl'
rows=[json.loads(line) for line in log.read_text().splitlines()]
x.P=int(rows[0]['address'],16); x.B=int(rows[0]['base'],16)
x.DUMP_PAGES.clear()
x.dump=x.MiniDump((Path(x.os.environ['LOCALAPPDATA'])/'CrashDumps'/'ForzaHorizon4.exe.19876.dmp').read_bytes(),x.data)
assert x.dump.base==x.B
for begin,length,position in x.dump.ranges:
 for page_start in range(begin & ~0xfff,begin+length,0x1000):
  lo=max(begin,page_start); hi=min(begin+length,page_start+0x1000)
  x.DUMP_PAGES.setdefault(page_start,[]).append((lo,hi,position+lo-begin))
# Apply image-base relocations to immutable pages loaded from the local PE.
disk=x.disk
reloc_rva,reloc_size=struct.unpack_from('<II',x.data,x.opt+112+5*8)
reloc_data=disk(reloc_rva,reloc_size)
relocs={}
i=0
while i+8<=len(reloc_data):
 page,size=struct.unpack_from('<II',reloc_data,i)
 if size<8:break
 for j in range(i+8,i+size,2):
  word,=struct.unpack_from('<H',reloc_data,j)
  if word>>12==10: relocs.setdefault(page,[]).append(word&0xfff)
 i+=size
def disk_relocated(a,n):
 raw=disk(a,n)
 if a%0x1000 or len(raw)!=0x1000 or a not in relocs:return raw
 b=bytearray(raw)
 for at in relocs[a]:
  if at+8<=n:
   q,=struct.unpack_from('<Q',b,at)
   struct.pack_into('<Q',b,at,q+x.B-x.image_base)
 return bytes(b)
x.disk=disk_relocated

def sample(t,index=-1):
 body=(p/f'private-focused-{t}.bin').read_bytes()
 stride=rows[0]['block_size']
 index=len(body)+index*stride
 return body[index:index+stride]
def fmt(b,a,n=4): return [f'{j:08x}' for j in x.words(b,a,n)]
def cases(t,index,name,global_stamp=None):
 m=sample(t,index)
 actual=[(int(i.stem,16),i.read_bytes()) for i in (p/f'private-inputs-{global_stamp or t}').glob('*.bin')]
 print(name, len(actual),'segments',fmt(m,0x20),fmt(m,0x40),flush=True)
 for k in range(4):
  q=x.P+0x1db0+k*0x15e0
  for a,args,extra in ((0x3890620,(x.P,x.A+0x200,x.A+0x220,x.A+0x600),struct.pack('<Q',q)),
       (0x388ba80,(x.P,x.A+0x200,x.A+0x220,q),struct.pack('<f',0.005))):
   u,status,missing,trace=x.execute(a,m,args,extras=((0x28,extra),),inputs=actual,visited=x.watch)
   b=bytes(u.mem_read(x.A,0x1000)); base=0x650+k*0x54
   print(k,hex(a),status,missing,fmt(b,0x200),fmt(b,0x220),fmt(b,base,2),trace)
 for a,args,spec in ((0x388b530,(x.P,x.A+0x600),'forces'),(0x388a170,(x.P,x.A+0x600),'derivatives')):
  u,status,missing,trace=x.execute(a,m,args,inputs=actual,xmm=((x.UC_X86_REG_XMM2,0.005),),visited=x.watch)
  b=bytes(u.mem_read(x.A,0x1000))
  outs=[fmt(b,0x64c+k*0x54,2) for k in range(4)]
  print(spec,status,missing,fmt(b,0x600),fmt(b,0x610),fmt(b,0x620),outs,trace)
if __name__=='__main__':
 for q in rows:
  if q.get('event')!='transition' or not q.get('scene') or q['scene']['scene_state'] not in (2,8):continue
  t=q['ns']
  if not (p/f'private-inputs-{t}').is_dir():continue
  car=q['scene']['car_id']
  print(q['scene'],t)
  cases(t,-1,car)
  if q['state']['position']['y_class']=='nan' and q['history']:
   cases(t,-2,f'{car} preceding')


