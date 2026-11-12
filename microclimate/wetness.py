"""M4: road-wetness model. A physical-ish PROXY with hand-set constants (not calibrated against observations).

State: W in [0, 100] per street point = wetness index ("death fog chance"). All rates are W units per hour and are
multiplied by dt_h = step_min / 60. Per 10-minute step and per point:

  surface cooling   dT = DT_MAX * SVF * (1 - cloud_low/100) * clip(1 - wind/WIND_CALM, 0, 1)      Ts = Ta - dT
  dew               add_dew  = K_DEW * max(0, Td - Ts) * porous
  fog deposition    s(t)     = clip(1 - (Ta-Td)/GAP_FOG, 0, 1) * sqrt(clip(cloud_low/CLOUD_FOG, 0, 1)) * onshore(dir)
                    local    = s(t - lag(x)) * clip(tracer/TRACER_REF, 0, 1)**FOG_EXP
                    add_fog  = K_FOG * local * (0.5 + wind/5) * porous
                    onshore(dir) = max(0, cos(dir - 270)) for wind-FROM directions 180..360, else 0 (offshore = no fog)
                    lag(x) = (x - x_west_edge) / max(mean night wind, 1.5)  (the front reaches inland points later)
  rain              add_rain = K_RAIN * (1 - exp(-precip_mm_h / RAIN_SAT)) * porous   (porous = rain soaks in / drains,
                    so porous surfaces register less standing wetness)
  evaporation       evap = (K_EVAP_BASE + K_EVAP_WIND * wind) * max(0, 1 - RH)   (RH from T, Td by Magnus)
  sun drying        K_SUN * SW/1000 only when the point is sunlit (horizon profile vs sun azimuth/elevation)
                    + K_DIFF * SW/1000 everywhere (diffuse, small)
  W <- clip(W + (add_dew + add_fog + add_rain - evap - sun) * dt_h, 0, 100)

Wind used is the Open-Meteo 10 m wind times the street wind ratio (~1 everywhere in the MVP) times the normalised refined
factor rel = clip(refine_corr/0.91, 0.25, 1) (USE_REFINED); it enters cooling, fog factor, evaporation and the lag.
The tracer is the 270-degree (west) 20 m fog field unless the direction library (dirlib.py, DIR_LIBRARY) holds validated
fields for other directions: then the tracer at each time step is the linear blend of the two neighbouring directions' fields
for the instantaneous wind direction and the front lag is measured along the night's mean (fog-weighted) flow direction
from the upwind domain edge. Without them other directions are approximated with the 270 field (the speed factor
(0.5 + wind/5) scales the deposition). Offshore wind gets no advected fog either way.
"""
import os
from pathlib import Path

import numpy as np

# ---- tunable constants (W units per hour unless stated) ----
DT_MAX = 4.0         # degC, maximum radiative surface cooling (clear, calm, full sky view)
WIND_CALM = 6.0      # m/s, wind at which cooling is fully suppressed
GAP_FOG = 4.0        # degC, T - Td at which the advected fog inflow strength is zero
CLOUD_FOG = 40.0     # % low cloud for full inflow strength (sqrt ramp)
TRACER_REF = 0.03    # tracer value that counts as fully fog-exposed (band 0 values at Potrero are 0.0005..0.045)
FOG_EXP = 0.5        # compresses the tracer's dynamic range
K_DEW = 8.0          # per degC of (Td - Ts)
K_FOG = 20.0
K_RAIN = 60.0
RAIN_SAT = 1.0       # mm/h
K_EVAP_BASE = 5.0
K_EVAP_WIND = 2.0    # per m/s
K_SUN = 80.0         # per 1000 W/m2 shortwave, direct sun only (sunlit points)
K_DIFF = 10.0        # per 1000 W/m2 shortwave, everywhere
LAG_MAX_H = 3.0
WIND_LAG_MIN = 1.5
X_WEST = 540100.0    # grid west edge, m (EPSG:32610)
DIR_LIBRARY = os.environ.get("DEATHFOG_DIR_LIBRARY", "0") != "0"  # replay writer: validated non-270 fields OFF by default (user decision pending); set DEATHFOG_DIR_LIBRARY=1 to enable. The live path always passes use_dirs=True.
USE_REFINED = True   # apply the nested 5 m wind correction (street_features.refine_corr) after normalisation
REFINE_NORM = 0.91   # raw refine_corr is over-damped (median 0.26, p95 0.91): divide by its p95 ...
REFINE_MIN = 0.25    # ... and clip to [REFINE_MIN, 1]; NaN (no patch) -> 1.0. Absolute magnitudes stay uncalibrated.
BAND_EDGES = [0, 150, 300, 500, 800]
BAND_OFFSET_M = 30.0  # 'band0' mode: the band is picked from point elevation + 30 m

