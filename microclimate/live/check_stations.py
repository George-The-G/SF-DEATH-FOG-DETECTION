"""Guide 03 step 6: leave-one-station-out check of the station anchors over the last N days (`python -m microclimate.live check --since 30d`).

For every station and variable (air temperature, dew point, wind) three predictions are compared with the hourly observation:
  raw     the RRFS forecast at the station (the model before any observation: historical-forecast archive of the same model id as the live run)
  city    today's citywide correction: wind ratio / direction offset from Fort Point, temperature / dew point offset = mean of today's reporting sites (rolling median 3 h)
  anchor  the guide 03 fit WITHOUT that station: regional inflow fit of the other in-grid wind stations (station_wind.npz), offset + lapse fit of the other stations
Fort Point is the reference of today's wind correction, so its 'city' wind column is in-sample (marked). Out-of-grid stations have no simulated wind vector: no anchor column.
Hourly means; one value per station-hour; the held-out station's own observation never enters its anchor or city prediction (except Fort Point wind).
"""
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

from . import anchors, config as C, forcing as Fc, stations as Sx
from .check import HIST, IEM, _get, hours_axis

ASOS = {"KSFO": "SFO", "KOAK": "OAK", "KHAF": "HAF"}
CITY_T_SITES = ("FTPC1", "KSFO", "KOAK", "KHAF", "SFOC1")     # today's temperature sites (forcing.sites)
CITY_TD_SITES = ("KSFO", "KOAK", "KHAF", "SFOC1")
NO_HISTORY = ("D3169", "46026", "46237")                       # NWS CWOP list has no 30-day archive; buoys are not anchors


# ------------------------------------------------------------------ history
def forecast_points(points, t0, t1, hours):
    """points: {name: (lat, lon)} -> {name: dict(T, Td, spd, dir [nh], elev)} from the Open-Meteo historical-forecast archive (one request)."""
    names = list(points)
    p = dict(latitude=",".join(f"{points[k][0]:.4f}" for k in names), longitude=",".join(f"{points[k][1]:.4f}" for k in names), models=C.RRFS_MODELS[0],
             hourly="temperature_2m,dew_point_2m,wind_speed_10m,wind_direction_10m", start_date=t0.strftime("%Y-%m-%d"), end_date=t1.strftime("%Y-%m-%d"),
             wind_speed_unit="ms", timezone="UTC")
    js = _get(HIST, p).json()
    js = [js] if isinstance(js, dict) else js
    out = {}
    for k, j in zip(names, js):
        h = j["hourly"]
        t = [dt.datetime.fromisoformat(x).replace(tzinfo=dt.timezone.utc) for x in h["time"]]
        pos = {x: i for i, x in enumerate(t)}
        idx = np.array([pos.get(x, -1) for x in hours])
        f = lambda key: np.where(idx >= 0, np.array([np.nan if v is None else v for v in h[key]], float)[np.clip(idx, 0, None)], np.nan)  # noqa: E731
        out[k] = dict(T=f("temperature_2m"), Td=f("dew_point_2m"), spd=f("wind_speed_10m"), dir=f("wind_direction_10m"), elev=float(j.get("elevation", 0.0)))
    return out


def asos(icao, t0, t1):
    e = t1 + dt.timedelta(days=1)
    p = dict(station=ASOS[icao], data=["tmpf", "dwpf", "sknt", "drct"], tz="Etc/UTC", format="onlycomma", latlon="no", elev="no", missing="M", trace="T", direct="no",
             report_type=[3, 4], year1=t0.year, month1=t0.month, day1=t0.day, year2=e.year, month2=e.month, day2=e.day)
    import time
    for k in range(5):                    # asos.py answers 429 when asked too fast: back off
        try:
            txt = _get(IEM, p).text
            break
        except requests.RequestException:
            if k == 4:
                raise
            time.sleep(4.0 * (k + 1))
    rows = [ln.split(",") for ln in txt.strip().splitlines() if ln and not ln.startswith("#")]
    head, rows = rows[0], rows[1:]
    ix = {k: i for i, k in enumerate(head)}
    g = lambda r, k: np.nan if r[ix[k]] == "M" else float(r[ix[k]])  # noqa: E731
    ts, T, Td, sp, di = [], [], [], [], []
    for r in rows:
        try:
            ts.append(dt.datetime.strptime(r[ix["valid"]], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc))
        except ValueError:
            continue
        T.append((g(r, "tmpf") - 32) / 1.8)
        Td.append((g(r, "dwpf") - 32) / 1.8)
        sp.append(g(r, "sknt") * 0.514444)
        di.append(g(r, "drct"))
    return {"T": (ts, np.array(T)), "Td": (ts, np.array(Td)), "wind": (ts, np.array(sp), np.array(di))}


