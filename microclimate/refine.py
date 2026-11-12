"""Variable-resolution refinement: one-way nested fine-grid runs that produce a drop-in street-wind correction layer.

For each patch (a tile of 20 m cells, default 30x30 cells = 600 m core + 100 m padding on every side) a fine LBM run
(XLB, same solver settings as wind.py) is driven by the 20 m result library/wind_<dir*10:04d>.npz. Footprints are voxelised
at fine resolution over 3DEP terrain. Output is NOT a replacement wind field but a correction layer on the 20 m grid:

    refined/patch_corr_<dir*10:04d>.npz      arrays [855, 811] (same indexing as the 20 m grid, x east, y north), NaN where not refined
        corr_street   float32  fine street-level speed / coarse reference speed. The main pipeline multiplies its street
                               wind ratio by this where finite.
        corr_shelter  float32  fine mean speed in the lowest 10 m (open cells) / coarse reference speed (residence proxy:
                               lower = more sheltered, air stays longer)
        tke_street    float32  resolved turbulent kinetic energy at street level, in (u/U_in)^2 units (0.5*sum var(u_i))
        street_fine   float32  fine-run mean speed at street level (1.5-5 m above ground, open columns), u/U_in
        street_ref    float32  coarse reference: 20 m run mean speed in the lowest fluid voxel of the column, u/U_in
        bcover        float32  fraction of the 20 m cell covered by building footprint columns in the fine grid
        patch_id      int16    index into the "patches" list of the JSON (-1 = not refined)
    refined/patch_corr_<dir*10:04d>.json     diagnostics: patches, fine resolution, runtime, rho range

Definitions: "street level" = heights 1.5..5 m above local ground (sampled every 0.5 m by linear interpolation in the
column), "open" = fine columns whose fine cell is not a building footprint (cover < 0.5). The 20 m cell value is the mean over
its open fine columns (NaN if fewer than 2 or if the coarse reference speed < 0.05 U_in).

CLI: python -m microclimate refine --direction 270 --zone potrero|all [--dx 5] [--steps N] [--max N] [--force]
Resumable: finished patches are cached in refined/patches/d<dir*10:04d>_dx<dx>/<id>.npz and skipped.
"""
import json
import math
import pickle
import time
from pathlib import Path

import numpy as np
import warp as wp
import xlb
from scipy.ndimage import binary_erosion, distance_transform_edt, map_coordinates
from xlb.compute_backend import ComputeBackend
from xlb.grid import grid_factory
from xlb.operator.boundary_condition import HalfwayBounceBackBC
from xlb.operator.macroscopic import Macroscopic
from xlb.operator.stepper import IncompressibleNavierStokesStepper
from xlb.precision_policy import PrecisionPolicy

from . import geometry as G

U_LAT = 0.05
CORE_CELLS = 30     # 600 m core
PAD_CELLS = 5       # 100 m padding
STREET_H = np.arange(1.5, 5.01, 0.5)
SHELTER_H = np.arange(1.0, 10.0, 2.0)
DATA = Path.home() / "deathfog-data"


# ---------------------------------------------------------------- planning
def load_grids(grids_dir):
    grids_dir = Path(grids_dir)
    spec = json.load(open(grids_dir / "grid_spec.json"))
    ter = np.load(grids_dir / "terrain_20m.npy")
    mean = np.load(grids_dir / "bldg_mean_h_20m.npy").astype(np.float32)
    solid = np.load(grids_dir / "solid_20m.npz")["solid"]
    return spec, ter, mean, solid


