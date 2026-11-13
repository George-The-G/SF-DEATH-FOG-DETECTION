"""Extra stations (guide 03 steps 2 and 4): parallel keyless fetch with a hard deadline, hourly tables, and the per-hour anchor analysis behind stations.json.

Every request is one try with a short timeout, all in parallel threads; whatever has not answered after C.STATION_FETCH_DEADLINE_S is abandoned.
A failing station or source is listed in status.json and never fails the build. The analysis is read-only unless C.NUDGE_ANCHORS is True.
"""
import datetime as dt
import json
import threading
import time

import numpy as np
import requests

from . import anchors, config as C
from .fetch import FetchError, parse_time


def _get(url, params, name):
    if C.blocked(name):
        raise FetchError(f"{name}: blocked for testing (DEATHFOG_LIVE_BLOCK)")
    r = requests.get(url, params=params, headers=C.UA, timeout=C.STATION_TIMEOUT_S)
    if r.status_code != 200:
        raise FetchError(f"{name}: HTTP {r.status_code}")
    return r


def _f(v):
    try:
        return np.nan if v in (None, "", "MM") else float(v)
    except (TypeError, ValueError):
        return np.nan


def _td_from_rh(t, rh):
    a, b = 17.625, 243.04
    t, rh = np.asarray(t, float), np.clip(np.asarray(rh, float), 1.0, 100.0)
    g = np.log(rh / 100.0) + a * t / (b + t)
    return b * g / (a - g)


# ------------------------------------------------------------------ one request each (return partial raw dicts)
def job_coops(sid, product, begin, end):
    src = C.STATIONS[sid]["src"]
    prm = dict(station=src, begin_date=begin.strftime("%Y%m%d %H:%M"), end_date=end.strftime("%Y%m%d %H:%M"), time_zone="gmt", units="metric", format="json",
               application="sf-death-fog", product=product)
    js = _get(C.COOPS_URL, prm, "coops_extra").json()
    d = js.get("data") or []
    times = [parse_time(x["t"].replace(" ", "T")) for x in d]
    if product == "wind":
        return {"wind": (times, np.array([_f(x.get("s")) for x in d]), np.array([_f(x.get("d")) for x in d]))}
    key = {"air_temperature": "T", "humidity": "RH", "visibility": "vis", "water_temperature": "sst"}[product]
    return {key: (times, np.array([_f(x.get("v")) for x in d]))}


