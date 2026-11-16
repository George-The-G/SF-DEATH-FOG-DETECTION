#!/usr/bin/env python3
"""Input prep for guide 13 (sky layers): saves one dated folder of the inputs the layer table will need, so the later agent has several nights to test on.
usage: python tools/collect_sky_inputs.py [out_root]     (default ~/deathfog-data/sky_inputs/<yyyymmdd>); run it once a day.
Saved: metar_raw.json (aviationweather, 72 h, ALL cloud layers: cover and base, not only the lowest ceiling), profile_hist.json (Open-Meteo historical-forecast, RRFS, 7 days up to yesterday, the PROFILE_POINT:
temperature, relative humidity and geopotential height at 1000 to 300 hPa, a wider ladder than the live fetch which stops at 850 hPa), goes5_store/ (the published 5-minute GOES store of the beta channel: grid.json and
the hour files listed in index.json), sounding_oak.txt (the newest OAK sounding text). Each part is best effort; a failure is written to manifest.json."""
import datetime as dt
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PAGES_BETA = "https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/v3/live_beta/"
LEVELS = (1000, 975, 950, 925, 900, 850, 800, 700, 600, 500, 400, 300)
POINT = (37.76, -122.45)
now = dt.datetime.now(dt.timezone.utc)
root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "deathfog-data" / "sky_inputs" / now.strftime("%Y%m%d")
root.mkdir(parents=True, exist_ok=True)
man = dict(collected_utc=now.isoformat(), parts={})


def get(url, params=None, timeout=60):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "sf-death-fog-sky-inputs"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def part(name, fn):
    try:
        man["parts"][name] = fn()
    except Exception as e:  # noqa: BLE001
        man["parts"][name] = f"FAILED: {type(e).__name__}: {str(e)[:150]}"


def metar():
    d = get("https://aviationweather.gov/api/data/metar", dict(ids="KSFO,KOAK,KHAF,KSQL,KHWD", format="json", hours=72))
    (root / "metar_raw.json").write_bytes(d)
    return f"{len(json.loads(d))} reports"


def profile():
    sd, ed = (now - dt.timedelta(days=7)).strftime("%Y-%m-%d"), (now - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    hv = ",".join([f"{v}_{lv}hPa" for v in ("temperature", "relative_humidity", "geopotential_height") for lv in LEVELS])
    d = get("https://historical-forecast-api.open-meteo.com/v1/forecast", dict(latitude=POINT[0], longitude=POINT[1], models="ncep_rrfs_conus", hourly=hv, start_date=sd, end_date=ed, timezone="UTC"))
    (root / "profile_hist.json").write_bytes(d)
    return f"{sd} to {ed}, {len(LEVELS)} levels"


def goes5():
    out = root / "goes5_store"
    out.mkdir(exist_ok=True)
    idx = json.loads(get(PAGES_BETA + "index.json?x=1"))
    files = (idx.get("goes5") or {}).get("files") or []
    n = 0
    for f in files:
        if ".." in f or f.startswith("/") or not (f.endswith(".npz") or f.endswith(".json")):
            continue
        (out / f).parent.mkdir(parents=True, exist_ok=True)
        (out / f).write_bytes(get(PAGES_BETA + f + "?x=1", timeout=120))
        n += 1
    return f"{n} store files"


def sounding():
    t = now.replace(minute=0, second=0, microsecond=0)
    t = t.replace(hour=12 if t.hour >= 12 else 0)
    for back in range(0, 4):
        v = t - dt.timedelta(hours=12 * back)
        try:
            d = get(f"https://www.spc.noaa.gov/exper/soundings/{v:%y%m%d%H}_OBS/OAK.txt")
            (root / "sounding_oak.txt").write_bytes(d)
            return f"valid {v:%Y-%m-%d %H}Z"
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("no sounding in the last 36 h")


for name, fn in (("metar", metar), ("profile", profile), ("goes5", goes5), ("sounding", sounding)):
    part(name, fn)
(root / "manifest.json").write_text(json.dumps(man, indent=1))
print(json.dumps(man, indent=1))
print("saved in", root)