def plan_patches(cfg, grids_dir, core=CORE_CELLS, min_cand=150, cand_h=0.75):
    """Tiles of core x core 20 m cells that hold at least min_cand 'invisible' building cells (cell-mean building height >=
    cand_h m, which at 20 m means partial/low cover that rarely becomes a voxel). Potrero tiles (core rectangle within the zone
    radius) come first, sorted by distance, then the rest by candidate count."""
    spec, ter, mean, solid = load_grids(grids_dir)
    nx, ny, c = spec["nx"], spec["ny"], spec["cell_m"]
    cand = mean >= cand_h
    z = cfg["zone"]["potrero"]
    zx, zy = G.to_utm().transform(z["start"][1], z["start"][0])
    out = []
    for ix in range(0, nx, core):
        for iy in range(0, ny, core):
            ncx, ncy = min(core, nx - ix), min(core, ny - iy)
            n = int(cand[ix:ix + ncx, iy:iy + ncy].sum())
            if n < min_cand:
                continue
            x0, y0 = spec["x0"] + ix * c, spec["y0"] + iy * c
            dxr = max(x0 - zx, 0, zx - (x0 + ncx * c))
            dyr = max(y0 - zy, 0, zy - (y0 + ncy * c))
            d = math.hypot(dxr, dyr)
            out.append({"id": f"p{ix:04d}_{iy:04d}", "ix0": ix, "iy0": iy, "ncx": ncx, "ncy": ncy, "n_cand": n,
                        "zone": "potrero" if d <= z["radius_m"] else "other", "dist_zone_m": round(d)})
    out.sort(key=lambda p: (p["zone"] != "potrero", p["dist_zone_m"] if p["zone"] == "potrero" else -p["n_cand"]))
    return out


# ---------------------------------------------------------------- fine geometry
_BLD = {}


def load_bldg_cache(raw_dir, cache_dir):
    if "geoms" in _BLD:
        return _BLD["geoms"], _BLD["h"], _BLD["tree"]
    import shapely
    cache = Path(cache_dir) / "bldg_utm.pkl"
    if cache.exists():
        geoms, h = pickle.load(open(cache, "rb"))
    else:
        geoms, h = G.load_buildings(Path(raw_dir) / "bldg.geojson")
        bad = ~shapely.is_valid(geoms)
        geoms[bad] = shapely.make_valid(geoms[bad])
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        pickle.dump((geoms, h), open(cache, "wb"), protocol=4)
    tree = shapely.STRtree(geoms)
    _BLD.update(geoms=geoms, h=h, tree=tree)
    return geoms, h, tree


def fine_terrain(dem_path, x0, y0, nx, ny, dx):
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    out = np.full((ny, nx), np.nan, dtype=np.float32)
    with rasterio.open(dem_path) as src:
        reproject(rasterio.band(src, 1), out, dst_transform=from_origin(x0, y0 + ny * dx, dx, dx), dst_crs="EPSG:32610",
                  resampling=Resampling.bilinear, dst_nodata=np.nan)
    out = np.nan_to_num(out, nan=0.0)
    out[out < 0] = 0
    return np.flipud(out).T.copy()


def fine_buildings(geoms, h, tree, x0, y0, nx, ny, dx, sub=5):
    """Cover fraction and mean height of covered sub-pixels per fine cell, arrays [nx, ny]. Footprints rasterised at dx/sub."""
    import shapely
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    ids = tree.query(shapely.box(x0, y0, x0 + nx * dx, y0 + ny * dx))
    ids = ids[h[ids] > 0]
    ids = ids[np.argsort(h[ids])]
    shapes = [(geoms[i], float(h[i])) for i in ids if not geoms[i].is_empty]
    f = dx / sub
    if shapes:
        fine = rasterize(shapes, out_shape=(ny * sub, nx * sub), transform=from_origin(x0, y0 + ny * dx, f, f), fill=0.0,
                         dtype="float32")
    else:
        fine = np.zeros((ny * sub, nx * sub), np.float32)
    b = fine.reshape(ny, sub, nx, sub)
    cover = (b > 0).mean(axis=(1, 3))
    hb = b.sum(axis=(1, 3)) / np.maximum((b > 0).sum(axis=(1, 3)), 1)
    flip = lambda a: np.flipud(a).T.copy()
    return flip(cover), flip(hb)


