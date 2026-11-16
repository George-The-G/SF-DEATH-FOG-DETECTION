"""Guide 05 (switches config.FOG_BASE / config.URBAN_LIFT, beta only): a physical floor for the 3D fog files.

Part A (fog_base): the cloud base on the 80 m tile grid, base = zm + LCL_M_PER_K * max(T - Td, 0) + cb_off (all m above sea level; zm = model terrain at the
tile, as in forcing.py), and weight3d[k] = fogtop.cloud_base_ramp(base, z_k): fog only where the air is saturated. A thickness guard keeps a layer that
the data says is strongly fed from vanishing on small base errors.
Part B (urban_lift): base += LCL_M_PER_K * dT_u over built-up ground, dT_u = DT_URBAN_K * urban * heat * vent * (1 - SUPPLY_DAMP * amp), capped.
All urban constants are FIRST GUESSES with no measurement behind them. Street wetness never sees this lifted base."""
import numpy as np

from .. import fogtop, wetness

FOG_MIN_THICK_M = 30.0   # m; a column with amp > AMP_STRONG keeps at least this much fog below its top (first guess)
AMP_STRONG = 0.5         # amp (source * satellite factor) above which the thickness guard acts
DT_URBAN_K = 1.0         # K; extra near-ground warmth of fully built-up ground with hot pavement and calm air (first guess)
SUPPLY_DAMP = 0.7        # thick, well-fed fog cools the surface and shrinks the lift (first guess)
DT_CAP_K = 1.5           # K; cap of dT_u
VENT_U0 = 3.0            # m/s; vent = 1 / (1 + U / VENT_U0): wind mixes the warm layer away (first guess)
HEAT_K = 10.0            # K of (road surface - air) that counts as heat = 1
SUN_REF_WM2 = 600.0      # W/m2 of mean street sunlight that counts as heat = 1 when there is no pavement model


def tile_table(lat_t, xs, ys, xy_to_ll):
    """Bilinear lattice indices [H*W, 4] and weights for the tile centres (xs: columns, ys: rows, UTM 10N)."""
    X, Y = np.meshgrid(xs, ys)
    return lat_t.weights(*xy_to_ll(lat_t, X.ravel(), Y.ravel()))


def _gat(a, idx, w):
    return (np.asarray(a, np.float32)[idx] * w.astype(np.float32)).sum(axis=1)


def base_field(lb, i, tbl, shape):
    """float32 [H, W] cloud base (m above sea level) at model step i from lb = dict(T, Td [lattice, steps], zm [lattice], off [steps])."""
    idx, w = tbl
    T, Td = _gat(lb["T"][:, i], idx, w), _gat(lb["Td"][:, i], idx, w)
    zm = _gat(lb["zm"], idx, w)
    return (zm + wetness.LCL_M_PER_K * np.maximum(T - Td, 0.0) + float(lb["off"][i])).astype(np.float32).reshape(shape)


def vent(u):
    return 1.0 / (1.0 + np.maximum(np.asarray(u, float), 0.0) / VENT_U0)


def lift_dT(urban_u8, heat, vent_, amp):
    """K of near-ground warming [H, W]: 0 where urban == 0 or heat == 0, never above DT_CAP_K; never decreases with urban, heat, calm or weaker fog supply."""
    d = DT_URBAN_K * (np.asarray(urban_u8, np.float32) / 255.0) * float(np.clip(heat, 0.0, 1.0)) * float(vent_) * (1.0 - SUPPLY_DAMP * np.clip(amp, 0.0, 1.0))
    return np.clip(d, 0.0, DT_CAP_K).astype(np.float32)


def guard(base, top_m, amp):
    """Where amp > AMP_STRONG the base is at most top - FOG_MIN_THICK_M."""
    return np.where(np.asarray(amp) > AMP_STRONG, np.minimum(base, float(top_m) - FOG_MIN_THICK_M), base).astype(np.float32)


