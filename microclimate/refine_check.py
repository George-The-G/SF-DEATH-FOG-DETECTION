"""Validation helpers: resolution convergence on one tile and a PNG map of the correction. Usage:
python -m microclimate.refine_check conv <patch_index> <steps> <dx1,dx2,...> [omega]
python -m microclimate.refine_check map 270 <dx>
"""
import json
import sys
from pathlib import Path

import numpy as np

from . import refine as R
from .config import load


def conv(pidx, steps, dxs, omega=1.95):
    cfg = load()
    spec, ter, mh, solid_c = R.load_grids(R.DATA / "grids")
    u_c = np.load(R.DATA / "library" / "wind_2700.npz")["u"]
    ref = R.coarse_reference(u_c, solid_c)
    p = R.plan_patches(cfg, R.DATA / "grids")[pidx]
    out = R.DATA / "refined" / "validation"
    out.mkdir(parents=True, exist_ok=True)
    res = {}
    for dx in dxs:
        r, d, _ = R.run_patch(spec, p, dx, 270, u_c, solid_c, ref, R.DATA / "raw", R.DATA / "refined" / "cache",
                              R.DATA / "raw" / "dem_n38w123.tif", steps, steps // 4, print, omega=omega)
        print(dx, json.dumps({k: v for k, v in d.items() if k != "baseline"}), flush=True)
        if r is None:
            continue
        res[dx] = r
        np.savez_compressed(out / f"conv_{p['id']}_dx{dx:g}_om{omega:g}.npz", **r)
    ks = sorted(res)
    for k in ("corr_street", "corr_bldg", "corr_shelter", "tke_street"):
        print(k)
        for dx in ks:
            v = res[dx][k]
            print(f"  dx={dx:g}: pct5/25/50/75/95 = {np.round(np.nanpercentile(v, [5, 25, 50, 75, 95]), 3)}")
        for a, b in zip(ks[:-1], ks[1:]):
            va, vb = res[a][k].ravel(), res[b][k].ravel()
            m = np.isfinite(va) & np.isfinite(vb)
            print(f"  dx {a:g} vs {b:g}: corr={np.corrcoef(va[m], vb[m])[0, 1]:.3f} median|diff|={np.median(np.abs(va[m] - vb[m])):.3f} "
                  f"mean ratio({a:g}/{b:g})={np.mean(va[m]) / np.mean(vb[m]):.3f} n={m.sum()}")


def make_map(direction, dx):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import TwoSlopeNorm
    tag = f"{int(direction * 10):04d}"
    d = np.load(R.DATA / "refined" / f"patch_corr_{tag}.npz")
    j = json.load(open(R.DATA / "refined" / f"patch_corr_{tag}.json"))
    spec, ter, mh, solid_c = R.load_grids(R.DATA / "grids")
    pid = d["patch_id"]
    ii, jj = np.where(pid >= 0)
    sl = (slice(ii.min(), ii.max() + 1), slice(jj.min(), jj.max() + 1))
    fig, ax = plt.subplots(1, 3, figsize=(20, 7))
    for a, (k, t, lim) in zip(ax, (("corr_street", "street / coarse speed", (0, 1.5)), ("corr_bldg", "building effect (with / without buildings)", (0.3, 1.7)),
                                   ("corr_bldg_shelter", "lowest-10 m building effect", (0.3, 1.7)))):
        v = d[k][sl].T
        im = a.imshow(v, origin="lower", cmap="RdBu_r" if k != "corr_street" else "viridis",
                      norm=TwoSlopeNorm(vcenter=1.0, vmin=lim[0], vmax=lim[1]) if k != "corr_street" else None,
                      extent=(sl[0].start, sl[0].stop, sl[1].start, sl[1].stop))
        a.contour(np.arange(sl[0].start, sl[0].stop) + 0.5, np.arange(sl[1].start, sl[1].stop) + 0.5, ter[sl].T, levels=10, colors="k", linewidths=0.3)
        a.set_title(f"{t}, wind 270 deg, fine dx={j['fine_dx_m']:g} m")
        plt.colorbar(im, ax=a, shrink=0.7)
    fig.tight_layout()
    f = R.DATA / "refined" / f"corr_map_{tag}.png"
    fig.savefig(f, dpi=80)
    print(f)


if __name__ == "__main__":
    if sys.argv[1] == "conv":
        conv(int(sys.argv[2]), int(sys.argv[3]), [float(x) for x in sys.argv[4].split(",")], float(sys.argv[5]) if len(sys.argv) > 5 else 1.95)
    else:
        make_map(float(sys.argv[2]), float(sys.argv[3]))
