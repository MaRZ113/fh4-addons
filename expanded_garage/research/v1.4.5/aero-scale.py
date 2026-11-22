import sys,struct
from pathlib import Path
sys.path.insert(0,str(Path('.scratch/expanded-garage-v1.4.4').resolve()))
from native import *
for a,fmt in ((0x38a8f53+0x22beeb5,'d'),(0x38a8f62+0x22b9b06,'f')):
 print(hex(a),disk(a,struct.calcsize(fmt)).hex(),struct.unpack('<'+fmt,disk(a,struct.calcsize(fmt)))[0])
a=struct.unpack('<f',disk(0x38a8f62+0x22b9b06,4))[0]
b=struct.unpack('<d',disk(0x38a8f53+0x22beeb5,8))[0]
f=lambda q:struct.unpack('<f',struct.pack('<f',q))[0]
print(f(f(f(3.4028234663852886e38)*a)*b))