def weight3d(base, z_m, fluid):
    """[24, H, W] in-cloud weight of every level under the base; zero where the cell is solid."""
    w = fogtop.cloud_base_ramp(np.asarray(base, np.float32)[None], np.asarray(z_m, np.float32)[:, None, None]).astype(np.float32)
    return w * (np.asarray(fluid) > 0)


def floor_weight_2d(base, ground_m, street_z=6.0):
    """[H, W] in-cloud weight of the street (ground + street_z, m above sea level) under the cloud base: the 2D tile is multiplied by this."""
    return fogtop.cloud_base_ramp(np.asarray(base, np.float32), np.asarray(ground_m, np.float32) + float(street_z)).astype(np.float32)


class FogBase:
    """Per run: the tile table, the heat series and the per-frame base. frame(f, amp, top) -> (weight3d, stats)."""

    def __init__(self, lb, tbl, shape, T, urban_on, heat, wind, spf, info):
        self.lb, self.tbl, self.shape, self.T, self.urban_on, self.heat, self.wind, self.spf, self.info = lb, tbl, shape, T, urban_on, heat, wind, spf, info

    def frame(self, f, amp, top_m):
        i = min(f * self.spf, len(self.lb["off"]) - 1)
        base = base_field(self.lb, i, self.tbl, self.shape)
        lift = np.zeros(self.shape, np.float32)
        if self.urban_on:
            lift = wetness.LCL_M_PER_K * lift_dT(self.T["urban"], self.heat[f], vent(self.wind[min(i, len(self.wind) - 1)]), amp)
            base = base + lift
        base = guard(base, top_m, amp)
        land = np.asarray(self.T["ground_m"]) > 1.0
        urb = np.asarray(self.T["urban"]) > 128
        stats = dict(base_m=float(np.median(base[land])) if land.any() else None, lift_m=float(lift[urb].mean()) if urb.any() else 0.0, base=base)
        return weight3d(base, self.T["z_m"], self.T["fluid"]), stats


def prepare(F, diag, T, shape, tile_xy, spf, urban_on, xy_to_ll, nF):
    """-> (FogBase | None, info). None (with info["reason"]) when the cloud base pieces are missing (cloud_base physics off)."""
    info = dict(applied=False, urban_lift=bool(urban_on), reason=None)
    lb = F.get("base_lat")
    if not lb:
        info["reason"] = "cloud_base physics is off: no lattice cloud base to start from"
        return None, info
    lat_t = F.get("lattice")
    xs, ys = tile_xy
    tbl = tile_table(lat_t, xs, ys, xy_to_ll)
    heat, wind = None, np.asarray(F["ser"]["wind_speed_10m"], float)
    if urban_on:
        heat, hinfo = heat_series(F, diag, spf, nF)
        info["heat"] = hinfo
    info["applied"] = True
    return FogBase(lb, tbl, shape, T, urban_on, heat, wind, spf, info), info


def heat_series(F, diag, spf, nF):
    """Per output frame in 0..1 (scalar over the city). Pavement on: mean (road surface - air) / HEAT_K; else mean street sunlight / SUN_REF_WM2 (sun_clear); else 0."""
    ts, sun = diag.get("ts_u8"), diag.get("sun_u8")
    if ts is not None:
        h = np.zeros(nF, np.float32)
        for f in range(nF):
            ta = F["forcing_fn"](min(f * spf, len(F["s_series"]) - 1), f)["temp"]
            tsurf = ts[f].astype(np.float32) / wetness.TS_U8_PER_K - wetness.TS_U8_OFFSET_C
            h[f] = float(np.clip(np.mean(tsurf - ta) / HEAT_K, 0.0, 1.0))
        return h, "road surface minus air (pavement)"
    if sun is not None:
        return np.clip(sun.astype(np.float32).mean(axis=1) * 10.0 / SUN_REF_WM2, 0.0, 1.0), "street sunlight (no pavement model)"
    return np.zeros(nF, np.float32), "none (no pavement or sun model): lift is 0"
