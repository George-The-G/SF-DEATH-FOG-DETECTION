"""Guide 03 step 3a, offline: `python -m microclimate.live stationwind [--data DIR] [--out FILE]`

For every wind station inside the 20 m grid (config.STATIONS entries with "wind", not hill stations) sample the library wind fields
(library/wind_<dir>.npz, velocity / inflow speed, axes [3, nx, ny, nz], z = (k + 0.5) * 20 m above sea level) at the station's x, y and sensor height above sea level
(ground or deck elevation + sensor height) for each library direction. Written to station_wind.npz:

  ids [S]            station ids
  dirs [D]           library directions (wind from, deg)
  uv [D, S, 2]       east and north velocity as a ratio of the inflow speed
  z_used [S]         height above sea level actually sampled (never below the first informative layer above the surface, as street.py does for streets)
  x, y [S]           UTM 10N
  layer_floor [S]    that first informative height, m

Needs the PC's wind library; never runs in the live build. The file is published as an extra release asset (not by this command).

--east (guide 14): the same table for ALL available directions including the east set (dirlib.available(east=True): 22.5 .. 135 and 180 .. 360; 157.5 is an accepted gap),
written to live_east.npz (default <data>/live/live_east.npz) with the extra fields `static_dirs` (the west directions of live_static.npz, which the live build checks)
and `east_dirs`. Rows of directions that station_wind.npz already holds (same stations and heights) are copied, not recomputed. CPU only.
"""
import json
import time
from pathlib import Path

import numpy as np

from .. import dirlib, street
from . import config as C


def sample(u, x, y, z, spec, kw):
    """u [3, nx, ny, nz] -> [3, N]: bilinear over the four columns around (x, y), linear in height between layer centres, floored at layer kw of each column."""
    c = spec["cell_m"]
    fx, fy = (np.asarray(x) - spec["x0"]) / c - 0.5, (np.asarray(y) - spec["y0"]) / c - 0.5
    i0, j0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
    tx, ty = fx - i0, fy - j0
    nx, ny, nz = u.shape[1:]
    out = np.zeros((3, len(fx)))
    for di, wx in ((0, 1 - tx), (1, tx)):
        for dj, wy in ((0, 1 - ty), (1, ty)):
            i, j = np.clip(i0 + di, 0, nx - 1), np.clip(j0 + dj, 0, ny - 1)
            kz = np.maximum(np.asarray(z) / c - 0.5, kw[i, j].astype(float))
            k0 = np.clip(np.floor(kz).astype(int), 0, nz - 2)
            t = np.clip(kz - k0, 0.0, 1.0)
            for a in range(3):
                va = (1 - t) * u[a, i, j, k0].astype(np.float64) + t * u[a, i, j, k0 + 1].astype(np.float64)
                out[a] += wx * wy * va
    return out


def run(argv):
    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default
    from pyproj import Transformer
    data = Path(opt("--data", Path.home() / "deathfog-data"))
    east = "--east" in argv
    out = Path(opt("--out", data / "live" / (C.EAST_FILE if east else C.STATION_WIND_FILE)))
    grids, lib = data / "grids", data / "library"
    spec = json.load(open(grids / "grid_spec.json"))
    solid = np.load(grids / "solid_20m.npz")["solid"]
    nx, ny, nz = solid.shape
    sw = solid.copy()
    sw[:, :, 0] = True
    kw = np.minimum(street.first_fluid_k(sw) + 1, nz - 1)       # first informative layer (as street.build / city.wind_refine)
    tr = Transformer.from_crs(4326, 32610, always_xy=True)
    ids, xs, ys, zs = [], [], [], []
    for sid, m in C.STATIONS.items():
        if "wind" not in m["vars"] or m.get("hill"):
            continue
        x, y = tr.transform(m["lon"], m["lat"])
        if not (spec["x0"] <= x < spec["x0"] + nx * spec["cell_m"] and spec["y0"] <= y < spec["y0"] + ny * spec["cell_m"]):
            print(f"  {sid}: outside the grid ({x:.0f}, {y:.0f}), no simulated vector")
            continue
        ids.append(sid)
        xs.append(x)
        ys.append(y)
        zs.append(m["elev_m"] + m["sensor_m"] if m["sensor_m"] or m["elev_m"] else 10.0)
    xs, ys, zs = np.array(xs), np.array(ys), np.array(zs)
    ci = np.clip(np.round((xs - spec["x0"]) / spec["cell_m"] - 0.5).astype(int), 0, nx - 1)
    cj = np.clip(np.round((ys - spec["y0"]) / spec["cell_m"] - 0.5).astype(int), 0, ny - 1)
    floor_m = (kw[ci, cj] + 0.5) * spec["cell_m"]
    z_used = np.maximum(zs, floor_m)
    dirs = dirlib.available(lib, east=east)
    have = {}
    prev = data / "live" / C.STATION_WIND_FILE
    if east and prev.exists():     # reuse the west rows of the published station_wind.npz when it is for the same stations and heights
        z0 = np.load(prev, allow_pickle=False)
        if [str(i) for i in z0["ids"]] == ids and np.allclose(z0["z_used"], z_used, atol=1e-3):
            have = {float(d): z0["uv"][i] for i, d in enumerate(z0["dirs"])}
    print("stations", ids, "z requested", zs, "layer floor", floor_m, "z used", z_used, "directions", dirs)
    uv = np.zeros((len(dirs), len(ids), 2))
    far_west = (spec["x0"] + 200.0 * np.ones(1), spec["y0"] + 8000.0 * np.ones(1), np.array([100.0]))
    for k, d in enumerate(dirs):
        if float(d) in have:
            uv[k] = have[float(d)]
            print(f"  dir {d}: copied from {C.STATION_WIND_FILE}")
            continue
        t0 = time.time()
        u = np.load(lib / f"wind_{dirlib._stem(d)}.npz")["u"]
        v = sample(u, xs, ys, z_used, spec, kw)
        uv[k, :, 0], uv[k, :, 1] = v[0], v[1]
        free = sample(u, *far_west, spec, kw)[:, 0]
        print(f"  dir {d}: {time.time() - t0:.0f} s; free-stream sample at the west edge (u, v, w) = {np.round(free, 2)}; stations (u, v) = {np.round(uv[k], 2).tolist()}")
        del u
    out.parent.mkdir(parents=True, exist_ok=True)
    extra = dict(static_dirs=np.array(dirlib.available(lib), np.float32), east_dirs=np.array([d for d in dirs if d < 180.0], np.float32)) if east else {}
    np.savez_compressed(out, ids=np.array(ids), dirs=np.array(dirs, np.float32), uv=uv.astype(np.float32), z_used=z_used.astype(np.float32), x=xs, y=ys,
                        layer_floor=floor_m.astype(np.float32), **extra)
    print("wrote", out, out.stat().st_size, "bytes")
    return 0
