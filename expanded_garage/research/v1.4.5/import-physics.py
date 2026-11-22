import sys
from pathlib import Path
sys.path.insert(0,str(Path('.scratch/expanded-garage-v1.4.4').resolve()))
from native import *
for x in render(0x2c7865c,0x2c786e0):print(x)
print([str(x[:3]) for x in sec if x[2] in range(0x8000000,0x9000000)])
try:
 import pefile
 pe=pefile.PE('game/ForzaHorizon4/ForzaHorizon4.exe',fast_load=True)
 pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
 print([(e.dll.decode(),r.name.decode()) for e in pe.DIRECTORY_ENTRY_IMPORT for r in e.imports if r.address-image_base==0x8821850])
except Exception as e: print(type(e), e)
