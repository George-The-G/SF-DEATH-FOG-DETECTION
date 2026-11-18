import json, numpy as np
from pathlib import Path
from pyproj import Transformer
from microclimate import street
from microclimate.live import config as C, stationwind as sw
D=Path("C:/Users/George/deathfog-data"); spec=json.load(open(D/"grids/grid_spec.json")); solid=np.load(D/"grids/solid_20m.npz")["solid"]
nx,ny,nz=solid.shape; s2=solid.copy(); s2[:,:,0]=True; kw=np.minimum(street.first_fluid_k(s2)+1,nz-1)
tr=Transformer.from_crs(4326,32610,always_xy=True)
old=np.load(D/"library/wind_2700.npz")["u"]; new=np.load(D/"scratch/windcube_city2700_v2.npz")["u"]
print("station            old speed  new speed   (ratio of inflow, wind from 270)")
for sid,m in C.STATIONS.items():
    if "wind" not in m["vars"] or m.get("hill"): continue
    x,y=tr.transform(m["lon"],m["lat"])
    if not(spec["x0"]<=x<spec["x0"]+nx*spec["cell_m"] and spec["y0"]<=y<spec["y0"]+ny*spec["cell_m"]): continue
    z=m["elev_m"]+m["sensor_m"] or 10.0
    a=sw.sample(old,[x],[y],[z],spec,kw)[:,0]; b=sw.sample(new,[x],[y],[z],spec,kw)[:,0]
    print(f"{sid:6s} z={z:5.0f} m  {np.hypot(a[0],a[1]):.2f}  {np.hypot(b[0],b[1]):.2f}")
