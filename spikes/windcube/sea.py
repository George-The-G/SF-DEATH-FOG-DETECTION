import json, numpy as np
from pathlib import Path
D=Path("C:/Users/George/deathfog-data"); spec=json.load(open(D/"grids/grid_spec.json")); solid=np.load(D/"grids/solid_20m.npz")["solid"]
new=np.load(D/"library_walls/wind_2700.npz")["u"].astype(np.float32); old=np.load(D/"library/wind_2700.npz")["u"].astype(np.float32)
nx,ny,nz=solid.shape; print(solid.shape,spec["cell_m"])
sea=(solid[:,:,1:4].sum(2)==0)   # open columns: no terrain in the lowest layers
for xi in (20,60,150,300):
    cols=sea[xi,:]; 
    print(f"x={xi} (west edge +{xi*20/1000:.1f} km): open-sea columns {cols.sum()}")
    for k in (1,2,3,5,10,20):
        s=lambda f:np.sqrt((f[:2,xi,cols,k]**2).sum(0)).mean()
        print(f"   z={(k+.5)*20:4.0f} m  old {s(old):.2f} new {s(new):.2f}")
