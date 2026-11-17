"""Sky layers (guide 13): up to three cloud layers per hour from the model humidity profile, airport reports and the satellite top.
Pure functions, no network and no files, so tests and the offline check (tools/eval_sky_layers.py) can use them.
Heights are metres above mean sea level unless a name says agl. A layer is a dict(base, top, cov, src); cov is 0..1; src names what made it
('model', 'model+metar', 'metar', 'sat').
Layer 0 (the fog layer) is not made here: runs that touch the lowest level, or lie below `floor_m`, belong to it and are skipped."""
from __future__ import annotations

import math

SKY_RH_THRESHOLD = 92.0     # percent: a level is saturated enough for cloud at or above this (first guess, plan 2026-11-16)
SKY_MIN_THICK_M = 60.0      # a run thinner than this is dropped
SKY_THIN_M = 150.0          # thickness of a deck seen only by the satellite (first guess)
SKY_MAX_LAYERS = 3
SKY_METAR_NUDGE = 0.5       # weight of an airport base against the model base (0 = ignore the airport, 1 = override) - weak evidence first
SKY_MATCH_M = 600.0         # an airport layer matches a model run when the bases are within this many metres
SKY_SAT_NUDGE = 0.6         # weight of the satellite top for the highest layer when the satellite is confident
SKY_SAT_CONF = 0.5          # the satellite top counts above this confidence
SKY_CIRRUS_M = 6000.0       # tops at or above this are high cloud (cirrus): not drawn in version 1
SKY_DEFAULT_TOP_M = 500.0   # default cloud height, shared with the macro surface (macro option 2)

COVER = {"SKC": 0.0, "CLR": 0.0, "NSC": 0.0, "NCD": 0.0, "FEW": 0.19, "SCT": 0.44, "BKN": 0.75, "OVC": 1.0, "VV": 1.0}
FT = 0.3048


def cloud_height_m(sat_top, fused_top, default=SKY_DEFAULT_TOP_M):
    """The easy height rule shared with macro option 2: the satellite top where it exists, else the fused fog top, else the default."""
    for v in (sat_top, fused_top):
        if v is not None and isinstance(v, (int, float)) and math.isfinite(v) and v > 0:
            return float(v)
    return float(default)


def _cross(z0, v0, z1, v1, thr):
    """height where a linear profile crosses thr between two levels"""
    if v1 == v0:
        return z0
    return z0 + (thr - v0) / (v1 - v0) * (z1 - z0)


def humidity_runs(z_m, rh, thr=SKY_RH_THRESHOLD, min_thick=SKY_MIN_THICK_M, floor_m=0.0):
    """Saturated runs of a profile. z_m: heights (m, any order), rh: relative humidity (%) at those heights (None or NaN skipped).
    Returns [(base, top)] from the lowest; edges are interpolated to where rh crosses thr. A run at the lowest level starts at that level.
    Runs thinner than min_thick, or ending at or below floor_m (the fog layer's territory), are dropped."""
    pts = sorted((float(z), float(r)) for z, r in zip(z_m, rh) if z is not None and r is not None and math.isfinite(z) and math.isfinite(r))
    runs, cur = [], None
    for i, (z, r) in enumerate(pts):
        if r >= thr:
            if cur is None:
                if i == 0:
                    cur = [z, z]
                else:
                    cur = [_cross(pts[i - 1][0], pts[i - 1][1], z, r, thr), z]
            cur[1] = z
        elif cur is not None:
            cur[1] = _cross(pts[i - 1][0], pts[i - 1][1], z, r, thr)
            runs.append(tuple(cur))
            cur = None
    if cur is not None:
        runs.append(tuple(cur))
    return [(b, t) for b, t in runs if t - b >= min_thick and t > floor_m]


def metar_layers(clouds, elev_m=0.0):
    """aviationweather `clouds` [{cover, base (ft agl)}] -> [dict(base (m msl), cov)], lowest first, clear entries dropped"""
    out = []
    for c in clouds or []:
        cov = COVER.get(str(c.get("cover", "")).upper())
        b = c.get("base")
        if not cov or b is None:
            continue
        out.append(dict(base=float(b) * FT + float(elev_m), cov=cov))
    return sorted(out, key=lambda d: d["base"])


def build_table(runs, airports=None, sat_top=None, sat_conf=None, floor_m=0.0, thin_m=SKY_THIN_M):
    """Layer table for one hour at one place.
    runs: humidity_runs(...) result. airports: list of metar_layers(...) results (one list per airport; the caller leaves one out when checking).
    Each model run takes the median base of the airport layers within SKY_MATCH_M and moves SKY_METAR_NUDGE of the way to it; an airport layer
    that matches no run is added (src 'metar') when its coverage is at least 0.44 (SCT), as a slab of `thin_m`.
    The highest layer's top moves SKY_SAT_NUDGE of the way to the satellite top when the satellite is confident.
    Layers lie above `floor_m` and the result keeps SKY_MAX_LAYERS of them, lowest first."""
    layers = [dict(base=max(b, floor_m), top=max(t, b + 1.0), cov=1.0, src="model") for b, t in runs if t > floor_m]
    pool = [l for a in (airports or []) for l in a if l["base"] > floor_m]
    used = set()
    for L in layers:
        near = [i for i, p in enumerate(pool) if abs(p["base"] - L["base"]) <= SKY_MATCH_M]
        if near:
            bases = sorted(pool[i]["base"] for i in near)
            med = bases[len(bases) // 2]
            covs = [pool[i]["cov"] for i in near]
            L["base"] = L["base"] + SKY_METAR_NUDGE * (med - L["base"])
            L["top"] = max(L["top"], L["base"] + SKY_MIN_THICK_M)
            L["cov"] = sum(covs) / len(covs)
            L["src"] = "model+metar"
            used.update(near)
    extra = {}
    for i, p in enumerate(pool):
        if i not in used and p["cov"] >= COVER["SCT"]:
            extra.setdefault(round(p["base"] / 100), []).append(p)
    for ps in extra.values():
        b = sorted(p["base"] for p in ps)[len(ps) // 2]
        layers.append(dict(base=b, top=b + thin_m, cov=sum(p["cov"] for p in ps) / len(ps), src="metar"))
    layers.sort(key=lambda d: d["base"])
    if layers and sat_top is not None and sat_conf is not None and sat_conf >= SKY_SAT_CONF and sat_top > floor_m:
        L = layers[-1]
        L["top"] = max(L["base"] + SKY_MIN_THICK_M, L["top"] + SKY_SAT_NUDGE * (sat_top - L["top"]))
    return layers[:SKY_MAX_LAYERS]


def thin_deck(sat_top, sat_prob, sat_conf, floor_m, thin_m=SKY_THIN_M, min_prob=0.5, min_conf=SKY_SAT_CONF):
    """A deck the model does not explain, seen by the satellite with decent confidence and a low top: top = satellite height,
    base = top - thin_m, never below `floor_m` (the cloud floor of the fog layer) and never the ground. None where the satellite is not sure
    or the top is cirrus."""
    if sat_top is None or sat_prob is None or sat_conf is None:
        return None
    if sat_prob < min_prob or sat_conf < min_conf or sat_top >= SKY_CIRRUS_M or sat_top <= floor_m:
        return None
    base = max(sat_top - thin_m, floor_m)
    return dict(base=base, top=max(sat_top, base + 1.0), cov=min(1.0, sat_prob), src="sat")
