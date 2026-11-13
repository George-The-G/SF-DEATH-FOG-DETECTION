"""East wind directions in the live build (guide 14, switch config.WIND_EAST / config.BETA["wind_east"]).

The library holds validated wind fields for 22.5, 45, 67.5, 90, 112.5, 135 (east) next to the 9 west directions 180 .. 360. 157.5 failed twice
(solver NaN) and is an ACCEPTED GAP: blends between 135 and 180 are linear in angle over the 45 degree gap (dirlib.weights). 360 = 0 wraps to 22.5.

What the switch changes: the wind vectors at the in-grid stations (live_east.npz, built by `python -m microclimate.live stationwind --east`) cover the 15 directions, so the
station-anchor wind fit (anchors.py, C.NUDGE_ANCHORS) can represent winds from 0 .. 180 instead of clamping them to 180 / 360. That is STREET WIND ONLY.

What it does not change: the fog (tracer) tables. The east directions get all-zero tracer rows (and zero 80 m tiles) here, so the fog exposure blended in at east wind is zero, and
wetness.onshore_weight already gives zero advected-fog inflow for wind from 0 .. 180. East / offshore wind therefore adds no fog (guide 12, in-place fog, is the later work).
The live_static.npz asset is not touched: the public build keeps working with the old asset; live_east.npz is an extra asset that only this module reads.
"""
import numpy as np


def apply(S, path):
    """S: static table (city.load_static). -> (S2, info). S2 is a shallow copy with the 15 directions (dirs, tracer, tr80 extended with zero rows for the east ones) or None when
    live_east.npz is missing, unreadable or not for this static table; info says which (info["applied"], info["reason"])."""
    info = dict(applied=False, file=str(getattr(path, "name", path)), reason=None)
    try:
        z = np.load(path, allow_pickle=False)
        dirs = np.asarray(z["dirs"], np.float64)
        base = np.asarray(z["static_dirs"], np.float64)
        ids = [str(i) for i in z["ids"]]
    except Exception as e:  # noqa: BLE001
        info["reason"] = f"{type(e).__name__}: {str(e)[:100]}"
        return None, info
    sd = np.asarray(S["dirs"], np.float64)
    if len(base) != len(sd) or not np.allclose(base, sd):
        info["reason"] = f"live_east.npz is for static directions {base.tolist()}, the table has {sd.tolist()}"
        return None, info
    if np.any(np.diff(dirs) <= 0) or not set(sd.tolist()) <= set(dirs.tolist()):
        info["reason"] = "live_east.npz directions are not ascending or miss static ones"
        return None, info
    pos = [int(np.argmin(np.abs(dirs - d))) for d in sd]
    tracer = np.zeros((len(dirs),) + S["tracer"].shape[1:], S["tracer"].dtype)
    tr80 = np.zeros((len(dirs),) + S["tr80"].shape[1:], S["tr80"].dtype)
    for k, p in enumerate(pos):
        tracer[p] = S["tracer"][k]
        tr80[p] = S["tr80"][k]
    S2 = dict(S)
    S2.update(dirs=dirs.astype(np.asarray(S["dirs"]).dtype), tracer=tracer, tr80=tr80)
    east = [float(d) for d in dirs if d < 180.0]
    info.update(applied=True, reason=None, directions=[float(d) for d in dirs], east_directions=east, gap_directions=[157.5], stations=len(ids),
                fog="east tracer rows are zero (street wind only)")
    return S2, info
