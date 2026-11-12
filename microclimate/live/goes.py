"""GOES-18 low cloud over San Francisco (PLAN_LIVE_DATA.md 3.3): ABI-L2-ACMC (clear sky mask, 2 km) + ABI-L2-ACHAC (cloud top height, 10 km)
from the public AWS bucket noaa-goes18 over anonymous HTTPS. Low cloud = (ACM probably cloudy / cloudy) AND (cloud top < LOW_TOP_M).
Only the CONUS file nearest each hour is used; the SF crop is decoded with h5py (no xarray / netCDF4 / s3fs needed).
Per hour the result is a low-cloud flag on the 2 km crop that can be sampled at any lon/lat (bilinear), plus the upwind-sector fraction.
"""
import datetime as dt
import math
import re
from pathlib import Path

import numpy as np

from . import config as C
from .fetch import FetchError, get


def _list_keys(prod, t):
    pre = f"{prod}/{t.year}/{t.timetuple().tm_yday:03d}/{t.hour:02d}/"
    r = get(C.GOES_BUCKET, {"list-type": 2, "prefix": pre}, "goes")
    return re.findall(r"<Key>(.*?)</Key>", r.text)


def _scan_start(key):
    m = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})", key)
    return dt.datetime(int(m[1]), 1, 1, int(m[3]), int(m[4]), int(m[5]), tzinfo=dt.timezone.utc) + dt.timedelta(days=int(m[2]) - 1)


def nearest_keys(t):
    """(ACM key, ACHA key) of the scans whose start is nearest to the hour t (UTC), or None."""
    out = []
    for prod in (C.GOES_MASK, C.GOES_HEIGHT):
        keys = _list_keys(prod, t - dt.timedelta(hours=1)) + _list_keys(prod, t)
        if not keys:
            return None
        out.append(min(keys, key=lambda k: abs((_scan_start(k) - t).total_seconds())))
    return tuple(out)


def download(key, cache):
    f = Path(cache) / key.split("/")[-1]
    if f.exists() and f.stat().st_size > 1000:
        return f
    Path(cache).mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".part")
    tmp.write_bytes(get(C.GOES_BUCKET + key, None, "goes").content)
    tmp.replace(f)
    return f


def prune(cache, keep_h=None):
    keep_h = C.GOES_CACHE_H if keep_h is None else keep_h
    lim = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=keep_h + 6)
    for f in Path(cache).glob("OR_ABI-L2-*.nc"):
        try:
            if _scan_start(f.name) < lim:
                f.unlink()
        except Exception:  # noqa: BLE001
            pass


class Nav:
    """GOES fixed-grid navigation (PUG): lon/lat -> scan angles x, y (rad)."""

    def __init__(self, proj):
        self.req = float(proj.attrs["semi_major_axis"][0])
        self.rpol = float(proj.attrs["semi_minor_axis"][0])
        self.H = float(proj.attrs["perspective_point_height"][0]) + self.req
        self.lam0 = math.radians(float(proj.attrs["longitude_of_projection_origin"][0]))

    def xy(self, lon, lat):
        lam, phi = np.radians(lon), np.radians(lat)
        e2 = (self.req ** 2 - self.rpol ** 2) / self.req ** 2
        phic = np.arctan((self.rpol ** 2 / self.req ** 2) * np.tan(phi))
        rc = self.rpol / np.sqrt(1 - e2 * np.cos(phic) ** 2)
        sx = self.H - rc * np.cos(phic) * np.cos(lam - self.lam0)
        sy = -rc * np.cos(phic) * np.sin(lam - self.lam0)
        sz = rc * np.sin(phic)
        return np.arcsin(-sy / np.sqrt(sx ** 2 + sy ** 2 + sz ** 2)), np.arctan(sz / sx)

    def lonlat(self, x, y):
        """Inverse of xy(): scan angles x, y (rad, arrays) -> (lon, lat) in degrees (PUG inverse navigation)."""
        x, y = np.asarray(x, float), np.asarray(y, float)
        k = self.req ** 2 / self.rpol ** 2
        a = np.sin(x) ** 2 + np.cos(x) ** 2 * (np.cos(y) ** 2 + k * np.sin(y) ** 2)
        b = -2.0 * self.H * np.cos(x) * np.cos(y)
        c = self.H ** 2 - self.req ** 2
        rs = (-b - np.sqrt(b ** 2 - 4.0 * a * c)) / (2.0 * a)
        sx, sy, sz = rs * np.cos(x) * np.cos(y), -rs * np.sin(x), rs * np.cos(x) * np.sin(y)
        lat = np.degrees(np.arctan(k * sz / np.sqrt((self.H - sx) ** 2 + sy ** 2)))
        lon = np.degrees(self.lam0 - np.arctan(sy / (self.H - sx)))
        return lon, lat


