"""Guide 03 (switch config.FOG3D / config.BETA["fog3d"]): hourly 3D fog files in the live build.

vol[k] = amp * sum_d w_d * sum_b a_b * tr3d[d, b, k] / 255, where amp = lagged source * GOES clear-sky factor (the same amp as the 2D tile),
w_d the direction weights of the hour's wind, a_b the band weights for a fog layer between lo and top (sim/guides/00_README.md).
Written as fog3d_<fff>.png atlases (6 x 4 tiles of 214 x 203, level k at column k % 6, row k // 6, row 0 of a tile = north)."""
import numpy as np

SCHEMA = 1
NZ, DZ = 24, 25.0
COLS, ROWS = 6, 4
BAND_EDGES = (0, 40, 100, 140, 200, 260, 300, 360, 420, 500, 640, 800)
TOL_DEG = 0.01


def load(path, expect_dirs=None):
    """Read the asset and check the contract. -> (T dict with tr3d kept as uint8, info). T is None (info["reason"] says why) when it cannot be used."""
    info = dict(applied=False, file=str(getattr(path, "name", path)), reason=None)
    try:
        z = np.load(path, allow_pickle=False)
        T = {k: z[k] for k in ("schema", "dirs", "band_edges_m", "z_m", "tr3d", "fluid", "ground_m", "urban")}
    except Exception as e:  # noqa: BLE001
        info["reason"] = f"{type(e).__name__}: {str(e)[:100]}"
        return None, info
    nb, nd = len(BAND_EDGES) - 1, len(T["dirs"])
    if int(T["schema"]) != SCHEMA:
        info["reason"] = f"schema {int(T['schema'])}, expected {SCHEMA}"
    elif T["tr3d"].dtype != np.uint8 or T["tr3d"].shape != (nd, nb, NZ) + T["tr3d"].shape[3:] or T["tr3d"].ndim != 5:
        info["reason"] = f"tr3d has shape {T['tr3d'].shape} / {T['tr3d'].dtype}"
    elif not np.allclose(T["band_edges_m"], BAND_EDGES) or len(T["z_m"]) != NZ:
        info["reason"] = "band edges or levels differ from the contract"
    elif expect_dirs is not None and not set(np.round(np.asarray(T["dirs"], float), 2)) <= set(np.round(np.asarray(expect_dirs, float), 2)):
        info["reason"] = f"asset directions {np.asarray(T['dirs']).tolist()} are not all in the static table's {np.asarray(expect_dirs).tolist()}"
    if info["reason"]:
        return None, info
    info.update(applied=True, bytes=int(T["tr3d"].nbytes), nz=NZ, dz_m=DZ)
    return T, info


def band_weights(top_m, lo_m=0.0, edges=BAND_EDGES):
    """[11] share of each inflow band inside a fog layer lo..top (m above sea level): clip((min(top, hi) - max(lo, lo_b)) / (hi - lo_b), 0, 1)."""
    e = np.asarray(edges, float)
    top = 0.0 if top_m is None or not np.isfinite(top_m) else float(top_m)
    return np.clip((np.minimum(top, e[1:]) - np.maximum(lo_m, e[:-1])) / (e[1:] - e[:-1]), 0.0, 1.0)


def dir_rows(dirs_used, Wt, asset_dirs):
    """[(row in the asset, weight)] for every direction of the weight vector with weight > 0 that the asset holds (matched by value, 0.01 deg).
    Directions the asset does not hold (east, offshore) contribute nothing; the weights are NOT renormalised."""
    ad = np.asarray(asset_dirs, float)
    out = []
    for d, w in zip(np.asarray(dirs_used, float), np.asarray(Wt, float)):
        if w <= 0:
            continue
        j = int(np.argmin(np.abs(ad - d)))
        if abs(ad[j] - d) <= TOL_DEG:
            out.append((j, float(w)))
    return out


def volume(T, rows, a, amp, weight3d=None):
    """-> float32 [24, H, W]. rows from dir_rows, a from band_weights, amp [H, W]; weight3d [24, H, W] (guide 05 hook) multiplies in at the end."""
    tr = T["tr3d"]
    nzh = tr.shape[2:]
    C3 = np.zeros(nzh, np.float32)
    bands = [b for b in range(len(a)) if a[b] > 0]
    for r, w in rows:
        for b in bands:
            C3 += np.float32(w * a[b] / 255.0) * tr[r, b]
    vol = np.asarray(amp, np.float32)[None] * C3
    if weight3d is not None:
        vol = vol * weight3d
    return vol


def encode(vol):
    return np.rint(255.0 * np.clip(vol, 0.0, 1.0)).astype(np.uint8)


def atlas(u8):
    """uint8 [24, H, W] -> PIL 'L' image, COLS x ROWS tiles."""
    from PIL import Image
    nz, h, w = u8.shape
    img = np.zeros((ROWS * h, COLS * w), np.uint8)
    for k in range(nz):
        r, c = divmod(k, COLS)
        img[r * h:(r + 1) * h, c * w:(c + 1) * w] = u8[k]
    return Image.fromarray(img, "L")


def top_layer(u8):
    nz = np.where((u8 > 0).reshape(u8.shape[0], -1).any(axis=1))[0]
    return int(nz[-1]) if len(nz) else -1
