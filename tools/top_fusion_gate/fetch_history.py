"""Leave-one-sounding-out gate for guide 06 (not part of the repo). Usage: gate.py REPO DAYS"""
import sys, json, datetime as dt, pickle
sys.path.insert(0, sys.argv[1])
import numpy as np
from microclimate.live import fetch, config as C, forcing
DAYS = int(sys.argv[2])
HURL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
_n = dt.datetime.now(dt.timezone.utc)
ED = (_n - dt.timedelta(days=1)).strftime("%Y-%m-%d"); SD = (_n - dt.timedelta(days=DAYS)).strftime("%Y-%m-%d")
lat, lon = fetch.lattice_points()
def rr_hist():
    for model in C.RRFS_MODELS:
        p = dict(latitude=",".join(f"{v:.4f}" for v in lat), longitude=",".join(f"{v:.4f}" for v in lon), models=model, hourly=",".join(C.SURFACE_VARS),
                 start_date=SD, end_date=ED, wind_speed_unit="ms", timezone="UTC")
        js = fetch.get(HURL, p, "rrfs").json()
        if isinstance(js, dict): js = [js]
        times, V = fetch._hourly_arrays(js, C.SURFACE_VARS)
        pp = dict(latitude=C.PROFILE_POINT[0], longitude=C.PROFILE_POINT[1], models=model, hourly=",".join(C.PROFILE_VARS), start_date=SD, end_date=ED, wind_speed_unit="ms", timezone="UTC")
        jp = fetch.get(HURL, pp, "rrfs").json()
        tp, ap = fetch._hourly_arrays([jp], C.PROFILE_VARS)
        ok = np.nonzero(np.isfinite(V["temperature_2m"]).all(axis=0))[0]
        print(model, len(times), times[0], times[-1], "prof finite", {k: int(np.isfinite(v).sum()) for k, v in list(ap.items())[:2]})
        return dict(model=model, times=times, lat=lat, lon=lon, elev=np.array([j.get("elevation", 34.0) for j in js], float), vars=V, prof={k: v[0] for k, v in ap.items()}, prof_elev=jp.get("elevation"), last_valid=times[ok[-1]])
rr = rr_hist()
mt = fetch.metar(hours=24 * DAYS)
print("metar rows", {k: len(v) for k, v in mt.items()}, min(r["t"] for r in mt["KSFO"]))
now = dt.datetime.now(dt.timezone.utc)
snds = []
import requests
base0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
for back in range(0, 2 * DAYS + 2):
    v = base0 - dt.timedelta(hours=12 * back)
    try:
        s = fetch.sounding(v + dt.timedelta(hours=1))
        if s["valid"] == v: snds.append(s)
    except Exception as e: pass
print("soundings", len(snds))
pickle.dump((rr, mt, snds), open(sys.argv[3], "wb"))
