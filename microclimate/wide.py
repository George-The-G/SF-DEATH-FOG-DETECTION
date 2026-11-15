"""Multi-scale guide 09 part A: wide terrain for the macro view (CPU). `python -m microclimate meshes-wide` -> site/v3/data/terrain_wide.bin

400 m grid in EPSG:32610 over the GOES box [-123.3, 37.2, -121.9, 38.3] from USGS 3DEP 1/3 arc-second tiles in deathfog-data/raw/dem_<tile>.tif (average resampling), sea = 0.
File: header '<4sHHddff' = magic b'TWD1', nx, ny, x0 (west edge, m), y0 (NORTH edge, m), cell (m), z scale (m per step, 0.5), then uint16 heights row-major, row 0 = north, in z-scale steps.
No triangles in the file: the viewer builds the grid mesh. Scene axes (viewer): x = east, z = -north."""
import struct
from pathlib import Path

import numpy as np

BOX = (-123.3, 37.2, -121.9, 38.3)    # west, south, east, north (same box as goes.BOX)
CELL_M = 400.0
Z_STEP = 0.5
MAGIC = b"TWD1"
HEADER = "<4sHHddff"
TILES = ("n38w124", "n38w123", "n38w122", "n39w124", "n39w123", "n39w122")   # named by the north-west corner: n38w123 covers lat 37-38, lon -123..-122


def grid_spec(box=BOX, cell=CELL_M):
    """x0 (west), y0 (north), nx, ny of the 400 m grid that covers the box in UTM 10N."""
    from pyproj import Transformer
    t = Transformer.from_crs(4326, 32610, always_xy=True)
    xs, ys = t.transform([box[0], box[2], box[0], box[2]], [box[1], box[1], box[3], box[3]])
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    nx, ny = int(np.ceil((x1 - x0) / cell)), int(np.ceil((y1 - y0) / cell))
    return dict(x0=float(np.floor(x0 / cell) * cell), y0=float(np.ceil(y1 / cell) * cell), nx=nx + 1, ny=ny + 1, cell=cell)


def build_heights(raw_dir, spec, tiles=TILES, log=print):
    """float32 [ny, nx] heights (m, sea 0) from the tiles that exist; NaN where no tile covers a cell."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.vrt import WarpedVRT
    dst = from_origin(spec["x0"], spec["y0"], spec["cell"], spec["cell"])
    out = np.full((spec["ny"], spec["nx"]), np.nan, np.float32)
    for t in tiles:
        p = Path(raw_dir) / f"dem_{t}.tif"
        if not p.exists():
            log(f"tile {t}: missing, skipped")
            continue
        with rasterio.open(p) as src, WarpedVRT(src, crs="EPSG:32610", transform=dst, width=spec["nx"], height=spec["ny"], resampling=Resampling.average, nodata=np.nan,
                                                  src_nodata=src.nodata) as vrt:
            a = vrt.read(1, masked=True).astype(np.float32).filled(np.nan)
        take = np.isnan(out) & np.isfinite(a)
        out[take] = a[take]
        log(f"tile {t}: {int(take.sum())} cells")
    return out


def encode(h, spec, z_step=Z_STEP):
    """bytes of terrain_wide.bin from heights [ny, nx] (NaN and negative -> 0)."""
    h = np.nan_to_num(np.asarray(h, np.float32), nan=0.0)
    q = np.clip(np.rint(np.maximum(h, 0.0) / z_step), 0, 65535).astype("<u2")
    return struct.pack(HEADER, MAGIC, spec["nx"], spec["ny"], spec["x0"], spec["y0"], spec["cell"], z_step) + q.tobytes()


def decode(data):
    """-> (spec dict, heights float32 [ny, nx] in metres)."""
    magic, nx, ny, x0, y0, cell, zs = struct.unpack_from(HEADER, data, 0)
    if magic != MAGIC:
        raise ValueError("not a TWD1 file")
    off = struct.calcsize(HEADER)
    q = np.frombuffer(data, "<u2", count=nx * ny, offset=off).reshape(ny, nx)
    return dict(x0=x0, y0=y0, nx=nx, ny=ny, cell=cell, z_step=zs), q.astype(np.float32) * np.float32(zs)


def height_at(spec, h, lat, lon):
    """Bilinear-free lookup (nearest cell) of the height at lat, lon."""
    from pyproj import Transformer
    x, y = Transformer.from_crs(4326, 32610, always_xy=True).transform(lon, lat)
    c, r = int((x - spec["x0"]) // spec["cell"]), int((spec["y0"] - y) // spec["cell"])
    return float(h[r, c])


def check_png(h, spec, city_box_utm, path):
    """Shaded top-down picture with the city box outlined."""
    from PIL import Image, ImageDraw
    hh = np.nan_to_num(h, nan=0.0)
    gy, gx = np.gradient(hh, spec["cell"])
    shade = np.clip(0.5 + 0.9 * (-gx + gy) * 0.8, 0, 1)          # light from the north-west
    land = hh > 0.5
    rgb = np.zeros(hh.shape + (3,), np.float32)
    rgb[..., 0] = np.where(land, 0.25 + 0.55 * shade + 0.0004 * hh, 0.10)
    rgb[..., 1] = np.where(land, 0.30 + 0.5 * shade + 0.0002 * hh, 0.22)
    rgb[..., 2] = np.where(land, 0.20 + 0.4 * shade, 0.45)
    im = Image.fromarray(np.clip(rgb * 255, 0, 255).astype(np.uint8)).resize((hh.shape[1] * 3, hh.shape[0] * 3), Image.BILINEAR)
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = city_box_utm
    px = lambda x, y: (3 * (x - spec["x0"]) / spec["cell"], 3 * (spec["y0"] - y) / spec["cell"])  # noqa: E731
    d.rectangle([px(x0, y1), px(x1, y0)], outline=(255, 230, 0), width=2)
    im.save(path)


def run(data_dir, out_path="site/v3/data/terrain_wide.bin", log=print):
    import json
    D = Path(data_dir)
    spec = grid_spec()
    log(f"grid {spec['nx']} x {spec['ny']} cells of {spec['cell']:.0f} m, origin ({spec['x0']:.0f}, {spec['y0']:.0f})")
    h = build_heights(D / "raw", spec, log=log)
    miss = int(np.isnan(h).sum())
    log(f"cells without data: {miss}")
    data = encode(h, spec)
    Path(out_path).write_bytes(data)
    g = json.load(open(D / "grids" / "grid_spec.json"))
    cb = (g["x0"], g["y0"], g["x0"] + g["nx"] * g["cell_m"], g["y0"] + g["ny"] * g["cell_m"])
    pic = D / "scratch" / "terrain_wide_check.png"
    pic.parent.mkdir(exist_ok=True)
    check_png(np.nan_to_num(h, nan=0.0), spec, cb, pic)
    log(f"wrote {out_path} ({len(data)} bytes) and {pic}")
    return dict(spec=spec, missing=miss, bytes=len(data), picture=str(pic), max_m=float(np.nanmax(h)))