def observations(t0, t1, log=print):
    """-> {id: raw dict (stations.py layout)} for every station with a history source; failures are logged and the station is skipped."""
    pt = __import__("zoneinfo").ZoneInfo("America/Los_Angeles")
    d0, d1 = t0.astimezone(pt).date(), t1.astimezone(pt).date() + dt.timedelta(days=1)
    days = [d0 + dt.timedelta(days=k) for k in range((d1 - d0).days + 1)]
    jobs, slow = [], []
    for sid, m in C.STATIONS.items():
        if sid in NO_HISTORY:
            continue
        if m["kind"] == "coops":
            prods = [p for p, need in (("wind", "wind"), ("air_temperature", "T"), ("humidity", "Td")) if need in m["vars"] or (p == "air_temperature" and "Td" in m["vars"])]
            t = t0
            while t < t1:
                e = min(t + dt.timedelta(days=28), t1)
                for p in prods:
                    jobs.append((sid, lambda s=sid, p=p, a=t, b=e: Sx.job_coops(s, p, a, b)))
                t = e
        elif m["kind"] in ("iem", "nws") and sid != "D3169":
            for d in days:
                jobs.append((sid, lambda s=sid, d=d: Sx.job_iem(s, d)))
        elif m["kind"] == "metar":
            slow.append((sid, lambda s=sid: asos(s, t0, t1)))
    raw = {sid: {} for sid in C.STATIONS}

    def run(j):
        try:
            return j[0], j[1]()
        except Exception as e:  # noqa: BLE001
            return j[0], repr(e)[:160]
    bad = {}
    with ThreadPoolExecutor(8) as ex:
        for sid, part in ex.map(run, jobs):
            if isinstance(part, str):
                bad.setdefault(sid, []).append(part)
            else:
                Sx._merge(raw[sid], part)
    for j in slow:                          # the ASOS archive is rate limited: one at a time
        sid, part = run(j)
        if isinstance(part, str):
            bad.setdefault(sid, []).append(part)
        else:
            Sx._merge(raw[sid], part)
    if bad:
        log(f"history requests that failed: {bad}")
    for sid, d in raw.items():
        if "Td" not in d and "RH" in d and "T" in d:
            n = {t: v for t, v in zip(*d["T"])}
            d["Td"] = (list(d["RH"][0]), np.array([Sx._td_from_rh(n[t], v) if t in n and np.isfinite(v) and np.isfinite(n[t]) else np.nan for t, v in zip(*d["RH"])]))
    return raw


# ------------------------------------------------------------------ predictions
def _city_wind(obs_f, fc_ref, fc_t, t0, nh):
    """Today's citywide wind correction (forcing.build): ratio and direction offset from Fort Point, rolling median, applied to the forecast at another station."""
    so, do = obs_f["spd"], obs_f["dir"]
    rat = so / np.maximum(fc_ref["spd"], 0.5)
    dl = Fc.ang_diff(do, fc_ref["dir"])
    rat_r = np.clip(Fc.rolling_median(rat, C.ROLL_H), *C.WIND_RATIO_CLIP)
    dl_r = np.full(nh, np.nan)
    for k in range(nh):
        seg = dl[max(0, k - C.ROLL_H + 1):k + 1]
        seg = seg[np.isfinite(seg)]
        if len(seg):
            dl_r[k] = np.clip(Fc.circ_mean_deg(seg), -C.WIND_DIR_CLIP_DEG, C.WIND_DIR_CLIP_DEG)
    r = np.where(np.isfinite(rat_r), rat_r, 1.0)
    d = np.where(np.isfinite(dl_r), dl_r, 0.0)
    return fc_t["spd"] * r, (fc_t["dir"] + d) % 360.0


