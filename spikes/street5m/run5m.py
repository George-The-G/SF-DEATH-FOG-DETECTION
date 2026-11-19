"""Guide 12 spike: one Potrero tile at 5 m, wind from 270, XLB on the GPU. Experiment only. Output folder: deathfog-data/spikes/street5m/ (never library/).
usage: python spikes/street5m/run5m.py geometry   (CPU: solid5m.npz + report)
       python spikes/street5m/run5m.py run [steps]  (GPU: needs the free gpu.lock; saves mean5m.npz and diagnostics)
Tile: 2 x 2 km centred on 20th and Kansas (37.7597, -122.4037, the zone start) plus a 400 m margin, 5 m cells, 560 x 560 x 60 (z up to 300 m above sea level).
Inflow: the same uniform inflow approach as the city run (the guide's 20 m-profile inflow and full nesting are a later refinement)."""
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from microclimate.paths import data_dir  # noqa: E402

DATA = data_dir()
OUT = DATA / "spikes" / "street5m"
OUT.mkdir(parents=True, exist_ok=True)
N, NZ, DX = 560, 60, 5.0
LAT, LON = 37.7597, -122.4037


def geometry():
    import rasterio
    from pyproj import Transformer
    from rasterio.enums import Resampling
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from rasterio.vrt import WarpedVRT
    from shapely.geometry import box, shape
    from shapely.ops import transform as shp_transform
    t = Transformer.from_crs(4326, 32610, always_xy=True)
    cx, cy = t.transform(LON, LAT)
    x0, y1 = cx - N * DX / 2, cy + N * DX / 2          # west edge, north edge
    tf = from_origin(x0, y1, DX, DX)
    # terrain: 3DEP tile resampled bilinear to 5 m
    with rasterio.open(DATA / "raw" / "dem_n38w123.tif") as src, WarpedVRT(src, crs="EPSG:32610", transform=tf, width=N, height=N, resampling=Resampling.bilinear, nodata=np.nan, src_nodata=src.nodata) as v:
        ter = np.nan_to_num(v.read(1, masked=True).astype(np.float32).filled(np.nan), nan=0.0)
    ter = np.maximum(ter, 0.0)
    # buildings: each footprint with its own height (hgt_median_m)
    d = json.load(open(DATA / "raw" / "bldg.geojson"))
    inv = Transformer.from_crs(4326, 32610, always_xy=True).transform
    bb = box(x0 - 50, y1 - N * DX - 50, x0 + N * DX + 50, y1 + 50)
    shapes, nb = [], 0
    for f in d["features"]:
        g = f.get("geometry")
        h = (f.get("properties") or {}).get("hgt_median_m")
        try:
            h = float(h)
        except (TypeError, ValueError):
            h = None
        if not g or h is None or not np.isfinite(h):
            continue
        try:
            geom = shp_transform(inv, shape(g))
        except Exception:  # noqa: BLE001
            continue
        if geom.intersects(bb):
            shapes.append((geom, float(max(h, 3.0))))
            nb += 1
    del d
    bh = rasterize(shapes, out_shape=(N, N), transform=tf, fill=0.0, dtype="float32", merge_alg=rasterio.enums.MergeAlg.replace) if shapes else np.zeros((N, N), np.float32)
    foot = rasterize([(g, 1) for g, _ in shapes], out_shape=(N, N), transform=tf, fill=0, dtype="uint8").astype(bool)
    z = (np.arange(NZ) + 0.5) * DX
    solid = np.zeros((N, N, NZ), bool)                                   # [x east, y north, z]; array rows run north to south, so flip
    top = (ter + np.where(foot, bh, 0.0))[::-1].T                        # [x, y]
    solid = z[None, None, :] < top[:, :, None]
    solid[:, :, 0] = True
    np.savez_compressed(OUT / "solid5m.npz", solid=solid, terrain=ter[::-1].T, bldg_h=np.where(foot, bh, 0.0)[::-1].T, x0=x0, y0=y1 - N * DX, dx=DX)
    open_street = float((~foot & (ter > -1)).sum() / foot.size)
    # street cells open at 5 m versus the 20 m grid: share of ground cells (not in a footprint at 5 m) that the 20 m grid would fill (cell mean building height above the terrain)
    b20 = foot.reshape(N // 4, 4, N // 4, 4).mean(axis=(1, 3))           # building cover per 20 m cell
    bm20 = np.where(foot, bh, 0.0).reshape(N // 4, 4, N // 4, 4).sum(axis=(1, 3)) / 16.0   # mean building height per 20 m cell
    street5 = ~foot
    street5_20 = np.repeat(np.repeat(bm20 > 2.0, 4, axis=0), 4, axis=1)  # a 20 m cell with mean height above 2 m is solid up to terrain + mean height
    rep = dict(buildings=nb, cells=[N, N, NZ], solid_share=float(solid.mean()), footprint_share=float(foot.mean()), terrain_range_m=[float(ter.min()), float(ter.max())],
               street_cells_open_at_5m_pct=100.0 * float(street5.mean()), street_cells_blocked_in_20m_grid_pct=100.0 * float((street5 & street5_20).sum() / max(street5.sum(), 1)),
               max_building_height_m=float(bh.max()), tile_centre_utm=[cx, cy])
    json.dump(rep, open(OUT / "geometry_report.json", "w"), indent=1)
    print(json.dumps(rep, indent=1))


def run(steps=30000):
    import shutil
    import tempfile

    import warp as wp
    from microclimate import fog3d, wind
    # the fix found by the wind-cube-check thread: a stale compiled XLB stepper from Warp's on-disk cache has no wall boundary conditions; compile fresh in an empty cache folder
    cache = tempfile.mkdtemp(prefix="warp_fresh_")
    wp.config.kernel_cache_dir = cache
    z = np.load(OUT / "solid5m.npz")
    solid = z["solid"]
    with fog3d.GpuLock(DATA / "library", "street5m"):
        t0 = time.time()
        u, diag = wind.run(solid, 270.0, steps=steps, avg_steps=5000, avg_every=100, check_every=1000, log=lambda s: print(s, flush=True))
        diag["total_seconds"] = round(time.time() - t0)
        json.dump(diag, open(OUT / "mean5m_walls.json", "w"), indent=1)
        if u is not None:
            np.savez_compressed(OUT / "mean5m_walls.npz", u=u)
        print(json.dumps(diag), flush=True)
    shutil.rmtree(cache, ignore_errors=True)


if __name__ == "__main__":
    if sys.argv[1] == "geometry":
        geometry()
    else:
        run(int(sys.argv[2]) if len(sys.argv) > 2 else 30000)