def job_iem(sid, date):
    src = C.STATIONS[sid]["src"]
    js = _get(C.IEM_OBHISTORY, dict(station=src, network="CA_DCP", date=date.strftime("%Y-%m-%d")), "iem").json()
    d = js.get("data") or []
    times = [dt.datetime.strptime(x["utc_valid"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc) for x in d]
    tc = lambda k: np.array([np.nan if x.get(k) is None else (float(x[k]) - 32.0) / 1.8 for x in d])  # noqa: E731
    sp = np.array([np.nan if x.get("sknt") is None else float(x["sknt"]) * 0.514444 for x in d])
    di = np.array([np.nan if x.get("drct") is None else float(x["drct"]) for x in d])
    return {"T": (times, tc("tmpf")), "Td": (times, tc("dwpf")), "wind": (times, sp, di)}


def job_nws(sid):
    js = _get(C.NWS_OBS_URL.format(id=C.STATIONS[sid]["src"]), dict(limit=60), "nws_extra").json()
    times, T, Td, sp, di, vis = [], [], [], [], [], []
    for ft in js.get("features", []):
        p = ft["properties"]
        v = lambda k: np.nan if (p.get(k) or {}).get("value") is None else float(p[k]["value"])  # noqa: E731
        times.append(dt.datetime.fromisoformat(p["timestamp"]).astimezone(dt.timezone.utc))
        T.append(v("temperature"))
        Td.append(v("dewpoint"))
        sp.append(v("windSpeed") / 3.6)
        di.append(v("windDirection"))
        vis.append(v("visibility") / 1000.0)
    return {"T": (times, np.array(T)), "Td": (times, np.array(Td)), "wind": (times, np.array(sp), np.array(di)), "vis": (times, np.array(vis))}


def job_ndbc(sid, begin):
    txt = _get(C.NDBC_URL.format(id=C.STATIONS[sid]["src"]), None, "ndbc").text.splitlines()
    head = txt[0].lstrip("#").split()
    ix = {k: i for i, k in enumerate(head)}
    times, sp, di, T, Td, sst = [], [], [], [], [], []
    for ln in txt[2:]:
        c = ln.split()
        if len(c) < len(head):
            continue
        t = dt.datetime(int(c[0]), int(c[1]), int(c[2]), int(c[3]), int(c[4]), tzinfo=dt.timezone.utc)
        if t < begin:
            break
        times.append(t)
        sp.append(_f(c[ix["WSPD"]]))
        di.append(_f(c[ix["WDIR"]]))
        T.append(_f(c[ix["ATMP"]]))
        Td.append(_f(c[ix["DEWP"]]))
        sst.append(_f(c[ix["WTMP"]]))
    return {"wind": (times, np.array(sp), np.array(di)), "T": (times, np.array(T)), "Td": (times, np.array(Td)), "sst": (times, np.array(sst))}


def _merge(raw, part):
    for k, v in part.items():
        if k in raw:
            old = raw[k]
            raw[k] = tuple((list(o) + list(n)) if isinstance(o, list) else np.concatenate([o, n]) for o, n in zip(old, v))
        else:
            raw[k] = v


def collect(obs, t0, now, status=None, deadline=None):
    """-> dict(raw {id: {T, Td, RH, wind, vis, ceil, sst: tuples of (times, values...)}}, failed {name: error}, seconds, newest {id: iso}).
    Stations already fetched by the build (FTPC1 from CO-OPS 9414290, KSFO / KOAK / KHAF from METAR, SFOC1 from NWS) are reused, the rest are requested in parallel."""
    t_start = time.time()
    deadline = C.STATION_FETCH_DEADLINE_S if deadline is None else deadline
    raw = {sid: {} for sid in C.STATIONS}
    co = obs.get("coops")
    if co is not None:
        raw["FTPC1"]["wind"] = (list(co["t"]), np.asarray(co["s"], float), np.asarray(co["d"], float))
        if len(co.get("t_temp", [])):
            raw["FTPC1"]["T"] = (list(co["t_temp"]), np.asarray(co["temp"], float))
    for sid in ("KSFO", "KOAK", "KHAF"):
        rows = (obs.get("metar") or {}).get(sid) or []
        if rows:
            ts = [r["t"] for r in rows]
            raw[sid]["T"] = (ts, np.array([r["temp"] for r in rows]))
            raw[sid]["Td"] = (ts, np.array([r["dewp"] for r in rows]))
            raw[sid]["wind"] = (ts, np.array([r["wspd_ms"] for r in rows]), np.array([r["wdir"] for r in rows]))
            raw[sid]["vis"] = (ts, np.array([r["vis_mi"] * 1.609344 for r in rows]))
            raw[sid]["ceil"] = (ts, np.array([r["ceil_ft"] for r in rows]))
    rows = (obs.get("nws") or {}).get("SFOC1") or []
    if rows:
        ts = [r["t"] for r in rows]
        raw["SFOC1"]["T"] = (ts, np.array([r["temp"] for r in rows]))
        raw["SFOC1"]["Td"] = (ts, np.array([r["dewp"] for r in rows]))
    begin = t0 - dt.timedelta(hours=1)
    jobs = []     # (sid, label, callable)
    from zoneinfo import ZoneInfo
    pt = ZoneInfo("America/Los_Angeles")
    d0, d1 = begin.astimezone(pt).date(), now.astimezone(pt).date()
    days = [d0 + dt.timedelta(days=k) for k in range((d1 - d0).days + 1)]
    for sid, m in C.STATIONS.items():
        k = m["kind"]
        if k == "coops" and sid != "FTPC1":
            prods = []
            if "wind" in m["vars"]:
                prods.append("wind")
            if "T" in m["vars"] or "Td" in m["vars"]:
                prods.append("air_temperature")
            if "Td" in m["vars"]:
                prods.append("humidity")
            if "vis" in m["vars"]:
                prods.append("visibility")
            for p in prods:
                jobs.append((sid, f"coops_{p}", lambda s=sid, p=p: job_coops(s, p, begin, now)))
        elif k == "iem":
            for d in days:
                jobs.append((sid, f"iem_{d}", lambda s=sid, d=d: job_iem(s, d)))
        elif k == "nws" and sid != "SFOC1":
            jobs.append((sid, "nws", lambda s=sid: job_nws(s)))
        elif k == "ndbc":
            jobs.append((sid, "ndbc", lambda s=sid: job_ndbc(s, begin)))
    lock = threading.Lock()
    failed, done = {}, set()

    def run(i, sid, label, fn):
        try:
            part = fn()
            with lock:
                _merge(raw[sid], part)
                done.add(i)
        except Exception as e:  # noqa: BLE001
            with lock:
                failed[f"{sid}/{label}"] = repr(e)[:120]
                done.add(i)
    ths = [threading.Thread(target=run, args=(i, *j), daemon=True) for i, j in enumerate(jobs)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(max(0.0, deadline - (time.time() - t_start)))
    with lock:
        for i, j in enumerate(jobs):
            if i not in done:
                failed[f"{j[0]}/{j[1]}"] = f"no answer within {deadline:.0f} s"
        raw = {k: dict(v) for k, v in raw.items()}
    for sid, d in raw.items():     # humidity -> dew point where no dew point came with it
        if "Td" not in d and "RH" in d and "T" in d:
            tt, rh = d["T"], d["RH"]
            n = {t: v for t, v in zip(*tt)}
            td = [(_td_from_rh(n[t], v) if t in n and np.isfinite(v) and np.isfinite(n[t]) else np.nan) for t, v in zip(*rh)]
            d["Td"] = (list(rh[0]), np.array(td, float))
    newest = {}
    for sid, d in raw.items():
        ts = [max(v[0]) for v in d.values() if len(v[0])]
        if ts:
            newest[sid] = max(ts).isoformat()
    return dict(raw=raw, failed=failed, seconds=round(time.time() - t_start, 1), newest=newest, jobs=len(jobs))


# ------------------------------------------------------------------ hourly tables
def hourly(raw, t0, nh):
    """-> {id: dict(T, Td, spd, dir, u, v, vis, ceil, sst: [nh] arrays)} (hourly means in (h - 30 min, h + 30 min])."""
    from . import forcing as Fc
    out = {}
    for sid, d in raw.items():
        h = {k: np.full(nh, np.nan) for k in ("T", "Td", "spd", "dir", "u", "v", "vis", "ceil", "sst")}
        if "T" in d:
            h["T"] = Fc.hourly_mean(d["T"][0], d["T"][1], t0, nh)
        if "Td" in d:
            h["Td"] = Fc.hourly_mean(d["Td"][0], d["Td"][1], t0, nh)
        if "wind" in d and len(d["wind"][0]):
            h["spd"], h["dir"] = Fc.hourly_wind(*d["wind"], t0, nh)
            h["u"], h["v"] = anchors.to_uv(h["spd"], h["dir"])
        if "vis" in d:
            h["vis"] = Fc.hourly_mean(d["vis"][0], d["vis"][1], t0, nh)
        if "ceil" in d:
            h["ceil"] = Fc.hourly_mean(d["ceil"][0], d["ceil"][1], t0, nh)
        if "sst" in d:
            h["sst"] = Fc.hourly_mean(d["sst"][0], d["sst"][1], t0, nh)
        out[sid] = h
    return out


def load_station_wind(path, dirs):
    """station_wind.npz -> dict(ids, dirs, uv [D, S, 2], z_used) or None (missing file, unreadable, or its directions differ from the static table's)."""
    try:
        z = np.load(path, allow_pickle=False)
        sw = dict(ids=[str(i) for i in z["ids"]], dirs=np.asarray(z["dirs"], float), uv=np.asarray(z["uv"], float), z_used=np.asarray(z["z_used"], float))
        if dirs is not None and (len(sw["dirs"]) != len(dirs) or not np.allclose(sw["dirs"], np.asarray(dirs, float))):
            return None
        return sw
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ the analysis (3c, 3e, 3f)
def _temp_block(kind, ctx, S):
    """Offset / lapse fit of one variable ('T' or 'Td'). S: station table. -> dict or None when no station carries the variable."""
    from . import forcing as Fc
    G0 = C.LAPSE_T_K_PER_KM if kind == "T" else C.LAPSE_TD_K_PER_KM
    nh, n, g = ctx["nh"], ctx["n"], ctx["g"]
    sel = [i for i, sid in enumerate(S["ids"]) if np.isfinite(ctx["H"][sid][kind]).any()]
    if not sel:
        return None
    ids = [S["ids"][i] for i in sel]
    obs = np.array([ctx["H"][s][kind] for s in ids])                            # [S, nh]
    lat_v = S["lat" + kind][sel]                                                 # lattice value at the station [S, nh]
    dz = S["dz_km"][sel]                                                         # [S]
    base = lat_v + G0 * dz[:, None]
    r = np.clip(obs - base, -3 * C.TEMP_CLIP_C, 3 * C.TEMP_CLIP_C)
    a, b = np.full(nh, np.nan), np.full(nh, np.nan)
    nrep = np.isfinite(r).sum(axis=0)
    for h in range(nh):
        o = anchors.fit_T_hour(r[:, h], dz)
        if o is not None:
            a[h], b[h] = o
    fit_h = np.isfinite(a)
    a_rm, b_rm = Fc.rolling_median(a, C.ROLL_H), Fc.rolling_median(b, C.ROLL_H)
    a_rm = np.clip(np.where(fit_h, a_rm, np.nan), -C.TEMP_CLIP_C, C.TEMP_CLIP_C)
    b_rm = np.clip(np.where(fit_h, b_rm, np.nan), *C.T_LAPSE_ADJ_CLIP)
    r_rm = np.array([np.clip(Fc.rolling_median(ri, C.ROLL_H), -C.TEMP_CLIP_C, C.TEMP_CLIP_C) for ri in r])
    sx, sy = S["x"][sel], S["y"][sel]
    after = np.full_like(obs, np.nan)
    for h in range(nh):
        if nrep[h] == 0:
            continue
        if fit_h[h]:
            after[:, h] = base[:, h] + a_rm[h] + b_rm[h] * dz
        else:
            after[:, h] = base[:, h] + anchors.horizontal(r_rm[:, h], sx, sy, sx, sy)
    m = np.where(nrep > 0, fit_h.astype(float), np.nan)
    e = C.TEMP_E_FOLD_H
    a_g, hl = Fc.fade_series(a_rm, g, e, "add", 0.0)
    b_g, _ = Fc.fade_series(b_rm, g, e, "add", 0.0)
    m_g, _ = Fc.fade_series(m, g, e, "add", 0.0)
    r_g = np.array([Fc.fade_series(ri, g, e, "add", 0.0)[0] for ri in r_rm])
    return dict(ids=ids, sel=sel, G0=G0, obs=obs, base=base, resid=obs - after, after=after, a=a_rm, b=b_rm, fit_h=fit_h, nrep=nrep,
                a_g=a_g, b_g=b_g, m_g=np.clip(m_g, 0.0, 1.0), r_g=r_g, sx=sx, sy=sy, last_hour=hl, dz=dz)


def _wind_block(ctx, S):
    """Regional inflow fit; None when there is no station_wind.npz or no in-grid wind station reports."""
    from . import forcing as Fc
    sw = ctx.get("sw")
    if sw is None:
        return None
    nh, n, g, lat_t = ctx["nh"], ctx["n"], ctx["g"], ctx["lat_t"]
    ids = [sid for sid in sw["ids"] if sid in ctx["H"] and np.isfinite(ctx["H"][sid]["u"]).any() and not C.STATIONS[sid].get("hill")]
    if not ids:
        return None
    col = [sw["ids"].index(i) for i in ids]
    uvlib = sw["uv"][:, col, :]
    dirs = sw["dirs"]
    names = list(C.WIND_REGIONS)
    ll = np.array([C.WIND_REGIONS[k] for k in names])
    cx, cy = lat_t.t.transform(ll[:, 1], ll[:, 0])
    pos = {sid: j for j, sid in enumerate(S["ids"])}
    sx, sy = S["x"][[pos[i] for i in ids]], S["y"][[pos[i] for i in ids]]
    Wst = anchors.region_weights(sx, sy, cx, cy, C.WIND_REGION_SIGMA_M)
    nb = [(names.index(a), names.index(b)) for a, b in C.WIND_REGION_NEIGHBOURS]
    idx, w4 = lat_t.weights(ll[:, 0], ll[:, 1])
    from .forcing import interp_at, uv_to_wind

    def forecast(Ua, Va):
        s_, d_ = uv_to_wind(interp_at(Ua, idx, w4), interp_at(Va, idx, w4))      # [R, T]
        return s_, anchors.wrap_dir(d_)
    Ufh, thfh = forecast(ctx["Ug"], ctx["Vg"])                                       # hourly [R, nh]
    Ufg, thfg = forecast(ctx["Uraw_g"], ctx["Vraw_g"])                               # model steps [R, n]
    obs_uv = np.stack([np.stack([ctx["H"][i]["u"], ctx["H"][i]["v"]], axis=1) for i in ids], axis=1)    # [nh, S, 2]
    fit = anchors.fit_series(obs_uv, Wst, uvlib, dirs, Ufh.T, thfh.T, nb)
    R = len(names)
    U_rm = np.array([Fc.rolling_median(fit["U"][:, r], C.ROLL_H) for r in range(R)])           # [R, nh]
    th_rm = np.array([anchors.roll_med_dir(fit["th"][:, r], C.ROLL_H) for r in range(R)])
    cU = U_rm - Ufh
    cth = Fc.ang_diff(th_rm, thfh)
    e = C.WIND_E_FOLD_H
    cU_g = np.array([Fc.fade_series(cU[r], g, e, "add", 0.0)[0] for r in range(R)])
    cth_g = np.array([Fc.fade_series(cth[r], g, e, "add", 0.0)[0] for r in range(R)])
    hl = Fc.fade_series(cU[0], g, e, "add", 0.0)[1]
    U_g = np.clip(Ufg + cU_g, C.WIND_U_MIN_MS, C.WIND_U_MAX_MS)
    th_g = thfg + cth_g
    dirw = anchors.dir_weights(th_g.T, dirs)                                                # [n, R, D]
    before = np.stack([anchors.predict(Ufh[:, h], thfh[:, h], Wst, uvlib, dirs) for h in range(nh)])      # [nh, S, 2]
    after = np.full_like(before, np.nan)
    for h in range(nh):
        if np.isfinite(U_rm[:, h]).all() and np.isfinite(th_rm[:, h]).all():
            after[h] = anchors.predict(U_rm[:, h], th_rm[:, h], Wst, uvlib, dirs)
    return dict(ids=ids, names=names, cx=cx, cy=cy, Wst=Wst, fit=fit, U_g=U_g, th_g=th_g, dirw=dirw, before=before, after=after, obs_uv=obs_uv, Ufh=Ufh, thfh=thfh,
                U_rm=U_rm, th_rm=th_rm, last_hour=hl, dirs=dirs)


def analyze(ctx):
    """ctx keys: H hourly tables, lat_t, rrfs, nh, n, g, now_h, t_h, step_min, Ug Vg Tg Tdg (hourly lattice, raw), Uraw_g Vraw_g (lattice at model steps, raw), city (existing
    citywide corrections on the model steps: r, d, dT, dTd), sw (station_wind dict or None).
    -> dict(S, temp, dew, wind, kmax, flags). Pure: changes nothing it is given."""
    from . import forcing as Fc
    lat_t, rr, nh = ctx["lat_t"], ctx["rrfs"], ctx["nh"]
    ids = list(C.STATIONS)
    lat = np.array([C.STATIONS[i]["lat"] for i in ids])
    lon = np.array([C.STATIONS[i]["lon"] for i in ids])
    sx, sy = lat_t.t.transform(lon, lat)
    idx, w4 = lat_t.weights(lat, lon)
    zl = Fc.interp_at(np.asarray(rr["elev"], float), idx, w4)
    z = np.array([C.STATIONS[i]["elev_m"] for i in ids])
    S = dict(ids=ids, lat=lat, lon=lon, x=np.asarray(sx), y=np.asarray(sy), z=z, zlat=zl, dz_km=(z - zl) / 1000.0,
             latT=Fc.interp_at(ctx["Tg"], idx, w4), latTd=Fc.interp_at(ctx["Tdg"], idx, w4),
             latU=Fc.interp_at(ctx["Ug"], idx, w4), latV=Fc.interp_at(ctx["Vg"], idx, w4))
    flags = []
    out = dict(S=S, flags=flags)
    out["temp"] = _temp_block("T", ctx, S)
    out["dew"] = _temp_block("Td", ctx, S)
    out["wind"] = _wind_block(ctx, S)
    if ctx.get("sw") is None:
        flags.append("no station_wind.npz: wind anchors off")
    elif out["wind"] is None:
        flags.append("no in-grid wind station reported: wind anchors off")
    anyobs = np.zeros(nh, bool)
    for sid in ids:
        h = ctx["H"][sid]
        anyobs |= np.isfinite(h["T"]) | np.isfinite(h["spd"]) | np.isfinite(h["Td"])
    kmax = int(min(nh - 1, np.floor(ctx["now_h"])))
    while kmax >= 0 and not anyobs[kmax]:
        kmax -= 1
    out["kmax"] = kmax
    return out


# ------------------------------------------------------------------ stations.json (step 4) and the archive residuals (3e)
def _r(v, nd=1):
    v = np.asarray(v, float)
    return [None if not np.isfinite(x) else round(float(x), nd) for x in v]


def build_json(A, ctx, now, st_status):
    """stations.json: for each station its position and, for each hour of the observed part, the observation, the model before correction (raw lattice at the
    station), the model with today's citywide correction and the model with the anchor fit, plus a 24 h summary (mean absolute error)."""
    from . import forcing as Fc
    S, kmax, t_h = A["S"], A["kmax"], ctx["t_h"]
    K = kmax + 1
    spm = int(round(60.0 / ctx["step_min"]))
    cr, cd, cT, cTd = (np.asarray(ctx["city"][k], float) for k in ("r", "d", "dT", "dTd"))
    hk = np.arange(max(K, 0)) * spm
    pos = {sid: j for j, sid in enumerate(S["ids"])}
    tb, db, wb = A.get("temp"), A.get("dew"), A.get("wind")
    rows = {}
    for sid in S["ids"]:
        j, m, h = pos[sid], C.STATIONS[sid], ctx["H"][sid]
        has = {k: bool(np.isfinite(h[k][:K]).any()) for k in h}
        if not any(has.values()):
            rows[sid] = dict(name=m["name"], kind=m["kind"], lat=m["lat"], lon=m["lon"], x=round(float(S["x"][j]), 1), y=round(float(S["y"][j]), 1), z=m["elev_m"],
                             hill=bool(m.get("hill")), reporting=False, newest=st_status.get("newest", {}).get(sid))
            continue
        z_used = m["elev_m"] + m["sensor_m"]
        row = dict(name=m["name"], kind=m["kind"], lat=m["lat"], lon=m["lon"], x=round(float(S["x"][j]), 1), y=round(float(S["y"][j]), 1), z=round(z_used, 1),
                   hill=bool(m.get("hill")), reporting=True, newest=st_status.get("newest", {}).get(sid), obs={}, model_before={}, model_city={}, model_after={}, mae_24h={})
        ob = row["obs"]
        for key, nm, nd in (("T", "T", 1), ("Td", "Td", 1), ("spd", "wspd", 1), ("dir", "wdir", 0), ("vis", "vis_km", 1), ("ceil", "ceil_ft", 0), ("sst", "sst", 1)):
            if has[key]:
                ob[nm] = _r(h[key][:K], nd)
        for kind, blk, nm in (("T", tb, "T"), ("Td", db, "Td")):
            if has[kind] and blk is not None and sid in blk["ids"]:
                i = blk["ids"].index(sid)
                row["model_before"][nm] = _r(S["lat" + kind][j][:K], 1)
                row["model_city"][nm] = _r(S["lat" + kind][j][:K] + (cT if kind == "T" else cTd)[hk], 1)
                row["model_after"][nm] = _r(blk["after"][i][:K], 1)
                row["mae_24h"][nm] = [_mae(h[kind][:K], S["lat" + kind][j][:K]), _mae(h[kind][:K], blk["after"][i][:K]), _mae(h[kind][:K], S["lat" + kind][j][:K] + (cT if kind == "T" else cTd)[hk])]
        if has["spd"]:
            su, sv = S["latU"][j][:K], S["latV"][j][:K]
            sp0, d0 = Fc.uv_to_wind(su, sv)
            row["model_before"]["wspd"], row["model_before"]["wdir"] = _r(sp0, 1), _r(d0, 0)
            rr_, dd_ = cr[hk], cd[hk]
            spc, dc = sp0 * rr_, (d0 + dd_) % 360.0
            row["model_city"]["wspd"], row["model_city"]["wdir"] = _r(spc, 1), _r(dc, 0)
            row["mae_24h"]["wspd"] = [_mae(h["spd"][:K], sp0), None, _mae(h["spd"][:K], spc)]
            if wb is not None and sid in wb["ids"]:
                i = wb["ids"].index(sid)
                bef, aft = wb["before"][:K, i, :], wb["after"][:K, i, :]
                bs, bd = Fc.uv_to_wind(bef[:, 0], bef[:, 1])
                as_, ad = Fc.uv_to_wind(aft[:, 0], aft[:, 1])
                row["model_before"]["wspd"], row["model_before"]["wdir"] = _r(bs, 1), _r(bd, 0)     # for an in-grid anchor "before" is the library field with the forecast inflow
                row["model_after"]["wspd"], row["model_after"]["wdir"] = _r(as_, 1), _r(ad, 0)
                ov = np.stack([h["u"][:K], h["v"][:K]], axis=1)
                row["mae_24h"]["wspd"] = [_mae(h["spd"][:K], bs), _mae(h["spd"][:K], as_), _mae(h["spd"][:K], spc)]
                row["mae_24h"]["wind_vec"] = [_vmae(ov, bef), _vmae(ov, aft), None]
                row["anchor"] = "wind"
        row["mae_24h"] = {k: v for k, v in row["mae_24h"].items() if any(x is not None for x in v)}
        rows[sid] = row
    out = dict(version=1, generated_iso=now.isoformat(), t0_iso=ctx["t_h"][0].isoformat(), nudge_anchors=bool(C.NUDGE_ANCHORS),
               hours_iso=[t.isoformat() for t in t_h[:K]], mae_columns=["raw model", "anchor fit", "today's citywide correction"],
               note="model_before: forecast before any observation (wind of an in-grid anchor: library field with the forecast inflow); model_city: with today's citywide correction; "
                    "model_after: with the anchor fit (in-sample). Hill stations are data points at their height; out-of-grid stations have no simulated wind vector.",
               stations=rows)
    if wb is not None:
        f = wb["fit"]
        out["wind_fit"] = dict(regions=wb["names"], anchors=wb["ids"], mode=[str(x) for x in f["mode"][:K]], offshore=[bool(x) for x in f["offshore"][:K]],
                               n_used=[int(x) for x in f["n"][:K]], dropped_qc=[int(x) for x in f["dropped"][:K]],
                               inflow_ms=[_r(wb["U_rm"][r][:K], 1) for r in range(len(wb["names"]))], from_deg=[_r(wb["th_rm"][r][:K] % 360.0, 0) for r in range(len(wb["names"]))],
                               forecast_inflow_ms=[_r(wb["Ufh"][r][:K], 1) for r in range(len(wb["names"]))], forecast_from_deg=[_r(wb["thfh"][r][:K] % 360.0, 0) for r in range(len(wb["names"]))])
    if tb is not None:
        out["temp_fit"] = dict(stations=tb["ids"], offset_c=_r(tb["a"][:K], 2), lapse_adj_k_per_km=_r(tb["b"][:K], 1), n_reporting=[int(x) for x in tb["nrep"][:K]],
                               mode=["fit" if x else "horizontal" for x in tb["fit_h"][:K]])
    if db is not None:
        out["dew_fit"] = dict(stations=db["ids"], offset_c=_r(db["a"][:K], 2), lapse_adj_k_per_km=_r(db["b"][:K], 1))
    hills = [s for s, r in rows.items() if r.get("hill") and r.get("reporting")]
    if hills:
        al = {}
        for s in hills:
            o = rows[s]["obs"]
            if "wspd" in o:
                sp, _ = Fc.uv_to_wind(S["latU"][pos[s]][:K], S["latV"][pos[s]][:K])
                ob_ = np.array([np.nan if v is None else v for v in o["wspd"]])
                last = slice(max(0, K - 6), K)
                al[s] = dict(obs_mean_ms_6h=_nm(ob_[last]), forecast_mean_ms_6h=_nm(sp[last]))
        out["wind_aloft"] = al
    return out


def _nm(a):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    return None if not len(a) else round(float(a.mean()), 2)


def _mae(o, m, last=24):
    o, m = np.asarray(o, float)[-last:], np.asarray(m, float)[-last:]
    ok = np.isfinite(o) & np.isfinite(m)
    return round(float(np.abs(o[ok] - m[ok]).mean()), 2) if ok.any() else None


def _vmae(o, m, last=24):
    o, m = np.asarray(o, float)[-last:], np.asarray(m, float)[-last:]
    ok = np.isfinite(o).all(axis=1) & np.isfinite(m).all(axis=1)
    return round(float(np.hypot(*(o[ok] - m[ok]).T).mean()), 2) if ok.any() else None


def residuals(A, ctx):
    """Compact observed-minus-fitted record for the run archive (3e): residuals are kept, never spread over the map."""
    K = A["kmax"] + 1
    out = dict(v=1, t0=ctx["t_h"][0].isoformat(), hours=int(K))
    for key, blk in (("T", A.get("temp")), ("Td", A.get("dew"))):
        if blk is not None:
            out[key] = {sid: _r(blk["resid"][i][:K], 2) for i, sid in enumerate(blk["ids"])}
    wb = A.get("wind")
    if wb is not None:
        w = {}
        for i, sid in enumerate(wb["ids"]):
            d = wb["obs_uv"][:K, i, :] - wb["after"][:K, i, :]
            w[sid] = [_r(d[:, 0], 2), _r(d[:, 1], 2)]
        out["wind_uv"] = w
        out["offshore_hours"] = int(np.sum(wb["fit"]["offshore"][:K]))
    return out
