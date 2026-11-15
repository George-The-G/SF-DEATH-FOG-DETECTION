import sys, pickle, datetime as dt, json
sys.path.insert(0, sys.argv[1])
import numpy as np
from microclimate.live import config as C, forcing, fetch
rr, mt, snds = pickle.load(open(sys.argv[2], "rb"))
x = np.array([545000., 550000., 552000.]); y = np.array([4178000., 4175000., 4172500.])
t_first, t_last = rr["times"][0], rr["times"][-1]
rows = []
def run(v, fusion, mt_c, causal_only=True):
    t0 = v - dt.timedelta(hours=36)
    n = 42 * 6 + 1
    C.TOP_FUSION = fusion
    obs = dict(coops=None, metar=mt_c, nws={}, sounding=None)
    F = forcing.build(rr, t0, n, v, x, y, obs, sector_fn=None, log=lambda *a: None, physics=())
    return F, 36 * 6
for s in sorted(snds, key=lambda s: s["valid"]):
    v = s["valid"]
    base = forcing._inv(s)
    if base is None or v - dt.timedelta(hours=36) < t_first or v + dt.timedelta(hours=6) > t_last:
        rows.append(dict(valid=v.isoformat(), skipped="no inversion" if base is None else "outside model window")); continue
    mt_c = {k: [r for r in rs if r["t"] <= v] for k, rs in mt.items()}
    nc = sum(1 for k in ("KHAF", "KSFO") for r in mt_c[k] if np.isfinite(r["ceil_ft"]) and r["ceil_ft"] <= 3000 and r["t"] > v - dt.timedelta(hours=6))
    try:
        Fo, i0 = run(v, False, mt_c); Fn, _ = run(v, True, mt_c)
    except Exception as e:
        rows.append(dict(valid=v.isoformat(), error=repr(e)[:120])); continue
    old, new = float(Fo["fog_top"][i0]), float(Fn["fog_top"][i0])
    ti = Fn["info"]["top_fusion"]
    rows.append(dict(valid=v.isoformat(), base=round(base), old=round(old), new=round(new), sd=round(float(Fn["fog_top_sigma"][i0])), ceil_obs_6h=nc,
                     stuck_old=round(ti["stuck_old"], 3), stuck_new=round(ti["stuck_new"], 3), mean_change=round(ti["mean_abs_change_m"]), rej=sum(d["rejected"] for d in ti.values() if isinstance(d, dict) and "rejected" in d)))
C.TOP_FUSION = False
ok = [r for r in rows if "base" in r]
for r in rows: print(r)
mae = lambda k, rs: float(np.mean([abs(r[k] - r["base"]) for r in rs]))
print("n", len(ok), "MAE old", round(mae("old", ok)), "new", round(mae("new", ok)), "| bias old", round(np.mean([r["old"] - r["base"] for r in ok])), "new", round(np.mean([r["new"] - r["base"] for r in ok])))
wc = [r for r in ok if r["ceil_obs_6h"] > 0]
if wc: print("with ceilings", len(wc), "MAE old", round(mae("old", wc)), "new", round(mae("new", wc)))
print("stuck share old", round(np.mean([r["stuck_old"] for r in ok]), 3), "new", round(np.mean([r["stuck_new"] for r in ok]), 3))
json.dump(rows, open(sys.argv[3], "w"), indent=1)
