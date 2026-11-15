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


BOX = (-123.3, 37.2, -121.9, 38.3)   # lon0, lat0, lon1, lat1 of the crop of LowCloud (and of the 5-minute store, microclimate/live/goes5.py)


def read_crop(a, h, box=BOX):
    """The ONE implementation of the crop of a scan pair (LowCloud and the 5-minute store both call it). a, h: open h5py files of the ACM and ACHA scans.
    -> dict(nav, x, y (crop axes, rad), c0, c1, r0, r1 (offsets in the ACM grid), acm (uint8 [H, W]), dqf (uint8 [H, W] or None), t (datetime), ht (float32 [H, W] metres, NaN = none))."""
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
    dqf = a["DQF"][r0:r1, c0:c1] if "DQF" in a else None   # ACM data quality flag (0 good, 1 bad quality, 2 space, 6 degraded; 255 fill), checked in a real file 2026-11
    t = dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=float(a["t"][()]))
    hx, hy = _axis(h, "x"), _axis(h, "y")
    hc = np.clip(np.rint(np.interp(x[c0:c1], hx, np.arange(len(hx)))).astype(int), 0, len(hx) - 1)
    hr = np.clip(np.rint(np.interp(y[r0:r1], hy[::-1], np.arange(len(hy))[::-1])).astype(int), 0, len(hy) - 1)
    ht_raw = h["HT"][:]
    sc = float(h["HT"].attrs["scale_factor"][0])
    fill = int(h["HT"].attrs["_FillValue"][0])
    ht = ht_raw[np.ix_(hr, hc)].astype(np.float32) * sc
    ht[ht_raw[np.ix_(hr, hc)] == fill] = np.nan
    return dict(nav=nav, x=x[c0:c1], y=y[r0:r1], c0=c0, c1=c1, r0=r0, r1=r1, acm=np.asarray(acm), dqf=None if dqf is None else np.asarray(dqf), t=t, ht=ht)


class LowCloud:
    """Low-cloud flag (0/1, float) on the 2 km GOES crop around San Francisco for one scan."""

    def __init__(self, acm_path, ha_path, box=BOX):
        import h5py
        with h5py.File(acm_path, "r") as a, h5py.File(ha_path, "r") as h:
            cr = read_crop(a, h, box)
        acm, ht = cr["acm"], cr["ht"]
        self.t = cr["t"]
        self.nav, self.x, self.y = cr["nav"], cr["x"], cr["y"]
        self.acm = np.asarray(acm)       # clear sky mask classes 0..3 (255 = none): the cloudiness factor of guide 06 is taken from them
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

    def k_field(self):
        """Cloudiness factor k per pixel from the mask class (C.SAT_K: clear 1.0, probably clear 0.9, probably cloudy 0.6, cloudy 0.35); NaN without a mask."""
        k = np.full(self.acm.shape, np.nan, np.float32)
        for c, v in enumerate(C.SAT_K):
            k[self.acm == c] = v
        return k

    def k_cells(self, lat, lon, dlat, dlon):
        """Mean cloudiness factor of the pixels (centres) inside the box lat +- dlat / 2, lon +- dlon / 2 of every cell centre (arrays [L]); NaN where the box holds no
        pixel with a mask. Reads the scan only."""
        plon, plat = self.pixel_lonlat()
        kf = self.k_field()
        out = np.full(len(lat), np.nan)
        for i, (la, lo) in enumerate(zip(lat, lon)):
            m = (np.abs(plat - la) <= dlat / 2.0) & (np.abs(plon - lo) <= dlon / 2.0) & np.isfinite(kf)
            if m.any():
                out[i] = float(kf[m].mean())
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


# ---- 5-minute store: one derivation of "satellite fog" (multi-scale guide 07, replay/goes-crop-format.md) ----
RULE_VERSION = 1


def _sun_elevation(t_unix, lon, lat):
    """Sun elevation (degrees) at lon/lat at a unix time, from the same pvlib call the sun code uses (microclimate.wetness.sun_position)."""
    from .. import wetness
    t = dt.datetime.fromtimestamp(float(t_unix), tz=dt.timezone.utc)
    return float(wetness.sun_position([t], lat, lon)[1][0])


