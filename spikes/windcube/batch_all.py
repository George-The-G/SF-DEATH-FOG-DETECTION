"""Recompute every wind direction with working walls into library_walls (library untouched). Resumable, one at a time, shared GPU lock."""
import os, sys, json, time, shutil, numpy as np
from pathlib import Path
from microclimate import windrot
D=Path("C:/Users/George/deathfog-data"); OUT=D/"library_walls"; LK=D/"library/runs/gpu.lock"
dirs=[270.0,247.5,292.5,225.0,315.0,202.5,337.5,180.0,360.0,22.5,45.0,67.5,90.0,112.5,135.0,
      ]
solid=np.load(D/"grids/solid_20m.npz")["solid"]
log=open(OUT/"batch.log","a",buffering=1)
def L(s): log.write(time.strftime("%H:%M:%S ")+str(s)+"\n")
for d in dirs:
    st=f"wind_{int(round(d*10)):04d}"; f=OUT/f"{st}.npz"
    if f.exists(): continue
    if d==270.0 and (D/"scratch/windcube_city2700_v2.npz").exists():
        shutil.copy(D/"scratch/windcube_city2700_v2.npz",f); shutil.copy(D/"scratch/windcube_city2700_v2.json",OUT/f"{st}.json"); L("270 copied"); continue
    while LK.exists(): L("gpu busy, waiting"); time.sleep(120)
    LK.write_text(str(os.getpid()))
    try:
        u,diag=windrot.run_direction(solid,d,steps=40000,avg_steps=8000,log=L)
    except Exception as e:
        L(f"{d} EXC {e!r}"); u,diag=None,{"nan":True}
    finally:
        LK.unlink(missing_ok=True)
    json.dump(diag,open(OUT/f"{st}.json","w"))
    if u is None or diag.get("nan"): L(f"{d} FAILED {diag}"); continue
    np.savez_compressed(OUT/f"{st}.tmp.npz",u=u); os.replace(OUT/f"{st}.tmp.npz",f); L(f"{d} done {json.dumps(diag)}")
L("ALL DONE")
