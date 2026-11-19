"""5-minute GOES-18 crop store and macro cloud frames (multi-scale guide 07, switch GOES5; storage contract replay/goes-crop-format.md, schema 1).
Every scan of ABI-L2-ACMC + ABI-L2-ACHAC is cropped to the box goes.BOX (the SAME crop function as goes.LowCloud) and kept raw for 48 h:
    <store>/grid.json                 once: schema, satellite, products, box, crop offsets, shape, fixed-grid axes, projection (for parallax)
    <store>/<YYYY-MM-DD>/<HH>.npz     one per UTC hour: t_scan int64 [n] (unix s of the scan start), acm uint8 [n,H,W] (254 = bad quality, 255 = missing),
                                      ht uint16 [n,H,W] (m, 0 = none), src str [n]
Everything derived (probability, confidence, parallax) comes from goes.low_cloud / goes.parallax_shift_m and is never stored.
Nothing here may fail a build: run() catches everything and reports it in the status. The hourly path (goes.hourly, cloud_score) is not touched."""
import datetime as dt
import io
import json
import os
import time
from pathlib import Path

import numpy as np

from . import config as C
from . import goes

SCHEMA = 1


class _Proj:
    """Stands in for the h5py projection group of goes.Nav (it reads .attrs[name][0])."""

    def __init__(self, d):
        self.attrs = {k: [v] for k, v in d.items()}


# ------------------------------------------------------------------ listing and pairing
def list_scans(t_from, t_to, lister=None, tol_s=None):
    """[(scan start (datetime UTC), ACM key, ACHA key)] oldest first, for the scans that start in [t_from, t_to]. The hours touched are listed for both products
    (lister(product, hour) -> keys, default goes._list_keys); an ACM and an ACHA scan pair when their scan starts are within tol_s seconds (C.GOES5_PAIR_TOL_S);
    a scan without a partner is skipped."""
    lister = lister or goes._list_keys
    tol = C.GOES5_PAIR_TOL_S if tol_s is None else tol_s
    h0 = t_from.replace(minute=0, second=0, microsecond=0)
    hours = []
    h = h0
    while h <= t_to:
        hours.append(h)
        h += dt.timedelta(hours=1)
    by = {}
    for prod in (C.GOES_MASK, C.GOES_HEIGHT):
        ks = []
        for hh in hours:
            ks += list(lister(prod, hh))
        by[prod] = sorted(((goes._scan_start(k), k) for k in ks), key=lambda x: x[0])
    out, used = [], set()
    for ta, ka in by[C.GOES_MASK]:
        if not (t_from <= ta <= t_to):
            continue
        best = None
        for j, (th, kh) in enumerate(by[C.GOES_HEIGHT]):
            if j in used:
                continue
            d = abs((th - ta).total_seconds())
            if d <= tol and (best is None or d < best[0]):
                best = (d, j, kh)
        if best is not None:
            used.add(best[1])
            out.append((ta, ka, best[2]))
    return out


# ------------------------------------------------------------------ store files
def hour_path(store, t):
    return Path(store) / f"{t:%Y-%m-%d}" / f"{t:%H}.npz"


def _atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def load_hour(path):
    """dict(t_scan, acm, ht, src) of an hour file, or None when it does not exist or cannot be read."""
    try:
        with np.load(path, allow_pickle=False) as z:
            return dict(t_scan=z["t_scan"].astype(np.int64), acm=z["acm"], ht=z["ht"], src=z["src"])
    except Exception:  # noqa: BLE001
        return None


def save_hour(path, d):
    """Atomic: the npz is built in memory, written to <name>.tmp and renamed; an exception leaves the old file (or none) and no partial file."""
    buf = io.BytesIO()
    np.savez_compressed(buf, t_scan=d["t_scan"].astype(np.int64), acm=d["acm"].astype(np.uint8), ht=d["ht"].astype(np.uint16), src=np.asarray(d["src"], dtype="U"))
    _atomic_bytes(path, buf.getvalue())


