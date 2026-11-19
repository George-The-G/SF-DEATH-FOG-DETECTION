"""Guide 12 step 5: compare the 5 m mean field with the 20 m field on the same area (street cells inside the 2 x 2 km core, 400 m margin dropped).
Speeds are ratios to the inflow speed (both runs use the same uniform inflow). Heights above the local ground: 7.5 m and 12.5 m (the first fluid layer above ground carries no information in XLB, so the guide's 2 m and 10 m become the second and third layers; the 20 m grid is coarser still: it
it cannot represent 2.5 m at all; it is sampled at the same absolute position, nearest cell)."""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from microclimate.paths import data_dir  # noqa: E402

DATA = data_dir()
OUT = DATA / "spikes" / "street5m"
z5 = np.load(OUT / "solid5m.npz")
solid5, ter, bh = z5["solid"], z5["terrain"], z5["bldg_h"]
x0, y0, dx = float(z5["x0"]), float(z5["y0"]), float(z5["dx"])
u5 = np.load(OUT / "mean5m.npz")["u"].astype(np.float32)
sp5 = np.sqrt((u5 ** 2).sum(0))
u20 = np.load(DATA / "library" / "wind_2700.npz")["u"]
spec = json.load(open(DATA / "grids" / "grid_spec.json"))
s20 = np.load(DATA / "grids" / "solid_20m.npz")["solid"]
N = ter.shape[0]
lo, hi = 80, N - 80
ii, jj = np.meshgrid(np.arange(lo, hi), np.arange(lo, hi), indexing="ij")
street = (bh[ii, jj] <= 0) & (ter[ii, jj] > 0.5)
res = {}
x = x0 + (ii + 0.5) * dx
y = y0 + (jj + 0.5) * dx
ci = np.clip(((x - spec["x0"]) // 20).astype(int), 0, spec["nx"] - 1)
cj = np.clip(((y - spec["y0"]) // 20).astype(int), 0, spec["ny"] - 1)
for h in (7.5, 12.5):
    kz5 = np.clip(np.floor((ter[ii, jj] + h) / dx).astype(int), 0, solid5.shape[2] - 1)
    a = sp5[ii, jj, kz5]
    fluid5 = ~solid5[ii, jj, kz5]
    z = ter[ii, jj] + h
    kz20 = np.clip(np.floor(z / 20).astype(int), 0, s20.shape[2] - 1)
    b = np.sqrt((u20[:, ci, cj, kz20].astype(np.float32) ** 2).sum(0))
    solid20 = s20[ci, cj, kz20]
    m = street & fluid5
    d = np.abs(a - b) / np.maximum(b, 0.1)
    res[f"{h} m"] = dict(street_cells=int(m.sum()), mean_speed_5m=float(a[m].mean()), mean_speed_20m=float(b[m].mean()),
                         pct_cells_differing_over_30pct=100.0 * float((d[m] > 0.3).mean()), pct_cells_blocked_in_20m_grid=100.0 * float(solid20[m].mean()),
                         median_ratio_5m_over_20m_where_20m_open=float(np.median(a[m & ~solid20] / np.maximum(b[m & ~solid20], 0.05))),
                         p10_p90_ratio=[float(np.percentile(a[m & ~solid20] / np.maximum(b[m & ~solid20], 0.05), q)) for q in (10, 90)],
                         pct_5m_faster_than_130pct_of_20m=100.0 * float((a[m] > 1.3 * np.maximum(b[m], 0.1)).mean()), pct_5m_slower_than_70pct_of_20m=100.0 * float((a[m] < 0.7 * np.maximum(b[m], 0.1)).mean()))
json.dump(res, open(OUT / "compare.json", "w"), indent=1)
print(json.dumps(res, indent=1))
# picture: speed ratio at 10 m and 2.5 m (5 m run) and the 20 m field at 10 m
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

fig, ax = plt.subplots(1, 3, figsize=(18, 6))
sh = np.zeros((hi - lo, hi - lo))
for axk, (h, name) in zip(ax[:2], ((7.5, "5 m run, 7.5 m above ground"), (12.5, "5 m run, 12.5 m above ground"))):
    kz = np.clip(np.floor((ter[ii, jj] + h) / dx).astype(int), 0, solid5.shape[2] - 1)
    s = np.where(solid5[ii, jj, kz] | (bh[ii, jj] > 0), np.nan, sp5[ii, jj, kz])
    im = axk.imshow(s.T[::-1], vmin=0, vmax=1.2, cmap="viridis")
    axk.set_title(name)
kz20 = np.clip(np.floor((ter[ii, jj] + 12.5) / 20).astype(int), 0, s20.shape[2] - 1)
b20 = np.sqrt((u20[:, ci, cj, kz20].astype(np.float32) ** 2).sum(0))
b20 = np.where(s20[ci, cj, kz20] | (bh[ii, jj] > 0), np.nan, b20)
ax[2].imshow(b20.T[::-1], vmin=0, vmax=1.2, cmap="viridis")
ax[2].set_title("20 m field at 12.5 m above ground, same cells")
fig.colorbar(im, ax=ax, shrink=0.7, label="speed / inflow speed")
fig.savefig(OUT / "compare_speed_maps.png", dpi=70)
print("picture", OUT / "compare_speed_maps.png")
