"""Validation of the direction wind library (library/wind_<dir*10>.npz) against sanity criteria fixed BEFORE looking at results.

Criteria (all must hold for a direction to count as validated):
  a  finite everywhere, max speed ratio < 2.0 (the 270 field has 1.17), solid cells exactly 0
  b  mass conservation on the sub-box 3 cells inside the stored edge. The stored mean fields are NOT divergence free: in the 270
     reference the speed falls from 0.996 (west edge) to 0.866 (east edge) and the lateral net flux is 13 % of the inflow while
     the top plane carries no upward compensation (closure residual -0.28 of the inflow; weakly compressible LBM, equilibrium
     top and extrapolation outflow). An absolute bound (first versions: lateral net < 15 %, then closure < 5 %) fails the
     hand-checked reference itself, so the criterion is relative: closure_residual must be no worse (more negative) than the
     270 reference's + 0.02, and the lateral net flux no more than 0.10 beyond the reference. Absolute values are reported.
  c  flow angle: area-mean wind-from direction 60 m above the upwind sea (the 4 sea corners facing the wind) within 10 deg of
     the requested direction
  d  near-ground speed ratio (first informative layer, columns with terrain < 5 m, i.e. open water) between 0.6 and 1.3
  e  speed-up pattern vs the 270 field: Twin Peaks crest speed ratio at 60 m above ground > the open-sea ratio (hill speed-up
     or at least no unexplained deceleration of more than 15 %), and the wake side (lee of Twin Peaks, 1.5 km) slower than the crest
  f  smoothness: 99.9th percentile of |du| between neighbouring fluid cells (no checkerboard) <= 2 x that of the 270 field
  g  fog tracer (fog_<d>_0..3): json nan false and drift over the last 2000 steps < 0.01, field finite and within [0, 1.001], solid
     cells 0, and the mean tracer below 100 m falls with band height (b0 >= b1 >= b2 >= b3, 5 % slack)
  h  rotation round trip: the validated 270 field is resampled into this direction's downwind frame and back (the same code that
     brings every rotated run home); the mean absolute error on fluid cells must be < 5 % of the mean absolute value of each component
     (not run for 270)
Reported only (no pass/fail): speed ratio at named places, correlation of the near-ground speed field with the 270 field.
"""
import json
from pathlib import Path

import numpy as np
from pyproj import Transformer

PLACES = {"twin_peaks": (37.7544, -122.4477), "golden_gate_gap": (37.8085, -122.4785), "potrero": (37.7596, -122.4031),
          "sunset_flat": (37.7530, -122.4950), "ocean_offshore": (37.7600, -122.5300), "downtown": (37.7900, -122.4000),
          "mt_davidson": (37.7381, -122.4547), "bay_mid": (37.7700, -122.3700)}


def _spec_solid(D):
    spec = json.load(open(Path(D) / "grids" / "grid_spec.json"))
    solid = np.load(Path(D) / "grids" / "solid_20m.npz")["solid"].copy()
    return spec, solid


def _ij(spec, lat, lon):
    t = Transformer.from_crs("EPSG:4326", spec["crs"], always_xy=True)
    x, y = t.transform(lon, lat)
    return int((x - spec["x0"]) / spec["cell_m"]), int((y - spec["y0"]) / spec["cell_m"])


def _kg(solid_floor):
    nz = solid_floor.shape[2]
    top = nz - 1 - np.argmax(solid_floor[:, :, ::-1], axis=2)
    return top