def merge_scan(old, t_scan, acm, ht, src):
    """Idempotent merge by t_scan: a scan already in the hour is replaced, the rest is kept, the result is sorted."""
    if old is None:
        return dict(t_scan=np.array([t_scan], np.int64), acm=acm[None], ht=ht[None], src=np.array([src]))
    keep = old["t_scan"] != t_scan
    ts = np.concatenate([old["t_scan"][keep], [t_scan]]).astype(np.int64)
    A = np.concatenate([old["acm"][keep], acm[None]])
    Hh = np.concatenate([old["ht"][keep], ht[None]])
    S = np.concatenate([old["src"][keep], np.array([src])])
    o = np.argsort(ts, kind="stable")
    return dict(t_scan=ts[o], acm=A[o], ht=Hh[o], src=S[o])


def hour_files(store):
    return sorted(Path(store).glob("*/[0-2][0-9].npz"))


def newest_scan(store):
    """Newest stored t_scan (unix s) or None."""
    for f in reversed(hour_files(store)):
        d = load_hour(f)
        if d is not None and len(d["t_scan"]):
            return int(d["t_scan"].max())
    return None


def prune_store(store, now, keep_h=None):
    """Delete hour files whose hour ended more than keep_h hours (C.GOES5_KEEP_H) before now; empty day folders go too. -> number of files deleted."""
    keep_h = C.GOES5_KEEP_H if keep_h is None else keep_h
    lim = now - dt.timedelta(hours=keep_h)
    n = 0
    for f in hour_files(store):
        try:
            t = dt.datetime.strptime(f"{f.parent.name} {f.stem}", "%Y-%m-%d %H").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        if t + dt.timedelta(hours=1) <= lim:
            f.unlink()
            n += 1
    for d in Path(store).glob("*-*-*"):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return n


def store_files(store):
    """Relative paths ('goes5/grid.json', 'goes5/2026-11-14/03.npz') of the store, for the carry list in index.json."""
    store = Path(store)
    out = [f"{store.name}/grid.json"] if (store / "grid.json").exists() else []
    return out + [f"{store.name}/{f.parent.name}/{f.name}" for f in hour_files(store)]


def store_bytes(store):
    return sum(f.stat().st_size for f in Path(store).rglob("*") if f.is_file())


# ------------------------------------------------------------------ grid
def make_grid(cr, box, acm_key=None):
    n = cr["nav"]
    return dict(schema=SCHEMA, satellite="goes18", products=[C.GOES_MASK, C.GOES_HEIGHT], box=list(box), row0="north",
                crop=dict(r0=cr["r0"], r1=cr["r1"], c0=cr["c0"], c1=cr["c1"]), shape=[int(cr["acm"].shape[0]), int(cr["acm"].shape[1])],
                x=[float(v) for v in cr["x"]], y=[float(v) for v in cr["y"]],
                projection=dict(semi_major_axis=n.req, semi_minor_axis=n.rpol, perspective_point_height=n.H - n.req,
                                longitude_of_projection_origin=math_deg(n.lam0)),
                sat_lon=math_deg(n.lam0), sat_height_m=n.H - n.req, ht_scale="uint16 metres, 0 = none", acm_flags="254 bad quality (ACM DQF 1), 255 missing")


def math_deg(r):
    return float(np.degrees(r))


def same_grid(g, cr):
    return (g["crop"] == dict(r0=cr["r0"], r1=cr["r1"], c0=cr["c0"], c1=cr["c1"]) and g["shape"] == [int(cr["acm"].shape[0]), int(cr["acm"].shape[1])])


# ------------------------------------------------------------------ one scan
def crop_scan(acm_path, ha_path, box=None):
    """Crop one scan pair with goes.read_crop -> (crop dict, acm uint8 [H,W] with 254/255 applied, ht uint16 [H,W])."""
    import h5py
    with h5py.File(acm_path, "r") as a, h5py.File(ha_path, "r") as h:
        cr = goes.read_crop(a, h, box or goes.BOX)
    acm = cr["acm"].astype(np.uint8).copy()
    if cr["dqf"] is not None:
        acm[cr["dqf"] == 1] = 254      # retrieved but flagged bad quality
        acm[cr["dqf"] == 2] = 255      # space pixel
    ht = np.nan_to_num(cr["ht"], nan=0.0)
    ht = np.clip(np.rint(ht), 0, 65535).astype(np.uint16)
    return cr, acm, ht


