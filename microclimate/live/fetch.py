"""One function per live source: 3 retries, timeouts, User-Agent. Each raises FetchError on failure (the builder logs it and goes on)."""
import datetime as dt
import re
import time

import numpy as np
import requests

from . import config as C


class FetchError(Exception):
    pass


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def get(url, params=None, name="", binary=False):
    if C.blocked(name):
        raise FetchError(f"{name}: blocked for testing (DEATHFOG_LIVE_BLOCK)")
    err = None
    t_start = time.monotonic()
    for k in range(C.RETRIES):
        wait_hint = None
        try:
            r = requests.get(url, params=params, headers=C.UA, timeout=C.TIMEOUT_S)
            if r.status_code == 200:
                return r
            err = f"HTTP {r.status_code}"
            if not (r.status_code >= 500 or r.status_code == 429):
                break  # 4xx other than 429: not transient, do not retry
            wait_hint = _retry_after(r)
        except requests.RequestException as e:  # timeouts, connection resets/errors (transient)
            err = repr(e)[:120]
        except Exception as e:  # noqa: BLE001  (anything else, e.g. a bad URL: not transient)
            err = repr(e)[:120]
            break
        if k == C.RETRIES - 1:
            break
        wait = min(wait_hint if wait_hint is not None else C.RETRY_WAITS_S[min(k, len(C.RETRY_WAITS_S) - 1)], C.RETRY_AFTER_MAX_S)
        if time.monotonic() - t_start + wait > C.RETRY_BUDGET_S:
            err = f"{err} (retry budget {C.RETRY_BUDGET_S:.0f} s exhausted)"
            break
        _log(f"[retry] {name or url}: {err}; attempt {k + 2}/{C.RETRIES} in {wait:.1f} s")
        _sleep(wait)
    raise FetchError(f"{name or url}: {err}")


_sleep = time.sleep
_log = lambda m: print(m, flush=True)  # noqa: E731


def _retry_after(r):
    try:
        return max(0.0, float(r.headers.get("Retry-After")))
    except (TypeError, ValueError, AttributeError):
        return None


