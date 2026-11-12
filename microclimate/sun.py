"""M3 part 2: horizon profile, sky view factor and sun/shadow helpers for the Potrero street points.

Surface model: inside a 2 m window around the zone (zone centre, radius 700 m plus 1000 m margin) it is the 3DEP DEM resampled
bilinearly to 2 m plus rasterised building footprints at hgt_median_m. Outside that window it is the 20 m grid surface
(terrain_20m + bldg_mean_h_20m). Rays are marched out to 3 km with a coarse-to-fine step (2 m near the observer, 4 % of the
distance in the middle, capped at 25 m far away). Horizon azimuths: 72 values, 5 degree steps, clockwise from north (azimuth 0 = north,
90 = east). Heights are metres above sea level; the observer is 1 m above the ground (DEM) at the point.
"""
import csv
import json
import time
from pathlib import Path

import numpy as np

N_AZ = 72
STEP_DEG = 360.0 / N_AZ
OBS_H = 1.0
RAY_MAX_M = 3000.0
ZONE_CENTER = (37.7597, -122.4037)
WINDOW_HALF_M = 700.0 + 1000.0
FINE_M = 2.0
POROUS_SURFACES = {"paving_stones", "sett", "cobblestone", "unpaved", "gravel"}
POROUS_FACTOR = 0.6
SITE = dict(lat=37.76, lon=-122.40, tz="America/Los_Angeles")


def ray_distances():
    d, out = 2.0, []
    while d <= RAY_MAX_M:
        out.append(d)
        d += float(np.clip(0.04 * d, 2.0, 25.0))
    return np.array(out)


def fine_surface(raw_dir, center=None, half_m=None):
    """Returns dict with dem (fine ground), surf (ground + buildings), bmask, origin x0,y0 and size, all [nx, ny] y north.
    center (lat, lon) and half_m set the 2 m window (default: the Potrero window)."""
    center = center or ZONE_CENTER
    half_m = WINDOW_HALF_M if half_m is None else half_m
    import rasterio
    from pyproj import Transformer
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    import shapely
    from .geometry import load_buildings

    cx, cy = Transformer.from_crs(4326, 32610, always_xy=True).transform(center[1], center[0])
    x0 = float(np.floor((cx - half_m) / FINE_M) * FINE_M)
    y0 = float(np.floor((cy - half_m) / FINE_M) * FINE_M)
    n = int(round(2 * half_m / FINE_M))
    tf = from_origin(x0, y0 + n * FINE_M, FINE_M, FINE_M)
    dem = np.full((n, n), np.nan, np.float32)
    with rasterio.open(Path(raw_dir) / "dem_n38w123.tif") as src:
        reproject(rasterio.band(src, 1), dem, dst_transform=tf, dst_crs="EPSG:32610", resampling=Resampling.bilinear,
                  dst_nodata=np.nan)
    dem = np.nan_to_num(dem, nan=0.0)
    dem[dem < 0] = 0
    geoms, h = load_buildings(Path(raw_dir) / "bldg.geojson")
    b = shapely.bounds(geoms)
    sel = (b[:, 2] >= x0) & (b[:, 0] <= x0 + n * FINE_M) & (b[:, 3] >= y0) & (b[:, 1] <= y0 + n * FINE_M) & (h > 0)
    order = np.argsort(h[sel])
    gs, hs = geoms[sel][order], h[sel][order]
    bh = rasterize([(g, float(v)) for g, v in zip(gs, hs) if not g.is_empty], out_shape=(n, n), transform=tf, fill=0.0,
                   dtype="float32")
    flip = lambda a: np.flipud(a).T.copy()  # [nx, ny], y increasing
    dem, bh = flip(dem), flip(bh)
    return {"dem": dem, "bh": bh, "x0": x0, "y0": y0, "n": n, "n_buildings": int(sel.sum())}


def load_points(grids_dir):
    """(rows, zones): the combined points.csv (else the legacy potrero_points.csv) and the zone table (row ranges and 2 m windows)."""
    gz = Path(grids_dir) / "zone"
    p = gz / "points.csv"
    if not p.exists():
        p = gz / "potrero_points.csv"
    rows = list(csv.DictReader(open(p, newline="")))
    zj = gz / "zones.json"
    if zj.exists():
        zones = json.load(open(zj))["zones"]
    else:
        zones = [{"id": "potrero", "name": "Potrero Hill District", "row0": 0, "row1": len(rows), "center": list(ZONE_CENTER),
                  "half_m": WINDOW_HALF_M, "osm": ["osm_potrero.json"]}]
    return rows, zones


