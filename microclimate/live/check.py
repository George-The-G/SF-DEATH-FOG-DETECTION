"""Model check (plan section 7): `python -m microclimate.live check [--since 30d] [--out DIR]`

Compares the RRFS-driven fog signal that feeds the live model (the forcing before any observation nudging) with what was observed:

  * fog at KHAF / KSFO / KOAK: model flag = RRFS low cloud >= 60 % and T - Td <= 2 C at the site; observed flag = visibility <= 3 mi
    or a BKN/OVC/VV ceiling <= 1000 ft (hourly, from the IEM ASOS archive). Hit rate (POD), false-alarm ratio, base rate, onset and
    clear timing error (nearest model transition within +-6 h).
  * wind at the CO-OPS Fort Point station: bias, ratio and direction bias of RRFS 10 m wind against 6-minute observations.

RRFS history comes from the Open-Meteo historical-forecast archive (same model id as the live run). The report also scores each replay
night (18:00 PT + 24 h). It goes to reports/live_check/<date>.json.
"""
import datetime as dt
import json
from pathlib import Path

import numpy as np
import requests

from . import config as C
from .fetch import utcnow

IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
HIST = "https://historical-forecast-api.open-meteo.com/v1/forecast"
SITES = {"KHAF": "HAF", "KSFO": "SFO", "KOAK": "OAK"}
LOW_CLOUD_PCT = 60.0
SPREAD_C = 2.0
VIS_MI, CEIL_FT = 3.0, 1000.0
MATCH_H = 6


def _get(url, params):
    for k in range(3):
        try:
            r = requests.get(url, params=params, headers=C.UA, timeout=C.TIMEOUT_S * 2)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if k == 2:
                raise


