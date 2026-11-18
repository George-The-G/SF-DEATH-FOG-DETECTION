"""One direction with an explicit omega (used for 135, which blows up at omega 1.95): python one_dir.py <deg> <omega>"""
import os, sys, json, time, numpy as np
from pathlib import Path
from microclimate import windrot, wind
d=float(sys.argv[1]); om=float(sys.argv[2])
D=Path("C:/Users/George/deathfog-data"); OUT=D/"library_walls"; LK=D/"library/runs/gpu.lock"
assert not LK.exists(); LK.write_text(str(os.getpid()))
try:
    solid=np.load(D/"grids/solid_20m.npz")["solid"]
    sr,fr=windrot.rotate_solid(solid,d)
    t0=time.time(); u_rot,diag=wind.run(sr,270.0,steps=40000,avg_steps=8000,omega=om,log=print)
finally: LK.unlink(missing_ok=True)
diag["direction_deg"]=d; diag["omega"]=om
st=f"wind_{int(round(d*10)):04d}"
if u_rot is None: print("FAILED",diag); json.dump(diag,open(OUT/f"{st}.json","w")); sys.exit(1)
u=windrot.unrotate_field(u_rot,solid,d,fr)
sp=np.sqrt((u.astype(np.float32)**2).sum(0)); diag["max_speed_ratio"]=float(sp.max())
diag["mean_speed_ratio_near_ground"]=wind.near_ground_mean(sp,np.concatenate([np.ones(solid.shape[:2]+(1,),bool),solid[:,:,1:]],2))
diag["method"]="rotated-frame wind.run at 270, omega %s"%om
json.dump(diag,open(OUT/f"{st}.json","w")); np.savez_compressed(OUT/f"{st}.tmp.npz",u=u); os.replace(OUT/f"{st}.tmp.npz",OUT/f"{st}.npz"); print("DONE",json.dumps(diag))
