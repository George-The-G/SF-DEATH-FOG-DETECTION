"""Whole-city street points and the static live table (PLAN_LIVE_DATA.md step 2).

Nothing here touches the replay pipeline: the district tables (grids/zone/points.csv, street_*.csv, street_horizon.npy) stay as they
are and the city rows live in their own files, appended only in the static table:

  grids/zone/city_points.csv       OSM streets of the domain (routes.RIDE classes), land only, one sample every ~20 m, two lanes
                                   (+-5 m), same columns as points.csv; ids continue after the district rows
  grids/zone/city_features.npz     wind_ratio, refine_corr per city row (same formulas as street.build)
  grids/zone/city_sun.npz          horizon [n,72] float16, svf, z_obs, shift_m, porous_factor per city row
  <out>/live_static.npz            district rows first (unchanged), then city rows: x y z svf porous wind_ratio rel_wind horizon,
                                   way seg side zone street ids, tracer [9,N], tr80 [9,H,W], direction list

Sun horizons: the city is cut into 1.5 km tiles; each tile gets its own 2 m surface (DEM + building footprints) over the tile plus a
700 m apron, rays go 3 km (2 m surface inside the window, the 20 m grid surface outside it), as sun.build does per zone.
Run: python -m microclimate city [points] [features] [sun] [static] [--out DIR] (no step name = all steps).
"""
import csv
import json
import math
import time
from pathlib import Path

import numpy as np

from . import routes, sun

TILE_M = 1500.0
APRON_M = 700.0
STEP_M = 20.0
SIDE_M = 5.0
EXCLUDE_M = 5.0
LAND_MIN_M = 0.5
ZONE_IDS = {"potrero": 0, "ggh": 1, "city": 2}
CITY_BBOX = (37.690, -122.545, 37.835, -122.350)   # south, west, north, east (OSM fetch; clipped to the model domain afterwards)
FIELDS = ["id", "way", "seg", "side", "x", "y", "lat", "lon", "z", "street", "bearing", "slope_pct", "surface", "on_route", "zone", "route_mask"]


def _paths(data):
    data = Path(data)
    return data, data / "raw", data / "grids", data / "grids" / "zone"


def _spec(grids):
    return json.load(open(grids / "grid_spec.json"))