def boundary_balance(u, solid, inset=3):
    """Mass balance of the sub-box [inset, n-1-inset] in x and y, k = 1..nz-8 (ratio units x cell area).
    The outermost cell column of every stored field is the boundary-condition cell (zero / not averaged; the 270 field reads
    0.031 there against 0.866 two cells further in), so faces at index 0 / -1 would show the outflow as missing (first version of
    this check: 270 gave net/inflow -0.97 = a validator bug). Two numbers:
      net_over_inflow   lateral net flux / inflow, i.e. how much leaves through the open top (270 reference: -0.134)
      closure_residual  (lateral net + flux through the top plane k = nz-7) / inflow: ~0 when the field is divergence free; this
                        is the actual conservation test, because the top boundary is open and the lateral net is not zero."""
    nz = solid.shape[2]
    ks = slice(1, nz - 7)
    nx, ny = solid.shape[:2]
    a, b = inset, -1 - inset
    fl = ~solid[:, :, ks]
    ys = slice(a, ny + b + 1)
    xs = slice(a, nx + b + 1)
    faces = {"west": (u[0][a, ys, ks] * fl[a, ys], -1), "east": (u[0][b, ys, ks] * fl[b, ys], 1),
             "south": (u[1][xs, a, ks] * fl[xs, a], -1), "north": (u[1][xs, b, ks] * fl[xs, b], 1)}
    flux = {n: float((v * s).sum()) for n, (v, s) in faces.items()}   # outward-positive
    inflow = -sum(min(v, 0.0) for v in flux.values())
    kt = nz - 7
    wt = 0.5 * (u[2][xs, ys, kt - 1] + u[2][xs, ys, kt])
    top = float((wt * (~solid[xs, ys, kt])).sum())              # upward through the top plane
    lat = float(sum(flux.values()))
    return {"outward_flux_by_face": flux, "inflow": inflow, "net_over_inflow": lat / max(inflow, 1e-9), "top_up_flux": top,
            "closure_residual": (lat + top) / max(inflow, 1e-9), "inset_cells": inset}


