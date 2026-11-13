"""GOES downward shortwave radiation at the surface (guide 06: k_sat = DSR / ghi_clear), product ABI-L2-DSR on the public AWS bucket.

Findings (2026-11-12, docs/model_guides/06_sun.md implementation notes): the CONUS product ABI-L2-DSRC is empty on noaa-goes18 and noaa-goes19; only the full-disk
ABI-L2-DSRF exists (10-minute scans, about 40 MB per file by day on goes18). Its grid is 2 km at nadir (about 2.5 x 2.4 km over San Francisco), not 25 km: neighbouring
pixels differ. A netCDF4 / HDF5 file can be read with HTTP range requests: DSR is stored in gzip chunks of 24 full-width rows, so a read of the city box needs the file
metadata and two or three chunks, about 0.6 MB per scan instead of 40 MB. h5py reads through the small file-like class below (no fsspec / s3fs needed).

The result per hour is a tiny city subset (DSR in W/m2 plus quality flags; the pixel grid is stored once), kept in a rolling JSON cache that is carried between runs like
the state files, so a run downloads only the hours that are not in it yet. Nothing here is fatal: a failure means "no DSR for that hour" and the caller falls back to the
mask classes.
"""
import datetime as dt
import io
import json
import re
import time
from pathlib import Path

import numpy as np

from . import config as C
from .fetch import get


# ------------------------------------------------------------------ ranged reads
class RangeFile(io.RawIOBase):
    """Read-only file object over HTTP range requests with an aligned block cache; counts the bytes it really transferred."""

    def __init__(self, url, block=None, session=None):
        import requests
        self.url, self.block = url, int(block or C.DSR_BLOCK_BYTES)
        self.http = session or requests.Session()
        self.http.headers.update(C.UA)
        self.pos, self.cache, self.bytes, self.requests = 0, {}, 0, 0
        r = self.http.head(url, timeout=C.TIMEOUT_S)
        r.raise_for_status()
        self.size = int(r.headers["Content-Length"])

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def _blk(self, i):
        if i not in self.cache:
            a = i * self.block
            b = min(self.size, a + self.block) - 1
            last = None
            for _ in range(C.RETRIES):
                try:
                    r = self.http.get(self.url, headers={"Range": f"bytes={a}-{b}"}, timeout=C.TIMEOUT_S)
                    if r.status_code in (200, 206) and len(r.content) == (b - a + 1):
                        self.cache[i] = r.content
                        self.bytes += len(r.content)
                        self.requests += 1
                        break
                    last = f"HTTP {r.status_code}, {len(r.content)} bytes for range {a}-{b}"
                except Exception as e:  # noqa: BLE001
                    last = repr(e)
                time.sleep(0.5)
            else:
                raise OSError(f"range read failed: {last}")
        return self.cache[i]

    def readinto(self, buf):
        n = min(len(buf), self.size - self.pos)
        if n <= 0:
            return 0
        out = bytearray()
        while len(out) < n:
            p = self.pos + len(out)
            i = p // self.block
            blk = self._blk(i)
            o = p - i * self.block
            out += blk[o:o + n - len(out)]
        buf[:n] = out[:n]
        self.pos += n
        return n


# ------------------------------------------------------------------ listing
def _scan_start(key):
    m = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})", key)
    return dt.datetime(int(m[1]), 1, 1, int(m[3]), int(m[4]), int(m[5]), tzinfo=dt.timezone.utc) + dt.timedelta(days=int(m[2]) - 1)


def nearest_scan(hour):
    """(product, key, scan start) of the DSR scan whose start is nearest to the hour (UTC), searching C.GOES_DSR_PRODUCTS in order (CONUS first: it is empty today,
    full disk is what exists); None when nothing is listed."""
    for prod in C.GOES_DSR_PRODUCTS:
        keys = []
        for h in (hour - dt.timedelta(hours=1), hour):
            pre = f"{prod}/{h.year}/{h.timetuple().tm_yday:03d}/{h.hour:02d}/"
            keys += re.findall(r"<Key>(.*?)</Key>", get(C.GOES_BUCKET, {"list-type": 2, "prefix": pre}, "goes_dsr").text)
        if keys:
            k = min(keys, key=lambda x: abs((_scan_start(x) - hour).total_seconds()))
            return prod, k, _scan_start(k)
    return None