# ------------------------------------------------------------------ points
def build_points(data, step=STEP_M, side_m=SIDE_M, exclude_m=EXCLUDE_M, log=print):
    from pyproj import Transformer
    from scipy.spatial import cKDTree
    import rasterio
    from rasterio.windows import from_bounds
    t0 = time.time()
    data, raw, grids, zdir = _paths(data)
    spec = _spec(grids)
    ter = np.load(grids / "terrain_20m.npy")
    osm_path = raw / "osm_city.json"
    osm = routes.fetch_bbox(CITY_BBOX, osm_path)
    tr = Transformer.from_crs(4326, 32610, always_xy=True)
    nd = [e for e in osm["elements"] if e["type"] == "node"]
    nid = {e["id"]: i for i, e in enumerate(nd)}
    nx_, ny_ = tr.transform([e["lon"] for e in nd], [e["lat"] for e in nd])
    ways = sorted((e for e in osm["elements"] if e["type"] == "way" and e.get("tags", {}).get("highway") in routes.RIDE
                   and all(n in nid for n in e["nodes"])), key=lambda e: e["id"])
    # existing district rows: (way, seg) already sampled there, and positions for the 5 m rule
    ex_rows = list(csv.DictReader(open(zdir / "points.csv", newline="")))
    n0 = len(ex_rows)
    ex_seg = {(int(r["way"]), int(r["seg"])) for r in ex_rows}
    ex_tree = cKDTree(np.array([[float(r["x"]), float(r["y"])] for r in ex_rows]))
    # DEM window for the domain
    with rasterio.open(raw / "dem_n38w123.tif") as src:
        w = from_bounds(CITY_BBOX[1] - 0.01, CITY_BBOX[0] - 0.01, CITY_BBOX[3] + 0.01, CITY_BBOX[2] + 0.01, src.transform).round_offsets().round_lengths()
        arr = src.read(1, window=w).astype(np.float32)
        tw = src.window_transform(w)
    inv = ~tw
    cand = []   # (way, seg, k, px, py, nxdir, nydir, bearing, slope, way_tags)
    for wy in ways:
        nodes = wy["nodes"]
        for si, (a, b) in enumerate(zip(nodes, nodes[1:])):
            if (wy["id"], si) in ex_seg:
                continue
            x1, y1, x2, y2 = nx_[nid[a]], ny_[nid[a]], nx_[nid[b]], ny_[nid[b]]
            L = math.hypot(x2 - x1, y2 - y1)
            if L < 1:
                continue
            dx, dy = (x2 - x1) / L, (y2 - y1) / L
            n = max(1, int(round(L / step)))
            for k in range(n):
                f = (k + 0.5) / n
                cand.append((wy["id"], si, k, x1 + dx * L * f, y1 + dy * L * f, dx, dy, L, a, b))
    log(f"candidate centre samples {len(cand)} ({time.time() - t0:.0f} s)")
    cx = np.array([c[3] for c in cand])
    cy = np.array([c[4] for c in cand])
    # land + domain clip on the 20 m grid
    ci = np.floor((cx - spec["x0"]) / spec["cell_m"]).astype(int)
    cj = np.floor((cy - spec["y0"]) / spec["cell_m"]).astype(int)
    inside = (ci >= 0) & (cj >= 0) & (ci < spec["nx"]) & (cj < spec["ny"])
    land = np.zeros(len(cand), bool)
    land[inside] = ter[ci[inside], cj[inside]] > LAND_MIN_M
    lon, lat = tr.transform(cx, cy, direction="INVERSE")
    colf, rowf = inv * (lon, lat)
    rows_i = np.clip(np.floor(rowf).astype(int), 0, arr.shape[0] - 1)
    cols_i = np.clip(np.floor(colf).astype(int), 0, arr.shape[1] - 1)
    zc = arr[rows_i, cols_i]
    wtag = {e["id"]: e.get("tags", {}) for e in ways}
    out, pid = [], n0
    dropped_excl = 0
    for i, c in enumerate(cand):
        if not land[i]:
            continue
        way, si, k, px, py, dx, dy, L, a, b = c
        tags = wtag[way]
        bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 180
        slope = None
        for side, sgn in (("left", 1), ("right", -1)):
            sx, sy = px - dy * side_m * sgn, py + dx * side_m * sgn
            if ex_tree.query([sx, sy])[0] < exclude_m:
                dropped_excl += 1
                continue
            out.append((way, si, side, sx, sy, float(lat[i]), float(lon[i]), float(zc[i]), tags.get("name", ""), bearing, tags.get("surface", ""), a, b))
    # slopes need node elevations: sample the DEM at the segment ends
    nlon = np.array([e["lon"] for e in nd])
    nlat = np.array([e["lat"] for e in nd])
    cf, rf = inv * (nlon, nlat)
    nz_ = arr[np.clip(np.floor(rf).astype(int), 0, arr.shape[0] - 1), np.clip(np.floor(cf).astype(int), 0, arr.shape[1] - 1)]
    rows = []
    for way, si, side, sx, sy, la, lo, z, nm, brg, surf, a, b in out:
        L = math.hypot(nx_[nid[b]] - nx_[nid[a]], ny_[nid[b]] - ny_[nid[a]])
        slope = (float(nz_[nid[b]]) - float(nz_[nid[a]])) / L * 100
        rows.append({"id": pid, "way": way, "seg": si, "side": side, "x": round(sx, 1), "y": round(sy, 1), "lat": round(la, 6), "lon": round(lo, 6),
                     "z": round(z, 1), "street": nm, "bearing": round(brg, 1), "slope_pct": round(slope, 1), "surface": surf, "on_route": 0,
                     "zone": "city", "route_mask": 0})
        pid += 1
    with open(zdir / "city_points.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=FIELDS)
        wr.writeheader()
        wr.writerows(rows)
    length_km = sum(1 for _ in rows) / 2 * step / 1000
    info = {"city_rows": len(rows), "district_rows": n0, "total_rows": n0 + len(rows), "approx_street_km": round(length_km, 1),
            "dropped_near_district": dropped_excl, "candidates": len(cand), "land_samples": int(land.sum()), "seconds": round(time.time() - t0, 1)}
    log(json.dumps(info))
    return info


def read_city_points(zdir):
    return list(csv.DictReader(open(Path(zdir) / "city_points.csv", newline="", encoding="utf-8")))


# ------------------------------------------------------------------ wind ratio and refined correction (same formulas as street.build)
def wind_refine(data, x, y, log=print):
    from . import street
    data, raw, grids, zdir = _paths(data)
    lib = data / "library"
    spec = _spec(grids)
    solid = np.load(grids / "solid_20m.npz")["solid"]
    nx, ny, nz = solid.shape
    sw = solid.copy()
    sw[:, :, 0] = True
    kw = np.minimum(street.first_fluid_k(sw) + 1, nz - 1)
    ii, jj = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    u = np.load(lib / "wind_2700.npz")["u"]
    sp1 = np.sqrt(sum(u[a][ii, jj, kw].astype(np.float32) ** 2 for a in range(3)))
    del u
    valid = np.ones((nx, ny), bool)
    wind = street.bilinear_columns(sp1, x, y, spec, valid)
    ci = np.clip(np.round((x - spec["x0"]) / spec["cell_m"] - 0.5).astype(int), 0, nx - 1)
    cj = np.clip(np.round((y - spec["y0"]) / spec["cell_m"] - 0.5).astype(int), 0, ny - 1)
    refine = np.full(len(x), np.nan)
    rf = data / "refined" / "patch_corr_2700.npz"
    if rf.exists():
        corr = np.load(rf)["corr_bldg"].copy()
        rf25 = rf.with_name("patch_corr_2700_dx2p5.npz")
        if rf25.exists():
            c25 = np.load(rf25)["corr_bldg"]
            corr = np.where(np.isfinite(c25), c25, corr)
        refine = corr[ci, cj].astype(float)
    return wind, refine


def build_features(data, log=print):
    data, raw, grids, zdir = _paths(data)
    rows = read_city_points(zdir)
    x = np.array([float(r["x"]) for r in rows])
    y = np.array([float(r["y"]) for r in rows])
    wind, refine = wind_refine(data, x, y)
    np.savez(zdir / "city_features.npz", wind_ratio=wind.astype(np.float32), refine_corr=refine.astype(np.float32))
    info = {"rows": len(rows), "wind_finite": float(np.isfinite(wind).mean()), "refine_finite": float(np.isfinite(refine).mean()),
            "wind_median": float(np.nanmedian(wind))}
    log(json.dumps(info))
    return info


# ------------------------------------------------------------------ sun horizons by tile
def _fine_surface(src, geoms, hts, bnd, cx, cy, half):
    import rasterio
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    F = sun.FINE_M
    x0 = float(np.floor((cx - half) / F) * F)
    y0 = float(np.floor((cy - half) / F) * F)
    n = int(round(2 * half / F))
    tf = from_origin(x0, y0 + n * F, F, F)
    dem = np.full((n, n), np.nan, np.float32)
    reproject(rasterio.band(src, 1), dem, dst_transform=tf, dst_crs="EPSG:32610", resampling=Resampling.bilinear, dst_nodata=np.nan)
    dem = np.nan_to_num(dem, nan=0.0)
    dem[dem < 0] = 0
    sel = (bnd[:, 2] >= x0) & (bnd[:, 0] <= x0 + n * F) & (bnd[:, 3] >= y0) & (bnd[:, 1] <= y0 + n * F) & (hts > 0)
    idx = np.nonzero(sel)[0]
    idx = idx[np.argsort(hts[idx])]
    bh = rasterize([(geoms[i], float(hts[i])) for i in idx if not geoms[i].is_empty], out_shape=(n, n), transform=tf, fill=0.0, dtype="float32")
    flip = lambda a: np.flipud(a).T.copy()  # noqa: E731
    return {"dem": flip(dem), "bh": flip(bh), "x0": x0, "y0": y0, "n": n, "n_buildings": int(len(idx))}


def horizon_points(px_all, py_all, grids, raw, tile_m=TILE_M, apron_m=APRON_M, log=print, max_tiles=None):
    """Horizon [N,72] float16, z_obs, bcover30, shift for arbitrary points, tile by tile. Same ray marching as sun.build."""
    import rasterio
    import shapely
    from scipy.ndimage import distance_transform_edt, map_coordinates, uniform_filter
    from .geometry import load_buildings
    spec = _spec(grids)
    ter20 = np.load(grids / "terrain_20m.npy").astype(np.float32)
    surf20 = ter20 + np.load(grids / "bldg_mean_h_20m.npy").astype(np.float32)
    geoms, hts = load_buildings(raw / "bldg.geojson")
    bnd = shapely.bounds(geoms)
    N = len(px_all)
    horizon = np.zeros((N, sun.N_AZ), np.float16)
    z_obs_all = np.zeros(N, np.float32)
    bcover_all = np.zeros(N, np.float32)
    shift_all = np.zeros(N, np.float32)
    dist = sun.ray_distances()
    az = np.deg2rad(np.arange(sun.N_AZ) * sun.STEP_DEG)
    ex, ey = np.sin(az), np.cos(az)
    c = spec["cell_m"]
    F = sun.FINE_M
    lo = F * 2
    CH = 400
    tx = np.floor((px_all - spec["x0"]) / tile_m).astype(int)
    ty = np.floor((py_all - spec["y0"]) / tile_m).astype(int)
    keys = sorted(set(zip(tx.tolist(), ty.tolist())))
    if max_tiles:
        keys = keys[:max_tiles]
    t0 = time.time()
    with rasterio.open(raw / "dem_n38w123.tif") as src:
        for ti, (a, b) in enumerate(keys):
            sl = np.nonzero((tx == a) & (ty == b))[0]
            cx0 = spec["x0"] + (a + 0.5) * tile_m
            cy0 = spec["y0"] + (b + 0.5) * tile_m
            half = tile_m / 2 + apron_m
            fs = _fine_surface(src, geoms, hts, bnd, cx0, cy0, half)
            surf2 = fs["dem"] + fs["bh"]
            bmask = fs["bh"] > 0.5
            px, py = px_all[sl].copy(), py_all[sl].copy()
            n = len(px)
            fi = np.clip(((px - fs["x0"]) / F).astype(int), 0, fs["n"] - 1)
            fj = np.clip(((py - fs["y0"]) / F).astype(int), 0, fs["n"] - 1)
            inside = bmask[fi, fj]
            shift = np.zeros(n)
            if inside.any():
                _, (ii, jj) = distance_transform_edt(bmask, return_indices=True)
                ni, nj = ii[fi, fj], jj[fi, fj]
                shift = np.where(inside, np.hypot((ni - fi) * F, (nj - fj) * F), 0.0)
                px = np.where(inside, fs["x0"] + (ni + 0.5) * F, px)
                py = np.where(inside, fs["y0"] + (nj + 0.5) * F, py)
                fi = np.clip(((px - fs["x0"]) / F).astype(int), 0, fs["n"] - 1)
                fj = np.clip(((py - fs["y0"]) / F).astype(int), 0, fs["n"] - 1)
            z_obs = map_coordinates(fs["dem"], [(px - fs["x0"]) / F - 0.5, (py - fs["y0"]) / F - 0.5], order=1, mode="nearest") + sun.OBS_H
            bcov = uniform_filter(bmask.astype(np.float32), size=31, mode="nearest")[fi, fj]
            hor = np.zeros((n, sun.N_AZ), np.float32)
            for s in range(0, n, CH):
                cs = slice(s, min(n, s + CH))
                X = px[cs, None, None] + ex[None, :, None] * dist[None, None, :]
                Y = py[cs, None, None] + ey[None, :, None] * dist[None, None, :]
                fx, fy = (X - fs["x0"]) / F - 0.5, (Y - fs["y0"]) / F - 0.5
                inw = (fx > lo) & (fy > lo) & (fx < fs["n"] - 1 - lo) & (fy < fs["n"] - 1 - lo)
                H = map_coordinates(surf20, [(X - spec["x0"]) / c - 0.5, (Y - spec["y0"]) / c - 0.5], order=1, mode="nearest")
                if inw.any():
                    H[inw] = map_coordinates(surf2, [fx[inw], fy[inw]], order=1, mode="nearest")
                ang = np.degrees(np.arctan2(H - z_obs[cs, None, None], dist[None, None, :]))
                hor[cs] = ang.max(axis=2)
            horizon[sl] = np.clip(hor, -90, 90).astype(np.float16)
            z_obs_all[sl], bcover_all[sl], shift_all[sl] = z_obs, bcov, shift
            el = time.time() - t0
            log(f"  tile {ti + 1}/{len(keys)} ({a},{b}) {n} pts, {fs['n_buildings']} bldgs, elapsed {el:.0f} s, projected {el / (ti + 1) * len(keys):.0f} s")
    return horizon, z_obs_all, bcover_all, shift_all


def porous_factors(raw, rows, log=print):
    """Porous factor per city row: OSM surface tag of the way, else the nearest tagged way within 20 m (as sun.surface_table)."""
    import shapely
    from pyproj import Transformer
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    osm = json.load(open(raw / "osm_city.json", encoding="utf-8"))
    t = Transformer.from_crs(4326, 32610, always_xy=True)
    nodes = {e["id"]: t.transform(e["lon"], e["lat"]) for e in osm["elements"] if e["type"] == "node"}
    ways = [e for e in osm["elements"] if e["type"] == "way" and all(n in nodes for n in e["nodes"])]
    tag = {w["id"]: (w.get("tags", {}).get("surface") or "") for w in ways}
    tagged = [w for w in ways if tag[w["id"]] and w.get("tags", {}).get("highway") in routes.RIDE]
    lines = [LineString([nodes[n] for n in w["nodes"]]) for w in tagged]
    tree = STRtree(lines) if lines else None
    out = np.ones(len(rows), np.float32)
    for i, r in enumerate(rows):
        s = tag.get(int(r["way"]), "")
        if not s and r.get("surface"):
            s = r["surface"]
        if not s and tree is not None:
            p = shapely.Point(float(r["x"]), float(r["y"]))
            j = int(tree.nearest(p))
            if lines[j].distance(p) <= 20.0:
                s = tag[tagged[j]["id"]]
        if s in sun.POROUS_SURFACES:
            out[i] = sun.POROUS_FACTOR
    return out


def build_sun(data, max_tiles=None, log=print):
    data, raw, grids, zdir = _paths(data)
    rows = read_city_points(zdir)
    x = np.array([float(r["x"]) for r in rows])
    y = np.array([float(r["y"]) for r in rows])
    t0 = time.time()
    hor, zo, bc, sh = horizon_points(x, y, grids, raw, log=log, max_tiles=max_tiles)
    svf = (np.cos(np.deg2rad(np.clip(hor.astype(np.float32), 0, 90))) ** 2).mean(axis=1).astype(np.float32)
    porous = porous_factors(raw, rows)
    np.savez(zdir / ("city_sun.npz" if not max_tiles else "city_sun_partial.npz"), horizon=hor, svf=svf, z_obs=zo, bcover30=bc, shift_m=sh, porous_factor=porous)
    info = {"rows": len(rows), "svf_median": float(np.median(svf)), "porous_share": float((porous < 1).mean()), "seconds": round(time.time() - t0, 1)}
    log(json.dumps(info))
    return info


# ------------------------------------------------------------------ static table
def build_static(data, out_path, log=print):
    from . import dirlib, fogtop, replay, wetness
    data, raw, grids, zdir = _paths(data)
    t0 = time.time()
    ex = list(csv.DictReader(open(zdir / "points.csv", newline="")))
    ci = read_city_points(zdir)
    allr = ex + ci
    N, n0 = len(allr), len(ex)
    for k, r in enumerate(allr):
        assert int(r["id"]) == k
    fe = list(csv.DictReader(open(zdir / "street_features.csv", newline="")))
    sur = list(csv.DictReader(open(zdir / "street_surface.csv", newline="")))
    cf = np.load(zdir / "city_features.npz")
    cs = np.load(zdir / "city_sun.npz")
    f = lambda k, rows: np.array([float(r[k]) for r in rows])  # noqa: E731
    refine = np.concatenate([f("refine_corr", fe), cf["refine_corr"].astype(float)])
    wind = np.concatenate([f("wind_ratio", fe), cf["wind_ratio"].astype(float)])
    svf = np.concatenate([np.load(zdir / "street_svf.npy").astype(np.float32), cs["svf"]])
    hor = np.concatenate([np.load(zdir / "street_horizon.npy").astype(np.float16), cs["horizon"]])
    porous = np.concatenate([f("porous_factor", sur).astype(np.float32), cs["porous_factor"]])
    x, y, z = f("x", allr), f("y", allr), f("z", allr)
    names, nidx, street = [], {}, np.zeros(N, np.uint32)
    for i, r in enumerate(allr):
        nm = r["street"]
        if nm not in nidx:
            nidx[nm] = len(names)
            names.append(nm)
        street[i] = nidx[nm]
    zone = np.array([ZONE_IDS.get(r["zone"], 255) for r in allr], np.uint8)
    pts = dict(x=x, y=y, z=z)
    lib = data / "library"
    dirs = dirlib.available(lib)
    log(f"directions {dirs}")
    tracer = np.zeros((len(dirs), N), np.float32)
    tr80 = []
    for k, d in enumerate(dirs):
        tracer[k] = fogtop.points_tracer(pts, data, d)
        tr80.append(replay.city_tracer_80m(data, d))
        pts.pop("_tracer_profile", None)
        pts.pop(f"_tracer_profile_{int(round(d * 10)):04d}", None)
        log(f"  tracer dir {d} done ({time.time() - t0:.0f} s)")
    np.savez_compressed(
        out_path, x=x, y=y, z=z.astype(np.float32), svf=svf.astype(np.float32), porous=porous.astype(np.float32),
        wind_ratio=wind.astype(np.float32), refine_corr=refine.astype(np.float32), rel_wind=wetness.refined_rel(refine),
        horizon=hor, way=np.array([int(r["way"]) for r in allr], np.int64), seg=np.array([int(r["seg"]) for r in allr], np.int32),
        side=np.array([0 if r["side"] == "left" else 1 for r in allr], np.uint8), zone=zone, street=street,
        street_names=np.array(names), bearing=f("bearing", allr).astype(np.float32), tracer=tracer, tr80=np.stack(tr80).astype(np.float32),
        dirs=np.array(dirs, np.float32), n_district=np.array(n0), zone_ids=np.array(json.dumps(ZONE_IDS)))
    sz = Path(out_path).stat().st_size
    info = {"N": N, "N_district": n0, "N_city": N - n0, "file_mb": round(sz / 1e6, 1), "seconds": round(time.time() - t0, 1)}
    log(json.dumps(info))
    return info


def load_static(path):
    """live_static.npz -> point dict usable by wetness.simulate (plus the extra arrays)."""
    z = np.load(path, allow_pickle=False)
    P = {k: z[k] for k in z.files}
    P["horizon"] = P["horizon"].astype(np.float32)
    return P


def run(data, argv):
    steps = [a for a in argv if a in ("points", "features", "sun", "static")] or ["points", "features", "sun", "static"]
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path(data).parent.parent / "deathfog-data" / "live"
    mt = int(argv[argv.index("--max-tiles") + 1]) if "--max-tiles" in argv else None
    if "points" in steps:
        build_points(data)
    if "features" in steps:
        build_features(data)
    if "sun" in steps:
        build_sun(data, max_tiles=mt)
    if "static" in steps:
        out.mkdir(parents=True, exist_ok=True)
        build_static(data, out / "live_static.npz")
    return 0
