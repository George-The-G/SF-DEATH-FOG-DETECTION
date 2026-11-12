"""Replay weather: hourly history from Open-Meteo (the same free model v2 already uses) and replay-night selection."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import requests

PT = ZoneInfo("America/Los_Angeles")
VARS = "temperature_2m,dew_point_2m,wind_speed_10m,wind_direction_10m,cloud_cover,cloud_cover_low,precipitation,shortwave_radiation"


LEVELS_HPA = (1000, 975, 950, 925, 900, 850)
# Extra hourly variables used by the fog-top estimator (fogtop.py). Fetched separately (and cached next to the main cache as
# <name>_prof.json) so that older caches keep working; if the request fails the extras are simply absent and fogtop falls back.
EXTRA_VARS = (["boundary_layer_height", "freezing_level_height"] + [f"temperature_{l}hPa" for l in LEVELS_HPA]
              + [f"geopotential_height_{l}hPa" for l in LEVELS_HPA] + [f"relative_humidity_{l}hPa" for l in LEVELS_HPA[:5]])


def fetch_extra(data, cache):
    """Merge EXTRA_VARS into data['hourly'] (same hourly time axis). Never raises: on failure data is returned unchanged."""
    ec = Path(cache).with_name(Path(cache).stem + "_prof.json")
    try:
        if ec.exists():
            ex = json.load(open(ec))
        else:
            times = data["hourly"]["time"]
            lat, lon = data.get("latitude"), data.get("longitude")
            url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly={','.join(EXTRA_VARS)}"
                   f"&start_date={times[0][:10]}&end_date={times[-1][:10]}&timezone=UTC")
            r = requests.get(url, timeout=90, headers={"User-Agent": "deathfog-v3-mvp/0.1"})
            r.raise_for_status()
            ex = r.json()
            json.dump(ex, open(ec, "w"))
        pos = {t: i for i, t in enumerate(ex["hourly"]["time"])}
        for k in EXTRA_VARS:
            if k in ex["hourly"]:
                src = ex["hourly"][k]
                data["hourly"][k] = [src[pos[t]] if t in pos else None for t in data["hourly"]["time"]]
    except Exception as e:  # network/limits: optional data
        print("weather extras unavailable:", e)
    return data


def fetch(lat, lon, cache, past_days=60):
    cache = Path(cache)
    if cache.exists():
        return fetch_extra(json.load(open(cache)), cache)
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly={VARS}&past_days={past_days}"
           f"&forecast_days=1&wind_speed_unit=ms&timezone=UTC")
    r = requests.get(url, timeout=60, headers={"User-Agent": "deathfog-v3-mvp/0.1"})
    r.raise_for_status()
    data = r.json()
    json.dump(data, open(cache, "w"))
    return fetch_extra(data, cache)


def table(data):
    h = data["hourly"]
    t = [datetime.fromisoformat(x).replace(tzinfo=timezone.utc) for x in h["time"]]
    cols = {k: np.array([np.nan if v is None else v for v in h[k]], float) for k in h if k != "time"}
    return t, cols


FOG_TIERS = [(1.5, 3, True), (2.0, 3, True), (2.5, 2, True), (2.5, 2, False)]  # (max T-Td, hours, require mostly onshore wind)
DRY_TIERS = [5.0, 4.5, 4.0]  # min T-Td all night (above ~3.5 the road model cannot form dew or fog)


def pick_nights(data, fog_nights=7, dry_nights=2, until=None, info=None):
    """Nights run 18:00-10:00 Pacific and must be complete (end before `until`, default now).
    Fog night (strict tier): T-Td under 1.5 C for 3+ hours, mean wind under 8 m/s, under 1 mm rain, and mostly onshore
    wind (mean onshore weight over those hours >= 0.3). If fewer than `fog_nights` qualify the criteria are relaxed
    stepwise (FOG_TIERS) and each night's tier is recorded in `info` (a dict id -> tier index, 0 = strict).
    Dry control: T-Td over 5 C all night and no rain (relaxed stepwise to 4 C, DRY_TIERS).
    Returns [(start_utc, kind)]: fog nights then dry nights, most recent first within a tier."""
    from .wetness import onshore_weight
    until = until or datetime.now(timezone.utc)
    t, c = table(data)
    gap = c["temperature_2m"] - c["dew_point_2m"]
    ons = onshore_weight(np.nan_to_num(c["wind_direction_10m"], nan=0.0))
    cands = []
    days = sorted({(x.astimezone(PT) - timedelta(hours=18)).date() for x in t}, reverse=True)
    for d in days:
        s = datetime(d.year, d.month, d.day, 18, tzinfo=PT).astimezone(timezone.utc)
        if s + timedelta(hours=16) > until:
            continue
        idx = [i for i, x in enumerate(t) if s <= x < s + timedelta(hours=16)]
        if len(idx) < 16 or np.isnan(gap[idx]).any():
            continue
        cands.append((s, gap[idx], c["wind_speed_10m"][idx], np.nan_to_num(c["precipitation"][idx]), ons[idx]))
    fog, dry = [], []
    for ti, (g_thr, hrs, need_on) in enumerate(FOG_TIERS):
        for s, g, w, p, o in cands:
            if len(fog) >= fog_nights:
                break
            if any(s == f for f, _ in fog):
                continue
            m = g < g_thr
            if m.sum() >= hrs and np.nanmean(w) < 8 and p.sum() < 1.0 and (not need_on or o[m].mean() >= 0.3):
                fog.append((s, "fog"))
                if info is not None:
                    info[s.isoformat()] = ti
    for ti, thr in enumerate(DRY_TIERS):
        for s, g, w, p, o in cands:
            if len(dry) >= dry_nights:
                break
            if any(s == f for f, _ in dry) or any(s == f for f, _ in fog):
                continue
            if g.min() > thr and p.sum() == 0:
                dry.append((s, "dry"))
                if info is not None:
                    info[s.isoformat()] = ti
    return fog + dry

def night_series(data, start_utc, step_min=10):
    """Interpolate one night (16 h from start_utc) to `step_min` steps. Wind direction is interpolated as a vector."""
    return series(data, start_utc, start_utc + timedelta(hours=16), step_min)


def series(data, start_utc, end_utc, step_min=10):
    """Interpolate any window [start_utc, end_utc] (inclusive, aware datetimes) of the hourly table to `step_min` steps
    (n = (end - start) / step + 1). Values outside the table are held at the edge value. Wind direction is interpolated as a vector."""
    t, c = table(data)
    base = np.array([(x - start_utc).total_seconds() / 3600 for x in t])
    n = int((end_utc - start_utc).total_seconds() / 60.0 / step_min + 1e-9) + 1
    tt = np.arange(n) * step_min / 60.0
    out = {"t_utc": [(start_utc + timedelta(minutes=step_min * i)) for i in range(n)]}
    for k in ("temperature_2m", "dew_point_2m", "wind_speed_10m", "cloud_cover", "cloud_cover_low", "precipitation", "shortwave_radiation"):
        out[k] = np.interp(tt, base, np.nan_to_num(c[k]))
    for k in EXTRA_VARS:  # optional profile variables (NaN where unavailable)
        if k in c:
            out[k] = np.interp(tt, base, c[k]) if np.isfinite(c[k]).any() else np.full(n, np.nan)
    out["elevation_m"] = float(data.get("elevation", 34.0))  # Open-Meteo grid-cell elevation (T2m/Td2m refer to it)
    th = np.radians(c["wind_direction_10m"])
    ex, ey = np.interp(tt, base, np.nan_to_num(np.sin(th))), np.interp(tt, base, np.nan_to_num(np.cos(th)))
    out["wind_dir"] = (np.degrees(np.arctan2(ex, ey)) + 360) % 360
    return out

