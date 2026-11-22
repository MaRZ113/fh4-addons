import sys
from pathlib import Path
sys.path.insert(0,str(Path('.scratch/expanded-garage-v1.4.4').resolve()))
from native import *
irva,isz=struct.unpack_from('<II',data,opt+112+8)
def string(r):return disk(r,160).split(b'\0')[0].decode('ascii','replace')
def read_iat(f):
 data_iat={}
 for row in struct.iter_unpack('<5I',disk(irva,isz//20*20)):
  ilt,_,_,name,iat=row
  if iat==0:break
  while q:=struct.unpack('<Q',disk(ilt,8))[0]:
   if q>>63==0: data_iat[iat]=(string(name),string(q+2))
   ilt+=8;iat+=8
 return data_iat.get(f)
for a in (0x8821248,0x8821850,0x88218f0,0x8821880,0x8821870):
 print(hex(a),read_iat(a))


