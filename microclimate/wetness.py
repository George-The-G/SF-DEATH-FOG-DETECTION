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
The tracer is the 270-degree (west) 20 m fog field; other directions are approximated with it (the speed factor
(0.5 + wind/5) scales the deposition) and offshore wind gets no advected fog.
"""
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

CONSTANTS = {k: v for k, v in dict(globals()).items() if k.isupper() and isinstance(v, (int, float))}


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


def fog_exposure(tracer):
    return np.clip(np.asarray(tracer, float) / TRACER_REF, 0, 1) ** FOG_EXP


def lagged(series, idx_float):
    """series[t] sampled at fractional step indices (vector) with the first value held before the start."""
    n = len(series)
    return np.interp(idx_float, np.arange(n), series, left=series[0])


def step(W, svf, porous, f, local_fog, lit, dt_h, wind_ratio=1.0, use_sun=True, fog_gain=1.0):
    """One time step. W [N]; svf, porous [N]; f = dict of scalar forcing (temp, dew, wind, cloud_low, precip, sw);
    local_fog [N] = lagged inflow * exposure; lit bool [N] (direct sun). Returns (W_new, parts)."""
    wind = f["wind"] * wind_ratio
    dT = DT_MAX * svf * (1 - f["cloud_low"] / 100.0) * np.clip(1 - wind / WIND_CALM, 0, 1)
    ts = f["temp"] - dT
    dew = K_DEW * np.maximum(0.0, f["dew"] - ts) * porous
    fog = K_FOG * local_fog * (0.5 + wind / 5.0) * fog_gain * porous
    rain = K_RAIN * (1 - np.exp(-max(f["precip"], 0.0) / RAIN_SAT)) * porous * np.ones_like(W)
    rh = magnus_rh(f["temp"], f["dew"])
    evap = (K_EVAP_BASE + K_EVAP_WIND * wind) * np.maximum(0.0, 1 - rh)
    sw = max(f["sw"], 0.0) / 1000.0
    sun = K_DIFF * sw + (K_SUN * sw * lit if use_sun else 0.0)
    out = np.clip(W + (dew + fog + rain - evap - sun) * dt_h, 0.0, 100.0)
    return out.astype(np.float32), dict(dew=dew * dt_h, fog=fog * dt_h, rain=rain * dt_h, evap=evap * dt_h)


def sun_position(times_utc, lat=37.76, lon=-122.40):
    import pandas as pd
    import pvlib
    sp = pvlib.solarposition.get_solarposition(pd.DatetimeIndex(times_utc), lat, lon)
    return sp["azimuth"].to_numpy(), sp["apparent_elevation"].to_numpy()


def simulate(pts, ser, step_min=10, tracer=None, svf=None, use_sun=True, W0=0.0, refined=None, fog_mode=None, fog_top=None):
    """Run one night. pts: dict(x, svf, porous, tracer, wind_ratio, horizon [N,72] or None, band-selected tracer; for the
    'profile' mode also y, z). ser: weather.night_series dict. Overrides: tracer / svf arrays (ablations); fog_mode
    ('band0' | 'profile', default FOG_HEIGHT_MODE); fog_top forces the fog top in profile mode. Returns (W [n,N] float32, diag)."""
    n = len(ser["temperature_2m"])
    N = len(pts["x"])
    mode = FOG_HEIGHT_MODE if fog_mode is None else fog_mode
    prof = mode == "profile"
    tr = pts["tracer"] if tracer is None else tracer
    top = fog_gain = None
    if prof:
        from . import fogtop
        if tracer is None:
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
    wmean = float(np.mean(ser["wind_speed_10m"]))
    lag_steps = lag_seconds(pts["x"], wmean * wr) / (step_min * 60.0)
    expo = fog_exposure(tr)
    az, el = sun_position(ser["t_utc"])
    dt_h = step_min / 60.0
    W = np.full(N, float(W0), np.float32)
    out = np.zeros((n, N), np.float32)
    lit_all = np.zeros((n, N), bool)
    dep = dict(fog=np.zeros(N), dew=np.zeros(N), rain=np.zeros(N), evap=np.zeros(N))
    hor = pts.get("horizon")
    for i in range(n):
        f = dict(temp=ser["temperature_2m"][i], dew=ser["dew_point_2m"][i], wind=ser["wind_speed_10m"][i],
                 cloud_low=ser["cloud_cover_low"][i], precip=ser["precipitation"][i], sw=ser["shortwave_radiation"][i])
        local = lagged(s, i - lag_steps) * expo
        if prof:  # street counts as inside the fog only while the (lagged) fog top is above it
            local = local * fogtop.fog_top_ramp(lagged(top, i - lag_steps), pts["z"])
        lit = np.zeros(N, bool)
        if hor is not None and el[i] > 0:
            from .sun import sunlit
            lit = sunlit(hor, az[i], el[i])
        W, parts = step(W, sv, porous, f, local, lit, dt_h, wr, use_sun, 1.0 if fog_gain is None else fog_gain)
        out[i] = W
        lit_all[i] = lit
        for k in dep:
            dep[k] += parts[k]
    diag = dict(sun_az=az, sun_el=el, s=s, lit=lit_all, lag_steps=lag_steps, fog_mode=mode, fog_top=top, tracer=np.asarray(tr, np.float32),
                **{k + "_total": v for k, v in dep.items()})
    return out, diag

