"""Patch ONLY the tracer-derived arrays (tracer [9, N], tr80 [9, H, W]) of live_static.npz from the new (walls wind) library bands; every other array is copied unchanged
(wind_ratio, rel_wind and refine_corr still come from the old wind: they need the street-feature rebuild of the wind thread). Writes a NEW file; the live file is not touched.
usage: python spikes/patch_static.py [out.npz] [data dir] [base static.npz]   (default deathfog-data/scratch/live_static_walls_tracer.npz)"""
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from microclimate import fogtop, replay  # noqa: E402

D = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.home() / "deathfog-data"      # data dir whose library\ holds the fog_<d>_<B> files to use
out = Path(sys.argv[1]) if len(sys.argv) > 1 else D / "scratch" / "live_static_walls_tracer.npz"
base = Path(sys.argv[3]) if len(sys.argv) > 3 else D / "live" / "live_static.npz"   # the static file whose other arrays are copied
z = np.load(base, allow_pickle=False)
S = {k: z[k] for k in z.files}
dirs = [float(d) for d in S["dirs"]]
pts = dict(x=S["x"].astype(float), y=S["y"].astype(float), z=S["z"].astype(float))
t0 = time.time()
tracer = np.zeros_like(S["tracer"])
tr80 = np.zeros_like(S["tr80"])
for k, d in enumerate(dirs):
    tracer[k] = fogtop.points_tracer(pts, D, d)
    tr80[k] = replay.city_tracer_80m(D, d)
    for key in [kk for kk in pts if kk.startswith("_tracer_profile")]:
        pts.pop(key)
    print(f"dir {d}: tracer mean {S['tracer'][k].mean():.3f} -> {tracer[k].mean():.3f}; tr80 mean {S['tr80'][k].mean():.3f} -> {tr80[k].mean():.3f} ({time.time() - t0:.0f} s)", flush=True)
S["tracer"], S["tr80"] = tracer.astype(S["tracer"].dtype), tr80.astype(S["tr80"].dtype)
np.savez_compressed(out, **S)
print("wrote", out)