def _city_temp(H, fc, kind, sites, skip, nh):
    offs = [H[s][kind] - fc[s][kind] for s in sites if s != skip and np.isfinite(H[s][kind]).any() and s in fc]
    if not offs:
        return np.zeros(nh)
    with np.errstate(all="ignore"), __import__("warnings").catch_warnings():
        __import__("warnings").simplefilter("ignore", RuntimeWarning)
        a = np.nanmean(np.array(offs), axis=0)
    a = np.clip(Fc.rolling_median(a, C.ROLL_H), -C.TEMP_CLIP_C, C.TEMP_CLIP_C)
    return np.where(np.isfinite(a), a, 0.0)


def _anchor_temp(H, fc, S, kind, skip, nh):
    """Offset / lapse fit without station `skip`, rolling median, predicted at `skip` (NaN where nothing can be said)."""
    G0 = C.LAPSE_T_K_PER_KM if kind == "T" else C.LAPSE_TD_K_PER_KM
    ids = [s for s in S["ids"] if s != skip and s in fc and np.isfinite(H[s][kind]).any()]
    dz = np.array([S["dz"][s] for s in ids])
    base = np.array([fc[s][kind] + G0 * S["dz"][s] for s in ids])
    r = np.array([H[s][kind] for s in ids]) - base
    a, b = np.full(nh, np.nan), np.full(nh, np.nan)
    for h in range(nh):
        o = anchors.fit_T_hour(r[:, h], dz)
        if o is not None:
            a[h], b[h] = o
    fit = np.isfinite(a)
    a_rm, b_rm = Fc.rolling_median(a, C.ROLL_H), Fc.rolling_median(b, C.ROLL_H)
    r_rm = np.array([Fc.rolling_median(ri, C.ROLL_H) for ri in r])
    xs = np.array([S["x"][s] for s in ids])
    ys = np.array([S["y"][s] for s in ids])
    t_base = fc[skip][kind] + G0 * S["dz"][skip]
    out = np.full(nh, np.nan)
    for h in range(nh):
        if fit[h]:
            out[h] = t_base[h] + np.clip(a_rm[h], -C.TEMP_CLIP_C, C.TEMP_CLIP_C) + np.clip(b_rm[h], *C.T_LAPSE_ADJ_CLIP) * S["dz"][skip]
        elif np.isfinite(r[:, h]).any():
            out[h] = t_base[h] + anchors.horizontal(np.clip(r_rm[:, h], -C.TEMP_CLIP_C, C.TEMP_CLIP_C), xs, ys, [S["x"][skip]], [S["y"][skip]])[0]
    return out


def _anchor_wind(H, fcr, sw, S, skip, nh, names, cx, cy, nb):
    """Regional inflow fit of the in-grid anchors other than `skip` (None = all, in-sample), rolling median; returns the predicted (u, v) [nh, 2] at every in-grid anchor."""
    ids = [i for i in sw["ids"] if i != skip and np.isfinite(H[i]["u"]).any()]
    if not ids:
        return None
    col = [sw["ids"].index(i) for i in ids]
    uvlib = sw["uv"][:, col, :]
    W = anchors.region_weights([S["x"][i] for i in ids], [S["y"][i] for i in ids], cx, cy, C.WIND_REGION_SIGMA_M)
    obs = np.stack([np.stack([H[i]["u"], H[i]["v"]], axis=1) for i in ids], axis=1)
    fit = anchors.fit_series(obs, W, uvlib, sw["dirs"], fcr["U"], fcr["th"], nb)
    R = len(names)
    U_rm = np.array([Fc.rolling_median(fit["U"][:, r], C.ROLL_H) for r in range(R)])
    th_rm = np.array([anchors.roll_med_dir(fit["th"][:, r], C.ROLL_H) for r in range(R)])
    allcol = list(range(len(sw["ids"])))
    Wall = anchors.region_weights([S["x"][i] for i in sw["ids"]], [S["y"][i] for i in sw["ids"]], cx, cy, C.WIND_REGION_SIGMA_M)
    out = np.full((nh, len(sw["ids"]), 2), np.nan)
    for h in range(nh):
        if np.isfinite(U_rm[:, h]).all() and np.isfinite(th_rm[:, h]).all():
            out[h] = anchors.predict(U_rm[:, h], th_rm[:, h], Wall, sw["uv"], sw["dirs"])
    return out, fit