def build_tile_geometry(spec, patch, dx, raw_dir, cache_dir, dem_path, nobuild=False, pad=PAD_CELLS, headroom=100.0):
    c = spec["cell_m"]
    r = int(round(c / dx))
    ia, ib = max(patch["ix0"] - pad, 0), min(patch["ix0"] + patch["ncx"] + pad, spec["nx"])
    ja, jb = max(patch["iy0"] - pad, 0), min(patch["iy0"] + patch["ncy"] + pad, spec["ny"])
    nx, ny = (ib - ia) * r, (jb - ja) * r
    x0, y0 = spec["x0"] + ia * c, spec["y0"] + ja * c
    ter = fine_terrain(dem_path, x0, y0, nx, ny, dx)
    if nobuild:
        cover, hb = np.zeros((nx, ny), np.float32), np.zeros((nx, ny), np.float32)
    else:
        geoms, h, tree = load_bldg_cache(raw_dir, cache_dir)
        cover, hb = fine_buildings(geoms, h, tree, x0, y0, nx, ny, dx)
    bmask = cover >= 0.5
    top = ter + np.where(bmask, hb, 0.0)
    z_base = math.floor(ter.min() / dx) * dx - dx
    nz = int(math.ceil((top.max() + headroom - z_base) / dx))
    zc = z_base + (np.arange(nz) + 0.5) * dx
    solid = zc[None, None, :] < top[:, :, None]
    solid[:, :, 0] = True
    return {"solid": solid, "ter": ter, "bmask": bmask, "cover": cover, "z_base": z_base, "dx": dx, "x0": x0, "y0": y0,
            "ia": ia, "ja": ja, "ib": ib, "jb": jb, "r": r, "nx": nx, "ny": ny, "nz": nz}


def coarse_on_fine(u_c, solid_c, geo, c=20.0):
    """Trilinear sample of the 20 m ratio field (solid cells filled with the nearest fluid value) at fine cell centres.
    Returns float32 [3, nx, ny, nz] in u/U_in units."""
    nxc, nyc, nzc = solid_c.shape
    dx, r, nz = geo["dx"], geo["r"], geo["nz"]
    zmax = geo["z_base"] + nz * dx
    ka = 0
    kb = min(int(zmax / c) + 3, nzc)
    ia, ib, ja, jb = max(geo["ia"] - 2, 0), min(geo["ib"] + 2, nxc), max(geo["ja"] - 2, 0), min(geo["jb"] + 2, nyc)
    sol = solid_c[ia:ib, ja:jb, ka:kb]
    idx = distance_transform_edt(sol, return_distances=False, return_indices=True)
    uf = np.empty((3,) + sol.shape, np.float32)
    for q in range(3):
        sub = u_c[q, ia:ib, ja:jb, ka:kb].astype(np.float32)
        uf[q] = sub[idx[0], idx[1], idx[2]]
    gx = geo["ia"] + (np.arange(geo["nx"]) + 0.5) * dx / c - 0.5 - ia
    gy = geo["ja"] + (np.arange(geo["ny"]) + 0.5) * dx / c - 0.5 - ja
    gz = (geo["z_base"] + (np.arange(nz) + 0.5) * dx) / c - 0.5 - ka
    X, Y, Z = np.meshgrid(gx, gy, gz, indexing="ij")
    coords = np.stack([X, Y, Z]).astype(np.float32)
    del X, Y, Z
    out = np.empty((3, geo["nx"], geo["ny"], nz), np.float32)
    for q in range(3):
        out[q] = map_coordinates(uf[q], coords, order=1, mode="nearest")
    return out


# ---------------------------------------------------------------- solver
@wp.kernel
def nudge_kernel(f: wp.array4d(dtype=wp.float32), u: wp.array4d(dtype=wp.float32), w: wp.array3d(dtype=wp.float32),
                 cx: wp.array(dtype=wp.float32), cy: wp.array(dtype=wp.float32), cz: wp.array(dtype=wp.float32),
                 wt: wp.array(dtype=wp.float32)):
    i, j, k = wp.tid()
    a = w[i, j, k]
    if a <= 0.0:
        return
    ux = u[0, i, j, k]
    uy = u[1, i, j, k]
    uz = u[2, i, j, k]
    usq = ux * ux + uy * uy + uz * uz
    for q in range(19):
        cu = cx[q] * ux + cy[q] * uy + cz[q] * uz
        feq = wt[q] * (1.0 + 3.0 * cu + 4.5 * cu * cu - 1.5 * usq)
        f[q, i, j, k] = (1.0 - a) * f[q, i, j, k] + a * feq


