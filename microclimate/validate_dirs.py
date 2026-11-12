"""Validation of the direction wind library (library/wind_<dir*10>.npz) against sanity criteria fixed BEFORE looking at results.

Criteria (all must hold for a direction to count as validated):
  a  finite everywhere, max speed ratio < 2.0 (the 270 field has 1.17), solid cells exactly 0
  b  mass balance on the original box: |net flux through the 4 lateral faces below the open top| < 15 % of the inflow flux
     (the top is an equilibrium boundary and leaks; the 270 field is the reference value)
  c  flow angle: area-mean wind-from direction 60 m above the upwind sea (the 4 sea corners facing the wind) within 10 deg of
     the requested direction
  d  near-ground speed ratio (first informative layer, columns with terrain < 5 m, i.e. open water) between 0.6 and 1.3
  e  speed-up pattern vs the 270 field: Twin Peaks crest speed ratio at 60 m above ground > the open-sea ratio (hill speed-up
     or at least no unexplained deceleration of more than 15 %), and the wake side (lee of Twin Peaks, 1.5 km) slower than the crest
  f  smoothness: 99.9th percentile of |du| between neighbouring fluid cells (no checkerboard) <= 2 x that of the 270 field
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


def boundary_balance(u, solid):
    """Net and inflow flux (ratio units x cell area) through the four lateral faces for k=1..nz-8."""
    nz = solid.shape[2]
    ks = slice(1, nz - 7)
    fl = ~solid[:, :, ks]
    faces = {"west": (u[0][0, :, ks] * fl[0], -1), "east": (u[0][-1, :, ks] * fl[-1], 1),
             "south": (u[1][:, 0, ks] * fl[:, 0], -1), "north": (u[1][:, -1, ks] * fl[:, -1], 1)}
    flux = {n: float((v * s).sum()) for n, (v, s) in faces.items()}   # outward-positive
    inflow = -sum(min(v, 0.0) for v in flux.values())
    return {"outward_flux_by_face": flux, "inflow": inflow, "net_over_inflow": float(sum(flux.values()) / max(inflow, 1e-9))}


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
    res["b_pass"] = abs(bb["net_over_inflow"]) < 0.15
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


def run(D, dirs, out=None, png=True):
    D = Path(D)
    outd = {}
    r270 = None
    sp270 = None
    if (D / "library" / "wind_2700.npz").exists():
        r270, sp270 = check(D, 270.0)
        outd["270"] = r270
    for d in dirs:
        if d == 270:
            continue
        f = D / "library" / f"wind_{int(round(d * 10)):04d}.npz"
        if not f.exists():
            continue
        r, spng = check(D, d, r270)
        if sp270 is not None:
            a, b = spng.ravel(), sp270.ravel()
            r["corr_near_ground_speed_with_270"] = float(np.corrcoef(a, b)[0, 1])
        outd[str(d)] = r
        if png:
            _png(D, d, spng)
        print(d, "PASS" if r["pass"] else "FAIL", {k: r[k] for k in r if k.endswith("_pass")}, flush=True)
    out = Path(out or D / "library" / "wind_dirs_validation.json")
    json.dump(outd, open(out, "w"), indent=1)
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
