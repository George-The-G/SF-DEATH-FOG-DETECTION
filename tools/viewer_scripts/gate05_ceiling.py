"""Guide 05 part A ceiling gate, offline, on the saved Open-Meteo historical RRFS + METAR data of the top-fusion gate (C:\\Users\\George\\AppData\\Local\\Temp\\gate\\data.pkl).
For each airport (KSFO, KOAK, KHAF) and each hour with a reported ceiling at or below 3000 ft: model base at the airport = zm + 125 * max(T - Td, 0) + offset, with T, Td, zm interpolated from the
lattice to the airport and the offset (guide 08 hourly median of obs_base - lcl) computed from the OTHER two airports only (leave-one-airport-out). Compared with the reported base (ceiling + elevation).
This is the same lattice code path as the tile base of guide 05 (fogbase.base_field), evaluated at the airport instead of at a tile. usage: python gate05_ceiling.py REPO"""
import datetime as dt
import pickle
import sys

import numpy as np

sys.path.insert(0, sys.argv[1])
from microclimate import wetness  # noqa: E402
from microclimate.live import config as C, forcing  # noqa: E402

rr, mt, snds = pickle.load(open(r"C:\Users\George\AppData\Local\Temp\gate\data.pkl", "rb"))
times = list(rr["times"])
nh = len(times)
lat_t = forcing.Lattice(rr["lat"], rr["lon"])
T, Td = np.asarray(rr["vars"]["temperature_2m"], float), np.asarray(rr["vars"]["dew_point_2m"], float)
zm_l = np.asarray(rr["elev"], float)
res = {}
all_err = []
for st in C.METAR_IDS:
    others = tuple(s for s in C.METAR_IDS if s != st)
    saved = C.METAR_IDS
    C.METAR_IDS = others
    info = dict(flags=[])
    off = forcing.base_offset_hourly(mt, times, nh, info)
    C.METAR_IDS = saved
    la, lo = C.METAR_LL[st]
    idx, w = lat_t.weights(np.array([la]), np.array([lo]))
    Ta, Tda, zma = (forcing.interp_at(a, idx, w)[0] for a in (T, Td, zm_l))
    elev = C.METAR_ELEV_M.get(st, 0.0)
    errs = []
    for x in mt.get(st, []):
        if not (np.isfinite(x["ceil_ft"]) and x["ceil_ft"] <= 3000):
            continue
        k = int(np.argmin([abs((x["t"] - t).total_seconds()) for t in times]))
        if abs((x["t"] - times[k]).total_seconds()) > 1800 or not np.isfinite(off[k]) or not np.isfinite(Ta[k]):
            continue
        base = zma + wetness.LCL_M_PER_K * max(Ta[k] - Tda[k], 0.0) + off[k]
        errs.append((base - (x["ceil_ft"] * 0.3048 + elev), x["t"]))
    e = np.array([a for a, _ in errs])
    days = sorted({t.date().isoformat() for _, t in errs})
    res[st] = dict(n=len(e), nights=days, mean_abs=float(np.abs(e).mean()) if len(e) else None, median_abs=float(np.median(np.abs(e))) if len(e) else None, bias=float(e.mean()) if len(e) else None)
    all_err += list(e)
    print(st, res[st])
e = np.array(all_err)
print("ALL: n", len(e), "mean abs %.0f m, median abs %.0f m, bias %.0f m" % (np.abs(e).mean(), np.median(np.abs(e)), e.mean()) if len(e) else "no data")
print("lcl only (no offset) for reference is the same formula with offset 0")