def low_cloud(crop, i, version=1, sun_el=None):
    """Probability, confidence and top of low cloud for scan i of a 5-minute crop -> (p, c, top_m, rule_version); p, c, top_m are float32 [H, W] (top_m 0 = none).
    crop needs: acm uint8 [n, H, W], ht uint16 [n, H, W] (metres, 0 = no retrieval), t_scan int64 [n] (unix s), centre (lon, lat) of the crop.
    Rule version 1:
      p  = {0: 0, 1: 0.25, 2: 0.75, 3: 1}[class] where the cloud top is below C.LOW_TOP_M or no height was retrieved; 0 where the top is high; 0 where acm >= 254.
      c  = 0 where acm >= 254 (bad quality / missing); 0 where more than C.CLOUD_HIGH_MAX of the valid pixels within +- C.GOES5_HIGH_WIN pixels are cloud with a top at
           or above C.LOW_TOP_M (high cloud hides the layer); 0 while the sun elevation is within +- C.GOES5_TERM_EL degrees (day / night algorithm swap);
           else C.GOES5_CONF_DAY by day (elevation above the window), C.GOES5_CONF_NIGHT by night. The elevation is taken once at the crop centre (it varies
           by about 1 degree over the crop). sun_el overrides it (tests).
    Parallax is not applied here: it belongs to sampling (parallax_shift_m, goes5.sample_macro)."""
    if version != RULE_VERSION:
        raise ValueError(f"unknown low-cloud rule version {version}")
    from scipy.ndimage import uniform_filter
    acm = np.asarray(crop.acm[i])
    ht = np.asarray(crop.ht[i]).astype(np.float32)
    bad = acm >= 254
    cls = np.where(bad, 0, acm).clip(0, 3)
    pc = np.array([0.0, 0.25, 0.75, 1.0], np.float32)[cls]
    high_top = ht >= C.LOW_TOP_M                      # ht 0 = none retrieved: not high
    p = np.where(bad | high_top, 0.0, pc).astype(np.float32)
    top = np.where(ht > 0, ht, 0).astype(np.float32)
    cloudy_high = ((cls >= 2) & ~bad & high_top).astype(np.float32)
    valid = (~bad).astype(np.float32)
    w = 2 * int(C.GOES5_HIGH_WIN) + 1
    nv = uniform_filter(valid, size=w, mode="nearest")
    nh = uniform_filter(cloudy_high, size=w, mode="nearest")
    hidden = nh > C.CLOUD_HIGH_MAX * np.maximum(nv, 1e-6)
    el = _sun_elevation(crop.t_scan[i], *crop.centre) if sun_el is None else float(sun_el)
    if abs(el) <= C.GOES5_TERM_EL:
        base = 0.0
    else:
        base = C.GOES5_CONF_DAY if el > 0 else C.GOES5_CONF_NIGHT
    c = np.where(bad | hidden, 0.0, base).astype(np.float32)
    return p, c, top, RULE_VERSION


def view_geometry(lon, lat, sat_lon, sat_h_m=35786023.0, r_earth_m=6371008.8):
    """View zenith angle (degrees) and azimuth from the ground point TOWARDS the satellite (degrees clockwise from north) of a geostationary satellite at
    longitude sat_lon (equator, height sat_h_m above a spherical earth of radius r_earth_m). Spherical formulas: central angle g between the point and the subpoint,
    cos g = cos(lat) cos(lon - sat_lon); zenith z from tan z = sin g / (cos g - R / (R + H)); azimuth = initial great-circle bearing to the subpoint.
    San Francisco (-122.4, 37.76) with the satellite at -137.0 gives a zenith of 46.4 degrees and an azimuth of 203 degrees (south-south-west)."""
    phi, dl = np.radians(lat), np.radians(np.asarray(sat_lon, float) - np.asarray(lon, float))
    cg = np.cos(phi) * np.cos(dl)
    sg = np.sqrt(np.maximum(1.0 - cg ** 2, 0.0))
    z = np.degrees(np.arctan2(sg, cg - r_earth_m / (r_earth_m + sat_h_m)))
    az = np.degrees(np.arctan2(np.sin(dl), -np.sin(phi) * np.cos(dl))) % 360.0
    return z, az


def parallax_shift_m(top_m, lon, lat, sat_lon):
    """(east, north) metres by which the true ground position of a cloud top of height top_m lies from where the satellite projects it: top_m * tan(zenith) TOWARDS
    the satellite (the line of sight meets the ground farther from the satellite than the cloud is, so the cloud is south-west of its apparent place at San Francisco)."""
    z, az = view_geometry(lon, lat, sat_lon)
    d = np.asarray(top_m, float) * np.tan(np.radians(z))
    return d * np.sin(np.radians(az)), d * np.cos(np.radians(az))