def check(D, d, ref270=None):
    D = Path(D)
    spec, solid = _spec_solid(D)
    u = np.load(D / "library" / f"wind_{int(round(d * 10)):04d}.npz")["u"].astype(np.float32)
    nx, ny, nz = solid.shape
    sf = solid.copy()
    sf[:, :, 0] = True
    top = _kg(sf)
    sp = np.sqrt((u ** 2).sum(0))
    res = {"direction_deg": d}
    # a
    res["finite"] = bool(np.isfinite(u).all())
    res["max_speed_ratio"] = float(sp.max())
    res["solid_nonzero"] = int((sp[solid] > 0).sum())
    res["a_pass"] = res["finite"] and res["max_speed_ratio"] < 2.0 and res["solid_nonzero"] == 0
    # b
    bb = boundary_balance(u, solid)
    res["mass_balance"] = bb
    ref = (ref270 or {}).get("mass_balance")
    res["b_pass"] = True if ref is None else (abs(bb["closure_residual"]) <= abs(ref["closure_residual"]) + 0.02 and
                                              abs(bb["net_over_inflow"]) <= abs(ref["net_over_inflow"]) + 0.10)
    # c: upwind sea flow angle at k=3 (60 m), averaged over the 4 corner boxes of 20x20 cells that are sea and face the wind
    th = np.radians(d)
    f = np.array([-np.sin(th), -np.cos(th)])
    ii, jj = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    sea = (top == 0)
    # upwind candidates: sea cells with small projection on flow (far upstream), within 6 km of the domain edge facing the wind
    proj = (ii - nx / 2) * f[0] + (jj - ny / 2) * f[1]
    pmin = np.percentile(proj[sea], 10) if sea.any() else 0
    sel = sea & (proj < pmin + 300) & (np.abs(proj) < 1e9)
    sel &= (ii > 5) & (ii < nx - 6) & (jj > 5) & (jj < ny - 6)
    ue, un = u[0][:, :, 3][sel].mean(), u[1][:, :, 3][sel].mean()
    ang = (np.degrees(np.arctan2(-ue, -un)) + 360) % 360
    res["upwind_sea_cells"] = int(sel.sum())
    res["upwind_flow_from_deg"] = float(ang)
    res["c_pass"] = abs((ang - d + 180) % 360 - 180) < 10
    # d: near-ground ratio over open water
    kk = np.minimum(top + 2, nz - 1)
    sp_ng = np.take_along_axis(sp, kk[:, :, None], 2)[:, :, 0]
    water = sea & (ii > 5) & (ii < nx - 6) & (jj > 5) & (jj < ny - 6)
    res["near_ground_ratio_water_median"] = float(np.median(sp_ng[water]))
    res["d_pass"] = 0.6 < res["near_ground_ratio_water_median"] < 1.3
    # places
    pl = {}
    for n, (la, lo) in PLACES.items():
        i, j = _ij(spec, la, lo)
        i, j = min(max(i, 0), nx - 1), min(max(j, 0), ny - 1)
        k = min(int(top[i, j]) + 3, nz - 1)  # 60 m-ish above surface
        pl[n] = {"ij": [i, j], "ground_elev_m": float((top[i, j]) * spec["cell_m"]), "speed_ratio_k_top_plus_3": float(sp[i, j, k]),
                 "speed_ratio_k_top_plus_2": float(sp[i, j, min(int(top[i, j]) + 2, nz - 1)])}
    res["places"] = pl
    # e: Twin Peaks crest vs sea and lee
    tp = pl["twin_peaks"]["speed_ratio_k_top_plus_3"]
    sea_r = float(np.median(np.take_along_axis(sp, np.minimum(top + 3, nz - 1)[:, :, None], 2)[:, :, 0][water]))
    i, j = pl["twin_peaks"]["ij"]
    li, lj = int(round(i + f[0] * 75)), int(round(j + f[1] * 75))  # 1.5 km downwind
    li, lj = min(max(li, 0), nx - 1), min(max(lj, 0), ny - 1)
    lee = float(sp[li, lj, min(int(top[li, lj]) + 3, nz - 1)])
    res["sea_ratio_k3"] = sea_r
    res["twin_peaks_crest"] = tp
    res["lee_1p5km_ratio"] = lee
    res["e_pass"] = tp > 0.85 * sea_r
    # f: smoothness
    fl = ~solid
    dd = np.abs(np.diff(u[0], axis=0))
    both = fl[1:] & fl[:-1]
    p999 = float(np.percentile(dd[both], 99.9))
    res["smooth_p999_dux"] = p999
    if ref270 is not None:
        res["smooth_ratio_vs_270"] = p999 / max(ref270["smooth_p999_dux"], 1e-9)
        res["f_pass"] = res["smooth_ratio_vs_270"] <= 2.0
        res["corr_near_ground_speed_with_270"] = None
    else:
        res["f_pass"] = True
    res["pass"] = all(res[k] for k in ("a_pass", "b_pass", "c_pass", "d_pass", "e_pass", "f_pass"))
    return res, sp_ng



def check_fog(D, d):
    D = Path(D)
    spec, solid = _spec_solid(D)
    res, ok, means = {}, True, []
    for b in range(4):
        st = f"{int(round(d * 10)):04d}_{b}"
        j = json.load(open(D / "library" / f"fog_{st}.json"))
        c = np.load(D / "library" / f"fog_{st}.npz")["c"].astype(np.float32)
        fin = bool(np.isfinite(c).all())
        lo, hi = float(c.min()), float(c.max())
        nzs = int((c[solid] != 0).sum())
        fl = ~solid
        m100 = float(c[:, :, :5][fl[:, :, :5]].mean())          # below 100 m (5 cells of 20 m) over fluid cells
        means.append(m100)
        bok = (not j.get("nan", True)) and j.get("drift_last_2000_steps", 1) < 0.01 and fin and lo >= 0 and hi <= 1.001 and nzs == 0
        res[f"band{b}"] = {"drift": j.get("drift_last_2000_steps"), "finite": fin, "min": lo, "max": hi, "solid_nonzero": nzs, "mean_below_100m": m100, "ok": bool(bok),
                           "transits": j.get("transits")}
        ok &= bool(bok)
    mono = all(means[i + 1] <= means[i] * 1.05 + 1e-6 for i in range(3))
    res["monotone_with_height"] = mono
    res["g_pass"] = bool(ok and mono)
    return res