def parse_time(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------- RRFS (Open-Meteo)
def lattice_points():
    lats = np.linspace(*C.LAT_RANGE, C.N_LAT)
    lons = np.linspace(*C.LON_RANGE, C.N_LON)
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    return LA.ravel(), LO.ravel()


def _hourly_arrays(js, names):
    h0 = js[0]["hourly"]
    times = [parse_time(x) for x in h0["time"]]
    out = {}
    for k in names:
        out[k] = np.array([[np.nan if v is None else v for v in j["hourly"].get(k, [None] * len(times))] for j in js], float)
    return times, out


def rrfs():
    """-> dict(model, times [nh], lat, lon, elev [L], vars {name: [L, nh]}, prof {name: [nh]}, prof_elev, last_valid)."""
    lat, lon = lattice_points()
    err = None
    for model in C.RRFS_MODELS:
        try:
            p = dict(latitude=",".join(f"{v:.4f}" for v in lat), longitude=",".join(f"{v:.4f}" for v in lon), models=model,
                     hourly=",".join(C.SURFACE_VARS), past_days=2, forecast_days=5, wind_speed_unit="ms", timezone="UTC")
            js = get(C.OPENMETEO_URL, p, "rrfs").json()
            if isinstance(js, dict):
                js = [js]
            times, vars_ = _hourly_arrays(js, C.SURFACE_VARS)
            if not np.isfinite(vars_["temperature_2m"]).any():
                raise FetchError(f"{model}: no data")
            pp = dict(latitude=C.PROFILE_POINT[0], longitude=C.PROFILE_POINT[1], models=model, hourly=",".join(C.PROFILE_VARS), past_days=2,
                      forecast_days=5, wind_speed_unit="ms", timezone="UTC")
            prof, prof_elev = {}, None
            try:
                jp = get(C.OPENMETEO_URL, pp, "rrfs").json()
                tp, ap = _hourly_arrays([jp], C.PROFILE_VARS)
                prof = {k: v[0] for k, v in ap.items()}
                assert len(tp) == len(times)
                prof_elev = jp.get("elevation")
            except Exception as e:  # noqa: BLE001  (profile is optional: fog top falls back to a constant)
                prof = {}
                err = f"profile unavailable: {e!r}"[:120]
            ok = np.nonzero(np.isfinite(vars_["temperature_2m"]).all(axis=0))[0]
            return dict(model=model, times=times, lat=lat, lon=lon, elev=np.array([j.get("elevation", 34.0) for j in js], float),
                        vars=vars_, prof=prof, prof_elev=prof_elev, last_valid=times[ok[-1]] if len(ok) else None, note=err)
        except Exception as e:  # noqa: BLE001
            err = f"{model}: {e!r}"[:160]
    raise FetchError("rrfs: " + str(err))


# ---------------------------------------------------------------- CO-OPS 9414290
def coops(begin, end):
    """6-minute wind (m/s, deg from, gust) and air temperature at Fort Point. -> dict(t [m] datetimes, s, d, g, temp, t_temp)."""
    prm = dict(station=C.COOPS_STATION, begin_date=begin.strftime("%Y%m%d %H:%M"), end_date=end.strftime("%Y%m%d %H:%M"), time_zone="gmt",
               units="metric", format="json", application="sf-death-fog")
    w = get(C.COOPS_URL, {**prm, "product": "wind"}, "coops").json().get("data") or []
    if not w:
        raise FetchError("coops: no wind data")
    f = lambda x: float(x) if x not in ("", None) else np.nan  # noqa: E731
    out = dict(t=[parse_time(x["t"].replace(" ", "T")) for x in w], s=np.array([f(x["s"]) for x in w]), d=np.array([f(x["d"]) for x in w]),
               g=np.array([f(x["g"]) for x in w]), t_temp=[], temp=np.array([]))
    try:
        a = get(C.COOPS_URL, {**prm, "product": "air_temperature"}, "coops").json().get("data") or []
        out["t_temp"] = [parse_time(x["t"].replace(" ", "T")) for x in a]
        out["temp"] = np.array([f(x["v"]) for x in a])
    except FetchError:
        pass
    return out


# ---------------------------------------------------------------- METAR
def _vis_mi(v):
    if v is None:
        return np.nan
    s = str(v).replace("+", "").strip()
    try:
        if "/" in s:
            parts = s.split()
            tot = 0.0
            for p in parts:
                a, _, b = p.partition("/")
                tot += float(a) / float(b) if b else float(a)
            return tot
        return float(s)
    except ValueError:
        return np.nan


def metar(hours=48):
    """-> {icao: list of dict(t, temp, dewp, wdir, wspd_ms, vis_mi, ceil_ft, wx = present weather string, e.g. '-DZ BR')} (ceil = lowest BKN/OVC/VV base, nan if none)."""
    js = get(C.METAR_URL, dict(ids=",".join(C.METAR_IDS), format="json", hours=hours), "metar").json()
    out = {k: [] for k in C.METAR_IDS}
    for x in js:
        ceil = np.nan
        for c in x.get("clouds") or []:
            if c.get("cover") in ("BKN", "OVC", "VV") and c.get("base") is not None:
                ceil = min(ceil, float(c["base"])) if np.isfinite(ceil) else float(c["base"])
        g = lambda k: np.nan if x.get(k) is None else float(x[k])  # noqa: E731
        spd = g("wspd")
        out.setdefault(x["icaoId"], []).append(dict(t=dt.datetime.fromtimestamp(x["obsTime"], dt.timezone.utc), temp=g("temp"), dewp=g("dewp"),
                                                    wdir=g("wdir") if str(x.get("wdir")).replace(".", "").isdigit() else np.nan,
                                                    wspd_ms=spd * 0.514444, vis_mi=_vis_mi(x.get("visib")), ceil_ft=ceil, wx=str(x.get("wxString") or "")))
    if not any(out.values()):
        raise FetchError("metar: empty")
    return out


# ---------------------------------------------------------------- NWS stations (T, Td)
def nws(station, limit=60):
    js = get(C.NWS_URL.format(id=station), dict(limit=limit), "nws").json()
    rows = []
    for f in js.get("features", []):
        p = f["properties"]
        v = lambda k: np.nan if (p.get(k) or {}).get("value") is None else float(p[k]["value"])  # noqa: E731
        rows.append(dict(t=dt.datetime.fromisoformat(p["timestamp"]).astimezone(dt.timezone.utc), temp=v("temperature"), dewp=v("dewpoint")))
    if not rows:
        raise FetchError(f"nws {station}: empty")
    return rows


# ---------------------------------------------------------------- Oakland sounding (SPC)
def sounding(now):
    """Newest OAK sounding (00/12 UTC) of the last 36 h. -> dict(valid, z [m], t [C], td [C], p [hPa])."""
    if C.blocked("sounding"):
        raise FetchError("sounding: blocked for testing (DEATHFOG_LIVE_BLOCK)")
    t = now.replace(minute=0, second=0, microsecond=0)
    t = t.replace(hour=12 if t.hour >= 12 else 0)
    last = None
    for back in range(0, 4):
        v = t - dt.timedelta(hours=12 * back)
        url = C.SOUNDING_URL.format(yy=f"{v:%y}", mm=f"{v:%m}", dd=f"{v:%d}", hh=f"{v:%H}")
        try:
            r = requests.get(url, headers=C.UA, timeout=C.TIMEOUT_S)
        except Exception as e:  # noqa: BLE001
            last = repr(e)[:100]
            continue
        if r.status_code == 200 and "%RAW%" in r.text:
            rows = []
            for ln in r.text.split("%RAW%")[1].splitlines():
                m = re.match(r"\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)", ln)
                if m:
                    p, z, tt, td = (float(m.group(i)) for i in range(1, 5))
                    if z > -9000 and tt > -9000:
                        rows.append((p, z, tt, td if td > -9000 else np.nan))
            a = np.array(rows)
            if len(a) > 10:
                return dict(valid=v, p=a[:, 0], z=a[:, 1], t=a[:, 2], td=a[:, 3])
        last = f"HTTP {r.status_code}"
    raise FetchError(f"sounding: none in the last 36 h ({last})")


def inversion_base(snd, rise_c=None, depth_m=None, zmax=2500.0):
    """Height (m ASL) of the marine inversion base: the lowest local temperature minimum above 100 m AGL from which the temperature rises
    by >= rise_c within depth_m. None if there is no such inversion below zmax."""
    rise_c = C.INV_RISE_C if rise_c is None else rise_c
    depth_m = C.INV_DEPTH_M if depth_m is None else depth_m
    z, t = snd["z"], snd["t"]
    o = np.argsort(z)
    z, t = z[o], t[o]
    z0 = z[0]
    for i in range(1, len(z) - 1):
        if z[i] - z0 < 100 or z[i] > zmax:
            continue
        if t[i] <= t[i - 1] and t[i] <= t[i + 1]:
            above = (z > z[i]) & (z <= z[i] + depth_m)
            if above.any() and t[above].max() - t[i] >= rise_c:
                return float(z[i])
    return None
