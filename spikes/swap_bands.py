"""Swap the tracer bands of the live library for the ones regenerated on the walls wind (2026-11-16, George: "swap bands").
1. back up the old fog_* and fog3d_* files of deathfog-data/library into deathfog-data/library_backup_prewalls_tracer (copy, sizes verified);
2. copy the 99 new fog3d_<dddd>_<bb>.npz/.json from deathfog-data/scratch/library_walls_tracer into library/;
3. rewrite the old 4-band fog_<dddd>_<B>.npz (key c, float16) for the 9 west directions as exact sums of the new bands (0-2, 3-5, 6-8, 9-10), with a json that says so.
East-direction fog_* files (22.5 to 135) and *.bak / *.unstable variants are left as they are (backed up). Run with --go to do it; without it only prints the plan."""
import json
import shutil
import sys
from pathlib import Path

import numpy as np

D = Path.home() / "deathfog-data"
LIB, NEW, BAK = D / "library", D / "scratch" / "library_walls_tracer", D / "library_backup_prewalls_tracer"
WEST = [180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5, 360]
OLD_TO_NEW = {0: (0, 1, 2), 1: (3, 4, 5), 2: (6, 7, 8), 3: (9, 10)}
OLD_BANDS = [[0, 150], [150, 300], [300, 500], [500, 800]]
stem = lambda d: f"{int(round(d * 10)):04d}"  # noqa: E731
go = "--go" in sys.argv

old = sorted(p for p in LIB.iterdir() if p.name.startswith(("fog_", "fog3d_")))
new = sorted(NEW.glob("fog3d_*"))
print(f"to back up: {len(old)} files, {sum(p.stat().st_size for p in old) / 1e9:.2f} GB; new fog3d files: {len(new)} ({sum(p.stat().st_size for p in new) / 1e9:.2f} GB)")
if not go:
    sys.exit(0)
BAK.mkdir(exist_ok=True)
for p in old:
    t = BAK / p.name
    if not t.exists():
        shutil.copy2(p, t)
    assert t.stat().st_size == p.stat().st_size, p.name
print("backup done and verified")
for p in new:
    if p.name.endswith(".tmp.npz"):
        continue
    shutil.copy2(p, LIB / p.name)
print("new fog3d files copied")
for d in WEST:
    s = stem(d)
    for B, parts in OLD_TO_NEW.items():
        acc = None
        for i in parts:
            c = np.load(LIB / f"fog3d_{s}_{i:02d}.npz")["c"].astype(np.float32)
            acc = c if acc is None else acc + c
        out = np.clip(acc, 0, 1).astype(np.float16)
        tmp = LIB / f"fog_{s}_{B}.tmp.npz"
        np.savez_compressed(tmp, c=out)
        tmp.replace(LIB / f"fog_{s}_{B}.npz")
        dg = [json.load(open(LIB / f"fog3d_{s}_{i:02d}.json")) for i in parts]
        json.dump(dict(direction_deg=d, band_m=OLD_BANDS[B], derived="sum of the 11-band fog3d fields " + str(list(parts)) + " from the walls wind (2026-11-16); exact by linearity",
                       nan=False, max=float(out.max()), drift_last_2000_steps=max(x["drift_last_2000_steps"] for x in dg),
                       mean_fluid_below_100m=float(sum(x["mean_fluid_below_100m"] for x in dg))), open(LIB / f"fog_{s}_{B}.json", "w"))
    print("4-band rewritten for", d, flush=True)
print("swap done")
