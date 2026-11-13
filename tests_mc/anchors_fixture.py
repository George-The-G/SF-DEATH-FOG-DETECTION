"""Synthetic inputs for forcing.build (no network): a lattice RRFS dict and observation dicts, deterministic."""
import hashlib
from datetime import datetime, timedelta, timezone

import numpy as np

from microclimate.live import config as C, fetch, forcing

T0 = datetime(2026, 11, 10, 2, tzinfo=timezone.utc)
NOW = T0 + timedelta(hours=40)
N_STEPS = 72 * 6 + 1


def fake_rrfs(hours=130, seed=1):
    r = np.random.default_rng(seed)
    lat, lon = fetch.lattice_points()
    L = len(lat)
    times = [T0 - timedelta(hours=48) + timedelta(hours=h) for h in range(hours)]
    h = np.arange(hours, dtype=float)
    base = lambda a, ph: a * np.sin(h / 7.0 + ph)  # noqa: E731
    V = {}
    V["temperature_2m"] = 13 + base(2, 0)[None, :] + r.normal(0, 0.3, (L, 1))
    V["dew_point_2m"] = 11 + base(1, 1)[None, :] + r.normal(0, 0.2, (L, 1))
    V["wind_speed_10m"] = 5 + base(1.5, 2)[None, :] + r.normal(0, 0.3, (L, 1))
    V["wind_direction_10m"] = 265 + base(25, 0.5)[None, :] + r.normal(0, 3, (L, 1))
    V["cloud_cover_low"] = np.clip(70 + base(25, 3)[None, :], 0, 100) + 0 * r.normal(0, 1, (L, 1))
    V["cloud_cover"] = np.clip(80 + base(15, 3)[None, :], 0, 100) + 0 * r.normal(0, 1, (L, 1))
    V["precipitation"] = np.zeros((L, hours))
    V["shortwave_radiation"] = np.maximum(0, 500 * np.sin((h - 6) / 24.0 * 2 * np.pi))[None, :] * np.ones((L, 1))
    V["visibility"] = np.full((L, hours), 20000.0)
    return dict(model="fake", times=times, lat=lat, lon=lon, elev=np.linspace(5, 120, L), vars=V, prof={}, prof_elev=34.0, last_valid=times[-1], note=None)


def fake_obs(seed=2):
    r = np.random.default_rng(seed)
    t = [T0 + timedelta(minutes=6 * k) for k in range(0, 6 * 38)]
    co = dict(t=t, s=4.5 + 0.8 * np.sin(np.arange(len(t)) / 40.0) + r.normal(0, 0.2, len(t)), d=250 + 10 * np.cos(np.arange(len(t)) / 50.0),
              g=np.full(len(t), 7.0), t_temp=t, temp=12.5 + np.sin(np.arange(len(t)) / 60.0))
    mt = {}
    for k, off in (("KHAF", -0.5), ("KSFO", 0.8), ("KOAK", 1.5)):
        rows = []
        for hh in range(0, 38):
            rows.append(dict(t=T0 + timedelta(hours=hh), temp=13 + off + np.sin(hh / 9.0), dewp=11 + off / 2, wdir=250.0 + hh % 7, wspd_ms=4.0 + (hh % 3) * 0.3,
                             vis_mi=10.0, ceil_ft=np.nan))
        mt[k] = rows
    nws = {"SFOC1": [dict(t=T0 + timedelta(hours=hh), temp=14.0 + np.sin(hh / 9.0), dewp=11.5) for hh in range(0, 38)]}
    return dict(coops=co, metar=mt, nws=nws, sounding=None)


def build(obs=None, flag=None, **kw):
    obs = fake_obs() if obs is None else obs
    r = np.random.default_rng(5)
    n = 50
    x = 540500 + r.uniform(0, 16000, n)
    y = 4172000 + r.uniform(0, 15000, n)
    return forcing.build(fake_rrfs(), T0, N_STEPS, NOW, x, y, obs, sector_fn=None, log=lambda *a: None, **kw), x, y


def digest(F, steps=(0, 100, 250, 400)):
    h = hashlib.sha256()
    for k in ("temperature_2m", "dew_point_2m", "wind_speed_10m", "wind_dir", "cloud_cover_low"):
        h.update(np.ascontiguousarray(F["ser"][k]).tobytes())
    for a in (F["s_series"], F["fog_top"]):
        h.update(np.ascontiguousarray(a).tobytes())
    f0 = {"temp": 0.0, "dew": 0.0, "wind": 0.0, "cloud_low": 0.0, "precip": 0.0, "sw": 0.0}
    for i in steps:
        f = F["forcing_fn"](i, f0)
        for k in sorted(f):
            h.update(k.encode())
            h.update(np.ascontiguousarray(f[k]).tobytes())
    return h.hexdigest()