def ingest(store, now, deadline_s=None, cache=None, fetch=None, lister=None, log=print, box=None):
    """Add the scans newer than the newest stored one to the store, oldest first, at most C.GOES5_MAX_SCANS_PER_RUN, no new scan after deadline_s seconds
    (C.GOES5_DEADLINE_S). Cold store: only the last C.GOES5_COLD_MIN minutes. Raw downloads of this module are deleted right after cropping.
    -> dict(new, listed, errors, seconds, newest_iso, cold, pruned, bytes, files)."""
    t0 = time.monotonic()
    deadline_s = C.GOES5_DEADLINE_S if deadline_s is None else deadline_s
    fetch = fetch or goes.download
    store = Path(store)
    cache = Path(cache or store.parent / "goes5_raw")
    info = dict(new=0, listed=0, errors=[], cold=False)
    newest = newest_scan(store)
    if newest is None:
        info["cold"] = True
        t_from = now - dt.timedelta(minutes=C.GOES5_COLD_MIN)
    else:
        t_from = dt.datetime.fromtimestamp(newest + 1, tz=dt.timezone.utc)
    scans = list_scans(t_from, now, lister)
    info["listed"] = len(scans)
    scans = scans[:C.GOES5_MAX_SCANS_PER_RUN]
    grid_p = store / "grid.json"
    grid = None
    try:
        grid = json.loads(grid_p.read_text()) if grid_p.exists() else None
    except Exception:  # noqa: BLE001
        grid = None
    for ts, ka, kh in scans:
        if time.monotonic() - t0 > deadline_s:
            info["errors"].append("deadline reached: the rest comes with the next run")
            break
        files = []
        try:
            files = [fetch(ka, cache), fetch(kh, cache)]
            cr, acm, ht = crop_scan(files[0], files[1], box)
            if grid is None:
                grid = make_grid(cr, box or goes.BOX)
                _atomic_bytes(grid_p, json.dumps(grid).encode())
            elif not same_grid(grid, cr):
                raise ValueError("crop grid differs from grid.json (schema change?)")
            t_scan = int(ts.timestamp())
            path = hour_path(store, ts)
            save_hour(path, merge_scan(load_hour(path), t_scan, acm, ht, f"{ka.split('/')[-1]}|{kh.split('/')[-1]}"))
            info["new"] += 1
        except Exception as e:  # noqa: BLE001
            info["errors"].append(f"{ts:%Y-%m-%d %H:%M}Z: {e!r}"[:200])
            log(f"goes5 {ts:%Y-%m-%d %H:%M}Z: {e!r}")
        finally:
            for f in files:
                try:
                    Path(f).unlink()
                except Exception:  # noqa: BLE001
                    pass
    try:
        info["pruned"] = prune_store(store, now)
    except Exception as e:  # noqa: BLE001
        info["errors"].append(f"prune: {e!r}"[:200])
    n = newest_scan(store) if store.exists() else None
    info.update(seconds=round(time.monotonic() - t0, 1), newest_iso=None if n is None else dt.datetime.fromtimestamp(n, tz=dt.timezone.utc).isoformat(),
                bytes=store_bytes(store) if store.exists() else 0, files=len(hour_files(store)) if store.exists() else 0)
    return info