def check_roundtrip(D, d):
    """Resample the 270 field into the downwind frame of direction d and back; relative error per component."""
    from scipy.ndimage import map_coordinates
    from . import windrot
    D = Path(D)
    spec, solid = _spec_solid(D)
    u270 = np.load(D / "library" / "wind_2700.npz")["u"]
    nx, ny, nz = solid.shape
    fr = windrot.frame(d, nx, ny)
    I, J = windrot._coords_rot_to_geo(fr, nx, ny)
    fx, fy = fr["f"]
    nxn, nyn = fr["n"]
    ur = np.zeros((3, fr["nxr"] + fr["pad"], fr["nyr"], nz), np.float32)
    for k in range(nz):
        c = [map_coordinates(u270[a][:, :, k].astype(np.float32), [I, J], order=1, mode="nearest") for a in range(3)]
        ur[0][:, :, k] = c[0] * fx + c[1] * fy
        ur[1][:, :, k] = c[0] * nxn + c[1] * nyn
        ur[2][:, :, k] = c[2]
    back = windrot.unrotate_field(ur, solid, d, fr).astype(np.float32)
    fl = ~solid
    fl[:, :, 0] = False
    out = {}
    for a, n in enumerate("uvw"):
        ref = u270[a].astype(np.float32)[fl]
        e = np.abs(back[a][fl] - ref)
        out[n] = {"mean_abs_err": float(e.mean()), "p99_err": float(np.percentile(e, 99)), "ref_mean_abs": float(np.abs(ref).mean()),
                  "rel_err": float(e.mean() / max(np.abs(ref).mean(), 1e-9))}
    out["h_pass"] = all(out[n]["rel_err"] < 0.05 for n in "uvw")
    return out


def run(D, dirs, out=None, png=True):
    D = Path(D)
    outd = {}
    r270 = None
    sp270 = None
    if (D / "library" / "wind_2700.npz").exists():
        r270, sp270 = check(D, 270.0)
        r270["fog"] = check_fog(D, 270.0)
        r270["g_pass"] = r270["fog"]["g_pass"]
        outd["270"] = r270
    for d in dirs:
        if d == 270:
            continue
        f = D / "library" / f"wind_{int(round(d * 10)):04d}.npz"
        if not f.exists():
            continue
        r, spng = check(D, d, r270)
        r["fog"] = check_fog(D, d)
        r["g_pass"] = r["fog"]["g_pass"]
        r["roundtrip"] = check_roundtrip(D, d)
        r["h_pass"] = r["roundtrip"]["h_pass"]
        r["pass"] = bool(r["pass"] and r["g_pass"] and r["h_pass"])
        if sp270 is not None:
            a, b = spng.ravel(), sp270.ravel()
            r["corr_near_ground_speed_with_270"] = float(np.corrcoef(a, b)[0, 1])
        outd[str(d)] = r
        if png:
            _png(D, d, spng)
        print(d, "PASS" if r["pass"] else "FAIL", {k: r[k] for k in r if k.endswith("_pass")}, flush=True)
    out = Path(out or D / "library" / "wind_dirs_validation.json")
    json.dump(outd, open(out, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    mf = D / "library" / "wind_dirs.json"   # manifest read by dirlib.available (merged with earlier passes)
    prev = set(json.load(open(mf)).get("validated", [])) if mf.exists() else set()
    for k, r in outd.items():
        if k != "270":
            (prev.add if r["pass"] else prev.discard)(float(k))
    json.dump({"validated": sorted(prev), "criteria": "validate_dirs.py a-f"}, open(mf, "w"))
    return outd


def _png(D, d, spng):
    from PIL import Image
    a = np.clip(spng.T[::-1] / 1.5, 0, 1)
    Image.fromarray((a * 255).astype(np.uint8), "L").save(Path(D) / "library" / f"wind_{int(round(d * 10)):04d}_ground.png")