# ---- fog height vs street elevation (see fogtop.py). All hand-set proxies, none calibrated. ----
FOG_HEIGHT_MODE = "profile"   # 'band0' = old behaviour (one hard-picked band, no fog top); 'profile' = fog top + vertical interpolation
DATA_DIR = None               # data folder holding grids/ and library/ (None -> ~/deathfog-data); used to sample the fog fields
BAND_CENTRES_M = [75.0, 225.0, 400.0, 650.0]  # centres of the four tracer source bands (above sea level)
TRACER_Z_OFFSET_M = 10.0      # profile mode: tracer sampled at street elevation + 10 m, interpolated between band centres
FOG_TOP_RAMP_M = 40.0         # half-width of the smooth wet/dry transition around the fog top (m)
FOG_TOP_MIN_M = 50.0          # fog top is never below grid elevation + this (hourly NWP cannot resolve thinner fog)
FOG_TOP_MAX_M = 900.0         # upper clamp of the fog top (m ASL)
FOG_TOP_FALLBACK_M = 300.0    # fog top used when the pressure-level data are missing
FOG_TOP_ABOVE_LAST_M = 100.0  # fog top when RH stays >= RH_CLOUD up to the highest level (900 hPa) = that level + this
RH_CLOUD = 80.0               # % relative humidity that counts as inside the saturated marine layer
INV_DT = 0.0                  # degC: T increase between consecutive pressure levels that marks the inversion base
ELEV_WIND_GAIN = 0.10         # profile mode: fog-deposition wind factor grows by this fraction per 100 m street elevation above 30 m
ELEV_WIND_REF_M = 30.0

# ---- physics switches (guide 01): named model changes, all OFF unless a caller passes them to simulate(physics=...) ----
KNOWN_PHYSICS = ("cloud_base", "lag_uniform", "vpd_evap", "total_cloud", "drizzle", "insitu_fog", "sun_clear", "film", "pavement", "grease")


def check_physics(physics):
    """-> frozenset of switch names; unknown names raise ValueError."""
    P = frozenset(physics or ())
    bad = sorted(P - set(KNOWN_PHYSICS))
    if bad:
        raise ValueError(f"unknown physics switch(es) {bad}; known: {list(KNOWN_PHYSICS)}")
    return P


CONSTANTS = {k: v for k, v in dict(globals()).items() if k.isupper() and isinstance(v, (int, float)) and k != "DIR_LIBRARY"}  # DIR_LIBRARY is a run switch, not a model constant (keeps the shipped index.json unchanged)


def magnus_rh(t, td):
    """Relative humidity 0-1 from air and dew point temperature (Magnus)."""
    a, b = 17.625, 243.04
    return np.clip(np.exp(a * td / (b + td)) / np.exp(a * t / (b + t)), 0.0, 1.0)


def onshore_weight(wdir):
    """Cosine weight about 270 (west) for wind-from directions 180-360, else 0 (offshore, no advected fog)."""
    d = np.asarray(wdir, float) % 360.0
    w = np.maximum(0.0, np.cos(np.radians(d - 270.0)))
    return np.where((d >= 180.0) & (d <= 360.0), w, 0.0)


def fog_inflow(ta, td, cloud_low, wdir):
    """Advected fog inflow strength s(t) in 0..1 (arrays over time or scalars)."""
    gap = np.asarray(ta, float) - np.asarray(td, float)
    return (np.clip(1.0 - gap / GAP_FOG, 0, 1) * np.sqrt(np.clip(np.asarray(cloud_low, float) / CLOUD_FOG, 0, 1))
            * onshore_weight(wdir))


def pick_tracer(fog_b, z):
    """fog_b [N,4] tracer per band; choose the band containing z + 30 m (above sea level)."""
    band = np.clip(np.searchsorted(BAND_EDGES[1:-1], np.asarray(z, float) + BAND_OFFSET_M, side="right"), 0, 3)
    return np.take_along_axis(np.asarray(fog_b, np.float32), band[:, None], axis=1)[:, 0]


