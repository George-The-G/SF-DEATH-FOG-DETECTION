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
VPD_REF_KPA = 1.40   # switch vpd_evap (guide 10): es at 12 degC, so the vapour-deficit evaporation equals the RH form at 12 degC
LAG_SPEED_FACTOR = 1.3   # switch lag_uniform (guide 09): fog front speed = 10 m wind x this, the same for every street (first guess: wind at ~80 m over the 10 m wind, onshore flow)
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

# ---- cloud base (guide 08, switch "cloud_base") ----
BASE_RAMP_M = 60.0     # m below the cloud base over which a street goes from out of cloud (0) to in cloud (1); first guess (base estimate good to ~+-100 m)
LCL_M_PER_K = 125.0    # cloud base height above the surface per kelvin of T - Td
LAPSE_DRY = 0.0098     # K/m, air temperature below the cloud base
LAPSE_MOIST = 0.006    # K/m, between the base and the fog top (first guess)
LAPSE_DEW = 0.0018     # K/m, dew point below the cloud base
BASE_DZ_CLIP_M = 400.0 # street height above / below the model terrain is clipped to this in the lapse adjustment
BASE_OFFSET_CLIP_M = 300.0  # |station-fitted correction of the base formula| is clipped to this

# ---- drizzle (guide 11, switch "drizzle"; needs cloud_base). First guesses, not calibrated. ----
DRIZZLE_MAX = 0.10       # mm/h from a thick stratus layer
DRIZZLE_T0, DRIZZLE_T1 = 200.0, 500.0   # m cloud thickness where drizzle starts / reaches DRIZZLE_MAX
DRIZZLE_OBS_DZ_MM_H = 0.05    # mm/h: at least this when a coastal airport reports drizzle (DZ)
DRIZZLE_OBS_CLEAR = 0.3  # factor on the model rate when a low ceiling is reported without DZ / RA / BR / FG
DRIZZLE_EVAP_M = 600.0   # m below the base over which drizzle evaporates down to DRIZZLE_EVAP_MIN of its rate
DRIZZLE_EVAP_MIN = 0.2
K_DRIZZLE = 150.0        # old (W units) path: wetting per mm of drizzle

# ---- physics switches (guide 01): named model changes, all OFF unless a caller passes them to simulate(physics=...) ----
KNOWN_PHYSICS = ("cloud_base", "lag_uniform", "vpd_evap", "total_cloud", "drizzle", "insitu_fog", "sun_clear", "film", "pavement", "grease")


def check_physics(physics):
    """-> frozenset of switch names; unknown names raise ValueError."""
    P = frozenset(physics or ())
    bad = sorted(P - set(KNOWN_PHYSICS))
    if bad:
        raise ValueError(f"unknown physics switch(es) {bad}; known: {list(KNOWN_PHYSICS)}")
    if "pavement" in P and not {"sun_clear", "film"} <= P:   # guide 07 uses the street sunlight S (guide 06) and the water film (guide 05)
        raise ValueError(f"physics switch 'pavement' needs 'sun_clear' and 'film' as well (missing: {sorted({'sun_clear', 'film'} - P)})")
    if "drizzle" in P and "cloud_base" not in P:   # guide 11 uses the cloud base of guide 08
        raise ValueError("physics switch 'drizzle' needs 'cloud_base' as well")
    return P


CONSTANTS = {k: v for k, v in dict(globals()).items() if k.isupper() and isinstance(v, (int, float)) and k != "DIR_LIBRARY"}  # DIR_LIBRARY is a run switch, not a model constant (keeps the shipped index.json unchanged)


