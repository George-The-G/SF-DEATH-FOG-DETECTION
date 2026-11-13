"""Spatial forcing and observation nudging (PLAN_LIVE_DATA.md 3.2-3.3).

RRFS on a 6 x 5 lattice -> [L, n] series at the model step; per street point a precomputed [N, 4] bilinear index/weight table gathers the
forcing inside the time loop (lattice series are never expanded to [n, N]). Observations correct the lattice for the past hours and fade into
the forecast: wind (speed ratio, direction offset, e-fold 6 h), temperature / dew point (additive, e-fold 6 h), fog source s(t) (3 h).
"""
import math

import warnings
import numpy as np

from .. import fogtop, wetness
from . import config as C


# ------------------------------------------------------------------ helpers
def hours_rel(times, t0):
    return np.array([(t - t0).total_seconds() / 3600.0 for t in times])


def last_finite_index(a):
    """Index of the last finite value of a 1-D series, or None. (Unlike fade_series' last-known hour, which comes from rolling-median
    smoothed series and so runs ROLL_H - 1 hours past the last real observation.)"""
    k = np.nonzero(np.isfinite(np.asarray(a, float)))[0]
    return None if len(k) == 0 else int(k[-1])


def cap_obs_until(obs_until, t_h, now_h):
    """The observed/forecast boundary can never lie after the build time: cap at the last whole model hour <= now."""
    if obs_until is None:
        return None
    return min(obs_until, t_h[min(len(t_h) - 1, max(0, int(math.floor(now_h))))])


def fill_series(a):
    """Linear fill of NaNs in a 1-D series, edges held; all-NaN stays NaN."""
    a = np.asarray(a, float)
    ok = np.isfinite(a)
    if ok.all() or not ok.any():
        return a
    return np.interp(np.arange(len(a)), np.nonzero(ok)[0], a[ok])


def to_grid(arr, th, g):
    """arr [..., nh] sampled at hours th -> [..., len(g)] at hours g (NaNs filled first, edge values held)."""
    a = np.asarray(arr, float)
    flat = a.reshape(-1, a.shape[-1])
    out = np.empty((flat.shape[0], len(g)))
    for i, row in enumerate(flat):
        ok = np.isfinite(row)
        out[i] = np.interp(g, th[ok], row[ok]) if ok.any() else np.nan
    return out.reshape(a.shape[:-1] + (len(g),))


def wind_to_uv(spd, d):
    r = np.radians(d)
    return -spd * np.sin(r), -spd * np.cos(r)