def refined_rel(refine_corr):
    """Relative local wind factor from the refined (5 m nested) correction: clip(corr / 0.91, 0.25, 1), NaN -> 1."""
    c = np.asarray(refine_corr, float)
    return np.where(np.isnan(c), 1.0, np.clip(c / REFINE_NORM, REFINE_MIN, 1.0)).astype(np.float32)


def lag_seconds(x, wind_mean):
    """wind_mean scalar or per-point array (m/s)."""
    return np.clip((np.asarray(x, float) - X_WEST) / np.maximum(wind_mean, WIND_LAG_MIN), 0, LAG_MAX_H * 3600.0)


GRID_X0, GRID_Y0, GRID_W_M, GRID_H_M = 540100.0, 4171500.0, 855 * 20.0, 811 * 20.0


def lag_seconds_dir(x, y, mean_dir, wind_mean):
    """Like lag_seconds but along the flow of wind-FROM direction mean_dir: distance from the upwind edge/corner of the grid
    (projection on the flow vector, zero at the most upwind grid corner). Equals lag_seconds(x) for mean_dir = 270."""
    th = np.radians(mean_dir)
    fx, fy = -np.sin(th), -np.cos(th)
    corners = [(GRID_X0 + a * GRID_W_M) * fx + (GRID_Y0 + b * GRID_H_M) * fy for a in (0, 1) for b in (0, 1)]
    d = np.asarray(x, float) * fx + np.asarray(y, float) * fy - min(corners)
    return np.clip(d / np.maximum(wind_mean, WIND_LAG_MIN), 0, LAG_MAX_H * 3600.0)


def mean_flow_dir(s, wdir):
    """Fog-inflow-weighted circular mean of the wind-from direction over a night (plain mean when s is all zero)."""
    th = np.radians(np.asarray(wdir, float))
    wgt = s if np.sum(s) > 0 else np.ones_like(s)
    return float((np.degrees(np.arctan2((wgt * np.sin(th)).sum(), (wgt * np.cos(th)).sum())) + 360) % 360)


def fog_exposure(tracer):
    return np.clip(np.asarray(tracer, float) / TRACER_REF, 0, 1) ** FOG_EXP


def lagged(series, idx_float):
    """series[t] sampled at fractional step indices (vector) with the first value held before the start."""
    n = len(series)
    return np.interp(idx_float, np.arange(n), series, left=series[0])


def step(W, svf, porous, f, local_fog, lit, dt_h, wind_ratio=1.0, use_sun=True, fog_gain=1.0, physics=frozenset()):
    """One time step. W [N]; svf, porous [N]; f = dict of forcing (temp, dew, wind, cloud_low, precip, sw), each a scalar or an [N] array;
    local_fog [N] = lagged inflow * exposure; lit bool [N] (direct sun). physics: set of switch names (none changes anything yet).
    Returns (W_new, parts)."""
    wind = f["wind"] * wind_ratio
    dT = DT_MAX * svf * (1 - f["cloud_low"] / 100.0) * np.clip(1 - wind / WIND_CALM, 0, 1)
    ts = f["temp"] - dT
    dew = K_DEW * np.maximum(0.0, f["dew"] - ts) * porous
    fog = K_FOG * local_fog * (0.5 + wind / 5.0) * fog_gain * porous
    rain = K_RAIN * (1 - np.exp(-np.maximum(f["precip"], 0.0) / RAIN_SAT)) * porous * np.ones_like(W)
    rh = magnus_rh(f["temp"], f["dew"])
    evap = (K_EVAP_BASE + K_EVAP_WIND * wind) * np.maximum(0.0, 1 - rh)
    sw = np.maximum(f["sw"], 0.0) / 1000.0
    sun = K_DIFF * sw + (K_SUN * sw * lit if use_sun else 0.0)
    out = np.clip(W + (dew + fog + rain - evap - sun) * dt_h, 0.0, 100.0)
    return out.astype(np.float32), dict(dew=dew * dt_h, fog=fog * dt_h, rain=rain * dt_h, evap=evap * dt_h)