# ---- guides 05 (film) and 06 (sun_clear): constants defined AFTER the CONSTANTS snapshot above, so index.json lists the same constants as before while the switches are off.
# All first guesses (literature order), none verified.
SUN_LAT, SUN_LON = 37.76, -122.40  # clear-sky site (city centre)
K_MIN = 0.05           # lowest cloudiness factor k (1 = clear sky)
K_BEAM_OFF = 0.35      # k at and below which no direct beam is left
SUN_MIN_EL = 5.0       # deg: k from the model shortwave only where the sun is higher than this; held below
SLOPE_CLIP = 0.35      # rise over run: slopes are clipped to +-this
FOG_LWC = 0.2          # g/m3 liquid water content of fog
V_SETTLE = 0.02        # m/s settling speed of fog droplets
C_TURB = 0.004         # turbulent deposition: m/s per m/s of wind
DEW_MM_PER_K = 0.01    # mm/h of dew per K of (Td - Ts)
DEW_MAX = 0.04         # mm/h upper limit of the dew rate
EVAP_A = 0.108         # mm/h per kPa of vapour pressure deficit
EVAP_B = 0.54          # s/m, wind factor of evaporation
SUN_MM_PER_KW = 0.8    # mm/h of drying per 1000 W/m2
CAP_MM = (0.5, 0.3, 0.2, 0.5)   # mm of water a flat street holds before it runs off: asphalt, concrete, porous, other (= asphalt)
CAP_SLOPE_MAX = 0.15   # slope (rise/run) at which the storage is reduced most ...
CAP_SLOPE_DIV = 0.30   # ... storage factor = 1 - min(|slope|, CAP_SLOPE_MAX) / CAP_SLOPE_DIV
FILM_SLICK = 0.02      # mm: slip = 100 * (1 - exp(-F / FILM_SLICK))
DRY_MM = 0.005         # mm: a street below this counts as dry
DEPTH_REF_MM = 0.01    # depth file: resolution near zero (mm) ...
DEPTH_MAX_MM = 1.0     # ... and the depth shown as 255 (mm)
DRIZZLE_MM_H = 0.3     # precipitation below this rate counts as drizzle for the cause of the water
CAUSE_H = 3.0          # hours looked back for the main source of the water
RAIN_WET_MM_H = 0.1    # rate that counts as rain for hours_since_rain
HSR_MAX_H = 240.0      # hours_since_rain when there is no history
WASH_MM_H = 1.0        # rain in one hour that washes the street (resets rain_since_wash; guide 13 may change it)
RSW_MAX_MM = 50.0      # upper limit of rain_since_wash
RAIN_Q_MM = 0.02       # hourly rain history is kept in units of this (uint8, up to 5.1 mm/h)
# ---- guide 07 (pavement heat): textbook values, no road-temperature measurement exists, all unverified ----
SIGMA_SB = 5.670374e-8  # W/m2/K4 Stefan-Boltzmann
PAV_ALBEDO = 0.12      # asphalt
PAV_EMISS = 0.95       # surface emissivity
PAV_EA_COEF = 1.24     # clear-sky emissivity eps_a = PAV_EA_COEF * (e_a_hPa / Ta_K) ^ (1/7)
PAV_CLOUD_LW = 0.22    # cloud adds this * c^2 to the clear-sky downward longwave
CLOUD_HIGH_WEIGHT = 0.5   # middle and high cloud count this much as low cloud in the effective cloud fraction (guide 10 part B, first guess)
PAV_HC0 = 5.8          # W/m2/K, heat transfer coefficient at no wind
PAV_HC1 = 4.1          # W/m2/K per m/s of the local street wind
PAV_GAMMA = 0.066      # kPa/K psychrometric constant
PAV_LV = 2.45e6        # J/kg latent heat of vaporisation (1 mm of water = 1 kg/m2)
PAV_OMEGA = 7.27e-5    # 1/s, earth rotation rate (force-restore)
PAV_DEEP_S = 86400.0   # s, time scale of the deep layer
PAV_CD = (2.35e5, 2.9e5, 2.35e5, 2.35e5)   # J/m2/K force-restore heat capacity by surface class: asphalt, concrete, porous (= asphalt), other (= asphalt)
PAV_SUBSTEP_MIN = 5.0  # minutes per sub-step
PAV_DTS_MAX = 3.0      # K, largest change of the surface temperature in one sub-step
TS_U8_OFFSET_C = 10.0  # tsurf file: u8 = round((ts + 10) * 4)
TS_U8_PER_K = 4.0


def sat_vp_kpa(t):
    """Saturation vapour pressure in kPa, Magnus constants of magnus_rh (guide 10 A)."""
    t = np.asarray(t, float)
    return 0.6108 * np.exp(17.625 * t / (t + 243.04))


def es_kpa(t):
    """Saturation vapour pressure in kPa (Tetens) at t in degC."""
    return 0.6108 * np.exp(17.27 * t / (t + 237.3))


def depth_u8(f_mm):
    """Water depth in mm -> uint8: round(255 * log1p(F / 0.01) / log1p(1.0 / 0.01)), capped at 255."""
    f = np.maximum(np.asarray(f_mm, np.float64), 0.0)
    return np.clip(np.rint(255.0 * np.log1p(f / DEPTH_REF_MM) / np.log1p(DEPTH_MAX_MM / DEPTH_REF_MM)), 0, 255).astype(np.uint8)


