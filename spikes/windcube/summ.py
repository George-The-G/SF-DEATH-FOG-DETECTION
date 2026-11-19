import numpy as np, json, glob, os
solid=np.load("C:/Users/George/source/repos/SF-DEATH-FOG-DETECTION/data/grids/solid_20m.npz")["solid"]
print("dir   max   cells>1.5  (where, if any)   near-ground mean   solid cells exactly 0   nan")
for f in sorted(glob.glob("C:/Users/George/source/repos/SF-DEATH-FOG-DETECTION/data/library_walls/wind_*[0-9].npz")):
    d=int(os.path.basename(f)[5:9])/10; u=np.load(f)["u"].astype(np.float32); sp=np.sqrt((u**2).sum(0)); j=json.load(open(f[:-4]+".json"))
    i=np.argwhere(sp>1.5); where=f"x{i[:,0].min()}-{i[:,0].max()} y{i[:,1].min()}-{i[:,1].max()} k{i[:,2].min()}-{i[:,2].max()}" if len(i) else "-"
    print(f"{d:6.1f} {sp.max():.2f} {len(i):6d}  {where:28s} {j.get('mean_speed_ratio_near_ground',0):.3f}  {bool((sp[solid]==0).all())}  {bool(np.isnan(u).any())}")