# ------------------------------------------------------------------ one scan
def read_scan(key, box=None, block=None):
    """Read the city box of one DSR file with range requests. -> dict(t, lon, lat, dsr (W/m2, NaN = no retrieval), dqf, bytes, requests, attrs). Raises on any problem."""
    import h5py
    from .goes import Nav, _axis
    box = box or C.DSR_BOX
    f = RangeFile(C.GOES_BUCKET + key, block)
    with h5py.File(f, "r") as h:
        nav = Nav(h["goes_imager_projection"])
        x, y = _axis(h, "x"), _axis(h, "y")
        lons = np.array([box[0], box[2], box[0], box[2]])
        lats = np.array([box[1], box[1], box[3], box[3]])
        bx, by = nav.xy(lons, lats)
        pad = 2 * float(abs(h["x"].attrs["scale_factor"][0]))
        ci = np.nonzero((x >= bx.min() - pad) & (x <= bx.max() + pad))[0]
        ri = np.nonzero((y >= by.min() - pad) & (y <= by.max() + pad))[0]
        if len(ci) == 0 or len(ri) == 0:
            raise ValueError("city box is outside the scan")
        c0, c1, r0, r1 = int(ci.min()), int(ci.max()) + 1, int(ri.min()), int(ri.max()) + 1
        d = h["DSR"]
        raw = d[r0:r1, c0:c1]
        sc, off, fill = float(d.attrs["scale_factor"][0]), float(d.attrs["add_offset"][0]), int(d.attrs["_FillValue"][0])
        dq = h["DQF"][r0:r1, c0:c1] if "DQF" in h else np.zeros(raw.shape, np.uint8)
        t = dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=float(h["t"][()]))
        units = d.attrs["units"]
        attrs = dict(units=units.decode() if hasattr(units, "decode") else str(units), scale=sc, product=key.split("/")[0],
                     resolution=str(h.attrs.get("spatial_resolution", b"")), scene=str(h.attrs.get("scene_id", b"")))
    dsr = np.where(raw == fill, np.nan, raw.astype(np.float64) * sc + off).astype(np.float32)
    gx, gy = np.meshgrid(x[c0:c1], y[r0:r1])
    lon, lat = nav.lonlat(gx, gy)
    return dict(t=t, lon=lon.astype(np.float32), lat=lat.astype(np.float32), dsr=dsr, dqf=np.asarray(dq), bytes=f.bytes, requests=f.requests, attrs=attrs)


# ------------------------------------------------------------------ the per-hour object the forcing uses
class Dsr:
    """DSR of one hour on the city box: pixel centres (lon, lat), DSR (W/m2, NaN = none), quality flag (0 good, 1 degraded or invalid), the scan time."""

    def __init__(self, t, lon, lat, dsr, dqf=None):
        self.t = t
        self.lon, self.lat = np.asarray(lon, np.float32), np.asarray(lat, np.float32)
        self.dsr = np.asarray(dsr, np.float32)
        self.dqf = np.zeros(self.dsr.shape, np.uint8) if dqf is None else np.asarray(dqf, np.uint8)

    def cells(self, lat, lon, dlat, dlon, scale=None):
        """Mean DSR (W/m2) of the pixel centres inside the box lat +- scale * dlat / 2, lon +- scale * dlon / 2 of every cell centre (arrays [L]); NaN where the box holds
        none. Pixels of quality flag 0 are used when the box has any, otherwise the degraded ones (the whole scene is flagged 1 when the sun is low: the values are
        still usable, see the 24 h table of guide 06)."""
        s = C.DSR_BOX_SCALE if scale is None else scale
        fin = np.isfinite(self.dsr)
        out = np.full(len(lat), np.nan)
        for i, (la, lo) in enumerate(zip(lat, lon)):
            m = (np.abs(self.lat - la) <= s * dlat / 2.0) & (np.abs(self.lon - lo) <= s * dlon / 2.0) & fin
            if m.any():
                g = m & (self.dqf == 0)
                out[i] = float(self.dsr[g].mean() if g.any() else self.dsr[m].mean())
        return out

    def city_mean(self):
        return float(np.nanmean(self.dsr)) if np.isfinite(self.dsr).any() else float("nan")

    def share_degraded(self):
        fin = np.isfinite(self.dsr)
        return float((self.dqf[fin] != 0).mean()) if fin.any() else float("nan")


def k_cells(dsr, ghi_clear_at_scan, lat, lon, dlat, dlon):
    """k_sat per lattice cell from DSR: mean DSR in the cell's box / clear-sky GHI at the scan time, clipped to 0..1.2 (the retrieval can exceed the Ineichen clear sky a
    little; the caller clips to K_MIN..1). NaN where the cell has no retrieval or the clear-sky GHI is below C.DSR_MIN_GHI."""
    if not np.isfinite(ghi_clear_at_scan) or ghi_clear_at_scan < C.DSR_MIN_GHI:
        return np.full(len(lat), np.nan)
    return np.clip(dsr.cells(lat, lon, dlat, dlon) / ghi_clear_at_scan, 0.0, 1.2)


# ------------------------------------------------------------------ rolling cache (carried between runs, published next to the live files)
def _empty_cache():
    return dict(schema=2, grid=None, hours={})


def load_cache(path):
    """-> dict(schema, grid {shape, lon, lat} or None, hours {hour_iso: entry}); entry = dict(scan, product, final, t, dsr [ints, -1 = none], dqf "0101..." or None data).
    A missing, unreadable or other-schema file = empty cache."""
    try:
        j = json.loads(Path(path).read_text(encoding="utf-8"))
        if j.get("schema") == 2 and isinstance(j.get("hours"), dict):
            return j
    except Exception:  # noqa: BLE001
        pass
    return _empty_cache()