def _axis(ds, name):
    a = ds[name]
    raw = a[:].astype(np.float64)
    return raw * float(a.attrs["scale_factor"][0]) + float(a.attrs["add_offset"][0])


class LowCloud:
    """Low-cloud flag (0/1, float) on the 2 km GOES crop around San Francisco for one scan."""

    def __init__(self, acm_path, ha_path, box=(-123.3, 37.2, -121.9, 38.3)):
        import h5py
        with h5py.File(acm_path, "r") as a, h5py.File(ha_path, "r") as h:
            nav = Nav(a["goes_imager_projection"])
            x, y = _axis(a, "x"), _axis(a, "y")
            lons = np.array([box[0], box[2], box[0], box[2]])
            lats = np.array([box[1], box[1], box[3], box[3]])
            bx, by = nav.xy(lons, lats)
            pad = 3 * 5.6e-5
            ci = np.nonzero((x >= bx.min() - pad) & (x <= bx.max() + pad))[0]        # x increases with column
            ri = np.nonzero((y >= by.min() - pad) & (y <= by.max() + pad))[0]        # y decreases with row
            c0, c1, r0, r1 = int(ci.min()), int(ci.max()) + 1, int(ri.min()), int(ri.max()) + 1
            acm = a["ACM"][r0:r1, c0:c1]
            self.t = dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=float(a["t"][()]))
            hx, hy = _axis(h, "x"), _axis(h, "y")
            hc = np.clip(np.rint(np.interp(x[c0:c1], hx, np.arange(len(hx)))).astype(int), 0, len(hx) - 1)
            hr = np.clip(np.rint(np.interp(y[r0:r1], hy[::-1], np.arange(len(hy))[::-1])).astype(int), 0, len(hy) - 1)
            ht_raw = h["HT"][:]
            sc = float(h["HT"].attrs["scale_factor"][0])
            fill = int(h["HT"].attrs["_FillValue"][0])
        ht = ht_raw[np.ix_(hr, hc)].astype(np.float32) * sc
        ht[ht_raw[np.ix_(hr, hc)] == fill] = np.nan
        self.nav, self.x, self.y = nav, x[c0:c1], y[r0:r1]
        cloudy = (acm == 2) | (acm == 3)
        self.valid = acm != 255
        self.low = (cloudy & np.isfinite(ht) & (ht < C.LOW_TOP_M)).astype(np.float32)
        self.cloudy = cloudy.astype(np.float32)

    def sample(self, lon, lat, field="low"):
        """Bilinear sample of the flag at lon/lat (arrays); NaN outside the crop."""
        gx, gy = self.nav.xy(np.asarray(lon, float), np.asarray(lat, float))
        fc = np.interp(gx, self.x, np.arange(len(self.x)), left=np.nan, right=np.nan)
        fr = np.interp(gy, self.y[::-1], np.arange(len(self.y))[::-1], left=np.nan, right=np.nan)
        a = getattr(self, field)
        ok = np.isfinite(fc) & np.isfinite(fr)
        out = np.full(np.shape(gx), np.nan)
        c0 = np.clip(np.floor(np.where(ok, fc, 0)).astype(int), 0, a.shape[1] - 2)
        r0 = np.clip(np.floor(np.where(ok, fr, 0)).astype(int), 0, a.shape[0] - 2)
        tx, ty = np.where(ok, fc, 0) - c0, np.where(ok, fr, 0) - r0
        v = (a[r0, c0] * (1 - tx) * (1 - ty) + a[r0, c0 + 1] * tx * (1 - ty) + a[r0 + 1, c0] * (1 - tx) * ty + a[r0 + 1, c0 + 1] * tx * ty)
        out[ok] = v[ok]
        return out

    def pixel_lonlat(self):
        """(lon, lat) of every pixel centre of the crop, arrays [rows, cols] (computed once, cached)."""
        if getattr(self, "_ll", None) is None:
            gx, gy = np.meshgrid(self.x, self.y)
            self._ll = self.nav.lonlat(gx, gy)
        return self._ll

    def box_fraction(self, lon0, lat0, lon1, lat1):
        """Pixels whose centre lies in the lon/lat box (west lon0, south lat0, east lon1, north lat1) -> (low, high, valid):
        low = share of the valid pixels that are low cloud (cloudy, top below LOW_TOP_M); high = share of the valid pixels that are cloudy but
        not low (top above LOW_TOP_M or no height retrieved: they hide fog from the satellite); valid = share of all pixels in the box that have a
        cloud mask. (nan, nan, 0.0) when no pixel centre lies in the box. Reads the scan only."""
        lon, lat = self.pixel_lonlat()
        m = (lon >= lon0) & (lon <= lon1) & (lat >= lat0) & (lat <= lat1)
        n = int(m.sum())
        if n == 0:
            return float("nan"), float("nan"), 0.0
        v = np.asarray(self.valid)[m].astype(bool)
        nv = int(v.sum())
        if nv == 0:
            return float("nan"), float("nan"), 0.0
        low = np.asarray(self.low)[m][v] >= 0.5
        cloudy = np.asarray(self.cloudy)[m][v] >= 0.5
        return float(low.mean()), float((cloudy & ~low).mean()), nv / n

    def sector_fraction(self, wind_from_deg):
        """Mean low-cloud flag in the upwind sector 2-15 km off the coast reference point, wind-from direction +- 22.5 deg."""
        lat0, lon0 = C.COAST_REF
        d = np.arange(C.BOX_NEAR_KM, C.BOX_FAR_KM + 0.01, 1.0)
        a = np.radians(wind_from_deg + np.arange(-C.SECTOR_HALF_DEG, C.SECTOR_HALF_DEG + 0.01, 7.5))
        D, A = np.meshgrid(d, a, indexing="ij")
        dn, de = D * np.cos(A), D * np.sin(A)           # km north / east towards the wind-from direction
        lat = lat0 + dn / 111.0
        lon = lon0 + de / (111.0 * math.cos(math.radians(lat0)))
        v = self.sample(lon, lat)
        return float(np.nanmean(v)) if np.isfinite(v).any() else float("nan")


def hourly(hours, cache, log=print, workers=6):
    """hours: list of UTC datetimes (on the hour). -> {hour: LowCloud or None}. Failures give None (and are logged)."""
    from concurrent.futures import ThreadPoolExecutor
    cache = Path(cache)

    def one(t):
        try:
            keys = nearest_keys(t)
            if keys is None:
                return t, None
            fa, fh = download(keys[0], cache), download(keys[1], cache)
            return t, LowCloud(fa, fh)
        except FetchError as e:
            log(f"goes {t:%Y-%m-%d %H}Z: {e}")
            return t, None
        except Exception as e:  # noqa: BLE001
            log(f"goes {t:%Y-%m-%d %H}Z: {e!r}")
            return t, None
    with ThreadPoolExecutor(workers) as ex:
        res = dict(ex.map(one, hours))
    try:
        prune(cache)
    except Exception:  # noqa: BLE001
        pass
    return res
