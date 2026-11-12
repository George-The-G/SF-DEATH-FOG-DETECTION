"""M3 part 1: per-street-point wind and fog features sampled from the 270-degree library.

Heights: the grid z axis is metres ABOVE SEA LEVEL (cell centre (k+0.5)*cell_m), and the fog bands in microclimate.toml
([0,150],[150,300],[300,500],[500,800]) are also metres above sea level (fog.py compares them with the same cell-centre z).
The wind field is stored as velocity / free-stream inflow speed, so a ratio of 1.0 means the 5 m/s reference speed.

Sampling: for every point we find the first fluid cell above the (terrain + mean building height) surface in each of the four
surrounding columns and blend them bilinearly with weights renormalised over finite columns. Because a 20 m cell covers
a building fraction, "street level" in a built-up cell is the first fluid cell above the mean roof level (see PROGRESS.md).
"""
import csv
import json
from pathlib import Path

import numpy as np

RATIO_FLOOR = 0.1  # residence proxy = 1 / max(ratio, floor), so at most 10
COLUMNS = ["idx", "wind_ratio", "wind_ratio_k1", "fog_b0", "fog_b1", "fog_b2", "fog_b3", "residence", "k_surface", "z_sample_m",
           "refine_corr"]


def first_fluid_k(solid):
    """Index of the first fluid cell above the highest solid cell of each column, [nx, ny] (nz if none)."""
    nz = solid.shape[2]
    top = nz - 1 - np.argmax(solid[:, :, ::-1], axis=2)
    has = solid.any(axis=2)
    return np.where(has, top + 1, 0).astype(np.int32)


def bilinear_columns(field_2d, x, y, spec, valid):
    """Blend field_2d[nx, ny] over the four columns around (x, y); NaN where valid is false. Returns float64 [N]."""
    c = spec["cell_m"]
    fx = (x - spec["x0"]) / c - 0.5
    fy = (y - spec["y0"]) / c - 0.5
    i0 = np.floor(fx).astype(int)
    j0 = np.floor(fy).astype(int)
    tx, ty = fx - i0, fy - j0
    num = np.zeros(len(x))
    den = np.zeros(len(x))
    for di, wx in ((0, 1 - tx), (1, tx)):
        for dj, wy in ((0, 1 - ty), (1, ty)):
            i = np.clip(i0 + di, 0, spec["nx"] - 1)
            j = np.clip(j0 + dj, 0, spec["ny"] - 1)
            ok = valid[i, j]
            w = wx * wy * ok
            num += w * np.where(ok, field_2d[i, j], 0.0)
            den += w
    return np.where(den > 1e-6, num / np.maximum(den, 1e-12), np.nan)


def read_points(path):
    rows = list(csv.DictReader(open(path, newline="")))
    return rows


def build(cfg, grids_dir, lib_dir, refined_dir=None, out_path=None):
    grids_dir, lib_dir = Path(grids_dir), Path(lib_dir)
    spec = json.load(open(grids_dir / "grid_spec.json"))
    solid = np.load(grids_dir / "solid_20m.npz")["solid"]
    pf = grids_dir / "zone" / "points.csv"  # combined table of all zones (python -m microclimate routes); legacy: potrero only
    pts = read_points(pf if pf.exists() else grids_dir / "zone" / "potrero_points.csv")
    n = len(pts)
    x = np.array([float(r["x"]) for r in pts])
    y = np.array([float(r["y"]) for r in pts])
    nx, ny, nz = solid.shape
    kf = np.minimum(first_fluid_k(solid), nz - 1)  # fog: same convention as fog.py (no forced floor layer)
    sw = solid.copy()
    sw[:, :, 0] = True  # wind.py forces k=0 to be a no-slip floor, so speed there is 0
    kw = np.minimum(first_fluid_k(sw) + 1, nz - 1)  # wind ratio: top+2, the first informative layer (k=top+1 is wall adjacent)
    kw1 = np.minimum(kw - 1, nz - 1)
    ii, jj = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    valid = np.ones((nx, ny), bool)

    u = np.load(lib_dir / "wind_2700.npz")["u"]  # [3,nx,ny,nz] float16
    sp1 = np.sqrt(sum(u[a][ii, jj, kw].astype(np.float32) ** 2 for a in range(3)))
    sp2 = np.sqrt(sum(u[a][ii, jj, kw1].astype(np.float32) ** 2 for a in range(3)))
    del u
    wind = bilinear_columns(sp1, x, y, spec, valid)
    wind2 = bilinear_columns(sp2, x, y, spec, valid)

    fog = []
    for b in range(len(cfg["library"]["fog_bands_m"])):
        c = np.load(lib_dir / f"fog_2700_{b}.npz")["c"]
        fog.append(bilinear_columns(c[ii, jj, kf].astype(np.float32), x, y, spec, valid))
        del c
    residence = 1.0 / np.maximum(wind, RATIO_FLOOR)

    # k_surface / z_sample_m: of the nearest column, for reference
    ci = np.clip(np.round((x - spec["x0"]) / spec["cell_m"] - 0.5).astype(int), 0, nx - 1)
    cj = np.clip(np.round((y - spec["y0"]) / spec["cell_m"] - 0.5).astype(int), 0, ny - 1)
    kk = kf[ci, cj]

    refine = np.full(n, np.nan)
    rf = Path(refined_dir or grids_dir.parent / "refined") / "patch_corr_2700.npz"
    if rf.exists():
        corr = np.load(rf)["corr_bldg"].copy()
        rf25 = rf.with_name("patch_corr_2700_dx2p5.npz")  # 2.5 m nested run (Potrero only) preferred where finite
        if rf25.exists():
            c25 = np.load(rf25)["corr_bldg"]
            corr = np.where(np.isfinite(c25), c25, corr)
        refine = corr[ci, cj].astype(float)

    out_path = Path(out_path or grids_dir / "zone" / "street_features.csv")
    ci_f = np.isfinite(refine)
    refine_cov = {}
    if "zone" in pts[0]:
        for zid in dict.fromkeys(r["zone"] for r in pts):
            sel = np.array([r["zone"] == zid for r in pts])
            refine_cov[zid] = float(ci_f[sel].mean())
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for i in range(n):
            w.writerow([i, f"{wind[i]:.4f}", f"{wind2[i]:.4f}"] + [f"{fog[b][i]:.4f}" for b in range(4)] +
                       [f"{residence[i]:.3f}", int(kk[i]), f"{(kk[i] + 0.5) * spec['cell_m']:.0f}", f"{refine[i]:.4f}"])
    fin = lambda a: float(np.isfinite(a).mean())
    return {"points": n, "out": str(out_path), "finite_wind": fin(wind), "finite_fog": [fin(a) for a in fog],
            "wind_ratio_median": float(np.nanmedian(wind)), "wind_ratio_p05_p95": [float(np.nanpercentile(wind, 5)), float(np.nanpercentile(wind, 95))],
            "fog_median": [float(np.nanmedian(a)) for a in fog], "refine_corr_present": bool(rf.exists()),
            "refine_corr_finite_by_zone": refine_cov}