def sponge_weights(solid, width):
    """1 on the outer layer of the 4 lateral faces and the top, decaying quadratically to 0 over `width` cells; 0 on solids
    (except the outer layer, which is always driven)."""
    nx, ny, nz = solid.shape
    ii, jj, kk = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij")
    d = np.minimum.reduce([ii, nx - 1 - ii, jj, ny - 1 - jj, nz - 1 - kk]).astype(np.float32)
    w = np.clip(1.0 - d / width, 0.0, 1.0) ** 2
    w[solid & (d > 0)] = 0.0
    w[:, :, 0] = 0.0
    return w.astype(np.float32), (d == 0)


def run_fine(geo, u_c, solid_c, direction_deg, steps=20000, avg_steps=5000, avg_every=10, omega=1.95, sponge_m=40.0,
             log=print, check_every=500, rho_lim=(0.9, 1.1)):
    solid = geo["solid"].copy()
    nx, ny, nz = solid.shape
    dx = geo["dx"]
    t0 = time.time()
    ub = coarse_on_fine(u_c, solid_c, geo) * U_LAT
    w, outer = sponge_weights(solid, max(sponge_m / dx, 3.0))
    ub[:, solid & outer] = 0.0
    ub[:, solid & ~outer] = 0.0
    # ground cells below: keep floor solid; mark outer solid cells as non-bounce-back (they are driven)
    shell = solid & ~binary_erosion(solid, structure=np.ones((3, 3, 3), bool), border_value=1)
    shell &= ~outer
    prec = PrecisionPolicy.FP32FP32
    be = ComputeBackend.WARP
    vs = xlb.velocity_set.D3Q19(precision_policy=prec, compute_backend=be)
    xlb.init(velocity_set=vs, default_backend=be, default_precision_policy=prec)
    # XLB 0.3.2 numbers BC ids from a process-global counter, and a stepper built for a later run in the same process silently
    # applies NO boundary condition (the cached kernel is bound to id 1). Reset the registry so every run gets id 1.
    from xlb.operator.boundary_condition.boundary_condition_registry import boundary_condition_registry as _reg
    _reg.next_id = 1
    _reg.id_to_bc.clear()
    _reg.bc_to_id.clear()
    grid = grid_factory((nx, ny, nz), compute_backend=be)
    i = np.where(shell)
    bcs = [HalfwayBounceBackBC(indices=[a.tolist() for a in i])]
    stepper = IncompressibleNavierStokesStepper(grid=grid, boundary_conditions=bcs, collision_type="SmagorinskyLESBGK")
    f0, f1, bc_mask, missing = stepper.prepare_fields()
    macro = Macroscopic(compute_backend=be, precision_policy=prec, velocity_set=vs)
    rho_f = grid.create_field(cardinality=1, dtype=prec.store_precision)
    u_f = grid.create_field(cardinality=3, dtype=prec.store_precision)
    c = np.asarray(vs.c, dtype=np.float32).reshape(3, 19)
    wt = np.asarray(vs.w, dtype=np.float32)
    dev = wp.get_device()
    ud = wp.array(np.ascontiguousarray(ub), dtype=wp.float32, device=dev)
    wd = wp.array(w, dtype=wp.float32, device=dev)
    cx, cy, cz, wtd = (wp.array(a, dtype=wp.float32, device=dev) for a in (c[0], c[1], c[2], wt))
    w_all = wp.array(np.ones_like(w), dtype=wp.float32, device=dev)

    def nudge(f, wa):
        wp.launch(nudge_kernel, dim=(nx, ny, nz), inputs=[f, ud, wa, cx, cy, cz, wtd], device=dev)

    nudge(f0, w_all)  # initial condition = coarse field everywhere
    nudge(f1, w_all)
    fluid = ~solid
    s1 = s2 = None
    n_acc = 0
    rmin, rmax = 9.0, 0.0
    status = "ok"
    for s in range(steps):
        f0, f1 = stepper(f0, f1, bc_mask, missing, omega, s)
        f0, f1 = f1, f0
        nudge(f0, wd)
        if s % check_every == 0 or s == steps - 1:
            macro(f0, rho_f, u_f)
            wp.synchronize()
            rho = rho_f.numpy()[0]
            uu = u_f.numpy()
            if not (np.isfinite(rho).all() and np.isfinite(uu).all()):
                status = f"nan at step {s}"
                break
            rr = rho[fluid]
            rmin, rmax = min(rmin, float(rr.min())), max(rmax, float(rr.max()))
            if rr.min() < rho_lim[0] or rr.max() > rho_lim[1]:
                status = f"rho out of range at step {s}: [{rr.min():.3f},{rr.max():.3f}]"
                break
            if s % (check_every * 4) == 0:
                log(f"  step {s}/{steps} {time.time() - t0:.0f}s rho[{rr.min():.4f},{rr.max():.4f}] max|u|={np.sqrt((uu ** 2).sum(0)).max() / U_LAT:.3f}")
        if s >= steps - avg_steps and s % avg_every == 0:
            macro(f0, rho_f, u_f)
            wp.synchronize()
            uu = u_f.numpy().astype(np.float64)
            s1 = uu if s1 is None else s1 + uu
            s2 = uu ** 2 if s2 is None else s2 + uu ** 2
            n_acc += 1
    diag = {"dx": dx, "shape": [nx, ny, nz], "steps": steps, "avg_samples": n_acc, "seconds": round(time.time() - t0, 1),
            "rho_min": rmin, "rho_max": rmax, "status": status}
    if status != "ok" or n_acc == 0:
        return None, None, diag
    m1 = s1 / n_acc
    var = s2 / n_acc - m1 ** 2
    mean = (m1 / U_LAT).astype(np.float32)
    mean[:, solid] = 0.0
    tke = (0.5 * np.maximum(var, 0).sum(0) / U_LAT ** 2).astype(np.float32)
    tke[solid] = 0.0
    return mean, tke, diag


