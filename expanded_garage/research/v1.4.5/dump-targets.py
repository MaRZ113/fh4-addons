import sys
from pathlib import Path
sys.path.insert(0,str(Path('.scratch/expanded-garage-v1.4.4').resolve()))
from native import *
out=Path('.scratch/expanded-garage-v1.4.5')
for a in (0x38621f5,0x3860f7f,0x3861364,0x38613b8,0x3866905,0x38774d2,0x388d6e0,0x3892c70,0x387e7e0,0x38932c0,0x388ad6a0,0x38910e0,0x388e1f2,0x388b6ff,0x388f940):
 s,z=bnd(a); (out/f'native-{s:08x}.txt').write_text('\n'.join(render(s,z))+'\n')