def slip_of(f_mm):
    """Slippery 0..100 from the water on the road: a thin film is already fully slippery."""
    return (100.0 * (1.0 - np.exp(-np.maximum(np.asarray(f_mm, np.float64), 0.0) / FILM_SLICK))).astype(np.float32)


def storage_cap(surf, slope):
    """mm of water the street holds before it runs off: CAP_MM[surface class] * (1 - min(|slope|, CAP_SLOPE_MAX) / CAP_SLOPE_DIV)."""
    return (np.asarray(CAP_MM, float)[np.asarray(surf, int)] * (1.0 - np.minimum(np.abs(np.asarray(slope, float)), CAP_SLOPE_MAX) / CAP_SLOPE_DIV)).astype(np.float32)


def step_pavement(ts, tdeep, F, svf, porous, S, temp, dew, wind, cloud_pct, dt_h, cd, substep_min=PAV_SUBSTEP_MIN):
    """Guide 07: surface energy balance and force-restore heat store for one model step (all inputs broadcast to [N]; temperatures in degC, F = water on the
    road in mm, S = street sunlight W/m2, wind = local street wind m/s, cloud_pct = effective cloud fraction in percent, cd = heat capacity J/m2/K).
    Sub-steps of about `substep_min` minutes. Evaporation only while there is water, scaled to dry the surface exactly; condensation (LE < 0, only when ts is below
    the dew point) adds water on porous streets. Returns (ts, tdeep, F, dict(evap, dew (mm this step), energy (J/m2: the net flux G summed over the step)))."""
    ts = np.asarray(ts, np.float64)
    tdeep = np.asarray(tdeep, np.float64)
    F = np.asarray(F, np.float64)
    temp = np.asarray(temp, np.float64) * np.ones_like(ts)
    ta_k = temp + 273.15
    ea = es_kpa(np.asarray(dew, np.float64) * np.ones_like(ts))     # kPa
    eps_a = PAV_EA_COEF * (ea * 10.0 / ta_k) ** (1.0 / 7.0)
    lta = SIGMA_SB * ta_k ** 4
    c = np.clip(np.asarray(cloud_pct, np.float64) / 100.0, 0.0, 1.0)
    l_sky = eps_a * lta * (1.0 + PAV_CLOUD_LW * c * c)
    l_down = svf * l_sky + (1.0 - svf) * PAV_EMISS * lta
    absorbed = (1.0 - PAV_ALBEDO) * np.asarray(S, np.float64)
    hc = PAV_HC0 + PAV_HC1 * np.maximum(np.asarray(wind, np.float64), 0.0)
    nsub = max(1, int(round(dt_h * 60.0 / substep_min)))
    dts = dt_h * 3600.0 / nsub
    evap = np.zeros_like(ts)
    dew_mm = np.zeros_like(ts)
    energy = np.zeros_like(ts)
    for _ in range(nsub):
        ts_k = ts + 273.15
        nlw = PAV_EMISS * l_down - PAV_EMISS * SIGMA_SB * ts_k ** 4
        H = hc * (ts - temp)
        le = hc * (es_kpa(ts) - ea) / PAV_GAMMA
        le = np.where(le > 0.0, np.minimum(le, np.maximum(F, 0.0) * PAV_LV / dts), le * porous)
        G = absorbed + nlw - H - le
        d_ts = np.clip((2.0 * G / cd - PAV_OMEGA * (ts - tdeep)) * dts, -PAV_DTS_MAX, PAV_DTS_MAX)
        tdeep = tdeep + (ts - tdeep) / PAV_DEEP_S * dts
        ts = ts + d_ts
        mm = le * dts / PAV_LV
        F = F - mm
        evap += np.maximum(mm, 0.0)
        dew_mm += np.maximum(-mm, 0.0)
        energy += G * dts
    return ts, tdeep, F, dict(evap=evap, dew=dew_mm, energy=energy)


def effective_cloud_pct(f):
    """Effective cloud fraction in percent for the longwave sky: low cloud plus CLOUD_HIGH_WEIGHT of the extra middle / high cloud when the total is given
    (as guide 10 part B); low cloud alone otherwise."""
    cl = np.asarray(f["cloud_low"], np.float64)
    if "cloud" in f:
        return np.clip(cl + CLOUD_HIGH_WEIGHT * np.maximum(np.asarray(f["cloud"], np.float64) - cl, 0.0), 0.0, 100.0)
    return cl