# ---------------------------------------------------------------- analysis
def column_sample(F, ter, k0, hs, z_base, dx):
    """Mean over heights hs above ground of F[nx,ny,nz], linear in z; below the first fluid cell the first value is used."""
    nz = F.shape[2]
    acc = 0.0
    for h in hs:
        kk = (ter + h - z_base) / dx - 0.5
        kk = np.clip(np.maximum(kk, k0), k0, nz - 2)
        i0 = np.floor(kk).astype(np.int64)
        fr = kk - i0
        a = np.take_along_axis(F, i0[..., None], 2)[..., 0]
        b = np.take_along_axis(F, (i0 + 1)[..., None], 2)[..., 0]
        acc = acc + a * (1 - fr) + b * fr
    return acc / len(hs)


def coarse_reference(u_c, solid_c):
    """Mean speed (u/U_in) in the lowest fluid voxel of each 20 m column (the run forces k=0 to be solid floor)."""
    sol = solid_c.copy()
    sol[:, :, 0] = True
    k0 = np.argmax(~sol, axis=2)
    ii, jj = np.indices(k0.shape)
    return np.sqrt((u_c[:, ii, jj, k0].astype(np.float32) ** 2).sum(0))


def analyse(geo, umean, tke, patch, ref):
    """Per 20 m core cell: street/shelter speed, tke, cover. Returns dict of [ncx, ncy] float32 arrays."""
    dx, r, zb = geo["dx"], geo["r"], geo["z_base"]
    solid, ter, bm = geo["solid"], geo["ter"], geo["bmask"]
    speed = np.sqrt((umean ** 2).sum(0))
    k0 = np.argmax(~solid[:, :, 1:], axis=2) + 1
    st = column_sample(speed, ter, k0, STREET_H, zb, dx)
    sh = column_sample(speed, ter, k0, SHELTER_H, zb, dx)
    tk = column_sample(tke, ter, k0, STREET_H, zb, dx)
    o = (~bm).astype(np.float32)
    ox, oy = (patch["ix0"] - geo["ia"]) * r, (patch["iy0"] - geo["ja"]) * r
    nxf, nyf = patch["ncx"] * r, patch["ncy"] * r
    sl = (slice(ox, ox + nxf), slice(oy, oy + nyf))

    def agg(a, wgt):
        a = (a * wgt)[sl].reshape(patch["ncx"], r, patch["ncy"], r).sum(axis=(1, 3))
        return a

    cnt = agg(np.ones_like(o), o)
    safe = np.maximum(cnt, 1)
    res = {}
    cov = geo["cover"][sl].reshape(patch["ncx"], r, patch["ncy"], r).mean(axis=(1, 3))
    refc = ref[patch["ix0"]:patch["ix0"] + patch["ncx"], patch["iy0"]:patch["iy0"] + patch["ncy"]]
    ok = (cnt >= 2) & (refc > 0.05)
    for name, a in (("street_fine", st), ("shelter_fine", sh), ("tke_street", tk)):
        v = agg(a, o) / safe
        res[name] = np.where(cnt >= 2, v, np.nan).astype(np.float32)
    res["street_ref"] = refc.astype(np.float32)
    res["corr_street"] = np.where(ok, res["street_fine"] / np.maximum(refc, 1e-6), np.nan).astype(np.float32)
    res["corr_shelter"] = np.where(ok, res["shelter_fine"] / np.maximum(refc, 1e-6), np.nan).astype(np.float32)
    res["bcover"] = cov.astype(np.float32)
    return res


