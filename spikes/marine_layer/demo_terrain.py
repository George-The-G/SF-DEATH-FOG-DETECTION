"""Guide 11 spike: cost and sensitivity on the REAL terrain (guide 09 wide grid, 400 m, block-averaged to 40 x 35 km at 400 m here; the guide's 200 m grid is run too for cost).
NOT a scored case: forcing and inflow are fixed illustrative values (offshore h 300 m, dTheta 6 K, west wind 7 m/s), no satellite or sounding comparison.
Writes the depth map and flow arrows picture and prints a table."""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[2]))
import swe  # noqa: E402
from microclimate import wide  # noqa: E402

OUT = Path(r"C:/Users/George/source/repos/SF-DEATH-FOG-DETECTION/data\scratch")
data = Path("site/v3/data/terrain_wide.bin").read_bytes()
spec, H = wide.decode(data)
# domain 40 x 35 km around the Golden Gate (UTM 10N): x 530000..570000, y 4165000..4200000 (west edge in the ocean)
X0, Y0, NXm, NYm = 530000.0, 4200000.0, 40000.0, 35000.0


def terrain(cell):
    nx, ny = int(NXm / cell), int(NYm / cell)
    c0, r0 = (X0 - spec["x0"]) / spec["cell"], (spec["y0"] - Y0) / spec["cell"]
    xs = (c0 + (np.arange(nx) + 0.5) * cell / spec["cell"]) - 0.5
    ys = (r0 + (np.arange(ny) + 0.5) * cell / spec["cell"]) - 0.5
    from scipy.ndimage import map_coordinates
    gx, gy = np.meshgrid(xs, ys)
    return map_coordinates(H, [gy, gx], order=1, mode="nearest")


def run(cell=400.0, dtheta=6.0, h_in=300.0, u_in=7.0, hours=2.5, steps_cap=None):
    b = terrain(cell)
    gp = swe.reduced_gravity(dtheta)
    h = np.maximum(h_in - b, 0.0)
    cd = np.where(b > 0.5, 0.005, 0.0015)
    # a pressure-gradient acceleration that balances the water drag of the open-ocean flow: a = Cd u^2 / h
    fx = 0.0015 * u_in ** 2 / h_in
    m = swe.Model(b, h, cell, gp, cd=cd, bc=dict(west=("inflow", h_in, u_in, 0.0), east="open", north="open", south="open"), forcing=(fx, 0.0))
    m.hu[:] = np.where(h > 0.5, h * u_in, 0.0)
    t0 = time.time()
    m.run(t_end=hours * 3600.0, steps=steps_cap)
    return m, time.time() - t0, b


def summary(m, b):
    land = b > 0.5
    top = b + m.h
    wet_land = land & (m.h > 5.0)
    return dict(layer_over_land_pct=100.0 * float(wet_land.sum() / max(land.sum(), 1)), mean_depth_over_land_m=float(m.h[land].mean()), max_top_m=float(top.max()))


if __name__ == "__main__":
    rows = []
    m, secs, b = run(400.0)
    base = summary(m, b)
    rows.append(("base 400 m (dTheta 6 K, h 300 m, 2.5 h)", secs, m.steps, base))
    for name, kw in (("dTheta 4 K", dict(dtheta=4.0)), ("dTheta 8 K", dict(dtheta=8.0)), ("offshore h 250 m", dict(h_in=250.0)), ("offshore h 350 m", dict(h_in=350.0))):
        mm, s, bb = run(400.0, **kw)
        rows.append((name, s, mm.steps, summary(mm, bb)))
    m2, s2, b2 = run(200.0)
    rows.append(("200 m grid (the guide's) 2.5 h", s2, m2.steps, summary(m2, b2)))
    for name, s, n, d in rows:
        print(f"{name:42s} {s:6.1f} s {n:6d} steps  layer over land {d['layer_over_land_pct']:5.1f} %  mean depth over land {d['mean_depth_over_land_m']:6.1f} m  top max {d['max_top_m']:6.1f} m")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    u, v = m.velocity()
    fig, ax = plt.subplots(1, 2, figsize=(14, 6))
    im = ax[0].imshow(np.where(b > 0.5, np.nan, 0), cmap="Blues", vmin=0, vmax=1)
    ax[0].imshow(np.where(b > 0.5, b, np.nan), cmap="Greys", alpha=0.5)
    d = ax[0].imshow(np.where(m.h > 1, m.h, np.nan), cmap="viridis", alpha=0.8)
    plt.colorbar(d, ax=ax[0], label="layer depth h (m)")
    k = 6
    ax[0].quiver(np.arange(0, b.shape[1], k), np.arange(0, b.shape[0], k), u[::k, ::k], -v[::k, ::k], color="w", scale=200, width=0.002)
    ax[0].set_title("Layer depth and flow after 2.5 h (400 m grid, illustrative forcing)")
    t = ax[1].imshow(np.where(m.h > 1, b + m.h, np.nan), cmap="magma")
    plt.colorbar(t, ax=ax[1], label="top b + h (m)")
    ax[1].set_title("Layer top (m above sea level)")
    fig.tight_layout()
    fig.savefig(OUT / "marine_layer_demo.png", dpi=80)
    print("picture:", OUT / "marine_layer_demo.png")