def step_film(Fm, svf, porous, f, local_fog, cap, dt_h, wind_ratio=1.0, pav=None):
    """One time step of the film model (switch `film`). Fm [N] = water on the road in mm; f as in step() plus f['sw_eff'] (W/m2 effective sunlight);
    cap [N] mm storage. Rates in mm/h: fog deposition, dew, rain (the precipitation rate itself), minus evaporation (vapour deficit and wind) and
    sun drying. Water above cap runs off. Returns (Fm_new, parts) with parts in mm (this step): fog, dew, rain, evap (actual), sun (actual), runoff.
    pav (switch `pavement`): dict(ts, tdeep, cd): dew, evaporation and sun drying come from step_pavement (surface energy balance, f['sw_eff'] = S) instead of
    the terms above; fog, rain, storage and runoff stay. parts then also holds the new ts and tdeep and sun = 0."""
    wind = f["wind"] * wind_ratio
    if pav is not None:
        one = np.ones_like(Fm)
        fog = FOG_LWC * (V_SETTLE + C_TURB * wind) * 3.6 * local_fog * porous * one
        rain = np.maximum(f["precip"], 0.0) * porous * one
        ts, tdeep, F2, pp = step_pavement(pav["ts"], pav["tdeep"], Fm + (fog + rain) * dt_h, svf, porous, f["sw_eff"], f["temp"], f["dew"], wind,
                                          effective_cloud_pct(f), dt_h, pav["cd"])
        runoff = np.maximum(F2 - cap, 0.0)
        out = np.clip(F2, 0.0, cap)
        return out.astype(np.float32), dict(fog=fog * dt_h, dew=pp["dew"], rain=rain * dt_h, evap=pp["evap"], sun=np.zeros_like(out), runoff=runoff,
                                            ts=ts.astype(np.float32), tdeep=tdeep.astype(np.float32))
    dT = DT_MAX * svf * (1 - f["cloud_low"] / 100.0) * np.clip(1 - wind / WIND_CALM, 0, 1)
    ts = f["temp"] - dT
    one = np.ones_like(Fm)
    fog = FOG_LWC * (V_SETTLE + C_TURB * wind) * 3.6 * local_fog * porous * one
    dew = np.minimum(DEW_MM_PER_K * np.maximum(f["dew"] - ts, 0.0), DEW_MAX) * porous * one
    rain = np.maximum(f["precip"], 0.0) * porous * one
    vpd = np.maximum(es_kpa(f["temp"]) - es_kpa(f["dew"]), 0.0)
    evap = EVAP_A * (1.0 + EVAP_B * wind) * vpd * one
    sun = SUN_MM_PER_KW * np.maximum(f["sw_eff"], 0.0) / 1000.0 * one
    net = Fm + (fog + dew + rain - evap - sun) * dt_h
    runoff = np.maximum(net - cap, 0.0)
    out = np.clip(net, 0.0, cap)
    pot = (evap + sun) * dt_h
    short = np.maximum(-net, 0.0)                       # removal asked for that there was no water for
    frac = np.where(pot > 0, np.maximum(pot - short, 0.0) / np.where(pot > 0, pot, 1.0), 0.0)
    return out.astype(np.float32), dict(fog=fog * dt_h, dew=dew * dt_h, rain=rain * dt_h, evap=evap * dt_h * frac, sun=sun * dt_h * frac, runoff=runoff)


def magnus_rh(t, td):
    """Relative humidity 0-1 from air and dew point temperature (Magnus)."""
    a, b = 17.625, 243.04
    return np.clip(np.exp(a * td / (b + td)) / np.exp(a * t / (b + t)), 0.0, 1.0)


def onshore_weight(wdir):
    """Cosine weight about 270 (west) for wind-from directions 180-360, else 0 (offshore, no advected fog)."""
    d = np.asarray(wdir, float) % 360.0
    w = np.maximum(0.0, np.cos(np.radians(d - 270.0)))
    return np.where((d >= 180.0) & (d <= 360.0), w, 0.0)


def drizzle_rate(thick, cloud_low):
    """Guide 11: model drizzle rate (mm/h) at the coast from the cloud thickness (m) and the low cloud cover (%). Broadcasts."""
    ramp = np.clip((np.asarray(thick, float) - DRIZZLE_T0) / (DRIZZLE_T1 - DRIZZLE_T0), 0.0, 1.0)
    return DRIZZLE_MAX * ramp * np.sqrt(np.clip(np.asarray(cloud_low, float) / CLOUD_FOG, 0.0, 1.0))