# ---------------------------------------------------------------- driver
def wind_ok(lib, direction):
    f = Path(lib) / f"wind_{int(direction * 10):04d}.npz"
    j = Path(lib) / f"wind_{int(direction * 10):04d}.json"
    if not (f.exists() and j.exists()):
        return False
    d = json.load(open(j))
    return (not d.get("nan", True)) and d.get("max_speed_ratio", 0) > 0


def run_patch(spec, patch, dx, direction, u_c, solid_c, ref, raw_dir, cache_dir, dem, steps, avg_steps, log, nobuild=False, omega=1.95,
              baseline=True):
    geo = build_tile_geometry(spec, patch, dx, raw_dir, cache_dir, dem, nobuild=nobuild)
    log(f"{patch['id']} dx={dx} fine grid {geo['nx']}x{geo['ny']}x{geo['nz']} ({geo['nx'] * geo['ny'] * geo['nz'] / 1e6:.1f} M cells), "
        f"solid {geo['solid'].mean():.3f}")
    mean, tke, diag = run_fine(geo, u_c, solid_c, direction, steps=steps, avg_steps=avg_steps, log=log, omega=omega)
    if mean is None:
        return None, diag, None
    res = analyse(geo, mean, tke, patch, ref)
    if baseline and not nobuild:
        # same run without buildings (terrain only): isolates the building effect from the wall-profile of the fine grid
        geo0 = build_tile_geometry(spec, patch, dx, raw_dir, cache_dir, dem, nobuild=True)
        m0, k0_, d0 = run_fine(geo0, u_c, solid_c, direction, steps=steps, avg_steps=avg_steps, log=log, omega=omega)
        diag["baseline"] = d0
        if m0 is None:
            return None, {**diag, "status": "baseline failed: " + d0["status"]}, None
        r0 = analyse(geo0, m0, k0_, patch, ref)
        res["street_base"] = r0["street_fine"]
        res["corr_bldg"] = np.where(np.isfinite(res["street_fine"]) & (r0["street_fine"] > 0.02), res["street_fine"] / np.maximum(r0["street_fine"], 1e-6), np.nan).astype(np.float32)
        res["shelter_base"] = r0["shelter_fine"]
        res["corr_bldg_shelter"] = np.where(np.isfinite(res["shelter_fine"]) & (r0["shelter_fine"] > 0.02), res["shelter_fine"] / np.maximum(r0["shelter_fine"], 1e-6), np.nan).astype(np.float32)
        diag["seconds"] = round(diag["seconds"] + d0["seconds"], 1)
        diag["rho_min"] = min(diag["rho_min"], d0["rho_min"])
        diag["rho_max"] = max(diag["rho_max"], d0["rho_max"])
    return res, diag, (geo, mean, tke)


