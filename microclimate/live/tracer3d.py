"""Guide 02, offline (PC with the tracer library): `python -m microclimate.live tracer3d [--data DIR] [--out FILE] [--check]`

Packs the 99 fog3d_<dddd>_<bb>.npz tracer fields (guide 01) into live_tracer3d.npz on the 80 m tile grid with 24 height levels (25 m).
Never runs in the live build. Contract (see sim/guides/02_tracer3d_asset.md):
  schema int 1; dirs [9]; band_edges_m [12]; z_m [24]; tr3d uint8 [9,11,24,203,214] (dir, band, level, row 0 = north, col 0 = west);
  fluid uint8 [24,203,214] (255 * air share); ground_m [203,214]; urban uint8 [203,214]; static_dirs_check [9]."""
import json
import time
from pathlib import Path

import numpy as np

from .. import fog3d

SCHEMA = 1
BAND_EDGES = [0, 40, 100, 140, 200, 260, 300, 360, 420, 500, 640, 800]
NZ3, DZ3 = 24, 25.0
SRC_DZ = 20.0
Z_M = ((np.arange(NZ3) + 0.5) * DZ3).astype(np.float32)
AIR_MIN = 0.8        # of 16 cells (5 %): below this the cell value is 0 (solid cells must not drag values down)
BLDG_H = 2.0         # m: a 20 m cell counts as built-up above this mean building height
URBAN_W_BLDG, URBAN_W_ROAD = 0.7, 0.3   # first guess, not measured


