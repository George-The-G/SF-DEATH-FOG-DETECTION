import os, sys, numpy as np
from microclimate import windrot, wind
om=float(sys.argv[1]); d=float(sys.argv[2])
LK='C:/Users/George/deathfog-data/library/runs/gpu.lock'
assert not os.path.exists(LK); open(LK,'w').write(str(os.getpid()))
try:
    solid=np.load("C:/Users/George/deathfog-data/grids/solid_20m.npz")["solid"]
    sr,fr=windrot.rotate_solid(solid,d)
    u,diag=wind.run(sr,270.0,steps=3000,avg_steps=500,omega=om,check_every=250,log=print,sea_slip=os.environ.get("SS","1")=="1",box_slip=os.environ.get("BS","0")=="1",outlet=os.environ.get("OUT","eq"))
finally: os.remove(LK)
print("RESULT omega",om,"nan",diag["nan"])
