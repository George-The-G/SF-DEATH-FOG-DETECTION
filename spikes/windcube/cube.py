import sys, json, time, numpy as np
from microclimate import wind
H=int(sys.argv[1]) if len(sys.argv)>1 else 20
steps=int(sys.argv[2]) if len(sys.argv)>2 else 12000
omega=float(sys.argv[3]) if len(sys.argv)>3 else 1.95
tag=sys.argv[4] if len(sys.argv)>4 else "base"
nx,ny,nz=10*H,5*H,3*H
s=np.zeros((nx,ny,nz),bool)
x0=3*H; yc=ny//2
s[x0:x0+H, yc-H//2:yc+H//2, 1:1+H]=True   # cube sits on floor k=0
import os
LK='C:/Users/George/deathfog-data/library/runs/gpu.lock'
assert not os.path.exists(LK),'gpu busy'
open(LK,'w').write(str(os.getpid()))
try:
  t=time.time()
  u,d=wind.run(s,270,steps=steps,avg_steps=steps//3,omega=omega)
  print(d, time.time()-t)
finally:
  os.remove(LK)
np.savez_compressed(f"C:/Users/George/deathfog-data/scratch/windcube_{tag}.npz",u=u,solid=s)