def refine(cfg, direction, zone="potrero", dx=5.0, steps=20000, avg_steps=5000, max_patches=None, force=False, log=print,
           data=DATA, omega=1.95):
    data = Path(data)
    lib, grids, raw, out = data / "library", data / "grids", data / "raw", data / "refined"
    if not wind_ok(lib, direction):
        raise SystemExit(f"wind_{int(direction * 10):04d}.npz missing or marked nan: not refining direction {direction}")
    tag = f"{int(direction * 10):04d}"
    pdir = out / "patches" / f"d{tag}_dx{dx:g}"
    pdir.mkdir(parents=True, exist_ok=True)
    spec, ter, mean_h, solid_c = load_grids(grids)
    u_c = np.load(lib / f"wind_{tag}.npz")["u"]
    ref = coarse_reference(u_c, solid_c)
    patches = plan_patches(cfg, grids)
    if zone == "potrero":
        patches = [p for p in patches if p["zone"] == "potrero"]
    if max_patches:
        patches = patches[:max_patches]
    log(f"{len(patches)} patches for direction {direction}, zone={zone}, dx={dx}")
    for n, p in enumerate(patches):
        f = pdir / f"{p['id']}.npz"
        if f.exists() and not force:
            continue
        log(f"[{n + 1}/{len(patches)}] {p['id']} zone={p['zone']} cand={p['n_cand']}")
        res, diag, _ = run_patch(spec, p, dx, direction, u_c, solid_c, ref, raw, out / "cache", raw / "dem_n38w123.tif", steps,
                                 avg_steps, log, omega=omega)
        diag["patch"] = p
        if res is None:
            log(f"  ABORT {p['id']}: {diag['status']}")
            json.dump(diag, open(pdir / f"{p['id']}.failed.json", "w"))
            continue
        np.savez_compressed(f, **res)
        json.dump(diag, open(pdir / f"{p['id']}.json", "w"))
        log(f"  done {diag['seconds']}s rho[{diag['rho_min']:.4f},{diag['rho_max']:.4f}] "
            f"corr_street median {np.nanmedian(res['corr_street']):.3f}")
    return merge(spec, patches, pdir, out, direction, dx)


def merge(spec, patches, pdir, out, direction, dx):
    nx, ny = spec["nx"], spec["ny"]
    names = ["corr_street", "corr_shelter", "corr_bldg", "corr_bldg_shelter", "tke_street", "street_fine", "street_base", "street_ref", "bcover"]
    arr = {k: np.full((nx, ny), np.nan, np.float32) for k in names}
    pid = np.full((nx, ny), -1, np.int16)
    plist = []
    for p in sorted(Path(pdir).glob("p*_*.npz")):
        j = json.load(open(p.with_suffix(".json")))
        pp = j["patch"]
        d = np.load(p)
        sl = (slice(pp["ix0"], pp["ix0"] + pp["ncx"]), slice(pp["iy0"], pp["iy0"] + pp["ncy"]))
        for k in names:
            arr[k][sl] = d[k]
        pid[sl] = len(plist)
        plist.append({**pp, "seconds": j["seconds"], "rho_min": j["rho_min"], "rho_max": j["rho_max"], "shape": j["shape"]})
    tag = f"{int(direction * 10):04d}"
    np.savez_compressed(Path(out) / f"patch_corr_{tag}.npz", patch_id=pid, **arr)
    cs = arr["corr_street"]
    diag = {"direction_deg": direction, "fine_dx_m": dx, "grid_shape": [nx, ny], "n_patches": len(plist), "patches": plist,
            "runtime_s_total": round(sum(p["seconds"] for p in plist), 1),
            "rho_min": min([p["rho_min"] for p in plist], default=None), "rho_max": max([p["rho_max"] for p in plist], default=None),
            "n_cells_refined": int(np.isfinite(cs).sum()),
            "corr_street_pct_5_25_50_75_95": [float(x) for x in np.nanpercentile(cs, [5, 25, 50, 75, 95])] if np.isfinite(cs).any() else None}
    json.dump(diag, open(Path(out) / f"patch_corr_{tag}.json", "w"), indent=1)
    return diag