def sun_position(times_utc, lat=37.76, lon=-122.40):
    import pandas as pd
    import pvlib
    sp = pvlib.solarposition.get_solarposition(pd.DatetimeIndex(times_utc), lat, lon)
    return sp["azimuth"].to_numpy(), sp["apparent_elevation"].to_numpy()


DEFAULT_DIRS = (180.0, 202.5, 225.0, 247.5, 270.0, 292.5, 315.0, 337.5, 360.0)   # the validated direction library


def simulate(pts, ser, step_min=10, tracer=None, svf=None, use_sun=True, W0=0.0, refined=None, fog_mode=None, fog_top=None, use_dirs=None,
             tracer_dirs=None, dirs_deg=None, forcing_fn=None, lag_block_h=None, out_every=1, keep_lit=True, s_series=None, physics=None,
             state0=None, save_at=None):
    """Run one window. pts: dict(x, svf, porous, tracer, wind_ratio, horizon [N,72] or None, band-selected tracer; for the
    'profile' mode also y, z). ser: weather.series / night_series dict (scalar series: coast forcing, fog source, fog top).
    Overrides: tracer / svf arrays (ablations); fog_mode ('band0' | 'profile', default FOG_HEIGHT_MODE); fog_top forces the fog
    top in profile mode; use_dirs: True/False forces the direction library on/off (default DIR_LIBRARY).
    Live-path options (defaults reproduce the replay exactly):
      tracer_dirs  [D, N] precomputed per-direction tracer table (live_static.npz 'tracer'), dirs_deg its directions (default
                   pts['dirs'] or the 9 library directions): no library files are read, the direction blend is used.
      forcing_fn   callable (i, f) -> f: replaces the scalar forcing dict of step i by one whose values may be [N] arrays
                   (spatial forcing interpolated from a lattice); the coast source s(t) stays the scalar series.
      lag_block_h  hours; the front lag uses the mean flow direction and wind of each block of that length (default: one value
                   for the whole window).
      out_every    keep W only every k-th step (frames 0, k, 2k, ...): memory for long windows.
      s_series     [n] fog source s(t) used instead of fog_inflow(ser) (live: coast series corrected by GOES / KHAF observations).
      keep_lit     keep the per-step direct-sun mask in diag['lit'] (replay validation needs it; live does not).
      physics      iterable of switch names from KNOWN_PHYSICS (default none = today's model); unknown names raise ValueError.
      state0       dict of arrays (key 'W' [N]) that replaces W0 as the starting state (carried state, guide 02).
      save_at      step index i: the state at the START of step i (before its forcing is applied) is returned in diag['state_at'] (dict of
                   arrays); None or i >= n = not saved.
    Returns (W [n_out, N] float32, diag)."""
    P = check_physics(physics)
    n = len(ser["temperature_2m"])
    N = len(pts["x"])
    mode = FOG_HEIGHT_MODE if fog_mode is None else fog_mode
    prof = mode == "profile"
    tr = pts["tracer"] if tracer is None else tracer
    top = fog_gain = None
    table = tracer_dirs is not None and use_dirs is not False
    if prof:
        from . import fogtop
        if tracer is None and tracer_dirs is None:
            tr = fogtop.points_tracer(pts, DATA_DIR)
        top, top_info = fogtop.estimate_fog_top(ser)
        if fog_top is not None:  # controlled experiments: force the fog top (scalar or [n], m ASL)
            top = np.broadcast_to(np.asarray(fog_top, float), (n,)).copy()
        fog_gain = 1.0 + ELEV_WIND_GAIN * np.maximum(np.asarray(pts["z"], float) - ELEV_WIND_REF_M, 0.0) / 100.0
    sv = pts["svf"] if svf is None else svf
    porous = pts["porous"]
    refined = USE_REFINED if refined is None else refined
    wr = pts.get("wind_ratio", 1.0) * (pts["rel_wind"] if refined and "rel_wind" in pts else 1.0)  # local wind / 10 m wind
    s = fog_inflow(ser["temperature_2m"], ser["dew_point_2m"], ser["cloud_cover_low"], ser["wind_dir"])
    if s_series is not None:
        s = np.asarray(s_series, float)
    wdir = np.asarray(ser["wind_dir"], float)
    wspd = np.asarray(ser["wind_speed_10m"], float)
    wmean = float(np.mean(wspd))
    mdir = mean_flow_dir(s, wdir)
    dirs = Wt = E = None
    if table:  # precomputed per-direction tracer table
        dd = np.asarray(pts["dirs"] if dirs_deg is None and "dirs" in pts else (DEFAULT_DIRS if dirs_deg is None else dirs_deg), float)
        assert len(dd) == len(tracer_dirs), "tracer_dirs rows must match dirs_deg"
        from . import dirlib
        dirs = dd
        E = fog_exposure(np.asarray(tracer_dirs)).astype(np.float32)
        Wt = dirlib.weights(wdir, dirs)
        if tracer is None:
            tr = np.asarray(tracer_dirs)[int(np.argmin(np.abs(dd - 270.0)))]
    elif prof and tracer is None and (DIR_LIBRARY if use_dirs is None else use_dirs):  # validated non-270 fields, blended per time step
        from . import dirlib
        dd = dirlib.available(Path(DATA_DIR or Path.home() / "deathfog-data") / "library")
        if len(dd) > 1:
            dirs = np.array(dd)
            E = np.stack([fog_exposure(fogtop.points_tracer(pts, DATA_DIR, d)) for d in dd]).astype(np.float32)
            Wt = dirlib.weights(wdir, dirs)

    def lag_for(mdir_, wmean_):
        if dirs is not None:
            return lag_seconds_dir(pts["x"], pts["y"], mdir_, wmean_ * wr) / (step_min * 60.0)
        return lag_seconds(pts["x"], wmean_ * wr) / (step_min * 60.0)

    blk = max(1, int(round(lag_block_h * 60.0 / step_min))) if lag_block_h else None
    lag_steps = None if blk else lag_for(mdir, wmean)
    expo = fog_exposure(tr)
    az, el = sun_position(ser["t_utc"])
    dt_h = step_min / 60.0
    if state0 is not None:
        W = np.asarray(state0["W"], np.float32).copy()
        assert W.shape == (N,), "state0['W'] must have one value per street point"
    else:
        W = np.full(N, float(W0), np.float32)
    state_at = None
    frames = (n + out_every - 1) // out_every
    out = np.zeros((frames, N), np.float32)
    lit_all = np.zeros((n, N), bool) if keep_lit else None
    dep = dict(fog=np.zeros(N), dew=np.zeros(N), rain=np.zeros(N), evap=np.zeros(N))
    hor = pts.get("horizon")
    block_dirs = []
    for i in range(n):
        if blk and i % blk == 0:  # time-varying lag: mean flow of this block
            sl = slice(i, min(n, i + blk))
            mb = mean_flow_dir(s[sl], wdir[sl])
            lag_steps = lag_for(mb, float(np.mean(wspd[sl])))
            block_dirs.append(mb)
        f = dict(temp=ser["temperature_2m"][i], dew=ser["dew_point_2m"][i], wind=ser["wind_speed_10m"][i],
                 cloud_low=ser["cloud_cover_low"][i], precip=ser["precipitation"][i], sw=ser["shortwave_radiation"][i])
        if forcing_fn is not None:
            f = forcing_fn(i, f)
        local = lagged(s, i - lag_steps) * (expo if E is None else Wt[i] @ E)
        if prof:  # street counts as inside the fog only while the (lagged) fog top is above it
            local = local * fogtop.fog_top_ramp(lagged(top, i - lag_steps), pts["z"])
        lit = np.zeros(N, bool)
        if hor is not None and el[i] > 0:
            from .sun import sunlit
            lit = sunlit(hor, az[i], el[i])
        if save_at is not None and i == save_at:  # state at the START of step i = initial state of a run that begins at that time
            state_at = dict(W=W.copy())
        W, parts = step(W, sv, porous, f, local, lit, dt_h, wr, use_sun, 1.0 if fog_gain is None else fog_gain, P)
        if i % out_every == 0:
            out[i // out_every] = W
        if keep_lit:
            lit_all[i] = lit
        for k in dep:
            dep[k] += parts[k]
    diag = dict(dirs_used=(None if dirs is None else dirs.tolist()), mean_flow_dir=mdir, sun_az=az, sun_el=el, s=s, lit=lit_all, lag_steps=lag_steps, fog_mode=mode, fog_top=top, tracer=np.asarray(tr, np.float32),
                out_every=out_every, block_dirs=block_dirs, physics=sorted(P), state_at=state_at, **{k + "_total": v for k, v in dep.items()})
    return out, diag