# ------------------------------------------------------------------ reading the store
class Crop5:
    """The stored scans (sorted by t_scan) as arrays: t_scan [n], acm [n,H,W], ht [n,H,W], src [n]; x, y axes; nav; centre (lon, lat); sat_lon. Input of goes.low_cloud."""

    def __init__(self, store, t_from=None):
        store = Path(store)
        self.grid = json.loads((store / "grid.json").read_text())
        g = self.grid
        self.nav = goes.Nav(_Proj(g["projection"]))
        self.x, self.y = np.array(g["x"]), np.array(g["y"])
        self.box = tuple(g["box"])
        self.centre = ((self.box[0] + self.box[2]) / 2.0, (self.box[1] + self.box[3]) / 2.0)
        self.sat_lon = float(g["sat_lon"])
        ts, A, Hh, S = [], [], [], []
        for f in hour_files(store):
            d = load_hour(f)
            if d is None:
                continue
            m = np.ones(len(d["t_scan"]), bool) if t_from is None else d["t_scan"] >= int(t_from.timestamp())
            ts.append(d["t_scan"][m]); A.append(d["acm"][m]); Hh.append(d["ht"][m]); S.append(d["src"][m])
        shape = tuple(g["shape"])
        if ts:
            ts, A, Hh, S = np.concatenate(ts), np.concatenate(A), np.concatenate(Hh), np.concatenate(S)
            o = np.argsort(ts, kind="stable")
            ts, A, Hh, S = ts[o], A[o], Hh[o], S[o]
        else:
            ts, A, Hh, S = np.zeros(0, np.int64), np.zeros((0,) + shape, np.uint8), np.zeros((0,) + shape, np.uint16), np.zeros(0, "U1")
        self.t_scan, self.acm, self.ht, self.src = ts, A, Hh, S

    def __len__(self):
        return len(self.t_scan)

    def time(self, i):
        return dt.datetime.fromtimestamp(int(self.t_scan[i]), tz=dt.timezone.utc)


# ------------------------------------------------------------------ macro frames
M_PER_DEG = 111320.0
GOES_TOP_STEP_M = 8.0        # metres per uint8 step in the macro PNG Blue channel (0..255 -> 0..2040 m)
GOES_TOP_MAX_M = 255.0 * GOES_TOP_STEP_M


def macro_axes(box, res):
    """lon, lat of the macro grid (inclusive of both box edges; row 0 = north): 141 x 111 for the GOES box at 0.01 degrees."""
    w = int(round((box[2] - box[0]) / res)) + 1
    h = int(round((box[3] - box[1]) / res)) + 1
    return box[0] + res * np.arange(w), box[3] - res * np.arange(h)


def _nearest_index(crop, lon, lat):
    """Nearest crop pixel (row, col, inside mask) of lon/lat arrays."""
    gx, gy = crop.nav.xy(lon, lat)
    fc = np.interp(gx, crop.x, np.arange(len(crop.x)), left=np.nan, right=np.nan)
    fr = np.interp(gy, crop.y[::-1], np.arange(len(crop.y))[::-1], left=np.nan, right=np.nan)
    ok = np.isfinite(fc) & np.isfinite(fr)
    c = np.clip(np.rint(np.where(ok, fc, 0)).astype(int), 0, len(crop.x) - 1)
    r = np.clip(np.rint(np.where(ok, fr, 0)).astype(int), 0, len(crop.y) - 1)
    return r, c, ok


def sample_macro(crop, i, p, c, top, lon, lat, default_top_m=None, iterations=2):
    """Nearest-neighbour sample of p, c, top_m (arrays from goes.low_cloud) on the ground points lon, lat (2-D arrays) with parallax.
    A pixel with cloud top h is displaced from the true place of its cloud: the true ground position is the apparent one plus h * tan(zenith) towards the satellite
    (goes.parallax_shift_m). For a ground point G the apparent position is therefore G minus that shift, and the shift depends on the height found there, so it is
    iterated (default 2 passes starting from the unshifted point). h is the retrieved height where valid, else default_top_m (C.GOES5_DEFAULT_TOP_M; a fused
    fog top will replace it with guide 06). Points outside the crop give p = c = top = 0."""
    dflt = C.GOES5_DEFAULT_TOP_M if default_top_m is None else default_top_m
    la, lo = lat.copy(), lon.copy()
    for _ in range(iterations):
        r, cc, ok = _nearest_index(crop, lo, la)
        h = np.where(top[r, cc] > 0, top[r, cc], dflt)
        de, dn = goes.parallax_shift_m(h, lon, lat, crop.sat_lon)
        la = lat - dn / M_PER_DEG
        lo = lon - de / (M_PER_DEG * np.cos(np.radians(lat)))
    r, cc, ok = _nearest_index(crop, lo, la)
    z = np.zeros(lon.shape, np.float32)
    return np.where(ok, p[r, cc], z), np.where(ok, c[r, cc], z), np.where(ok, top[r, cc], z)