def drizzle_at_street(D, top, base, z, top_ramp):
    """Guide 11: drizzle rate at each street: only below the fog top (top_ramp = fog_top_ramp(top, z)), less where it evaporates below the base."""
    return D * top_ramp * np.clip(1.0 - np.maximum(np.asarray(base, float) - np.asarray(z, float), 0.0) / DRIZZLE_EVAP_M, DRIZZLE_EVAP_MIN, 1.0)


def fog_inflow(ta, td, cloud_low, wdir, use_gap=True):
    """Advected fog inflow strength s(t) in 0..1 (arrays over time or scalars). use_gap=False (switch cloud_base) drops the T - Td factor:
    the cloud base test per street already counts the gap."""
    gap = np.asarray(ta, float) - np.asarray(td, float)
    return ((np.clip(1.0 - gap / GAP_FOG, 0, 1) if use_gap else 1.0) * np.sqrt(np.clip(np.asarray(cloud_low, float) / CLOUD_FOG, 0, 1))
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
    cl = effective_cloud_pct(f) if ("total_cloud" in physics and "cloud" in f) else f["cloud_low"]   # guide 10 B: middle / high cloud also slows the night cooling
    dT = DT_MAX * svf * (1 - cl / 100.0) * np.clip(1 - wind / WIND_CALM, 0, 1)
    ts = f["temp"] - dT
    dew = K_DEW * np.maximum(0.0, f["dew"] - ts) * porous
    fog = K_FOG * local_fog * (0.5 + wind / 5.0) * fog_gain * porous
    rain = K_RAIN * (1 - np.exp(-np.maximum(f["precip"], 0.0) / RAIN_SAT)) * porous * np.ones_like(W)
    rh = magnus_rh(f["temp"], f["dew"])
    evap = (K_EVAP_BASE + K_EVAP_WIND * wind) * np.maximum(0.0, 1 - rh)
    if "vpd_evap" in physics:   # guide 10 A: vapour pressure deficit instead of RH, equal to the RH form at 12 degC
        evap = (K_EVAP_BASE + K_EVAP_WIND * wind) * np.maximum(sat_vp_kpa(f["temp"]) - sat_vp_kpa(f["dew"]), 0.0) / VPD_REF_KPA
    sw = np.maximum(f["sw"], 0.0) / 1000.0
    sun = K_DIFF * sw + (K_SUN * sw * lit if use_sun else 0.0)
    if "sun_clear" in physics and "sw_eff" in f:   # guide 06: drying by the street's own sunlight S (direct on lit streets + diffuse), no separate K_DIFF
        sun = K_SUN * np.maximum(f["sw_eff"], 0.0) / 1000.0
    if "drizzle" in physics and "drizzle" in f:   # guide 11 (old W path): the larger of rain and drizzle wetting
        rain = np.maximum(rain, K_DRIZZLE * np.asarray(f["drizzle"], float) * porous)
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
             state0=None, save_at=None, ts0=None):
    """Run one window. pts: dict(x, svf, porous, tracer, wind_ratio, horizon [N,72] or None, band-selected tracer; for the
    'profile' mode also y, z). ser: weather.series / night_series dict (scalar series: coast forcing, fog source, fog top).
    Overrides: tracer / svf arrays (ablations); fog_mode ('band0' | 'profile', default FOG_HEIGHT_MODE); fog_top forces the fog
    top in profile mode; use_dirs: True/False forces the direction library on/off (default DIR_LIBRARY).
    Live-path options (defaults reproduce the replay exactly):
      tracer_dirs  [D, N] precomputed per-direction tracer table (live_static.npz 'tracer'), dirs_deg its directions (default
                   pts['dirs'] or the 9 library directions): no library files are read, the direction blend is used.
      forcing_fn   callable (i, f) -> f: replaces the scalar forcing dict of step i by one whose values may be [N] arrays
                   (spatial forcing interpolated from a lattice); the coast source s(t) stays the scalar series.
      dir_w        (key of the dict forcing_fn returns, guide 03) [D, N] direction weights per street point and step, D = len(tracer_dirs): the fog exposure
                   is sum_d dir_w[d] * E[d] per point instead of the citywide blend of that step; absent = today's path, unchanged.
      lag_block_h  hours; the front lag uses the mean flow direction and wind of each block of that length (default: one value
                   for the whole window).
      out_every    keep W only every k-th step (frames 0, k, 2k, ...): memory for long windows.
      s_series     [n] fog source s(t) used instead of fog_inflow(ser) (live: coast series corrected by GOES / KHAF observations).
      keep_lit     keep the per-step direct-sun mask in diag['lit'] (replay validation needs it; live does not).
      physics      iterable of switch names from KNOWN_PHYSICS (default none = today's model); unknown names raise ValueError.
      state0       dict of arrays (key 'W' [N]) that replaces W0 as the starting state (carried state, guide 02).
      ts0          (switch pavement) start temperature of the road, degC, scalar or [N], when state0 has no ts / tdeep (live: the mean air temperature of the previous
                   24 h, see forcing.build); default the mean air temperature of the first 24 h of ser.
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
    s = fog_inflow(ser["temperature_2m"], ser["dew_point_2m"], ser["cloud_cover_low"], ser["wind_dir"], use_gap="cloud_base" not in P)
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

    uni = "lag_uniform" in P

    def lag_for(mdir_, wmean_):
        if uni:   # guide 09: the front travels with the wind above the roofs, not with each street's sheltered wind
            wmean_ = max(float(wmean_) * LAG_SPEED_FACTOR, WIND_LAG_MIN)
            if dirs is not None:
                return lag_seconds_dir(pts["x"], pts["y"], mdir_, wmean_) / (step_min * 60.0)
            return lag_seconds(pts["x"], wmean_) / (step_min * 60.0)
        if dirs is not None:
            return lag_seconds_dir(pts["x"], pts["y"], mdir_, wmean_ * wr) / (step_min * 60.0)
        return lag_seconds(pts["x"], wmean_ * wr) / (step_min * 60.0)

    blk = max(1, int(round(lag_block_h * 60.0 / step_min))) if lag_block_h else None
    lag_steps = None if blk else lag_for(mdir, wmean)
    expo = fog_exposure(tr)
    az, el = sun_position(ser["t_utc"])
    dt_h = step_min / 60.0
    film, sunc, pavm = "film" in P, "sun_clear" in P, "pavement" in P
    if state0 is not None:
        W = np.asarray(state0["W"], np.float32).copy()
        assert W.shape == (N,), "state0['W'] must have one value per street point"
    else:
        W = np.full(N, float(W0), np.float32)
    state_at = None
    frames = (n + out_every - 1) // out_every
    out = np.zeros((frames, N), np.float32)
    extra_missing = ("slope" not in pts) if (film or sunc) else False   # guide 06 extra table absent: flat asphalt
    if sunc:   # guide 06: clear-sky sun, road normal, cloudiness
        from . import sunclear
        ghi_c, dni_c, _ = sunclear.clear_sky(ser["t_utc"], SUN_LAT, SUN_LON)
        nrm = sunclear.normals(pts.get("bearing", np.zeros(N)), pts.get("slope", np.zeros(N)))
        k_ser = sunclear.k_from_sw(np.asarray(ser["shortwave_radiation"], float), ghi_c, el)
        sun_u8 = np.zeros((frames, N), np.uint8)
        sun_k = np.zeros(frames, np.float32)
    if film:   # guide 05: water on the road, rain tracking
        cap = storage_cap(pts.get("surf", np.zeros(N, np.uint8)), pts.get("slope", np.zeros(N)))
        spb = max(1, int(round(60.0 / step_min)))
        cause_n = max(1, int(round(CAUSE_H * 60.0 / step_min)))
        Fm = np.zeros(N, np.float32)
        hist = np.zeros((24, N), np.uint8)
        hsr = np.full(N, HSR_MAX_H, np.float32)
        rsw = np.zeros(N, np.float32)
        if state0 is not None and "film" in state0:
            Fm = np.asarray(state0["film"], np.float32).copy()
            assert Fm.shape == (N,), "state0['film'] must have one value per street point"
            if "rain_hist" in state0:
                hist = np.asarray(state0["rain_hist"], np.uint8).copy()
            if "hours_since_rain" in state0:
                hsr = np.asarray(state0["hours_since_rain"], np.float32).copy()
            if "rain_since_wash" in state0:
                rsw = np.asarray(state0["rain_since_wash"], np.float32).copy()
        rain_cur = np.zeros(N, np.float32)
        dew_n = np.zeros(N, np.int32)      # steps with dew on the street (compare report: dew frequency)
        film_out = np.zeros((frames, N), np.float16)
        cause_out = np.zeros((frames, N), np.uint8)
        ring = np.zeros((cause_n, 4, N), np.float16)
        wet_bits = []
    if pavm:   # guide 07: surface and deep temperature (degC). Start: carried state, else ts0, else the mean air temperature of the first 24 h of the window
        cd = np.asarray(PAV_CD, float)[np.asarray(pts.get("surf", np.zeros(N, np.uint8)), int)]
        if state0 is not None and "ts" in state0 and "tdeep" in state0:
            ts_s = np.asarray(state0["ts"], np.float32).copy()
            td_s = np.asarray(state0["tdeep"], np.float32).copy()
            assert ts_s.shape == (N,) and td_s.shape == (N,), "state0['ts'] / ['tdeep'] must have one value per street point"
        else:
            t_init = np.mean(np.asarray(ser["temperature_2m"], float)[:int(round(24 * 60 / step_min))]) if ts0 is None else ts0
            ts_s = np.full(N, 1.0, np.float32) * np.asarray(t_init, np.float32)
            td_s = ts_s.copy()
        ts_out = np.zeros((frames, N), np.uint8)
    lit_all = np.zeros((n, N), bool) if keep_lit else None
    dep = dict(fog=np.zeros(N), dew=np.zeros(N), rain=np.zeros(N), evap=np.zeros(N))
    if film:
        dep.update(sun=np.zeros(N), runoff=np.zeros(N))
    hor = pts.get("horizon")
    block_dirs = []
    in_cloud = np.zeros(N)
    drz_tot = np.zeros(N)
    for i in range(n):
        if blk and i % blk == 0:  # time-varying lag: mean flow of this block
            sl = slice(i, min(n, i + blk))
            mb = mean_flow_dir(s[sl], wdir[sl])
            lag_steps = lag_for(mb, float(np.mean(wspd[sl])))
            block_dirs.append(mb)
        f = dict(temp=ser["temperature_2m"][i], dew=ser["dew_point_2m"][i], wind=ser["wind_speed_10m"][i],
                 cloud_low=ser["cloud_cover_low"][i], precip=ser["precipitation"][i], sw=ser["shortwave_radiation"][i])
        if (pavm or "total_cloud" in P) and "cloud_cover" in ser:
            f["cloud"] = ser["cloud_cover"][i]
        if forcing_fn is not None:
            f = forcing_fn(i, f)
        if "drizzle" in P and "drizzle" in f:   # guide 11: wetting is the larger of rain and drizzle, never the sum
            drz_mm = f["drizzle"]
            if film:
                f = dict(f, precip=np.maximum(f["precip"], drz_mm))
            drz_tot += np.asarray(drz_mm) * dt_h
        dw = f.get("dir_w") if E is not None else None   # guide 03: per-point direction weights [D, N] (sum to 1 over D) replace the single citywide blend
        local = lagged(s, i - lag_steps) * (expo if E is None else (Wt[i] @ E if dw is None else (np.asarray(dw) * E).sum(axis=0)))
        if prof:  # street counts as inside the fog only while the (lagged) fog top is above it
            local = local * fogtop.fog_top_ramp(lagged(top, i - lag_steps), pts["z"])
            if "cloud_base" in P and "cloud_base" in f:   # guide 08: streets below the cloud base are under the cloud, not in it
                wb = fogtop.cloud_base_ramp(f["cloud_base"], pts["z"])
                local = local * wb
                in_cloud += wb * dt_h
        lit = np.zeros(N, bool)
        if hor is not None and el[i] > 0:
            from .sun import sunlit
            lit = sunlit(hor, az[i], el[i])
        if sunc:   # street sunlight S (W/m2): clear-sky beam on the tilted road where lit + diffuse by sky view, scaled by the cloudiness k
            S = sunclear.street_irradiance(f["k"] if "k" in f else k_ser[i], ghi_c[i], dni_c[i], sunclear.cos_incidence(nrm, az[i], el[i]),
                                           lit if use_sun else np.zeros(N, bool), sv, el[i])
            f = dict(f, sw_eff=S)
        elif film:
            swp = np.maximum(f["sw"], 0.0)
            f = dict(f, sw_eff=swp * lit + 0.125 * swp if use_sun else 0.125 * swp)   # as today's drying: direct on lit streets + K_DIFF / K_SUN of the shortwave everywhere
        if save_at is not None and i == save_at:  # state at the START of step i = initial state of a run that begins at that time
            if film:
                state_at = dict(W=slip_of(Fm), film=Fm.copy(), rain_hist=hist.copy(), hours_since_rain=hsr.copy(), rain_since_wash=rsw.copy())
                if pavm:
                    state_at.update(ts=ts_s.copy(), tdeep=td_s.copy())
            else:
                state_at = dict(W=W.copy())
        if film:
            Fm, parts = step_film(Fm, sv, porous, f, local, cap, dt_h, wr, pav=dict(ts=ts_s, tdeep=td_s, cd=cd) if pavm else None)
            dew_n += parts["dew"] > 1e-9
            if pavm:
                ts_s, td_s = parts["ts"], parts["tdeep"]
            r = np.maximum(np.asarray(f["precip"], np.float32), 0.0) * np.ones(N, np.float32)
            hsr = np.minimum(np.where(r >= RAIN_WET_MM_H, 0.0, hsr + dt_h), HSR_MAX_H).astype(np.float32)
            rain_cur += r * dt_h
            if (i + 1) % spb == 0:   # an hour is complete: push it into the 24 h history; a washing hour resets rain_since_wash
                hist = np.concatenate([hist[1:], np.clip(np.rint(rain_cur / RAIN_Q_MM), 0, 255).astype(np.uint8)[None]])
                rsw = np.where(rain_cur >= WASH_MM_H, 0.0, np.minimum(rsw + rain_cur, RSW_MAX_MM)).astype(np.float32)
                rain_cur = np.zeros(N, np.float32)
            drz = r < DRIZZLE_MM_H
            ring[i % cause_n] = np.stack([parts["fog"], parts["dew"], np.where(drz, parts["rain"], 0.0), np.where(drz, 0.0, parts["rain"])]).astype(np.float16)
            wet_bits.append(np.packbits(Fm >= DRY_MM))
            if i % out_every == 0:
                fi = i // out_every
                out[fi] = slip_of(Fm)
                film_out[fi] = Fm
                if pavm:
                    ts_out[fi] = np.clip(np.rint((ts_s + TS_U8_OFFSET_C) * TS_U8_PER_K), 0, 255)
                tot = np.stack([ring[:, s].sum(axis=0, dtype=np.float32) for s in range(4)])
                cause_out[fi] = np.where(tot.max(axis=0) > 0, tot.argmax(axis=0) + 1, 0)
        else:
            W, parts = step(W, sv, porous, f, local, lit, dt_h, wr, use_sun, 1.0 if fog_gain is None else fog_gain, P)
            if i % out_every == 0:
                out[i // out_every] = W
        if sunc and i % out_every == 0:
            sun_u8[i // out_every] = np.clip(np.rint(S / 10.0), 0, 250)
            sun_k[i // out_every] = float(np.mean(f["k"] if "k" in f else k_ser[i]))
        if keep_lit:
            lit_all[i] = lit
        for k in dep:
            dep[k] += parts[k]
    extra = {}
    if sunc:
        extra.update(sun_u8=sun_u8, sun_k=sun_k, extra_missing=extra_missing)
    if film:
        nd = np.full(N, n, np.int32)   # dry-in: for every output frame the first step from then on at which the street is dry (read from the simulated steps)
        dry_out = np.zeros((frames, N), np.uint8)
        for j in range(n - 1, -1, -1):
            nd = np.where(~np.unpackbits(wet_bits[j], count=N).astype(bool), j, nd)
            if j % out_every == 0:
                dry_out[j // out_every] = np.where(nd >= n, 255, np.minimum(np.ceil((nd - j) * dt_h - 1e-9), 254))
        extra.update(film_mm=film_out, dry_in_h=dry_out, cause=cause_out, extra_missing=extra_missing, dew_steps=dew_n, n_steps=n,
                     rain_track=dict(rain_mm_24h=hist.sum(axis=0, dtype=np.float32) * RAIN_Q_MM + rain_cur, hours_since_rain=hsr, rain_since_wash=rsw))
    if pavm:
        extra.update(ts_u8=ts_out, ts_end=ts_s, tdeep_end=td_s)
    if "cloud_base" in P:
        extra["in_cloud_hours"] = in_cloud
    if "drizzle" in P:
        extra["drizzle_total"] = drz_tot
    diag = dict(dirs_used=(None if dirs is None else dirs.tolist()), mean_flow_dir=mdir, sun_az=az, sun_el=el, s=s, lit=lit_all, lag_steps=lag_steps, fog_mode=mode, fog_top=top, tracer=np.asarray(tr, np.float32),
                out_every=out_every, block_dirs=block_dirs, physics=sorted(P), state_at=state_at, **extra, **{k + "_total": v for k, v in dep.items()})
    return out, diag
