"""Rebuild wind_ratio in a copy of the tracer thread's live_static_walls_tracer.npz from the NEW library (same formula as street.build /
city.wind_refine). refine_corr and rel_wind are NOT recomputed (they come from refined/patch_corr_2700.npz, not from the library)."""
import json, numpy as np
from pathlib import Path
from microclimate import street
D=Path("C:/Users/George/deathfog-data"); spec=json.load(open(D/"grids/grid_spec.json")); solid=np.load(D/"grids/solid_20m.npz")["solid"]
nx,ny,nz=solid.shape; sw=solid.copy(); sw[:,:,0]=True; kw=np.minimum(street.first_fluid_k(sw)+1,nz-1)
ii,jj=np.meshgrid(np.arange(nx),np.arange(ny),indexing="ij")
u=np.load(D/"library/wind_2700.npz")["u"]; sp=np.sqrt(sum(u[a][ii,jj,kw].astype(np.float32)**2 for a in range(3))); del u
z=dict(np.load(D/"scratch/live_static_walls_tracer.npz",allow_pickle=True))
new=street.bilinear_columns(sp,z["x"],z["y"],spec,np.ones((nx,ny),bool)).astype(np.float32)
old=z["wind_ratio"].copy(); z["wind_ratio_old_nowalls"]=old; z["wind_ratio"]=new
out=D/"scratch/live_static_walls_tracer_newwind.npz"; np.savez(out,**z)
p=lambda a:np.round(np.nanpercentile(a,[5,50,95]),3).tolist()
print("rows",len(new),"finite",float(np.isfinite(new).mean()),"wind_ratio p5/50/95 old",p(old),"new",p(new)); print("rel_wind unchanged p5/50/95",p(z["rel_wind"]),"refine_corr",p(z["refine_corr"]))
print("combined wind_ratio*rel_wind old",p(old*z["rel_wind"]),"new",p(new*z["rel_wind"]),"new without rel_wind",p(new))
print(out, out.stat().st_size)
