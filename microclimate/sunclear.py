"""Clear-sky sun for a street (guide 06, switch `sun_clear`).

The sun a street can get with no cloud is known exactly: sun position, skyline shadow (horizon profile), road slope. That is the maximum:

    S_max = dni_clear * cos_inc * lit + dhi_clear * svf

Cloudiness k (0.05 .. 1; 1 = clear) scales it down. Under cloud the direct beam goes first:

    direct  = dni_clear * max((k - K_BEAM_OFF) / (1 - K_BEAM_OFF), 0)
    diffuse = max(k * ghi_clear - direct * sin(el), 0)
    S       = direct * cos_inc * lit + diffuse * svf

Road normal from the bearing b (deg, along-street direction, 0..180) and the slope s (rise over run ALONG b): n = (-s sin b, -s cos b, 1) / sqrt(1 + s^2)
in (east, north, up); sun vector (cos el sin az, cos el cos az, sin el); cos_inc = max(n . sun, 0).
Pure numpy except clear_sky (pvlib, Ineichen). No state, no I/O.
"""
import numpy as np

from . import wetness


def clear_sky(times_utc, lat, lon):
    """-> (ghi, dni, dhi) clear-sky irradiance in W/m2 (float64 arrays over times) from pvlib's Ineichen model for the city centre."""
    import pandas as pd
    import pvlib
    loc = pvlib.location.Location(lat, lon, tz="UTC")
    cs = loc.get_clearsky(pd.DatetimeIndex(times_utc), model="ineichen")
    return tuple(np.maximum(cs[k].to_numpy(float), 0.0) for k in ("ghi", "dni", "dhi"))


def k_from_sw(sw, ghi_clear, el):
    """Cloudiness k = clip(SW / ghi_clear, K_MIN, 1) where the sun is more than SUN_MIN_EL degrees up; elsewhere the last such value is held
    (before the first one: the first one; with none: 1). Arrays broadcast on the last axis (time)."""
    sw, g, el = np.broadcast_arrays(np.asarray(sw, float), np.asarray(ghi_clear, float), np.asarray(el, float))
    ok = (el > wetness.SUN_MIN_EL) & (g > 1.0)
    k = np.clip(sw / np.maximum(g, 1.0), wetness.K_MIN, 1.0)
    n = k.shape[-1]
    idx = np.where(ok, np.arange(n), -1)
    idx = np.maximum.accumulate(idx, axis=-1)
    first = np.where(ok.any(axis=-1), np.argmax(ok, axis=-1), -1)[..., None]
    idx = np.where(idx < 0, first, idx)
    out = np.take_along_axis(k, np.maximum(idx, 0), axis=-1)
    return np.where(idx < 0, 1.0, out)


def normals(bearing_deg, slope):
    """Unit road normals (nx, ny, nz) float32 [N] from bearing (deg) and slope (rise/run along the bearing; scalar or [N])."""
    b = np.radians(np.asarray(bearing_deg, float))
    s = np.clip(np.asarray(slope, float), -wetness.SLOPE_CLIP, wetness.SLOPE_CLIP) * np.ones_like(b)
    nrm = np.sqrt(1.0 + s * s)
    return ((-s * np.sin(b) / nrm).astype(np.float32), (-s * np.cos(b) / nrm).astype(np.float32), (1.0 / nrm).astype(np.float32))


def cos_incidence(nrm, az_deg, el_deg):
    """cos of the angle between the road normal and the sun, floored at 0. nrm from normals(); az / el scalars (deg)."""
    a, e = np.radians(float(az_deg)), np.radians(float(el_deg))
    sx, sy, sz = np.cos(e) * np.sin(a), np.cos(e) * np.cos(a), np.sin(e)
    return np.maximum(nrm[0] * sx + nrm[1] * sy + nrm[2] * sz, 0.0)


def street_irradiance(k, ghi_c, dni_c, cos_inc, lit, svf, el_deg):
    """S in W/m2 per street point at one instant. k scalar or [N]; ghi_c, dni_c scalars; cos_inc, svf [N]; lit bool [N]. Zero when the sun is down."""
    if el_deg <= 0.0:
        return np.zeros(np.shape(cos_inc), np.float32)
    k = np.clip(k, wetness.K_MIN, 1.0)
    direct = dni_c * np.maximum((k - wetness.K_BEAM_OFF) / (1.0 - wetness.K_BEAM_OFF), 0.0)
    diffuse = np.maximum(k * ghi_c - direct * np.sin(np.radians(el_deg)), 0.0)
    return (direct * cos_inc * lit + diffuse * svf).astype(np.float32)
