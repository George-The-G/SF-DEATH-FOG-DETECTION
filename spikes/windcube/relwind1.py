import numpy as np
from pathlib import Path
p=Path("C:/Users/George/deathfog-data/scratch/live_static_walls_tracer_newwind.npz")
z=dict(np.load(p,allow_pickle=True))
if "rel_wind_5m_old" not in z: z["rel_wind_5m_old"]=z["rel_wind"].copy()
z["rel_wind"]=np.ones_like(z["rel_wind"])
np.savez(p.with_suffix(".tmp.npz"),**z); p.with_suffix(".tmp.npz").replace(p)
w=np.load(p,allow_pickle=True); r=w["wind_ratio"]*w["rel_wind"]
print("rel_wind all 1:",bool((w["rel_wind"]==1).all()),"street wind p5/50/95",np.round(np.percentile(r,[5,50,95]),3).tolist(),"keys kept:",[k for k in w.files if "old" in k])