def macro_rgba(p, c, top):
    """R = 255 p, G = 255 c, B = min(top_m / GOES_TOP_STEP_M, 255), A = 255, uint8 [h, w, 4]."""
    out = np.empty(p.shape + (4,), np.uint8)
    out[..., 0] = np.clip(np.rint(255.0 * p), 0, 255)
    out[..., 1] = np.clip(np.rint(255.0 * c), 0, 255)
    out[..., 2] = np.clip(np.rint(top / GOES_TOP_STEP_M), 0, 255)
    out[..., 3] = 255
    return out


def png_bytes(rgba):
    """RGBA 8-bit PNG, no palette, no quantising; deterministic."""
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def read_png(path):
    from PIL import Image
    with Image.open(path) as im:
        return np.array(im.convert("RGBA"))


def write_macro(store, out_dir, now, hours=None, res=None, log=print):
    """Frames of the scans of the last `hours` (C.GOES5_MACRO_H) -> <out_dir>/<yyyymmddThhmm>.png + index.json (schema 1). A frame file whose bytes would not change is left
    alone; frames of older scans (or of nothing) are deleted. -> dict(frames, written, deleted, bytes)."""
    hours = C.GOES5_MACRO_H if hours is None else hours
    res = C.GOES5_MACRO_RES if res is None else res
    out_dir = Path(out_dir)
    crop = Crop5(store, t_from=now - dt.timedelta(hours=hours))
    lon1, lat1 = macro_axes(crop.box, res)
    lon, lat = np.meshgrid(lon1, lat1)
    frames, written = [], 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(len(crop)):
        t = crop.time(i)
        name = f"{t:%Y%m%dT%H%M}.png"
        p, c, top, _ = goes.low_cloud(crop, i)
        data = png_bytes(macro_rgba(*sample_macro(crop, i, p, c, top, lon, lat)))
        f = out_dir / name
        if not (f.exists() and f.read_bytes() == data):
            _atomic_bytes(f, data)
            written += 1
        frames.append(dict(t=t.isoformat(), file=name))
    keep = {x["file"] for x in frames}
    deleted = 0
    for f in out_dir.glob("*.png"):
        if f.name not in keep:
            f.unlink()
            deleted += 1
    idx = dict(schema=1, box=list(crop.box), w=int(len(lon1)), h=int(len(lat1)), res=res, row0="north", rule_version=goes.RULE_VERSION, frames=frames)
    _atomic_bytes(out_dir / "index.json", json.dumps(idx, indent=1).encode())
    return dict(frames=len(frames), written=written, deleted=deleted, bytes=sum(f.stat().st_size for f in out_dir.glob("*.png")))


# ------------------------------------------------------------------ the build hook
def run(out_dir, now, cache=None, log=print, **kw):
    """Ingest + macro frames into out_dir (store out_dir/GOES5_DIR, frames out_dir/GOES5_MACRO_DIR). NEVER raises: -> dict for status.json["goes5"]."""
    out_dir = Path(out_dir)
    store = out_dir / C.GOES5_DIR
    info = dict(on=True)
    try:
        info["ingest"] = ingest(store, now, cache=cache or out_dir.parent / "goes5_raw", log=log, **kw)
    except Exception as e:  # noqa: BLE001
        info["ingest"] = dict(error=repr(e)[:200])
        log(f"goes5 ingest: {e!r}")
    try:
        if (store / "grid.json").exists():
            info["macro"] = write_macro(store, out_dir / C.GOES5_MACRO_DIR, now, log=log)
    except Exception as e:  # noqa: BLE001
        info["macro"] = dict(error=repr(e)[:200])
        log(f"goes5 macro: {e!r}")
    try:
        info["store_files"] = store_files(store) if store.exists() else []
    except Exception as e:  # noqa: BLE001
        info["store_files"] = []
        info["files_error"] = repr(e)[:100]
    return info
