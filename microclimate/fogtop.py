"""Fog height versus street elevation: fog-top estimate, vertical tracer sampling and the elevation ramp.

Everything here is a PROXY with hand-set constants (see the top of wetness.py); nothing is calibrated.

 * estimate_fog_top(ser): fog-layer top (m above sea level) per time step from Open-Meteo pressure-level data:
     top_RH  = height where the relative humidity profile (2 m, 1000, 975, 950, 925, 900 hPa) first drops below RH_CLOUD
               (linear interpolation between levels): the top of the saturated marine layer / stratus.
     top_inv = height of the level below the first temperature inversion (T rises with height, from the 1000 hPa level
               upward; the nocturnal surface inversion below 1000 hPa is ignored): the marine inversion base.
     top     = max(floor, min(top_RH, top_inv)),  floor = grid elevation + FOG_TOP_MIN_M.
   Without the profile variables a constant FOG_TOP_FALLBACK_M is used (and flagged in the info dict).
   boundary_layer_height, freezing level and LCL were examined and are NOT used: at night the model PBL height is the
   (shallow) mixed layer, not the marine layer depth, and the LCL (125 m x (T-Td)) is a cloud BASE.
 * fog_top_ramp(top, z): smooth street-wetting weight, 1 below the top, 0 above, smoothstep over +/- FOG_TOP_RAMP_M.
 * sample_bands(x, y, z_target): the four band tracer fields (library/fog_2700_<b>.npz) bilinearly in x,y and linearly
   in z at the target height (above sea level), for ANY point set (columns whose target cell is solid use their first
   fluid cell). blend_bands(bands, z) interpolates the four bands vertically by band centre (no hard band selection).
"""
from pathlib import Path

import numpy as np

_CACHE = {}


def _w():
    from . import wetness
    return wetness


