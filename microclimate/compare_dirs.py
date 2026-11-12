"""What would replay change if the direction library were used? Runs every replay night twice (270-only vs direction library)
through wetness.simulate and tabulates the dawn (09:00) and whole-night differences. Nothing is written to site/v3.

python -m microclimate.compare_dirs   (writes ~/deathfog-data/library/dirs_replay_compare.json and prints a table)
"""
import json
from pathlib import Path

import numpy as np

from . import replay, wetness
from .config import load

DATA = Path.home() / "deathfog-data"


def main():
    cfg = load()
    wetness.DATA_DIR = DATA
    P = replay.load_inputs(DATA)
    nights = replay.get_nights(cfg, DATA)
    step_min = cfg["replay"]["step_min"]
    frame = int(replay.DAWN_STEP_H * 60 / step_min)
    pot = P["zone"] == "potrero"
    zt = replay.zone_table(DATA)
    zones = zt["zones"]
    route_ids = zt["routes"]

    def stats(sel, Wo, Wn):
        o, n = Wo[frame][sel], Wn[frame][sel]
        dd = n - o
        return {"points": int(sel.sum()), "old_mean": round(float(o.mean()), 1), "new_mean": round(float(n.mean()), 1), "old_p95": round(float(np.percentile(o, 95)), 1),
                "new_p95": round(float(np.percentile(n, 95)), 1), "mean_change": round(float(dd.mean()), 1), "mean_abs_change": round(float(np.abs(dd).mean()), 1),
                "p95_abs_change": round(float(np.percentile(np.abs(dd), 95)), 1)}
    rows = []
    for nt in nights:
        res = {}
        for label, flag in (("old", False), ("new", True)):
            wetness.DIR_LIBRARY = flag
            P2 = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in P.items() if not k.startswith("_tracer_profile")}
            W, diag = replay.run_night(P2, nt, step_min)
            res[label] = (W, diag)
        Wo, Wn = res["old"][0], res["new"][0]
        meta_old = None
        wetness.DIR_LIBRARY = False
        meta_old = replay.night_meta(nt, DATA)
        wetness.DIR_LIBRARY = True
        meta_new = replay.night_meta(nt, DATA)
        d = Wn - Wo
        rows.append({
            "night": nt["id"], "kind": nt["kind"], "mean_dir": round(meta_new["mean_dir"], 1), "mean_wind_ms": round(meta_new["mean_wind_ms"], 2),
            "approx_old": meta_old["approx_direction"], "approx_new": meta_new["approx_direction"],
            "dirs_used": res["new"][1].get("dirs_used"),
            "dawn_mean_old_all": round(float(Wo[frame].mean()), 2), "dawn_mean_new_all": round(float(Wn[frame].mean()), 2),
            "dawn_mean_old_potrero": round(float(Wo[frame][pot].mean()), 2), "dawn_mean_new_potrero": round(float(Wn[frame][pot].mean()), 2),
            "dawn_abs_diff_mean": round(float(np.abs(d[frame]).mean()), 2), "dawn_abs_diff_p95": round(float(np.percentile(np.abs(d[frame]), 95)), 2),
            "dawn_abs_diff_max": round(float(np.abs(d[frame]).max()), 2),
            "night_peak_W_old": round(float(Wo.max(0).mean()), 2), "night_peak_W_new": round(float(Wn.max(0).mean()), 2),
            "zones": {z["id"]: stats((np.arange(len(pot)) >= z["row0"]) & (np.arange(len(pot)) < z["row1"]), Wo, Wn) for z in zones},
            "routes": {rid: stats(((P["route_mask"] >> bi) & 1) == 1, Wo, Wn) for bi, rid in enumerate(route_ids) if (((P["route_mask"] >> bi) & 1) == 1).any()},
            "dawn_frac_points_changed_gt5": round(float((np.abs(d[frame]) > 5).mean()), 3),
        })
        print(rows[-1], flush=True)
    json.dump(rows, open(DATA / "library" / "dirs_replay_compare.json", "w"), indent=1)
    return rows


if __name__ == "__main__":
    main()