def build(cfg, raw_dir, grids_dir):
    """Horizon, SVF and surface table for every point. Each zone gets its own 2 m window (zone centre + half_m); outside a
    window rays use the 20 m surface. Per point the result does not depend on the other zones."""
    from scipy.ndimage import distance_transform_edt, map_coordinates, uniform_filter

    t_start = time.time()
    grids_dir = Path(grids_dir)
    spec = json.load(open(grids_dir / "grid_spec.json"))
    ter20 = np.load(grids_dir / "terrain_20m.npy").astype(np.float32)
    bm20 = np.load(grids_dir / "bldg_mean_h_20m.npy").astype(np.float32)
    surf20 = ter20 + bm20
    rows, zones = load_points(grids_dir)
    N = len(rows)
    px_all = np.array([float(r["x"]) for r in rows])
    py_all = np.array([float(r["y"]) for r in rows])
    horizon = np.zeros((N, N_AZ), np.float16)
    z_obs_all = np.zeros(N, np.float32)
    bcover_all = np.zeros(N, np.float32)
    shift_all = np.zeros(N)
    info_z = {}
    dist = ray_distances()
    az = np.deg2rad(np.arange(N_AZ) * STEP_DEG)
    ex, ey = np.sin(az), np.cos(az)  # east, north components
    c = spec["cell_m"]
    lo = FINE_M * 2
    CH = 400
    for zn in zones:
        sl = slice(zn["row0"], zn["row1"])
        fs = fine_surface(raw_dir, tuple(zn["center"]), zn["half_m"])
        surf2 = fs["dem"] + fs["bh"]
        bmask = fs["bh"] > 0.5
        px, py = px_all[sl].copy(), py_all[sl].copy()
        n = len(px)

        # points that fall inside a footprint (OSM/footprint misalignment) are moved to the nearest non-building 2 m cell
        fi = np.clip(((px - fs["x0"]) / FINE_M).astype(int), 0, fs["n"] - 1)
        fj = np.clip(((py - fs["y0"]) / FINE_M).astype(int), 0, fs["n"] - 1)
        inside = bmask[fi, fj]
        shift = np.zeros(n)
        if inside.any():
            _, (ii, jj) = distance_transform_edt(bmask, return_indices=True)
            ni, nj = ii[fi, fj], jj[fi, fj]
            sx = (ni - fi) * FINE_M
            sy = (nj - fj) * FINE_M
            shift = np.where(inside, np.hypot(sx, sy), 0.0)
            px = np.where(inside, fs["x0"] + (ni + 0.5) * FINE_M, px)
            py = np.where(inside, fs["y0"] + (nj + 0.5) * FINE_M, py)
            fi = np.clip(((px - fs["x0"]) / FINE_M).astype(int), 0, fs["n"] - 1)
            fj = np.clip(((py - fs["y0"]) / FINE_M).astype(int), 0, fs["n"] - 1)
        z_obs = map_coordinates(fs["dem"], [(px - fs["x0"]) / FINE_M - 0.5, (py - fs["y0"]) / FINE_M - 0.5], order=1, mode="nearest") + OBS_H
        bcover30 = uniform_filter(bmask.astype(np.float32), size=31, mode="nearest")[fi, fj]  # 62 m box

        hor = np.zeros((n, N_AZ), np.float32)
        for s in range(0, n, CH):
            cs = slice(s, min(n, s + CH))
            X = px[cs, None, None] + ex[None, :, None] * dist[None, None, :]
            Y = py[cs, None, None] + ey[None, :, None] * dist[None, None, :]
            fx, fy = (X - fs["x0"]) / FINE_M - 0.5, (Y - fs["y0"]) / FINE_M - 0.5
            inw = (fx > lo) & (fy > lo) & (fx < fs["n"] - 1 - lo) & (fy < fs["n"] - 1 - lo)
            H = map_coordinates(surf20, [(X - spec["x0"]) / c - 0.5, (Y - spec["y0"]) / c - 0.5], order=1, mode="nearest")
            if inw.any():
                Hf = map_coordinates(surf2, [fx[inw], fy[inw]], order=1, mode="nearest")
                H[inw] = Hf
            ang = np.degrees(np.arctan2(H - z_obs[cs, None, None], dist[None, None, :]))
            hor[cs] = ang.max(axis=2)
        horizon[sl] = np.clip(hor, -90, 90).astype(np.float16)
        z_obs_all[sl], bcover_all[sl], shift_all[sl] = z_obs, bcover30, shift
        info_z[zn["id"]] = {"points": n, "window_buildings": fs["n_buildings"], "window_half_m": zn["half_m"],
                            "points_inside_footprint": int(inside.sum()), "max_shift_m": float(shift.max())}
        del fs, surf2, bmask
    np.save(grids_dir / "zone" / "street_horizon.npy", horizon)
    svf = (np.cos(np.deg2rad(np.clip(horizon.astype(np.float32), 0, 90))) ** 2).mean(axis=1).astype(np.float32)
    np.save(grids_dir / "zone" / "street_svf.npy", svf)
    np.savez(grids_dir / "zone" / "street_sun_meta.npz", bcover30=bcover_all, z_obs=z_obs_all, shift_m=shift_all.astype(np.float32))
    surface_table(raw_dir, grids_dir, rows, zones)
    return {"points": N, "azimuths": N_AZ, "ray_steps": int(len(dist)), "zones": info_z,
            "points_inside_footprint": int((shift_all > 0).sum()), "max_shift_m": float(shift_all.max()),
            "svf_median": float(np.median(svf)), "seconds": round(time.time() - t_start, 1)}


