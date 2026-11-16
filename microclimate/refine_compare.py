"""Compare two refine resolutions over the Potrero patches: python -m microclimate.refine_compare 270 5 2.5"""
import json
import sys

import numpy as np
import pandas as pd

from . import refine as R

CLASSES = [0.0, 0.2, 0.5, 0.8, 9.0]  # corr_bldg classes: very sheltered / sheltered / moderate / exposed


def main(direction, dxa, dxb):
    tag = f"{int(direction * 10):04d}"
    out = R.DATA / "refined"
    sa = "" if dxa == 5 else "_dx" + f"{dxa:g}".replace(".", "p")
    sb = "" if dxb == 5 else "_dx" + f"{dxb:g}".replace(".", "p")
    A = np.load(out / f"patch_corr_{tag}{sa}.npz")
    B = np.load(out / f"patch_corr_{tag}{sb}.npz")
    pj = json.load(open(out / f"patch_corr_{tag}{sb}.json"))["patches"]
    ids = {p["id"] for p in pj}
    pa = {p["id"]: p for p in json.load(open(out / f"patch_corr_{tag}{sa}.json"))["patches"]}
    rows = []
    for p in pj:
        if p["id"] not in pa:
            continue
        sl = (slice(p["ix0"], p["ix0"] + p["ncx"]), slice(p["iy0"], p["iy0"] + p["ncy"]))
        row = {"patch": p["id"]}
        for k in ("corr_bldg", "corr_street", "corr_bldg_shelter"):
            a, b = A[k][sl].ravel(), B[k][sl].ravel()
            m = np.isfinite(a) & np.isfinite(b)
            row[k + "_med_a"], row[k + "_med_b"] = np.median(a[m]), np.median(b[m])
            row[k + "_corr"] = np.corrcoef(a[m], b[m])[0, 1]
        rows.append(row)
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    print(df.round(3).to_string(index=False))
    allm = {}
    for k in ("corr_bldg", "corr_street", "corr_bldg_shelter"):
        a, b = A[k], B[k]
        sel = np.isin(B["patch_id"], [i for i, p in enumerate(pj)]) & (B["patch_id"] >= 0) & np.isfinite(a) & np.isfinite(b)
        allm[k] = (np.median(a[sel]), np.median(b[sel]), np.corrcoef(a[sel], b[sel])[0, 1], np.mean(b[sel]) / np.mean(a[sel]))
        print(f"{k}: median dx{dxa:g}={allm[k][0]:.3f} dx{dxb:g}={allm[k][1]:.3f} corr={allm[k][2]:.3f} mean ratio b/a={allm[k][3]:.3f} n={sel.sum()}")
    pts = pd.read_csv(R.DATA / "grids" / "zone" / "potrero_points.csv")
    spec = json.load(open(R.DATA / "grids" / "grid_spec.json"))
    ci = np.clip(np.round((pts.x - spec["x0"]) / 20 - 0.5).astype(int), 0, spec["nx"] - 1)
    cj = np.clip(np.round((pts.y - spec["y0"]) / 20 - 0.5).astype(int), 0, spec["ny"] - 1)
    for k in ("corr_bldg", "corr_street"):
        ca, cb = A[k][ci, cj], B[k][ci, cj]
        m = np.isfinite(ca) & np.isfinite(cb)
        if k == "corr_street":
            edges = [0, 0.02, 0.05, 0.1, 9]
        else:
            edges = CLASSES
        la, lb = np.digitize(ca[m], edges), np.digitize(cb[m], edges)
        print(f"{k}: street points with both finite {m.sum()} of {len(pts)}; class change {np.mean(la != lb) * 100:.1f}% "
              f"(classes by edges {edges[1:-1]}); relative change median {np.median(cb[m] / ca[m]):.3f}, "
              f"|rel change|>25%: {np.mean(np.abs(cb[m] / ca[m] - 1) > 0.25) * 100:.1f}%; 5m<->2.5m two-class (corr_bldg<0.3) flips "
              f"{np.mean((ca[m] < 0.3) != (cb[m] < 0.3)) * 100:.1f}%")


if __name__ == "__main__":
    main(float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]))
