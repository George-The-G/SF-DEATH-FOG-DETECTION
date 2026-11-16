import numpy as np, os, shutil
from pathlib import Path
L=Path("C:/Users/George/deathfog-data/library_walls")
for d in ("1125","1350"):
    f=L/f"wind_{d}.npz"; raw=L/f"wind_{d}.raw.npz"
    if not raw.exists(): shutil.copy(f,raw)
    u=np.load(raw)["u"].astype(np.float32); sp=np.sqrt((u**2).sum(0)); bad=sp>1.5
    n=int(bad.sum()); solid=(sp==0); good=~bad&~solid
    for i,j,k in np.argwhere(bad):
        sl=(slice(max(i-3,0),i+4),slice(max(j-3,0),j+4),slice(max(k-3,0),k+4)); m=good[sl]
        for a in range(3): u[a,i,j,k]=np.median(u[a][sl][m])
    sp2=np.sqrt((u**2).sum(0)); print(d,"replaced",n,"cells; new max",float(sp2.max()))
    np.savez_compressed(L/f"wind_{d}.tmp.npz",u=u.astype(np.float16)); os.replace(L/f"wind_{d}.tmp.npz",f)