def save_cache(path, cache, now, keep_h=None):
    keep_h = C.GOES_CACHE_H if keep_h is None else keep_h
    lim = (now - dt.timedelta(hours=keep_h + 6)).isoformat()
    j = dict(cache, saved_iso=now.isoformat(), product=list(C.GOES_DSR_PRODUCTS), hours={k: v for k, v in sorted(cache["hours"].items()) if k >= lim})
    txt = json.dumps(j, separators=(",", ":"))
    Path(path).write_text(txt, encoding="utf-8", newline="\n")
    return len(txt.encode("utf-8"))


def _pack(scan, prod, final, d):
    """Cache entry from a Dsr (or None when the scan has no retrieval)."""
    e = dict(scan=scan, product=prod, final=bool(final), t=None, dsr=None, dqf=None)
    if d is not None:
        e.update(t=d.t.isoformat(), dsr=[-1 if not np.isfinite(v) else int(round(float(v))) for v in d.dsr.ravel()], dqf="".join("1" if q else "0" for q in d.dqf.ravel()))
    return e


def _unpack(e, grid):
    sh = tuple(grid["shape"])
    return Dsr(dt.datetime.fromisoformat(e["t"]), np.array(grid["lon"], np.float32).reshape(sh), np.array(grid["lat"], np.float32).reshape(sh),
               np.array([np.nan if v < 0 else v for v in e["dsr"]], np.float32).reshape(sh), np.array([int(c) for c in e["dqf"]], np.uint8).reshape(sh))


def hourly(hours, cache_path, now, log=print, deadline_s=None):
    """hours: daylight UTC datetimes on the hour (observed hours; the caller leaves out the night). -> ({hour: Dsr}, stats). Hours already in the rolling cache are not
    fetched again once they are final (older than C.DSR_REFRESH_H hours when fetched); the newest hours are looked up again each run (their scan may not have been
    published at the last run) and downloaded only when the nearest scan changed. Hours with no retrieval are remembered. Writes the cache file (also when nothing is new).
    Every failure is logged and means 'no DSR for that hour'."""
    deadline = time.time() + (C.DSR_DEADLINE_S if deadline_s is None else deadline_s)
    cache = load_cache(cache_path)
    stats = dict(hours=len(hours), cached=0, fetched=0, no_scan=0, no_retrieval=0, failed=0, deadline=0, bytes=0, requests=0, seconds=0.0, product=None, attrs=None)
    t_start = time.time()
    old = now - dt.timedelta(hours=C.DSR_REFRESH_H)
    for h in sorted(hours):
        key = h.isoformat()
        e = cache["hours"].get(key)
        if e is not None and e.get("final"):
            stats["cached"] += 1
            continue
        if time.time() > deadline:
            stats["deadline"] += 1
            continue
        try:
            ns = nearest_scan(h)
            if ns is None or abs((ns[2] - h).total_seconds()) > C.DSR_MAX_SCAN_OFFSET_MIN * 60:
                stats["no_scan"] += 1
                continue
            prod, k, _ = ns
            stats["product"] = prod
            if e is not None and e.get("scan") == k:                    # the nearest scan is the one already cached
                e["final"] = bool(h < old)
                stats["cached"] += 1
                continue
            sc = read_scan(k)
            stats["bytes"] += sc["bytes"]
            stats["requests"] += sc["requests"]
            stats["attrs"] = sc["attrs"]
            shape = list(sc["dsr"].shape)
            grid = cache.get("grid")
            if grid is None or grid["shape"] != shape or not (np.allclose(grid["lon"], sc["lon"].ravel(), atol=2e-3) and np.allclose(grid["lat"], sc["lat"].ravel(), atol=2e-3)):
                cache = _empty_cache()                                   # another pixel grid (box or product changed): the old hours do not fit
                cache["grid"] = dict(shape=shape, lon=np.round(sc["lon"], 4).ravel().tolist(), lat=np.round(sc["lat"], 4).ravel().tolist())
            has = bool(np.isfinite(sc["dsr"]).any())
            stats["fetched"] += 1
            if not has:
                stats["no_retrieval"] += 1
            cache["hours"][key] = _pack(k, prod, h < old, Dsr(sc["t"], sc["lon"], sc["lat"], sc["dsr"], sc["dqf"]) if has else None)
        except Exception as ex:  # noqa: BLE001
            log(f"dsr {h:%Y-%m-%d %H}Z: {ex!r}"[:240])
            stats["failed"] += 1
    res = {}
    if cache.get("grid"):
        for h in hours:
            e = cache["hours"].get(h.isoformat())
            if e is not None and e.get("dsr") is not None:
                try:
                    res[h] = _unpack(e, cache["grid"])
                except Exception:  # noqa: BLE001
                    pass
    stats["seconds"] = round(time.time() - t_start, 1)
    try:
        stats["cache_bytes"] = save_cache(cache_path, cache, now)
    except Exception as ex:  # noqa: BLE001
        log(f"dsr cache not written: {ex!r}")
    return res, stats