# ------------------------------------------------------------------ the check
def stat(o, m):
    ok = np.isfinite(o) & np.isfinite(m)
    if not ok.any():
        return None
    d = m[ok] - o[ok]
    return dict(n=int(ok.sum()), mae=round(float(np.abs(d).mean()), 2), bias=round(float(d.mean()), 2))


def vstat(ou, ov, mu, mv):
    ok = np.isfinite(ou) & np.isfinite(ov) & np.isfinite(mu) & np.isfinite(mv)
    if not ok.any():
        return None
    e = np.hypot(mu[ok] - ou[ok], mv[ok] - ov[ok])
    return dict(n=int(ok.sum()), mae=round(float(e.mean()), 2), rmse=round(float(np.sqrt((e ** 2).mean())), 2))


def evaluate(t0, t1, sw_path=None, log=print):
    hours = hours_axis(t0, t1)
    nh = len(hours)
    sw = Sx.load_station_wind(sw_path or (Path(C.STATIC_DEFAULT).parent / C.STATION_WIND_FILE), None)
    log(f"hours {nh}; station_wind {'loaded' if sw else 'MISSING (wind anchors skipped)'}")
    raw = observations(t0, t1, log)
    H = Sx.hourly(raw, t0, nh)
    live = [s for s in C.STATIONS if s not in NO_HISTORY and any(np.isfinite(H[s][k]).any() for k in ("T", "Td", "spd"))]
    names = list(C.WIND_REGIONS)
    pts = {s: (C.STATIONS[s]["lat"], C.STATIONS[s]["lon"]) for s in live}
    pts.update({f"region:{n}": C.WIND_REGIONS[n] for n in names})
    fc = forecast_points(pts, t0, t1, hours)
    from pyproj import Transformer
    tr = Transformer.from_crs(4326, 32610, always_xy=True)
    S = dict(ids=live, x={}, y={}, dz={})
    for s in C.STATIONS:
        S["x"][s], S["y"][s] = tr.transform(C.STATIONS[s]["lon"], C.STATIONS[s]["lat"])
    for s in live:
        S["dz"][s] = (C.STATIONS[s]["elev_m"] - fc[s]["elev"]) / 1000.0
    cx, cy = tr.transform([C.WIND_REGIONS[n][1] for n in names], [C.WIND_REGIONS[n][0] for n in names])
    nb = [(names.index(a), names.index(b)) for a, b in C.WIND_REGION_NEIGHBOURS]
    fcr = dict(U=np.array([fc[f"region:{n}"]["spd"] for n in names]).T, th=np.array([anchors.wrap_dir(fc[f"region:{n}"]["dir"]) for n in names]).T)
    ok_reg = np.isfinite(fcr["U"]).all(axis=1) & np.isfinite(fcr["th"]).all(axis=1)
    fcr["U"], fcr["th"] = np.where(ok_reg[:, None], fcr["U"], 5.0), np.where(ok_reg[:, None], fcr["th"], 270.0)
    res = {}
    # ---- temperature and dew point
    for kind, sites in (("T", CITY_T_SITES), ("Td", CITY_TD_SITES)):
        for s in live:
            if not np.isfinite(H[s][kind]).any():
                continue
            raw_m = fc[s][kind]
            city = raw_m + _city_temp(H, fc, kind, sites, s, nh)
            anc = _anchor_temp(H, fc, S, kind, s, nh)
            res.setdefault(s, {})[kind] = dict(raw=stat(H[s][kind], raw_m), city=stat(H[s][kind], city), anchor=stat(H[s][kind], anc), hill=bool(C.STATIONS[s].get("hill")))
    # ---- wind
    ref = H["FTPC1"]
    for s in live:
        if not np.isfinite(H[s]["spd"]).any():
            continue
        o = H[s]
        spd_c, dir_c = _city_wind(ref, fc["FTPC1"], fc[s], t0, nh) if "FTPC1" in fc and np.isfinite(ref["spd"]).any() else (fc[s]["spd"] * np.nan,) * 2
        u_r, v_r = anchors.to_uv(fc[s]["spd"], fc[s]["dir"])
        u_c, v_c = anchors.to_uv(spd_c, dir_c)
        row = dict(raw=vstat(o["u"], o["v"], u_r, v_r), city=vstat(o["u"], o["v"], u_c, v_c), speed_raw=stat(o["spd"], fc[s]["spd"]), speed_city=stat(o["spd"], spd_c),
                   anchor=None, speed_anchor=None, city_in_sample=(s == "FTPC1"), hill=bool(C.STATIONS[s].get("hill")))
        if sw is not None and s in sw["ids"]:
            out = _anchor_wind(H, fcr, sw, S, s, nh, names, cx, cy, nb)
            if out is not None:
                pred, fit = out
                k = sw["ids"].index(s)
                row["anchor"] = vstat(o["u"], o["v"], pred[:, k, 0], pred[:, k, 1])
                row["speed_anchor"] = stat(o["spd"], np.hypot(pred[:, k, 0], pred[:, k, 1]))
        res.setdefault(s, {})["wind"] = row
    flags = {}
    if sw is not None:
        full = _anchor_wind(H, fcr, sw, S, None, nh, names, cx, cy, nb)
        if full is not None:
            f = full[1]
            flags = dict(hours_fitted=int((f["mode"] != "forecast").sum()), regional=int((f["mode"] == "regional").sum()), citywide=int((f["mode"] == "citywide").sum()),
                         offshore_flagged=int(f["offshore"].sum()), qc_dropped=int(f["dropped"].sum()), hours=nh)
    return dict(start=t0.isoformat(), end=t1.isoformat(), hours=nh, stations=res, wind_fit=flags,
                note="raw = RRFS forecast at the station; city = today's citywide correction; anchor = guide 03 fit without that station. Fort Point wind city column is in-sample.")