def block80(a):
    """[nx, ny, ...] 20 m array -> sum over 4 x 4 blocks, tile orientation [ceil(ny/4), ceil(nx/4), ...] (row 0 = north, col 0 = west),
    edge-padded exactly like replay.city_tracer_80m. Extra trailing axes are kept."""
    nx, ny = a.shape[:2]
    a = np.moveaxis(a, (0, 1), (1, 0))[::-1]          # [ny, nx, ...], row 0 = north
    fh, fw = -(-ny // 4), -(-nx // 4)
    pad = [(0, fh * 4 - ny), (0, fw * 4 - nx)] + [(0, 0)] * (a.ndim - 2)
    a = np.pad(a, pad, mode="edge")
    rest = a.shape[2:]
    return a.reshape(fh, 4, fw, 4, *rest).sum(axis=(1, 3))


def to_levels(num, air, z_src_dz=SRC_DZ, nz_out=NZ3, dz_out=DZ3):
    """num, air [nz_src, ...] (per-cell sums over blocks) -> (c [nz_out, ...] float32, air_out [nz_out, ...]).
    Linear weights between the two source levels around each target centre (clamped at the ends); num and air are
    interpolated separately and c = num / air where air >= AIR_MIN."""
    nsrc = num.shape[0]
    f = np.clip(((np.arange(nz_out) + 0.5) * dz_out - 0.5 * z_src_dz) / z_src_dz, 0, nsrc - 1)
    i0 = np.minimum(np.floor(f).astype(int), nsrc - 2)
    w = (f - i0).reshape((-1,) + (1,) * (num.ndim - 1)).astype(np.float32)
    n = (1 - w) * num[i0] + w * num[i0 + 1]
    a = (1 - w) * air[i0] + w * air[i0 + 1]
    c = np.where(a >= AIR_MIN, n / np.maximum(a, 1e-9), 0.0)
    return c.astype(np.float32), a.astype(np.float32)


def air80_levels(solid):
    """solid bool [nx, ny, nz] -> air count per 80 m cell [nz, H, W] (0..16)."""
    return np.moveaxis(block80((~solid).astype(np.float32)), 2, 0)


def tracer_levels(c, air80, solid):
    """One tracer field c [nx, ny, nz] (float16) -> [24, H, W] float32 on the tile grid."""
    num = block80(c.astype(np.float32) * (~solid))
    num = np.moveaxis(num, 2, 0)
    cc, _ = to_levels(num, air80)
    return cc


def urban_map(bldg_mean_h, x, y, spec, shape):
    """uint8 [H, W]: 0.7 * built-up share + 0.3 * street-point density (count / p95 of non-zero counts, clipped). First guess."""
    fh, fw = shape
    bf = block80((bldg_mean_h > BLDG_H).astype(np.float32)) / 16.0
    cell = 4 * spec["cell_m"]
    col = np.clip(((x - spec["x0"]) // cell).astype(int), 0, fw - 1)
    row = np.clip(fh - 1 - ((y - spec["y0"]) // cell).astype(int), 0, fh - 1)  # y0 is the south edge; row 0 = north
    cnt = np.zeros((fh, fw), np.float32)
    np.add.at(cnt, (row, col), 1)
    nz = cnt[cnt > 0]
    rf = np.clip(cnt / np.percentile(nz, 95), 0, 1) if nz.size else cnt
    return np.rint(255 * np.clip(URBAN_W_BLDG * bf + URBAN_W_ROAD * rf, 0, 1)).astype(np.uint8)


def build(data_dir, out_path, log=print, static=None, lib=None):
    D = Path(data_dir)
    static = Path(static) if static else D / "live" / "live_static.npz"
    lib = Path(lib) if lib else D / "library"
    t0 = time.time()
    spec = json.load(open(D / "grids" / "grid_spec.json"))
    solid = np.load(D / "grids" / "solid_20m.npz")["solid"]
    air80 = air80_levels(solid)
    nz_src, fh, fw = air80.shape
    _, air_out = to_levels(np.zeros_like(air80), air80)
    fluid = np.rint(255 * air_out / 16.0).astype(np.uint8)
    dirs = np.array(fog3d.WEST_DIRS, float)
    nb = len(BAND_EDGES) - 1
    tr = np.zeros((len(dirs), nb, NZ3, fh, fw), np.uint8)
    for di, d in enumerate(dirs):
        for b in range(nb):
            c = np.load(lib / f"fog3d_{fog3d.stem(d)}_{b:02d}.npz")["c"]
            tr[di, b] = np.rint(255 * np.clip(tracer_levels(c, air80, solid), 0, 1)).astype(np.uint8)
            del c
        log(f"direction {d} done ({time.time() - t0:.0f}s)")
    terr = np.load(D / "grids" / "terrain_20m.npy").astype(np.float32)
    ground = (block80(terr) / 16.0).astype(np.float32)
    st = np.load(static)   # street point table (x, y of 285 809 points); city_points.csv is not on this PC
    bm = np.load(D / "grids" / "bldg_mean_h_20m.npy")
    urban = urban_map(bm, st["x"], st["y"], spec, (fh, fw))
    np.savez_compressed(out_path, schema=np.int64(SCHEMA), dirs=dirs, band_edges_m=np.array(BAND_EDGES, np.float32), z_m=Z_M, tr3d=tr,
                        fluid=fluid, ground_m=ground, urban=urban, static_dirs_check=st["dirs"].astype(np.float64))
    log(f"wrote {out_path} ({Path(out_path).stat().st_size / 1e6:.1f} MB, {time.time() - t0:.0f}s)")


def check(data_dir, path, report_dir, static=None):
    from ..city import load_static
    D = Path(data_dir)
    rep = {}
    t0 = time.time()
    z = np.load(path)
    rep["load_seconds"] = round(time.time() - t0, 2)
    rep["compressed_mb"] = round(Path(path).stat().st_size / 1e6, 1)
    tr, fluid, ground, urban = z["tr3d"], z["fluid"], z["ground_m"], z["urban"]
    shapes = {"tr3d": (9, 11, 24, 203, 214), "fluid": (24, 203, 214), "ground_m": (203, 214), "urban": (203, 214), "dirs": (9,), "band_edges_m": (12,), "z_m": (24,)}
    want_dt = {"tr3d": "uint8", "fluid": "uint8", "ground_m": "float32", "urban": "uint8", "dirs": "float64", "band_edges_m": "float32", "z_m": "float32"}
    keys_ok = set(z.files) == {"schema", "dirs", "band_edges_m", "z_m", "tr3d", "fluid", "ground_m", "urban", "static_dirs_check"}
    rep["a_shapes_ok"] = bool(keys_ok and all(z[k].shape == s and str(z[k].dtype) == want_dt[k] for k, s in shapes.items()) and int(z["schema"]) == 1)
    # b: old-tile agreement
    st = load_static(Path(static) if static else D / "live" / "live_static.npz")
    rep["b_old_tile"] = {}
    lvl = np.argmax(fluid >= 128, axis=0)               # lowest level with fluid >= 128 (0 where none: masked below)
    has = (fluid >= 128).any(axis=0)
    ii, jj = np.meshgrid(np.arange(203), np.arange(214), indexing="ij")
    ok_b = True
    for di, d in enumerate(z["dirs"]):
        s3 = tr[di, :3].astype(np.float32).sum(axis=0) / 255.0   # [24, H, W]
        new = s3[lvl, ii, jj][has]
        old = st["tr80"][list(st["dirs"]).index(d)][has]
        r = float(np.corrcoef(new, old)[0, 1])
        mad = float(np.abs(new - old).mean())
        rep["b_old_tile"][str(d)] = {"pearson_r": round(r, 4), "mean_abs_diff": round(mad, 4), "ok": bool(r >= 0.97 and mad <= 0.04)}
        ok_b &= r >= 0.97 and mad <= 0.04
    rep["b_ok"] = bool(ok_b)
    # c: orientation
    terr = np.load(D / "grids" / "terrain_20m.npy").astype(np.float32)
    ind = np.zeros((203, 214), np.float32)
    t = terr.T[::-1]
    t = np.pad(t, ((0, 812 - t.shape[0]), (0, 856 - t.shape[1])), mode="edge")
    for a in range(4):
        for b in range(4):
            ind += t[a::4, b::4]
    ind /= 16.0
    pk, ik = np.unravel_index(ground.argmax(), ground.shape), np.unravel_index(ind.argmax(), ind.shape)
    rep["c_peak_cell"], rep["c_peak_cell_independent"] = [int(v) for v in pk], [int(v) for v in ik]
    rep["c_coast_mean_ground_west3cols_m"] = round(float(ground[:, :3].mean()), 2)
    rep["c_ok"] = bool(abs(pk[0] - ik[0]) <= 1 and abs(pk[1] - ik[1]) <= 1 and ground[:, :3].mean() < 5.0)
    # d: no fluid below ground
    rng = np.random.default_rng(0)
    bad = 0
    for _ in range(100):
        r, c = int(rng.integers(203)), int(rng.integers(214))
        below = z["z_m"] < ground[r, c] - 25
        bad += int((fluid[below, r, c] != 0).sum())
    rep["d_violations"], rep["d_ok"] = bad, bad == 0
    rep["urban_range"] = [int(urban.min()), int(urban.max())]
    rep["pass"] = bool(rep["a_shapes_ok"] and rep["b_ok"] and rep["c_ok"] and rep["d_ok"])
    Path(report_dir).mkdir(parents=True, exist_ok=True)
    json.dump(rep, open(Path(report_dir) / "asset_check.json", "w"), indent=1)
    slices(z, D, report_dir)
    return rep


def slices(z, D, report_dir, direction=270.0, top=300.0, y_section=4177700.0):
    """reports/fog3d/slices_270.png: 3 horizontal slices + a west-east section at y = y_section for a fog layer 0..top."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    di = list(z["dirs"]).index(direction)
    e = z["band_edges_m"]
    a = np.clip((np.minimum(top, e[1:]) - np.maximum(0, e[:-1])) / (e[1:] - e[:-1]), 0, 1)
    f = np.tensordot(a, z["tr3d"][di].astype(np.float32) / 255.0, axes=(0, 0))   # [24, H, W]
    f = np.where(z["fluid"] >= 128, f, np.nan)
    row = int(np.clip(round((4171500.0 + 20 * 811 - y_section) / 80.0 - 0.5), 0, 202))
    fig, ax = plt.subplots(2, 2, figsize=(12, 10))
    for k, a_ in zip((1, 5, 10), ax.flat):
        a_.imshow(f[k], vmin=0, vmax=1, cmap="viridis")
        a_.set_title(f"level {k}: z = {z['z_m'][k]:.1f} m (top {top:.0f} m, dir {direction:g})")
    sec = ax.flat[3]
    sec.imshow(f[:, row, :], origin="lower", aspect="auto", vmin=0, vmax=1, cmap="viridis", extent=[0, 214, 0, 24 * DZ3])
    sec.plot(np.arange(214) + 0.5, z["ground_m"][row], "k", lw=1)
    sec.set_title(f"west-east section at y = {y_section:.0f} (row {row}); black = terrain")
    fig.tight_layout()
    fig.savefig(Path(report_dir) / f"slices_{int(direction)}.png", dpi=80)
    plt.close(fig)


def run(argv):
    from .__main__ import opt
    from ..paths import data_dir
    D = Path(opt(argv, "--data", data_dir()))
    out = Path(opt(argv, "--out", D / "live" / "live_tracer3d.npz"))
    if "--check" in argv:
        rep = check(D, out, opt(argv, "--report", "reports/fog3d"), static=opt(argv, "--static"))
        print(json.dumps(rep, indent=1))
        return 0 if rep["pass"] else 1
    build(D, out, static=opt(argv, "--static"), lib=opt(argv, "--lib"))
    return 0
