"""Replay weather: hourly history from Open-Meteo (the same free model v2 already uses) and replay-night selection."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import requests

PT = ZoneInfo("America/Los_Angeles")
VARS = "temperature_2m,dew_point_2m,wind_speed_10m,wind_direction_10m,cloud_cover,cloud_cover_low,precipitation,shortwave_radiation"


def fetch(lat, lon, cache, past_days=60):
    cache = Path(cache)
    if cache.exists():
        return json.load(open(cache))
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly={VARS}&past_days={past_days}"
           f"&forecast_days=1&wind_speed_unit=ms&timezone=UTC")
    r = requests.get(url, timeout=60, headers={"User-Agent": "deathfog-v3-mvp/0.1"})
    r.raise_for_status()
    data = r.json()
    json.dump(data, open(cache, "w"))
    return data


def table(data):
    h = data["hourly"]
    t = [datetime.fromisoformat(x).replace(tzinfo=timezone.utc) for x in h["time"]]
    cols = {k: np.array([np.nan if v is None else v for v in h[k]], float) for k in h if k != "time"}
    return t, cols


def pick_nights(data, fog_nights=7, dry_nights=2):
    """Nights run 18:00-10:00 Pacific. Fog night = gap under 1.5 C for 3+ hours, calm-to-moderate wind, little rain.
    Dry control = gap over 5 C all night and no rain. Returns [(start_utc, kind)] most recent first."""
    t, c = table(data)
    gap = c["temperature_2m"] - c["dew_point_2m"]
    out_fog, out_dry = [], []
    days = sorted({(x.astimezone(PT) - timedelta(hours=18)).date() for x in t}, reverse=True)
    for d in days:
        s = datetime(d.year, d.month, d.day, 18, tzinfo=PT).astimezone(timezone.utc)
        idx = [i for i, x in enumerate(t) if s <= x < s + timedelta(hours=16)]
        if len(idx) < 16:
            continue
        g, w, p = gap[idx], c["wind_speed_10m"][idx], np.nan_to_num(c["precipitation"][idx])
        if np.isnan(g).any():
            continue
        if (g < 1.5).sum() >= 3 and np.nanmean(w) < 8 and p.sum() < 1.0:
            out_fog.append((s, "fog"))
        elif g.min() > 5 and p.sum() == 0:
            out_dry.append((s, "dry"))
    return out_fog[:fog_nights] + out_dry[:dry_nights]


def night_series(data, start_utc, step_min=10):
    """Interpolate one night to `step_min` steps. Wind direction is interpolated as a vector."""
    t, c = table(data)
    base = np.array([(x - start_utc).total_seconds() / 3600 for x in t])
    n = int(16 * 60 / step_min) + 1
    tt = np.arange(n) * step_min / 60.0
    out = {"t_utc": [(start_utc + timedelta(minutes=step_min * i)) for i in range(n)]}
    for k in ("temperature_2m", "dew_point_2m", "wind_speed_10m", "cloud_cover", "cloud_cover_low", "precipitation", "shortwave_radiation"):
        out[k] = np.interp(tt, base, np.nan_to_num(c[k]))
    th = np.radians(c["wind_direction_10m"])
    ex, ey = np.interp(tt, base, np.nan_to_num(np.sin(th))), np.interp(tt, base, np.nan_to_num(np.cos(th)))
    out["wind_dir"] = (np.degrees(np.arctan2(ex, ey)) + 360) % 360
    return out