def to_markdown(rep):
    L = [f"# Station anchors, leave-one-station-out ({rep['start'][:10]} to {rep['end'][:10]}, {rep['hours']} h)", "", rep["note"], ""]
    if rep["wind_fit"]:
        w = rep["wind_fit"]
        L += [f"Wind fit with all in-grid anchors: {w['hours_fitted']} of {w['hours']} h fitted ({w['regional']} regional, {w['citywide']} citywide); hours flagged offshore (library cannot represent the direction): {w['offshore_flagged']}; station-hours dropped by the outlier check: {w['qc_dropped']}.", ""]
    for kind, title, unit in (("T", "Air temperature", "C"), ("Td", "Dew point", "C")):
        L += [f"## {title}: mean absolute error ({unit}), bias in brackets", "", "| station | hours | raw model | today's citywide | anchor fit |", "|---|---|---|---|---|"]
        for s, d in rep["stations"].items():
            if kind in d and d[kind]["raw"]:
                x = d[kind]
                f = lambda k: "n/a" if not x[k] else f"{x[k]['mae']:.2f} ({x[k]['bias']:+.2f})"  # noqa: E731
                L.append(f"| {s}{' (hill)' if x['hill'] else ''} | {x['raw']['n']} | {f('raw')} | {f('city')} | {f('anchor')} |")
        L.append("")
    L += ["## Wind: mean vector error (m/s), speed error in brackets", "", "| station | hours | raw model | today's citywide | anchor fit |", "|---|---|---|---|---|"]
    for s, d in rep["stations"].items():
        if "wind" in d and d["wind"]["raw"]:
            x = d["wind"]
            g = lambda k, sk: "n/a" if not x[k] else f"{x[k]['mae']:.2f} ({x[sk]['mae']:.2f})"  # noqa: E731
            L.append(f"| {s}{' (hill)' if x['hill'] else ''} | {x['raw']['n']} | {g('raw', 'speed_raw')} | {g('city', 'speed_city')}{' in-sample' if x['city_in_sample'] else ''} | {g('anchor', 'speed_anchor')} |")
    return "\n".join(L) + "\n"


def run(now, days, out, log=print):
    t0, t1 = now - dt.timedelta(days=days), now - dt.timedelta(hours=24)
    rep = evaluate(t0, t1, log=log)
    out = Path(out)
    (out / f"stations_loo_{now:%Y-%m-%d}.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    md = to_markdown(rep)
    (out / f"stations_loo_{now:%Y-%m-%d}.md").write_text(md, encoding="utf-8")
    log(md)
    return rep
