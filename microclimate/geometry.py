"""M1 part 1: rasterise terrain and buildings onto the 20 m simulation grid (metres, EPSG:32610, z = elevation above sea level)."""
import json
import math
from pathlib import Path

import numpy as np
import rasterio
import shapely
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from shapely.geometry import shape

SUB = 5  # building raster is SUB times finer than the simulation grid, then averaged down


def to_utm():
    return Transformer.from_crs(4326, 32610, always_xy=True)


def grid_spec(cfg):
    d = cfg["domain"]
    t = to_utm()
    x0, y0 = t.transform(d["west"], d["south"])
    x1, y1 = t.transform(d["east"], d["north"])
    c = d["cell_m"]
    x0, y0 = math.floor(x0 / c) * c, math.floor(y0 / c) * c
    nx, ny = math.ceil((x1 - x0) / c), math.ceil((y1 - y0) / c)
    nz = int(d["top_m"] / c)
    return {"x0": x0, "y0": y0, "nx": nx, "ny": ny, "nz": nz, "cell_m": c, "crs": "EPSG:32610"}


def terrain(spec, dem_path):
    """Mean ground elevation (m) per cell, array [nx, ny]; water and nodata become 0."""
    c, nx, ny = spec["cell_m"], spec["nx"], spec["ny"]
    dst_tf = from_origin(spec["x0"], spec["y0"] + ny * c, c, c)
    out = np.full((ny, nx), np.nan, dtype=np.float32)
    with rasterio.open(dem_path) as src:
        reproject(rasterio.band(src, 1), out, dst_transform=dst_tf, dst_crs="EPSG:32610",
                  resampling=Resampling.average, dst_nodata=np.nan)
    out = np.nan_to_num(out, nan=0.0)
    out[out < 0] = 0
    return np.flipud(out).T.copy()  # [nx, ny], y increasing


def load_buildings(geojson_path):
    feats = json.load(open(geojson_path))["features"]
    geoms = np.array([shape(f["geometry"]) for f in feats], dtype=object)
    h = np.array([float(f["properties"].get("hgt_median_m") or 0) for f in feats], dtype=np.float32)
    t = to_utm()
    geoms = shapely.transform(geoms, lambda xy: np.column_stack(t.transform(xy[:, 0], xy[:, 1])))
    return geoms, h


def building_heights(spec, geoms, heights):
    """Mean and max building height above ground per cell, arrays [nx, ny]. Mean includes empty ground."""
    c, nx, ny = spec["cell_m"], spec["nx"], spec["ny"]
    f = c / SUB
    tf = from_origin(spec["x0"], spec["y0"] + ny * c, f, f)
    order = np.argsort(heights)  # tall last so they win overlaps
    shapes = [(geoms[i], float(heights[i])) for i in order if heights[i] > 0 and not geoms[i].is_empty]
    fine = rasterize(shapes, out_shape=(ny * SUB, nx * SUB), transform=tf, fill=0.0, dtype="float32")
    blocks = fine.reshape(ny, SUB, nx, SUB)
    mean = blocks.mean(axis=(1, 3))
    mx = blocks.max(axis=(1, 3))
    cover = (blocks > 0).mean(axis=(1, 3))
    flip = lambda a: np.flipud(a).T.copy()
    return flip(mean), flip(mx), flip(cover)


def solid_mask(spec, terrain_m, bldg_mean):
    """True where a cell is inside terrain or building. z is elevation above sea level; cell centres at (k+0.5)*c."""
    c, nz = spec["cell_m"], spec["nz"]
    top = terrain_m + bldg_mean
    zc = (np.arange(nz) + 0.5) * c
    return zc[None, None, :] < top[:, :, None]


def build(cfg, raw_dir, out_dir):
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = grid_spec(cfg)
    ter = terrain(spec, raw_dir / "dem_n38w123.tif")
    geoms, h = load_buildings(raw_dir / "bldg.geojson")
    mean, mx, cover = building_heights(spec, geoms, h)
    solid = solid_mask(spec, ter, mean)
    np.save(out_dir / "terrain_20m.npy", ter)
    np.save(out_dir / "bldg_mean_h_20m.npy", mean.astype(np.float16))
    np.save(out_dir / "bldg_max_h_20m.npy", mx.astype(np.float16))
    np.savez_compressed(out_dir / "solid_20m.npz", solid=solid)
    json.dump(spec, open(out_dir / "grid_spec.json", "w"), indent=1)
    summary = {"buildings": int(len(h)), "buildings_with_height": int((h > 0).sum()), "max_source_height_m": float(h.max()),
               "terrain_max_m": float(ter.max()), "bldg_max_cell_m": float(mx.max()),
               "solid_fraction": float(solid.mean()), "shape": [spec["nx"], spec["ny"], spec["nz"]]}
    json.dump(summary, open(out_dir / "geometry_summary.json", "w"), indent=1)
    return summary
