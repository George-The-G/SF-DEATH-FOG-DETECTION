"""Guide 12 step 6: derived viewer maps on a 10 m grid, 6 levels above ground (2.5, 7.5, 15, 25, 40, 60 m): flow vector (2 x int8, ratio of inflow speed, scale 1/64) and shelter factor
(local speed / open-terrain speed at the same height, uint8 0..255; open-terrain speed = the inflow speed, 1.0). Over the 2 x 2 km core (400 m margin dropped): 200 x 200 x 6 x 3 bytes."""
import json
import sys
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from microclimate.paths import data_dir  # noqa: E402

DATA = data_dir()
OUT = DATA / "spikes" / "street5m"
z = np.load(OUT / "solid5m.npz")
solid, ter = z["solid"], z["terrain"]
u = np.load(OUT / "mean5m.npz")["u"].astype(np.float32)
dx, N = 5.0, ter.shape[0]
lo, hi = 80, N - 80
H = (2.5, 7.5, 15.0, 25.0, 40.0, 60.0)
n10 = (hi - lo) // 2
flow = np.zeros((6, n10, n10, 2), np.int8)
shel = np.zeros((6, n10, n10), np.uint8)
for li, h in enumerate(H):
    k = np.clip(np.floor((ter[lo:hi, lo:hi] + h) / dx).astype(np.int64), 0, solid.shape[2] - 1)
    ii, jj = np.meshgrid(np.arange(lo, hi), np.arange(lo, hi), indexing="ij")
    ux, uy = u[0][ii, jj, k], u[1][ii, jj, k]
    fl = ~solid[ii, jj, k]
    ux, uy = np.where(fl, ux, 0.0), np.where(fl, uy, 0.0)
    b = lambda a: a.reshape(n10, 2, n10, 2).mean(axis=(1, 3))  # noqa: E731
    fx, fy = b(ux), b(uy)
    flow[li, :, :, 0] = np.clip(np.rint(fx * 64), -127, 127)
    flow[li, :, :, 1] = np.clip(np.rint(fy * 64), -127, 127)
    sp = np.sqrt(fx ** 2 + fy ** 2)
    shel[li] = np.clip(np.rint(255 * np.clip(sp, 0, 1)), 0, 255)
raw = flow.tobytes() + shel.tobytes()
comp = len(zlib.compress(raw, 9))
rep = dict(grid=[n10, n10], levels_above_ground_m=list(H), raw_bytes=len(raw), compressed_bytes=comp, per_tile_6_directions_compressed_mb=round(6 * comp / 1e6, 2),
           ten_neighbourhoods_6_directions_compressed_mb=round(60 * comp / 1e6, 1))
json.dump(rep, open(OUT / "derived_report.json", "w"), indent=1)
np.savez_compressed(OUT / "derived_270.npz", flow=flow, shelter=shel, levels=np.array(H))
print(json.dumps(rep, indent=1))