def smoothstep(u):
    u = np.clip(np.asarray(u, float), 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


def fog_top_ramp(top, z, half_width=None):
    """Weight in [0,1]: 1 when the fog top is above the street by more than half_width, 0 when below by more than
    half_width, smoothstep in between. Non-increasing in z, non-decreasing in top. Broadcasts."""
    hw = _w().FOG_TOP_RAMP_M if half_width is None else half_width
    return smoothstep((np.asarray(top, float) - np.asarray(z, float)) / (2.0 * hw) + 0.5)


def blend_bands(bands, z_target, centres=None):
    """bands [N,4] (tracer per source band) -> [N]; linear interpolation in height between the band centres, clamped
    to the first/last band outside the range. z_target [N] or scalar, above sea level."""
    centres = np.asarray(_w().BAND_CENTRES_M if centres is None else centres, float)
    b = np.asarray(bands, np.float32)
    z = np.broadcast_to(np.asarray(z_target, float), (b.shape[0],))
    j = np.clip(np.searchsorted(centres, z, side="right") - 1, 0, len(centres) - 2)
    t = np.clip((z - centres[j]) / (centres[j + 1] - centres[j]), 0.0, 1.0).astype(np.float32)
    lo = np.take_along_axis(b, j[:, None], 1)[:, 0]
    hi = np.take_along_axis(b, (j + 1)[:, None], 1)[:, 0]
    return (lo * (1 - t) + hi * t).astype(np.float32)


def _library(data_dir):
    key = str(data_dir)
    if key not in _CACHE:
        D = Path(data_dir)
        solid = np.load(D / "grids" / "solid_20m.npz")["solid"]
        nz = solid.shape[2]
        any_s = solid.any(axis=2)
        last = nz - 1 - np.argmax(solid[:, :, ::-1], axis=2)
        kf = np.where(any_s, last + 1, 0).astype(np.int32)
        fields = [np.load(D / "library" / f"fog_2700_{b}.npz")["c"] for b in range(4)]
        _CACHE[key] = (kf, fields)
    return _CACHE[key]


def sample_bands(x, y, z_target, data_dir=None, x0=540100.0, y0=4171500.0, cell=20.0):
    """Tracer of each of the 4 source bands at (x, y, z_target), z_target metres above sea level. Returns [N,4] float32.
    Bilinear over the 4 surrounding 20 m columns; in each column the height is raised to the first fluid cell if the
    target is inside the solid, then interpolated linearly between cell centres (k + 0.5) * cell."""
    data_dir = _w().DATA_DIR if data_dir is None else data_dir
    data_dir = Path.home() / "deathfog-data" if data_dir is None else data_dir
    kf, fields = _library(data_dir)
    nx, ny, nz = fields[0].shape
    x, y = np.asarray(x, float), np.asarray(y, float)
    zt = np.broadcast_to(np.asarray(z_target, float), x.shape)
    fx, fy = (x - x0) / cell - 0.5, (y - y0) / cell - 0.5
    i0, j0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
    wx, wy = fx - i0, fy - j0
    out = np.zeros((len(x), 4), np.float32)
    for di, dj, w in ((0, 0, (1 - wx) * (1 - wy)), (1, 0, wx * (1 - wy)), (0, 1, (1 - wx) * wy), (1, 1, wx * wy)):
        i, j = np.clip(i0 + di, 0, nx - 1), np.clip(j0 + dj, 0, ny - 1)
        zc = np.maximum(zt, (kf[i, j] + 0.5) * cell)
        fk = np.clip(zc / cell - 0.5, 0, nz - 1)
        k0 = np.floor(fk).astype(int)
        k1 = np.minimum(k0 + 1, nz - 1)
        wz = (fk - k0).astype(np.float32)
        for b in range(4):
            c = fields[b]
            v = c[i, j, k0].astype(np.float32) * (1 - wz) + c[i, j, k1].astype(np.float32) * wz
            out[:, b] += (w * v).astype(np.float32)
    return out


def points_tracer(pts, data_dir=None):
    """Profile-mode tracer for a point table dict with x, y, z (z = street elevation above sea level): bands sampled
    at z + TRACER_Z_OFFSET_M, blended by band centre. Cached in pts['_tracer_profile']."""
    if "_tracer_profile" not in pts:
        zt = np.asarray(pts["z"], float) + _w().TRACER_Z_OFFSET_M
        pts["_tracer_profile"] = blend_bands(sample_bands(pts["x"], pts["y"], zt, data_dir), zt)
    return pts["_tracer_profile"]


def estimate_fog_top(ser):
    """Fog-layer top [n] in metres above sea level for a night series (weather.night_series) and an info dict."""
    w = _w()
    n = len(ser["temperature_2m"])
    elev = float(ser.get("elevation_m", 34.0))
    floor = elev + w.FOG_TOP_MIN_M
    lv = [l for l in (1000, 975, 950, 925, 900, 850)
          if f"temperature_{l}hPa" in ser and f"geopotential_height_{l}hPa" in ser]
    have = len(lv) >= 3 and all(np.isfinite(ser[f"temperature_{l}hPa"]).all() for l in lv)
    if not have:
        return np.full(n, max(w.FOG_TOP_FALLBACK_M, floor)), dict(source="fallback_constant", floor=floor)
    T2, Td2 = np.asarray(ser["temperature_2m"], float), np.asarray(ser["dew_point_2m"], float)
    rh_sfc = 100.0 * w.magnus_rh(T2, Td2)
    zl = np.stack([np.asarray(ser[f"geopotential_height_{l}hPa"], float) for l in lv], 1)
    tl = np.stack([np.asarray(ser[f"temperature_{l}hPa"], float) for l in lv], 1)
    rl_lv = [l for l in lv if f"relative_humidity_{l}hPa" in ser]
    rl = np.stack([np.asarray(ser[f"relative_humidity_{l}hPa"], float) for l in rl_lv], 1)
    zr = np.stack([np.asarray(ser[f"geopotential_height_{l}hPa"], float) for l in rl_lv], 1)
    top_rh, top_inv = np.full(n, np.inf), np.full(n, np.inf)
    for i in range(n):
        zz = np.concatenate([[elev], zr[i]])
        rr = np.concatenate([[rh_sfc[i]], rl[i]])
        below = np.nonzero(rr < w.RH_CLOUD)[0]
        if len(below) == 0:
            top_rh[i] = zz[-1] + w.FOG_TOP_ABOVE_LAST_M  # saturated through the top level
        elif below[0] == 0:
            top_rh[i] = elev                               # grid-cell surface not saturated
        else:
            j = below[0]
            f = (rr[j - 1] - w.RH_CLOUD) / max(rr[j - 1] - rr[j], 1e-6)
            top_rh[i] = zz[j - 1] + f * (zz[j] - zz[j - 1])
        dT = np.diff(tl[i])
        up = np.nonzero(dT > w.INV_DT)[0]
        if len(up):
            top_inv[i] = zl[i, up[0]]
    top = np.maximum(np.minimum(top_rh, top_inv), floor)
    top = np.minimum(top, w.FOG_TOP_MAX_M)
    return top, dict(source="profile", floor=floor, top_rh=top_rh, top_inv=top_inv)
