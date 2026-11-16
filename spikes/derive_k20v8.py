"""Derived assets from the k20v8 tracer bands, all in scratch (the live library is not touched).
1. scratch/k20v8_data/{grids (junction), library (fog_<dddd>_<B> = sums of the new bands)}  2. patched static (tracer, tr80 from those; other arrays from live_static_walls.npz)  3. 3D asset + checks."""
import subprocess
import sys
from pathlib import Path

import numpy as np

D = Path.home() / "deathfog-data"
O = D / "scratch" / "library_walls_tracer_k20v8"
W = D / "scratch" / "k20v8_data"
(W / "library").mkdir(parents=True, exist_ok=True)
(W / "live").mkdir(exist_ok=True)
if not (W / "grids").exists():
    subprocess.run(["cmd", "/c", "mklink", "/J", str(W / "grids"), str(D / "grids")], check=True)
OLD_TO_NEW = {0: (0, 1, 2), 1: (3, 4, 5), 2: (6, 7, 8), 3: (9, 10)}
for d in (180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5, 360):
    s = f"{int(round(d * 10)):04d}"
    for B, parts in OLD_TO_NEW.items():
        acc = sum(np.load(O / f"fog3d_{s}_{i:02d}.npz")["c"].astype(np.float32) for i in parts)
        np.savez_compressed(W / "library" / f"fog_{s}_{B}.npz", c=np.clip(acc, 0, 1).astype(np.float16))
    print("4-band sums for", d, flush=True)
