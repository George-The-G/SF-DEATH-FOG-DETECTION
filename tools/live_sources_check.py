#!/usr/bin/env python3
"""Step 1.1 of PLAN_LIVE_DATA.md: fetch ~24 h from every live source for San Francisco, print latency (newest timestamp vs now),
the fields present, and save samples under bench/live_samples/ (gitignored). Not shipped.

    python tools/live_sources_check.py [--out bench/live_samples]
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

import requests

UA = {"User-Agent": "sf-death-fog (github.com/George-The-G/SF-DEATH-FOG-DETECTION)"}
NOW = dt.datetime.now(dt.timezone.utc)
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Path("bench/live_samples")
OUT.mkdir(parents=True, exist_ok=True)
results = {}


def lag(ts):
    return None if ts is None else round((NOW - ts).total_seconds() / 60.0, 1)


def get(url, **kw):
    return requests.get(url, headers=UA, timeout=60, **kw)


def report(name, **kw):
    results[name] = kw
    print(f"[{name}] " + "  ".join(f"{k}={v}" for k, v in kw.items()), flush=True)


def rrfs():
    V = ("temperature_2m,dew_point_2m,wind_speed_10m,wind_direction_10m,cloud_cover_low,precipitation,shortwave_radiation,"
         "visibility,temperature_925hPa,relative_humidity_925hPa,geopotential_height_925hPa")
    for model in ("ncep_rrfs_conus", "ncep_rrfs_north_america"):
        try:
            r = get("https://api.open-meteo.com/v1/forecast", params=dict(
                latitude="37.70,37.76,37.80,37.83", longitude="-122.52,-122.45,-122.40,-122.36", models=model, hourly=V, past_days=1,
                forecast_days=4, wind_speed_unit="ms", timezone="UTC"))
            j = r.json()
            (OUT / f"rrfs_{model}.json").write_text(json.dumps(j))
            h = j[0]["hourly"]
            t = [dt.datetime.fromisoformat(x).replace(tzinfo=dt.timezone.utc) for x in h["time"]]
            ok = [i for i, v in enumerate(h["temperature_2m"]) if v is not None]
            report("rrfs:" + model, status=r.status_code, points=len(j), hours=len(t), first=t[ok[0]].isoformat(), last_valid=t[ok[-1]].isoformat(),
                   horizon_h=round((t[ok[-1]] - NOW).total_seconds() / 3600, 1), fields=[k for k in h if k != "time"])
        except Exception as e:
            report("rrfs:" + model, error=str(e))


def coops():
    end, beg = NOW, NOW - dt.timedelta(hours=24)
    for prod in ("wind", "air_temperature", "air_pressure", "visibility"):
        try:
            r = get("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter", params=dict(
                station="9414290", product=prod, begin_date=beg.strftime("%Y%m%d %H:%M"), end_date=end.strftime("%Y%m%d %H:%M"),
                time_zone="gmt", units="metric", format="json", application="sf-death-fog"))
            j = r.json()
            (OUT / f"coops_{prod}.json").write_text(json.dumps(j))
            d = j.get("data")
            if not d:
                report("coops:" + prod, status=r.status_code, note=str(j.get("error"))[:80])
                continue
            ts = dt.datetime.strptime(d[-1]["t"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
            report("coops:" + prod, n=len(d), fields=list(d[0].keys()), newest=ts.isoformat(), latency_min=lag(ts), station=j["metadata"]["name"])
        except Exception as e:
            report("coops:" + prod, error=str(e))


def metar():
    try:
        r = get("https://aviationweather.gov/api/data/metar", params=dict(ids="KHAF,KSFO,KOAK", format="json", hours=24))
        j = r.json()
        (OUT / "metar.json").write_text(json.dumps(j))
        for st in ("KHAF", "KSFO", "KOAK"):
            rows = [x for x in j if x["icaoId"] == st]
            newest = max((dt.datetime.fromtimestamp(x["obsTime"], dt.timezone.utc) for x in rows), default=None)
            report("metar:" + st, n=len(rows), newest=None if newest is None else newest.isoformat(), latency_min=lag(newest),
                   fields=sorted(rows[0].keys()) if rows else None)
    except Exception as e:
        report("metar", error=str(e))


def nws():
    for st in ("SFOC1", "KSFO", "KHAF"):
        try:
            r = get(f"https://api.weather.gov/stations/{st}/observations", params=dict(limit=30))
            j = r.json()
            (OUT / f"nws_{st}.json").write_text(json.dumps(j))
            f = j["features"]
            ts = dt.datetime.fromisoformat(f[0]["properties"]["timestamp"])
            have = [k for k, v in f[0]["properties"].items() if isinstance(v, dict) and v.get("value") is not None]
            report("nws:" + st, n=len(f), newest=ts.isoformat(), latency_min=lag(ts), fields_with_value=have)
        except Exception as e:
            report("nws:" + st, error=str(e))


def sounding():
    try:
        best = None
        for back in range(0, 5):
            t = (NOW - dt.timedelta(hours=12 * back)).replace(minute=0, second=0, microsecond=0)
            t = t.replace(hour=12 if t.hour >= 12 else 0)
            url = f"https://www.spc.noaa.gov/exper/soundings/{t:%y%m%d%H}_OBS/OAK.txt"
            r = get(url)
            if r.status_code == 200 and "%RAW%" in r.text:
                best = (t, r.text, url)
                break
        if best is None:
            report("sounding:SPC OAK", error="no sounding in last 48 h")
            return
        t, txt, url = best
        (OUT / "sounding_OAK.txt").write_text(txt)
        n = len(re.findall(r"^\s*\d+\.\d+,", txt, re.M))
        report("sounding:SPC OAK", url=url, valid=t.isoformat(), latency_h=round((NOW - t).total_seconds() / 3600, 1), levels=n,
               columns="LEVEL(hPa) HGHT(m) TEMP(C) DWPT(C) WDIR WSPD")
    except Exception as e:
        report("sounding:SPC OAK", error=str(e))


def goes():
    b = "https://noaa-goes18.s3.amazonaws.com/"
    for prod in ("ABI-L2-ACMC", "ABI-L2-ACHAC", "ABI-L2-CMIPC", "ABI-L2-FLSF"):
        try:
            keys = []
            for back in (0, 1):
                t = NOW - dt.timedelta(hours=back)
                pre = f"{prod}/{t.year}/{t.timetuple().tm_yday:03d}/{t.hour:02d}/"
                r = get(b, params={"list-type": 2, "prefix": pre})
                keys += re.findall(r"<Key>(.*?)</Key>", r.text)
            if prod == "ABI-L2-CMIPC":
                keys = [k for k in keys if "M6C13" in k or "M6C07" in k]
            if not keys:
                report("goes18:" + prod, n=0, note="nothing in last 2 h (product not on AWS?)")
                continue
            last = sorted(keys)[-1]
            m = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})", last)
            ts = dt.datetime(int(m[1]), 1, 1, int(m[3]), int(m[4]), tzinfo=dt.timezone.utc) + dt.timedelta(days=int(m[2]) - 1)
            report("goes18:" + prod, files_last_2h=len(keys), newest=last.split("/")[-1], scan_start=ts.isoformat(), latency_min=lag(ts))
            if prod in ("ABI-L2-ACMC", "ABI-L2-ACHAC"):
                f = OUT / last.split("/")[-1]
                if not f.exists():
                    f.write_bytes(get(b + last).content)
        except Exception as e:
            report("goes18:" + prod, error=str(e))


def rwis():
    ok = []
    for d in ("d04", "d4"):
        for ext in ("json", "csv"):
            try:
                r = get(f"https://cwwp2.dot.ca.gov/data/{d}/rwis/rwisStatusD04.{ext}")
                ok.append((d, ext, r.status_code))
            except Exception as e:
                ok.append((d, ext, str(e)[:40]))
    report("rwis:caltrans D04", tried=ok, near_SF="none found (district 4 RWIS feed returns HTTP 500 / no stations)")


if __name__ == "__main__":
    for fn in (rrfs, coops, metar, nws, sounding, goes, rwis):
        fn()
    (OUT / "summary.json").write_text(json.dumps(dict(now=NOW.isoformat(), results=results), indent=1, default=str))
    print("samples in", OUT.resolve())