def hours_axis(t0, t1):
    n = int((t1 - t0).total_seconds() // 3600) + 1
    return [t0 + dt.timedelta(hours=i) for i in range(n)]


def obs_fog(icao, t0, t1, hours):
    """Hourly observed fog flag (1 / 0, nan if no report that hour) from ASOS."""
    e = t1 + dt.timedelta(days=1)
    p = dict(station=SITES[icao], data=["vsby", "skyc1", "skyc2", "skyc3", "skyl1", "skyl2", "skyl3"], tz="Etc/UTC", format="onlycomma", latlon="no", elev="no",
             missing="M", trace="T", direct="no", report_type=[3, 4], year1=t0.year, month1=t0.month, day1=t0.day, year2=e.year, month2=e.month, day2=e.day)
    txt = _get(IEM, p).text
    rows = [ln.split(",") for ln in txt.strip().splitlines() if ln and not ln.startswith("#")]
    head, rows = rows[0], rows[1:]
    ix = {k: i for i, k in enumerate(head)}
    flag = np.full(len(hours), np.nan)
    h0 = hours[0]
    for r in rows:
        try:
            t = dt.datetime.strptime(r[ix["valid"]], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        i = int(round((t - h0).total_seconds() / 3600.0))
        if not 0 <= i < len(hours):
            continue
        vis = float(r[ix["vsby"]]) if r[ix["vsby"]] != "M" else np.nan
        ceil = np.nan
        for k in "123":
            c, l = r[ix["skyc" + k]], r[ix["skyl" + k]]
            if c in ("BKN", "OVC", "VV") and l != "M":
                ceil = min(ceil, float(l)) if np.isfinite(ceil) else float(l)
        f = float((np.isfinite(vis) and vis <= VIS_MI) or (np.isfinite(ceil) and ceil <= CEIL_FT))
        if np.isnan(flag[i]) or f > flag[i]:          # any report in the hour with fog counts
            flag[i] = f
    return flag


def model_fog(lat, lon, t0, t1, hours):
    p = dict(latitude=lat, longitude=lon, models=C.RRFS_MODELS[0], hourly="cloud_cover_low,temperature_2m,dew_point_2m,wind_speed_10m,wind_direction_10m",
             start_date=t0.strftime("%Y-%m-%d"), end_date=t1.strftime("%Y-%m-%d"), wind_speed_unit="ms", timezone="UTC")
    js = _get(HIST, p).json()["hourly"]
    t = [dt.datetime.fromisoformat(x).replace(tzinfo=dt.timezone.utc) for x in js["time"]]
    f = lambda k: np.array([np.nan if v is None else v for v in js[k]], float)  # noqa: E731
    cl, T, Td, ws, wd = f("cloud_cover_low"), f("temperature_2m"), f("dew_point_2m"), f("wind_speed_10m"), f("wind_direction_10m")
    pos = {x: i for i, x in enumerate(t)}
    idx = np.array([pos.get(h, -1) for h in hours])
    take = lambda a: np.where(idx >= 0, a[np.clip(idx, 0, None)], np.nan)  # noqa: E731
    cl, T, Td, ws, wd = take(cl), take(T), take(Td), take(ws), take(wd)
    flag = np.where(np.isfinite(cl) & np.isfinite(T) & np.isfinite(Td), ((cl >= LOW_CLOUD_PCT) & (T - Td <= SPREAD_C)).astype(float), np.nan)
    return flag, ws, wd


def transitions(flag):
    f = np.nan_to_num(flag, nan=0.0)
    d = np.diff(f)
    return list(np.nonzero(d > 0)[0] + 1), list(np.nonzero(d < 0)[0] + 1)


def timing(obs_ix, mod_ix):
    errs = []
    for i in obs_ix:
        if mod_ix:
            j = min(mod_ix, key=lambda k: abs(k - i))
            if abs(j - i) <= MATCH_H:
                errs.append(int(j - i))
    return dict(n_obs=len(obs_ix), n_matched=len(errs), median_err_h=(float(np.median(errs)) if errs else None),
                mean_abs_err_h=(round(float(np.mean(np.abs(errs))), 2) if errs else None))


def score(obs, mod):
    ok = np.isfinite(obs) & np.isfinite(mod)
    o, m = obs[ok] > 0.5, mod[ok] > 0.5
    hit, miss, fa, cn = int((o & m).sum()), int((o & ~m).sum()), int((~o & m).sum()), int((~o & ~m).sum())
    on_o, cl_o = transitions(np.where(ok, obs, 0.0))
    on_m, cl_m = transitions(np.where(ok, mod, 0.0))
    return dict(hours=int(ok.sum()), base_rate=round(float(o.mean()), 3) if o.size else None, hits=hit, misses=miss, false_alarms=fa, correct_neg=cn,
                hit_rate=round(hit / (hit + miss), 3) if hit + miss else None, false_alarm_ratio=round(fa / (hit + fa), 3) if hit + fa else None,
                onset=timing(on_o, on_m), clear=timing(cl_o, cl_m))


def coops_wind(t0, t1, hours, ws_m, wd_m):
    ts, ss, ds = [], [], []
    t = t0
    while t < t1:
        e = min(t + dt.timedelta(days=28), t1)
        try:
            r = _get(C.COOPS_URL, dict(station=C.COOPS_STATION, product="wind", begin_date=t.strftime("%Y%m%d %H:%M"), end_date=e.strftime("%Y%m%d %H:%M"),
                                       time_zone="gmt", units="metric", format="json", application="sf-death-fog")).json().get("data") or []
        except requests.RequestException:
            r = []
        for x in r:
            try:
                ts.append(dt.datetime.strptime(x["t"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc))
                ss.append(float(x["s"]))
                ds.append(float(x["d"]))
            except (ValueError, KeyError):
                pass
        t = e
    if not ts:
        return dict(n=0)
    h0 = hours[0]
    ix = np.array([int(round((x - h0).total_seconds() / 3600.0)) for x in ts])
    ss, ds = np.array(ss), np.array(ds)
    n = len(hours)
    S, D = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(n):
        m = ix == i
        if m.any():
            S[i] = ss[m].mean()
            u, v = (-ss[m] * np.sin(np.radians(ds[m]))).mean(), (-ss[m] * np.cos(np.radians(ds[m]))).mean()
            D[i] = np.degrees(np.arctan2(-u, -v)) % 360.0
    ok = np.isfinite(S) & np.isfinite(ws_m)
    if not ok.any():
        return dict(n=0)
    dd = (wd_m - D + 180.0) % 360.0 - 180.0
    okd = ok & np.isfinite(dd)
    return dict(n=int(ok.sum()), obs_mean_ms=round(float(S[ok].mean()), 2), model_mean_ms=round(float(ws_m[ok].mean()), 2), bias_ms=round(float((ws_m[ok] - S[ok]).mean()), 2),
                ratio_obs_over_model=round(float(S[ok].mean() / max(ws_m[ok].mean(), 0.1)), 2), rmse_ms=round(float(np.sqrt(((ws_m[ok] - S[ok]) ** 2).mean())), 2),
                dir_bias_deg=(round(float(dd[okd].mean()), 1) if okd.any() else None))


def replay_nights():
    root = Path(__file__).resolve().parents[2]
    js = json.loads((root / "site" / "v3" / "replay" / "index.json").read_text(encoding="utf-8"))
    return sorted(n["id"] for n in js["nights"])


def evaluate(t0, t1, label):
    hours = hours_axis(t0, t1)
    rep = dict(label=label, start=t0.isoformat(), end=t1.isoformat(), hours=len(hours), fog={}, notes=[])
    for icao, ll in C.METAR_LL.items():
        try:
            of = obs_fog(icao, t0, t1, hours)
            mf, _, _ = model_fog(ll[0], ll[1], t0, t1, hours)
            rep["fog"][icao] = score(of, mf)
        except Exception as e:  # noqa: BLE001
            rep["notes"].append(f"{icao}: {e!r}"[:200])
    try:
        _, ws, wd = model_fog(C.COOPS_LL[0], C.COOPS_LL[1], t0, t1, hours)
        rep["wind_coops_fort_point"] = coops_wind(t0, t1, hours, ws, wd)
    except Exception as e:  # noqa: BLE001
        rep["notes"].append(f"coops: {e!r}"[:200])
    return rep


def run(argv):
    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default
    root = Path(__file__).resolve().parents[2]
    out = Path(opt("--out", root / "reports" / "live_check"))
    out.mkdir(parents=True, exist_ok=True)
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    days = int(str(opt("--since", "30d")).rstrip("d"))
    report = dict(generated_iso=now.isoformat(), model_version=C.MODEL_VERSION, rrfs_model=C.RRFS_MODELS[0],
                  definition=dict(model_fog=f"RRFS low cloud >= {LOW_CLOUD_PCT:.0f} % and T-Td <= {SPREAD_C} C", obs_fog=f"vis <= {VIS_MI} mi or BKN/OVC/VV <= {CEIL_FT:.0f} ft",
                                  note="RRFS before observation nudging: the skill the nudging has to correct; the street index itself has no station truth"))
    report["recent"] = evaluate(now - dt.timedelta(days=days), now - dt.timedelta(hours=24), f"last {days} d")
    per = {}
    for nid in replay_nights():
        d = dt.date.fromisoformat(nid)
        t0 = dt.datetime(d.year, d.month, d.day, 18, tzinfo=dt.timezone.utc) + dt.timedelta(hours=8)    # about 18:00 PT
        per[nid] = evaluate(t0, t0 + dt.timedelta(hours=24), nid)
    report["replay_nights"] = per
    p = out / f"{now:%Y-%m-%d}.json"
    p.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("wrote", p)
    for k, v in report["recent"]["fog"].items():
        print(k, {a: v[a] for a in ("hours", "base_rate", "hit_rate", "false_alarm_ratio")}, "onset", v["onset"]["median_err_h"], "clear", v["clear"]["median_err_h"])
    print("wind", report["recent"].get("wind_coops_fort_point"), "notes", report["recent"]["notes"])
    if "--no-stations" not in argv:       # guide 03 step 6: leave-one-station-out table (raw model, today's citywide correction, anchor fit); never fatal
        try:
            from . import check_stations
            check_stations.run(now, days, out)
        except Exception as e:  # noqa: BLE001
            print("station leave-one-out check failed:", repr(e)[:300])
    return 0