def surface_table(raw_dir, grids_dir, rows, zones=None):
    """street_surface.csv: OSM surface tag of the point's way (else nearest tagged way within 20 m) and the porous factor.
    Each zone only looks at the ways of its own OSM file(s), so adding a zone never changes another zone's rows."""
    import shapely
    from pyproj import Transformer
    from shapely.geometry import LineString
    from shapely.strtree import STRtree

    zones = zones or [{"row0": 0, "row1": len(rows), "osm": ["osm_potrero.json"]}]
    t = Transformer.from_crs(4326, 32610, always_xy=True)
    out = [None] * len(rows)
    for zn in zones:
        nodes, ways, seen = {}, [], set()
        for fn in zn.get("osm", ["osm_potrero.json"]):
            osm = json.load(open(Path(raw_dir) / fn, encoding="utf-8"))
            nodes.update({e["id"]: t.transform(e["lon"], e["lat"]) for e in osm["elements"] if e["type"] == "node"})
            for e in osm["elements"]:
                if e["type"] == "way" and e["id"] not in seen:
                    seen.add(e["id"])
                    ways.append(e)
        ways = [e for e in ways if all(n in nodes for n in e["nodes"])]
        tag = {w["id"]: (w.get("tags", {}).get("surface") or "") for w in ways}
        tagged = [w for w in ways if tag[w["id"]]]
        lines = [LineString([nodes[n] for n in w["nodes"]]) for w in tagged]
        tree = STRtree(lines) if lines else None
        for i in range(zn["row0"], zn["row1"]):
            r = rows[i]
            wid = int(r["way"])
            s, src = tag.get(wid, ""), "own"
            if not s and r.get("surface"):
                s = r["surface"]
            if not s:
                src = "none"
                if tree is not None:
                    p = shapely.Point(float(r["x"]), float(r["y"]))
                    j = int(tree.nearest(p))
                    if lines[j].distance(p) <= 20.0:
                        s, src = tag[tagged[j]["id"]], "nearest"
            porous = s in POROUS_SURFACES
            out[i] = (i, wid, s, src if s else "none", int(porous), POROUS_FACTOR if porous else 1.0)
    with open(Path(grids_dir) / "zone" / "street_surface.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["idx", "way", "osm_surface", "surface_src", "porous", "porous_factor"])
        w.writerows(out)


def sunlit(horizon_deg, sun_az, sun_el):
    """Bool [N]: sun above the horizon profile at the sun azimuth (linear interpolation between the 5-degree samples).
    horizon_deg [N,72] (azimuth clockwise from north), sun_az and sun_el scalars in degrees."""
    h = np.asarray(horizon_deg, np.float32)
    if sun_el <= 0:
        return np.zeros(h.shape[0], bool)
    pos = (float(sun_az) % 360.0) / STEP_DEG
    i0 = int(np.floor(pos)) % N_AZ
    i1 = (i0 + 1) % N_AZ
    f = pos - np.floor(pos)
    return float(sun_el) > (h[:, i0] * (1 - f) + h[:, i1] * f)


def sun_track(date, step_min=5, lat=SITE["lat"], lon=SITE["lon"], tz=SITE["tz"]):
    import pandas as pd
    import pvlib

    t = pd.date_range(f"{date} 00:00", periods=int(24 * 60 / step_min), freq=f"{step_min}min", tz=tz)
    sp = pvlib.solarposition.get_solarposition(t, lat, lon)
    return t, sp["azimuth"].to_numpy(), sp["apparent_elevation"].to_numpy()


def sunlit_hours(horizon_deg, date="2026-12-21", step_min=5):
    """Hours of direct sun per street point on a local date (America/Los_Angeles), pvlib position at 37.76 N, -122.40 E."""
    _, az, el = sun_track(date, step_min)
    n = np.zeros(np.asarray(horizon_deg).shape[0])
    for a, e in zip(az, el):
        if e > 0:
            n += sunlit(horizon_deg, a, e)
    return n * step_min / 60.0


def _fraction_pairs(rows, hours):
    """East-west segments (bearing within 20 deg of 90/270): compare the south-side point (lower y) with the north-side point."""
    seg = {}
    for i, r in enumerate(rows):
        seg.setdefault((r["way"], r["seg"]), []).append(i)
    less = tie = more = 0
    for k, idx in seg.items():
        if len(idx) != 2:
            continue
        a, b = idx
        brg = float(rows[a]["bearing"]) % 180.0
        if abs(brg - 90.0) > 20.0:
            continue
        ya, yb = float(rows[a]["y"]), float(rows[b]["y"])
        if abs(ya - yb) < 3.0:
            continue
        s, n = (a, b) if ya < yb else (b, a)
        d = hours[s] - hours[n]
        if d < -0.05:
            less += 1
        elif d > 0.05:
            more += 1
        else:
            tie += 1
    tot = less + tie + more
    return {"segments": tot, "south_less": less, "tie": tie, "south_more": more,
            "frac_south_less": less / max(tot, 1), "frac_south_less_or_tie": (less + tie) / max(tot, 1),
            "frac_south_less_of_nontied": less / max(less + more, 1)}


def check(cfg, raw_dir, grids_dir):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    gz = Path(grids_dir) / "zone"
    if not (gz / "street_horizon.npy").exists():
        print("horizon missing, building first", flush=True)
        print(json.dumps(build(cfg, raw_dir, grids_dir)))
    if not (gz / "street_features.csv").exists():
        from . import street
        street.build(cfg, grids_dir, Path(grids_dir).parent / "library")
    rows_all, zones = load_points(grids_dir)
    hor_all = np.load(gz / "street_horizon.npy")
    svf_all = np.load(gz / "street_svf.npy")
    meta_all = dict(np.load(gz / "street_sun_meta.npz"))
    # the gating checks (a, b, c) run on the Potrero zone exactly as in the single-zone pipeline; every other zone is reported alongside
    npot = next(z["row1"] for z in zones if z["id"] == "potrero") if any(z["id"] == "potrero" for z in zones) else len(rows_all)
    rows, hor, svf = rows_all[:npot], hor_all[:npot], svf_all[:npot]
    meta = {k: v[:npot] for k, v in meta_all.items()}
    res, ok = {}, {}

    t, az, el = sun_track("2026-12-21", 1)
    noon = float(np.nanmax(el))
    res["a_dec21_noon_elevation_deg"] = noon
    ok["a"] = abs(noon - 28.8) <= 0.5

    hours_all = sunlit_hours(hor_all, "2026-12-21", 5)
    np.save(gz / "street_sunlit_dec21.npy", hours_all.astype(np.float32))
    hours = hours_all[:npot]
    res["b"] = _fraction_pairs(rows, hours)
    res["b"]["definition"] = "south side = side whose point has the lower y; fewer sunlit hours means south_hours < north_hours - 0.05 h"
    res["dec21_sunlit_hours_median"] = float(np.median(hours))
    res["dec21_sunlit_hours_p05_p95"] = [float(np.percentile(hours, 5)), float(np.percentile(hours, 95))]
    ok["b"] = res["b"]["frac_south_less"] >= 0.70

    z = np.array([float(r["z"]) for r in rows])
    bc = meta["bcover30"]
    hill = z >= np.percentile(z, 95)
    narrow = bc >= np.percentile(bc, 90)
    opn = bc <= np.percentile(bc, 10)
    res["c"] = {"svf_min": float(svf.min()), "svf_max": float(svf.max()), "svf_median": float(np.median(svf)),
                "svf_p05_p95": [float(np.percentile(svf, 5)), float(np.percentile(svf, 95))],
                "median_hilltop_top5pct_elev": float(np.median(svf[hill])), "median_narrow_top10pct_bldg_cover": float(np.median(svf[narrow])),
                "median_open_bottom10pct_bldg_cover": float(np.median(svf[opn])),
                "points_moved_out_of_footprint": int((meta["shift_m"] > 0).sum()), "max_shift_m": float(meta["shift_m"].max())}
    ok["c"] = bool(svf.min() >= 0 and svf.max() <= 1 and 0.3 <= np.median(svf) <= 0.8
                   and res["c"]["median_hilltop_top5pct_elev"] > res["c"]["median_narrow_top10pct_bldg_cover"])

    fe = list(csv.DictReader(open(gz / "street_features.csv", newline="")))
    cols = ["wind_ratio", "fog_b0", "fog_b1", "fog_b2", "fog_b3"]
    fin = {c: float(np.mean([np.isfinite(float(r[c])) for r in fe])) for c in cols}
    res["d"] = {"finite_fraction": fin, "rows": len(fe), "rows_match_points": len(fe) == len(rows_all)}
    ok["d"] = len(fe) == len(rows_all) and min(fin.values()) >= 0.99

    # other zones: same statistics, reported (not gating). E-W segment pairs: south side vs north side sunlit hours on Dec 21.
    res["other_zones"] = {}
    for zn in zones:
        if zn["id"] == "potrero":
            continue
        s0, s1 = zn["row0"], zn["row1"]
        zr, zh, zs, zb = rows_all[s0:s1], hours_all[s0:s1], svf_all[s0:s1], meta_all["bcover30"][s0:s1]
        zz = np.array([float(r["z"]) for r in zr])
        res["other_zones"][zn["id"]] = {
            "points": s1 - s0, "pairs_ew": _fraction_pairs(zr, zh),
            "sunlit_hours_dec21_median": float(np.median(zh)), "sunlit_hours_dec21_p05_p95": [float(np.percentile(zh, 5)), float(np.percentile(zh, 95))],
            "svf_min": float(zs.min()), "svf_max": float(zs.max()), "svf_median": float(np.median(zs)),
            "svf_p05_p95": [float(np.percentile(zs, 5)), float(np.percentile(zs, 95))],
            "median_svf_top5pct_elev": float(np.median(zs[zz >= np.percentile(zz, 95)])),
            "median_svf_top10pct_bldg_cover": float(np.median(zs[zb >= np.percentile(zb, 90)])),
            "median_svf_bottom10pct_bldg_cover": float(np.median(zs[zb <= np.percentile(zb, 10)])),
            "elevation_m_min_max": [float(zz.min()), float(zz.max())],
            "points_moved_out_of_footprint": int((meta_all["shift_m"][s0:s1] > 0).sum())}

    res["pass"] = ok
    res["ALL_PASS"] = all(ok.values())
    json.dump(res, open(gz / "sun_check.json", "w"), indent=1)

    x = np.array([float(r["x"]) for r in rows])
    y = np.array([float(r["y"]) for r in rows])
    for zn in zones:
        if zn["id"] == "potrero":
            continue
        s0, s1 = zn["row0"], zn["row1"]
        for name, v, vmax, lab, cm in ((f"svf_map_{zn['id']}.png", svf_all[s0:s1], 1, "sky view factor", "viridis"),
                                       (f"sunlit_dec21_{zn['id']}.png", hours_all[s0:s1], max(1.0, float(hours_all[s0:s1].max())), "sunlit hours, Dec 21", "inferno")):
            fig, ax = plt.subplots(figsize=(7, 9), dpi=100)
            sc = ax.scatter([float(r["x"]) for r in rows_all[s0:s1]], [float(r["y"]) for r in rows_all[s0:s1]], c=v, s=3, cmap=cm, vmin=0, vmax=vmax)
            ax.set_aspect("equal")
            ax.set_title(f"{zn['name']}: {lab}")
            fig.colorbar(sc, shrink=0.7, label=lab)
            fig.savefig(gz / name)
            plt.close(fig)
    for name, v, vmin, vmax, lab, cm in (("svf_map.png", svf, 0, 1, "sky view factor", "viridis"),
                                         ("sunlit_dec21.png", hours, 0, max(1.0, float(hours.max())), "sunlit hours, Dec 21", "inferno")):
        fig, ax = plt.subplots(figsize=(9, 9), dpi=110)
        sc = ax.scatter(x, y, c=v, s=3, cmap=cm, vmin=vmin, vmax=vmax)
        ax.set_aspect("equal")
        ax.set_title(lab)
        fig.colorbar(sc, shrink=0.7, label=lab)
        fig.savefig(gz / name)
        plt.close(fig)
    for k in ("a", "b", "c", "d"):
        print(f"({k}) {'PASS' if ok[k] else 'FAIL'}")
    print("PASS" if res["ALL_PASS"] else "FAIL")
    print(json.dumps(res, indent=1))
    return res