def uv_to_wind(u, v):
    return np.hypot(u, v), (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0


def circ_mean_deg(d, w=None):
    d = np.asarray(d, float)
    ok = np.isfinite(d)
    if not ok.any():
        return np.nan
    w = np.ones_like(d) if w is None else np.asarray(w, float)
    return float((np.degrees(np.arctan2((w * np.sin(np.radians(d)))[ok].sum(), (w * np.cos(np.radians(d)))[ok].sum())) + 360.0) % 360.0)


def ang_diff(a, b):
    return (np.asarray(a, float) - np.asarray(b, float) + 180.0) % 360.0 - 180.0


def hourly_mean(times, vals, t0, nh):
    """Mean of observations in (h - 30 min, h + 30 min] for every hour h = t0 + k h; NaN where none."""
    out = np.full(nh, np.nan)
    if len(times) == 0:
        return out
    th = hours_rel(times, t0)
    v = np.asarray(vals, float)
    for k in range(nh):
        m = (th > k - 0.5) & (th <= k + 0.5) & np.isfinite(v)
        if m.any():
            out[k] = v[m].mean()
    return out


def hourly_wind(times, spd, d, t0, nh):
    th = hours_rel(times, t0)
    u, v = wind_to_uv(np.asarray(spd, float), np.asarray(d, float))
    s, dd = np.full(nh, np.nan), np.full(nh, np.nan)
    for k in range(nh):
        m = (th > k - 0.5) & (th <= k + 0.5) & np.isfinite(u) & np.isfinite(v)
        if m.any():
            s[k], dd[k] = uv_to_wind(u[m].mean(), v[m].mean())
    return s, dd


def rolling_median(a, w):
    out = np.full(len(a), np.nan)
    for k in range(len(a)):
        seg = a[max(0, k - w + 1):k + 1]
        seg = seg[np.isfinite(seg)]
        if len(seg):
            out[k] = np.median(seg)
    return out


def fade_series(vals_h, g, e_fold, mode="add", ones=0.0):
    """vals_h: correction at hours 0..nh-1 (NaN = unknown). Returns the correction at the (fractional) hours g: interpolated across the
    observed hours, held at the first value before them, faded after the last known hour with exp(-dt / e_fold) (mode 'add': value * f,
    mode 'ratio': value ** f). Returns (series, last_known_hour or None)."""
    ok = np.isfinite(vals_h)
    if not ok.any():
        return np.full(len(g), ones), None
    hk = np.nonzero(ok)[0]
    hl = float(hk[-1])
    base = np.interp(g, hk, vals_h[ok])
    f = np.where(g > hl, np.exp(-(g - hl) / e_fold), 1.0)
    return (base * f if mode == "add" else base ** f), hl


# ------------------------------------------------------------------ lattice weights
class Lattice:
    def __init__(self, lat, lon):
        self.lat0, self.lat1 = C.LAT_RANGE
        self.lon0, self.lon1 = C.LON_RANGE
        self.nlat, self.nlon = C.N_LAT, C.N_LON
        self.lat, self.lon = lat, lon
        from pyproj import Transformer
        self.t = Transformer.from_crs(4326, 32610, always_xy=True)
        self.x, self.y = self.t.transform(lon, lat)

    def weights(self, lat, lon):
        """Bilinear indices [N,4] into the flattened lattice (lat-major) and weights [N,4]; positions outside are clamped."""
        fi = np.clip((np.asarray(lat) - self.lat0) / (self.lat1 - self.lat0) * (self.nlat - 1), 0, self.nlat - 1 - 1e-9)
        fj = np.clip((np.asarray(lon) - self.lon0) / (self.lon1 - self.lon0) * (self.nlon - 1), 0, self.nlon - 1 - 1e-9)
        i0, j0 = np.floor(fi).astype(int), np.floor(fj).astype(int)
        ti, tj = fi - i0, fj - j0
        idx = np.stack([i0 * self.nlon + j0, i0 * self.nlon + j0 + 1, (i0 + 1) * self.nlon + j0, (i0 + 1) * self.nlon + j0 + 1], axis=1)
        w = np.stack([(1 - ti) * (1 - tj), (1 - ti) * tj, ti * (1 - tj), ti * tj], axis=1)
        return idx, w


def interp_at(arr, idx, w):
    """arr [L, ...] -> value at points with weights: [N, ...]."""
    a = np.asarray(arr)
    return sum(w[:, k].reshape((-1,) + (1,) * (a.ndim - 1)) * a[idx[:, k]] for k in range(4))


# ------------------------------------------------------------------ main builder
def build(rrfs, t0, n, now, x, y, obs, sector_fn=None, step_min=C.STEP_MIN, log=print, physics=(), goes_hours=None, z=None, dsr_hours=None):
    """rrfs: fetch.rrfs() dict. t0: window start (aware UTC). n: number of model steps. now: current UTC. x, y: street point coordinates (UTM 10N).
    obs: dict(coops=, metar=, nws={id: rows}, sounding=) with missing sources absent / None. sector_fn(hours, dirs) -> list of GOES upwind low-cloud fractions
    (None = no GOES). goes_hours: {hour: goes.LowCloud} the sector function filled (sun_clear: cloudiness from the mask classes). Returns dict(ser, s_series, fog_top, forcing_fn, info, lattice series ...)."""
    import datetime as dt
    L = len(rrfs["lat"])
    lat_t = Lattice(rrfs["lat"], rrfs["lon"])
    th = hours_rel(rrfs["times"], t0)
    g = np.arange(n) * step_min / 60.0                 # model hours since t0
    nh = int(math.floor(g[-1])) + 1                    # hourly grid 0..nh-1 (+ the last partial hour handled by the 10-min grid)
    hg = np.arange(nh, dtype=float)
    V = rrfs["vars"]
    T = to_grid(V["temperature_2m"], th, g)
    Td = to_grid(V["dew_point_2m"], th, g)
    CL = to_grid(V["cloud_cover_low"], th, g)
    CC = to_grid(V["cloud_cover"], th, g)
    PR = np.maximum(to_grid(np.nan_to_num(V["precipitation"], nan=0.0), th, g), 0.0)
    SW = np.maximum(to_grid(np.nan_to_num(V["shortwave_radiation"], nan=0.0), th, g), 0.0)
    u_h, v_h = wind_to_uv(V["wind_speed_10m"], V["wind_direction_10m"])
    U, Vv = to_grid(u_h, th, g), to_grid(v_h, th, g)
    info = dict(model=rrfs["model"], rrfs_last_valid=None if rrfs["last_valid"] is None else rrfs["last_valid"].isoformat(), flags=[],
                physics=sorted(wetness.check_physics(physics)))

    # --- model at the observation sites (hourly, unnudged) for the corrections
    def model_at(lat, lon, arrg):
        i, w = lat_t.weights(np.array([lat]), np.array([lon]))
        return interp_at(arrg, i, w)[0]
    t_h = [t0 + dt.timedelta(hours=float(k)) for k in range(nh)]
    Ug, Vg, Tg, Tdg = (to_grid(a, th, hg) for a in (u_h, v_h, V["temperature_2m"], V["dew_point_2m"]))

    # --- wind correction from CO-OPS 9414290
    r_series = np.ones(n)
    d_series = np.zeros(n)
    obs_until = None
    co = obs.get("coops")
    if co is not None:
        so, do = hourly_wind(co["t"], co["s"], co["d"], t0, nh)
        sm, dm = uv_to_wind(*(model_at(*C.COOPS_LL, a) for a in (Ug, Vg)))
        rat = so / np.maximum(sm, 0.5)
        dl = ang_diff(do, dm)
        rat_r = np.clip(rolling_median(rat, C.ROLL_H), *C.WIND_RATIO_CLIP)
        dl_r = np.full(nh, np.nan)
        for k in range(nh):
            seg = dl[max(0, k - C.ROLL_H + 1):k + 1]
            seg = seg[np.isfinite(seg)]
            if len(seg):
                dl_r[k] = np.clip(circ_mean_deg(seg), -C.WIND_DIR_CLIP_DEG, C.WIND_DIR_CLIP_DEG)
        r_series, hl = fade_series(rat_r, g, C.WIND_E_FOLD_H, "ratio", 1.0)
        d_series, _ = fade_series(dl_r, g, C.WIND_E_FOLD_H, "add", 0.0)
        k_raw = last_finite_index(so)   # the real last observed hour (hl runs ROLL_H - 1 hours later: rolling medians carry forward)
        if k_raw is not None:
            obs_until = t_h[k_raw]
        info["wind_nudge"] = dict(last_hour=None if hl is None else t_h[int(hl)].isoformat(),
                                  ratio_last=None if hl is None else float(rat_r[int(hl)]), dir_offset_last=None if hl is None else float(dl_r[int(hl)]))
    else:
        info["flags"].append("no CO-OPS wind: model wind not nudged")
    U0, V0 = U, Vv                                       # lattice wind before any observation nudging (guide 03: forecast prior; arrays are never changed in place)
    sp, dr = uv_to_wind(U, Vv)
    sp = sp * r_series[None, :]
    dr = (dr + d_series[None, :]) % 360.0
    U, Vv = wind_to_uv(sp, dr)

    # --- temperature / dew point corrections: mean of station minus model offsets
    def temp_corr(kind):
        offs = []
        for name, lat, lon, times, vals in sites(obs, kind):
            o = hourly_mean(times, vals, t0, nh)
            m = model_at(lat, lon, Tg if kind == "T" else Tdg)
            offs.append(o - m)
        if not offs:
            return np.full(n, 0.0), None
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            a = np.nanmean(np.array(offs), axis=0) if np.isfinite(np.array(offs)).any() else np.full(nh, np.nan)
        a = np.clip(rolling_median(a, C.ROLL_H), -C.TEMP_CLIP_C, C.TEMP_CLIP_C)
        s, hl = fade_series(a, g, C.TEMP_E_FOLD_H, "add", 0.0)
        return s, (None if hl is None else (t_h[int(hl)].isoformat(), float(a[int(hl)])))
    dT, iT = temp_corr("T")
    dTd, iTd = temp_corr("Td")
    info["temp_nudge"] = dict(T=iT, Td=iTd)
    if iT is None:
        info["flags"].append("no station temperature: model temperature not nudged")
    T0, Td0 = T, Td                                      # lattice temperature / dew point before any observation nudging
    T = T + dT[None, :]
    Td = np.minimum(Td + dTd[None, :], T)                # never above the air temperature

    # --- stations as anchors (guide 03): the analysis always runs when station data exist (stations.json, residuals); it changes the model only with C.NUDGE_ANCHORS
    anch, st_ctx, st_obs = None, None, obs.get("stations")
    if st_obs is not None:
        try:
            from . import stations as Sx
            st_ctx = dict(H=Sx.hourly(st_obs["raw"], t0, nh), lat_t=lat_t, rrfs=rrfs, nh=nh, n=n, g=g, now_h=(now - t0).total_seconds() / 3600.0, t_h=t_h, step_min=step_min,
                          Ug=Ug, Vg=Vg, Tg=Tg, Tdg=Tdg, Uraw_g=U0, Vraw_g=V0, city=dict(r=r_series, d=d_series, dT=dT, dTd=dTd), sw=obs.get("station_wind"))
            anch = Sx.analyze(st_ctx)
            info["flags"].extend(anch["flags"])
        except Exception as e:  # noqa: BLE001
            anch = None
            info["flags"].append(f"station analysis failed: {e!r}"[:160])

    # --- coast series (scalar): lattice points west of COAST_X_MAX
    west = lat_t.x < C.COAST_X_MAX
    if not west.any():
        west = lat_t.x <= np.sort(lat_t.x)[C.N_LAT - 1]
    cs = lambda a: a[west].mean(axis=0)  # noqa: E731
    cT, cTd, cCL, cCC, cPR, cSW = (cs(a) for a in (T, Td, CL, CC, PR, SW))
    cU, cV = cs(U), cs(Vv)
    cspd, cdir = uv_to_wind(cU, cV)
    ser = {"t_utc": [t0 + dt.timedelta(minutes=step_min * i) for i in range(n)], "temperature_2m": cT, "dew_point_2m": cTd, "wind_speed_10m": cspd,
           "wind_dir": cdir, "cloud_cover": cCC, "cloud_cover_low": cCL, "precipitation": cPR, "shortwave_radiation": cSW,
           "elevation_m": float(np.mean(rrfs["elev"][west]))}
    pf = rrfs.get("prof") or {}
    if pf and all(np.isfinite(v).any() for v in pf.values()):
        for k, v in pf.items():
            ser[k] = to_grid(v[None, :], th, g)[0]
    else:
        info["flags"].append("no pressure-level profile: fog top from the fallback constant")

    # --- fog source s(t): model, then observation correction
    s_model = wetness.fog_inflow(cT, cTd, cCL, cdir)
    s_series = s_model.copy()
    dir_h = np.array([circ_mean_deg(cdir[(g > k - 0.5) & (g <= k + 0.5)]) for k in range(nh)])
    smod_h = np.array([s_model[(g > k - 0.5) & (g <= k + 0.5)].mean() for k in range(nh)])
    now_h = (now - t0).total_seconds() / 3600.0
    past = np.arange(nh) <= now_h
    goes_frac = np.full(nh, np.nan)
    if sector_fn is not None:
        hrs = [t_h[k] for k in range(nh) if past[k]]
        vals = sector_fn(hrs, [dir_h[k] for k in range(nh) if past[k]])
        for k, v in zip([k for k in range(nh) if past[k]], vals):
            goes_frac[k] = np.nan if v is None else v
    else:
        info["flags"].append("no GOES low cloud")
    khaf = np.full(nh, np.nan)
    ceil_floor = np.full(nh, np.nan)
    mt = obs.get("metar")
    if mt is not None and mt.get("KHAF"):
        rows = mt["KHAF"]
        for k in range(nh):
            r = [x_ for x_ in rows if abs((x_["t"] - t_h[k]).total_seconds()) <= 1800]
            if r:
                fog = any((np.isfinite(x_["vis_mi"]) and x_["vis_mi"] <= C.KHAF_VIS_MI) or (np.isfinite(x_["ceil_ft"]) and x_["ceil_ft"] <= C.KHAF_CEIL_FT) for x_ in r)
                khaf[k] = 1.0 if fog else 0.0
    else:
        info["flags"].append("no METAR: fog source not corrected by KHAF, no ceiling floor on the fog top")
    if mt is not None:   # ceiling floor from KHAF / KSFO
        for k in range(nh):
            fl = []
            for st in ("KHAF", "KSFO"):
                for x_ in mt.get(st, []) or []:
                    if abs((x_["t"] - t_h[k]).total_seconds()) <= 1800 and np.isfinite(x_["ceil_ft"]) and x_["ceil_ft"] <= 3000:
                        fl.append(x_["ceil_ft"] * 0.3048 + 5.0 + C.FOG_TOP_ABOVE_CEILING_M)
            if fl:
                ceil_floor[k] = max(fl)
            elif any(abs((x_["t"] - t_h[k]).total_seconds()) <= 1800 for x_ in (mt.get("KHAF", []) or [])):
                ceil_floor[k] = 0.0
    ons = wetness.onshore_weight(dir_h)
    both = np.vstack([goes_frac, khaf])
    cnt = np.isfinite(both).sum(axis=0)
    has = cnt > 0
    s_obs = np.where(has, np.nansum(both, axis=0) / np.maximum(cnt, 1), np.nan) * ons
    corr = np.where(has, (C.W_MODEL * smod_h + C.W_OBS * np.nan_to_num(s_obs)) - smod_h, np.nan)
    csr, hl = fade_series(corr, g, C.FOG_E_FOLD_H, "add", 0.0)
    s_series = np.clip(s_model + csr, 0.0, 1.0)
    info["fog_source"] = dict(hours_with_obs=int(has.sum()), last_obs_hour=None if hl is None else t_h[int(hl)].isoformat(),
                              goes_hours=int(np.isfinite(goes_frac).sum()), khaf_hours=int(np.isfinite(khaf).sum()),
                              mean_model=float(s_model.mean()), mean_used=float(s_series.mean()))
    if hl is not None:
        obs_until = max(obs_until, t_h[int(hl)]) if obs_until is not None else t_h[int(hl)]

    # --- fog top: RRFS estimate, sounding inversion base, METAR ceiling floor
    top, tinfo = fogtop.estimate_fog_top(ser)
    top = np.asarray(top, float).copy()
    info["fog_top_source"] = tinfo.get("source")
    snd = obs.get("sounding")
    if snd is not None:
        base = _inv(snd)
        if base is not None:
            tv = (snd["valid"] - t0).total_seconds() / 3600.0
            w = np.clip(np.minimum((g - (tv - 1.5)) / 1.5, ((tv + C.SOUNDING_VALID_H + 6.0) - g) / 6.0), 0.0, 1.0)
            floor = ser["elevation_m"] + wetness.FOG_TOP_MIN_M
            top = w * max(base, floor) + (1 - w) * top
            info["sounding"] = dict(valid=snd["valid"].isoformat(), inversion_base_m=base)
        else:
            info["sounding"] = dict(valid=snd["valid"].isoformat(), inversion_base_m=None)
    else:
        info["flags"].append("no sounding")
    cf, hlc = fade_series(np.where(np.isfinite(ceil_floor), ceil_floor, np.nan), g, C.FOG_E_FOLD_H, "add", 0.0)
    if hlc is not None:
        top = np.maximum(top, cf)
    top = np.minimum(top, wetness.FOG_TOP_MAX_M + 400.0)

    # --- per-point gather
    idx, w4 = lat_t.weights(*_xy_to_ll(lat_t, x, y))
    Ts, Tds, CLs, PRs, SWs, Us, Vs = (np.ascontiguousarray(a) for a in (T, Td, CL, PR, SW, U, Vv))
    Ks = CCs = None
    sun_out, pav_out = {}, {}
    if "pavement" in info["physics"]:   # guide 07: total cloud for the sky longwave, cold start temperature of the road
        CCs = np.ascontiguousarray(CC)
        Tm, pinfo = pavement_cold_start(V["temperature_2m"], th)
        pav_out = dict(pav_ts0=(Tm[idx] * w4).sum(axis=1).astype(np.float32))
        info["pavement"] = pinfo
    if "sun_clear" in info["physics"]:   # guide 06: cloudiness k per lattice cell and step
        Ks, sun_out = cloudiness(lat_t, SW, g, t_h, now_h, ser["t_utc"], goes_hours, info, log, dsr_hours=dsr_hours)

    def forcing_fn(i, f):
        gat = lambda A: (A[idx, i] * w4).sum(axis=1).astype(np.float32)  # noqa: E731
        out = dict(temp=gat(Ts), dew=gat(Tds), wind=np.hypot(gat(Us), gat(Vs)), cloud_low=gat(CLs), precip=gat(PRs), sw=gat(SWs))
        if Ks is not None:
            out["k"] = gat(Ks)
        if CCs is not None:
            out["cloud"] = gat(CCs)
        return out
    extra = {}
    if anch is not None:
        try:
            if C.NUDGE_ANCHORS:
                forcing_fn = _anchored_forcing(anch, forcing_fn, x, y, z, rrfs, lat_t, idx, w4, T0, Td0, U, Vv, info)
            from . import stations as Sx
            sj = Sx.build_json(anch, st_ctx, now, st_obs)
            res = Sx.residuals(anch, st_ctx)
            sj["residuals"] = res
            extra = dict(stations_json=sj, stations=res)
            info["anchors"] = dict(nudge_anchors=bool(C.NUDGE_ANCHORS), kmax=anch["kmax"], wind=_wind_summary(anch), fetch_failed=sorted((st_obs or {}).get("failed", {})))
        except Exception as e:  # noqa: BLE001
            info["flags"].append(f"station outputs failed: {e!r}"[:160])
    obs_until = cap_obs_until(obs_until, t_h, now_h)
    info["obs_until"] = None if obs_until is None else obs_until.isoformat()
    info["coast_lattice_points"] = int(west.sum())
    return dict(ser=ser, s_series=s_series, fog_top=top, forcing_fn=forcing_fn, info=info, s_model=s_model, obs_until=obs_until,
                dir_hourly=dir_h, lattice_cl=CL, goes_frac=goes_frac, khaf=khaf, t_hours=t_h, idx=idx, w=w4, lattice=lat_t, **sun_out, **pav_out, **extra)


def pavement_cold_start(T_l, th, hours=24.0):
    """Cold start of the road temperature (guide 07): the mean RRFS air temperature of the 24 h before the window start t0, per lattice cell.
    T_l [L, nh] hourly RRFS temperature, th [nh] the hours of its columns relative to t0 (RRFS carries about 2 days of past hours, so this is normally complete).
    When fewer than 24 past hourly values exist, the first C.HISTORY_H hours of the window (t0 ...) fill up the 24 h. -> (mean [L], dict(past_hours, window_hours))."""
    th = np.asarray(th, float)
    T_l = np.asarray(T_l, float)
    prev = (th >= -hours - 1e-6) & (th < -1e-6) & np.isfinite(T_l).any(axis=0)
    n_prev = int(prev.sum())
    sel, n_win = prev.copy(), 0
    if n_prev < int(round(hours)):
        n_win = min(int(C.HISTORY_H), int(round(hours)) - n_prev)
        sel |= (th >= -1e-6) & (th < n_win - 1e-6)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        m = np.nanmean(T_l[:, sel], axis=1) if sel.any() else np.full(T_l.shape[0], np.nan)
    if not np.isfinite(m).all():
        m = np.where(np.isfinite(m), m, np.nanmean(m) if np.isfinite(m).any() else 12.0)
    return m, dict(past_hours=n_prev, window_hours=n_win)


def cloudiness(lat_t, SW, g, t_h, now_h, t_utc, goes_hours, info, log=print, dsr_hours=None):
    """Cloudiness factor k [L, n] (guide 06): k_model = clip(SW / clear-sky GHI, 0.05, 1) where the sun is above SUN_MIN_EL (held below); in observed hours
    k_sat from the GOES mask classes of the pixels in each lattice cell's box; the model k moves by K_SAT_WEIGHT * (k_sat - k_model), faded into the
    forecast with FOG_E_FOLD_H. -> (K [L, n], dict(k_model, k_sat_hourly, k_hours) for the hand-back and tests)."""
    from .. import sunclear
    from . import dsr as dsrlib
    n = SW.shape[1]
    nh = len(t_h)
    ghi_c, _, _ = sunclear.clear_sky(t_utc, wetness.SUN_LAT, wetness.SUN_LON)
    _, el = wetness.sun_position(t_utc)
    k_model = sunclear.k_from_sw(SW, ghi_c[None, :], el[None, :])
    spb = int(round(60.0 / C.STEP_MIN))
    k_model_h = k_model[:, ::spb][:, :nh]
    L = SW.shape[0]
    k_sat = np.full((L, nh), np.nan)
    dlat = (C.LAT_RANGE[1] - C.LAT_RANGE[0]) / (C.N_LAT - 1)
    dlon = (C.LON_RANGE[1] - C.LON_RANGE[0]) / (C.N_LON - 1)
    for h in range(nh):
        lc = (goes_hours or {}).get(t_h[h])
        if lc is not None and h <= now_h and getattr(lc, "acm", None) is not None:
            k_sat[:, h] = lc.k_cells(lat_t.lat, lat_t.lon, dlat, dlon)
    k_mask = k_sat.copy()
    k_dsr = np.full((L, nh), np.nan)
    if C.SUN_DSR and dsr_hours:   # GOES downward shortwave (dsr.py): DSR / clear-sky GHI at the scan time, preferred where it exists; the mask classes fill the rest
        hs = [h for h in range(nh) if h <= now_h and dsr_hours.get(t_h[h]) is not None]
        if hs:
            ghi_s, _, _ = sunclear.clear_sky([dsr_hours[t_h[h]].t for h in hs], wetness.SUN_LAT, wetness.SUN_LON)
            for h, gs in zip(hs, ghi_s):
                k_dsr[:, h] = dsrlib.k_cells(dsr_hours[t_h[h]], gs, lat_t.lat, lat_t.lon, dlat, dlon)
        k_sat = np.where(np.isfinite(k_dsr), k_dsr, k_mask)
    K = k_model.copy()
    if np.isfinite(k_sat).any():
        corr_h = C.K_SAT_WEIGHT * (k_sat - k_model_h)
        corr = np.stack([fade_series(corr_h[l], g, C.FOG_E_FOLD_H, "add", 0.0)[0] for l in range(L)])
        K = k_model + corr
    else:
        info["flags"].append("no GOES cloud mask: sunlight cloudiness from the model only")
    K = np.clip(K, wetness.K_MIN, 1.0)
    info["sun_k"] = dict(mean_model=float(k_model.mean()), mean_used=float(K.mean()), sat_hours=int(np.isfinite(k_sat).any(axis=0).sum()),
                         dsr_hours=int(np.isfinite(k_dsr).any(axis=0).sum()), mask_hours=int(np.isfinite(k_mask).any(axis=0).sum()), dsr_on=bool(C.SUN_DSR),
                         table=_k_table(t_h, now_h, ghi_c[::spb][:nh], SW[:, ::spb][:, :nh], k_model_h, k_mask, k_dsr, K[:, ::spb][:, :nh]))
    return K, dict(k_model=k_model, k_sat_hourly=k_sat, k_lattice=K, k_mask_hourly=k_mask, k_dsr_hourly=k_dsr)


def _k_table(t_h, now_h, ghi_h, sw_h, k_model_h, k_mask, k_dsr, k_used_h, hours=24):
    """The last `hours` observed daylight hours: city means (over the lattice cells) of k from the model (RRFS SW / clear-sky GHI), from the GOES mask classes, from DSR,
    and the k used after the correction; plus the clear-sky GHI and the RRFS SW of the hour. None where a source has nothing for that hour."""
    def m(a):
        a = np.asarray(a, float)
        return None if not np.isfinite(a).any() else round(float(np.nanmean(a)), 3)
    rows = []
    for h in range(len(t_h)):
        if h > now_h or ghi_h[h] < C.DSR_MIN_GHI:
            continue
        rows.append(dict(hour=t_h[h].isoformat(), ghi_clear=round(float(ghi_h[h]), 0), sw_model=round(float(np.mean(sw_h[:, h])), 0), k_model=m(k_model_h[:, h]),
                         k_mask=m(k_mask[:, h]), k_dsr=m(k_dsr[:, h]), k_used=m(k_used_h[:, h])))
    return rows[-hours:]



def _wind_summary(anch):
    wb = anch.get("wind")
    if wb is None:
        return None
    f, k = wb["fit"], anch["kmax"] + 1
    modes = [str(m) for m in f["mode"][:k]]
    return dict(anchors=wb["ids"], hours=k, regional=modes.count("regional"), citywide=modes.count("citywide"), no_station=modes.count("forecast"),
                offshore_flagged=int(np.sum(f["offshore"][:k])), qc_dropped=int(np.sum(f["dropped"][:k])))


def _anchored_forcing(anch, base_fn, x, y, z, rrfs, lat_t, idx, w4, T0, Td0, U1, V1, info):
    """The per-point forcing with the station anchors (C.NUDGE_ANCHORS): wind = regional inflow, direction weights per point, temperature and dew point =
    lattice + sea-level offset + (base lapse + fitted adjustment) x height above the lattice cell. Parts without data keep today's forcing."""
    from . import anchors
    zl = interp_at(np.asarray(rrfs["elev"], float), idx, w4)
    dz = ((np.asarray(z, float) if z is not None else zl) - zl) / 1000.0
    wb, tb, db = anch.get("wind"), anch.get("temp"), anch.get("dew")
    T0c, Td0c = np.ascontiguousarray(T0), np.ascontiguousarray(Td0)
    wpts = None
    if wb is not None:
        wpts = anchors.region_weights(x, y, wb["cx"], wb["cy"], C.WIND_REGION_SIGMA_M).astype(np.float32)
        info["flags"].append("anchors: wind from the regional station fit")
    if tb is not None:
        info["flags"].append("anchors: temperature from the station offset / lapse fit")
    hor = {}

    def horiz(blk, key, i):
        if blk["m_g"][i] >= 1.0:
            return 0.0
        if key not in hor:
            d2 = (x[:, None] - blk["sx"][None, :]) ** 2 + (y[:, None] - blk["sy"][None, :]) ** 2
            Wh = np.exp(-d2 / (2.0 * C.T_HORIZ_L_M ** 2)).astype(np.float32)
            hor[key] = (Wh, np.maximum(1.0, Wh.sum(axis=1)))
        Wh, den = hor[key]
        return (Wh @ blk["r_g"][:, i].astype(np.float32)) / den

    def var(blk, A, i, gat):
        m = blk["m_g"][i]
        v = gat(A) + blk["G0"] * dz + m * (blk["a_g"][i] + blk["b_g"][i] * dz)
        return v + (1.0 - m) * horiz(blk, id(blk), i) if m < 1.0 else v

    def fn(i, f):
        out = base_fn(i, f)
        gat = lambda A: (A[idx, i] * w4).sum(axis=1)  # noqa: E731
        if tb is not None:
            t = var(tb, T0c, i, gat)
            out["temp"] = t.astype(np.float32)
            if db is not None:
                out["dew"] = np.minimum(var(db, Td0c, i, gat), t).astype(np.float32)
        if wb is not None:
            out["wind"] = (wpts * wb["U_g"][:, i, None].astype(np.float32)).sum(axis=0)
            out["dir_w"] = (wb["dirw"][i].T.astype(np.float32) @ wpts)
        return out
    return fn


def sites(obs, kind):
    """Observation sites for 'T' (air temperature) or 'Td' (dew point): (name, lat, lon, times, values)."""
    out = []
    co = obs.get("coops")
    if kind == "T" and co is not None and len(co.get("t_temp", [])):
        out.append(("coops", *C.COOPS_LL, co["t_temp"], co["temp"]))
    mt = obs.get("metar")
    if mt:
        for st, rows in mt.items():
            if rows and st in C.METAR_LL:
                out.append((st, *C.METAR_LL[st], [r["t"] for r in rows], [r["temp" if kind == "T" else "dewp"] for r in rows]))
    for st, rows in (obs.get("nws") or {}).items():
        if rows and st in C.NWS_STATIONS:
            out.append((st, *C.NWS_STATIONS[st], [r["t"] for r in rows], [r["temp" if kind == "T" else "dewp"] for r in rows]))
    return out


def _inv(snd):
    from .fetch import inversion_base
    return inversion_base(snd)


def _xy_to_ll(lat_t, x, y):
    lon, lat = lat_t.t.transform(np.asarray(x), np.asarray(y), direction="INVERSE")
    return lat, lon
