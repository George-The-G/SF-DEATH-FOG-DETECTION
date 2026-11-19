#!/usr/bin/env python3
"""Offline gate for guide 13: the layer table against airport reports, using the folders tools/collect_sky_inputs.py saved.
usage: python tools/eval_sky_layers.py [sky_inputs_root]      (default ~/deathfog-data/sky_inputs; all dated folders are pooled)
For every hour where a day folder has both a profile and METARs: model layers from the RRFS profile at the Golden Gate Park point, then the
airport's lowest layer ABOVE the model's fog-layer floor is compared with the model's lowest upper layer:
  baseline  = model only
  loo       = model nudged by the OTHER two airports (leave one airport out), the same idea as the ceiling offset.
Prints the median and 90th percentile absolute base error (m) and the hit counts. Target (plan): under 150 m on the lowest upper layer."""
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from microclimate.live import skylayers as S  # noqa: E402

AIRPORTS = {"KSFO": 4.0, "KOAK": 3.0, "KHAF": 20.0}
LEVELS = (1000, 975, 950, 925, 900, 850, 800, 700, 600, 500, 400, 300)
FLOOR_AGL_M = 200.0      # the fog layer owns everything below this; the upper layers start above it


def model_runs(h, i, thr):
    z = [h[f"geopotential_height_{l}hPa"][i] for l in LEVELS]
    rh = [h[f"relative_humidity_{l}hPa"][i] for l in LEVELS]
    return S.humidity_runs(z, rh, thr=thr, floor_m=FLOOR_AGL_M)


def main(root):
    errs_base, errs_loo, n_none = [], [], 0
    for day in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        try:
            prof = json.loads((day / "profile_hist.json").read_text())["hourly"]
            metars = json.loads((day / "metar_raw.json").read_text())
        except Exception:  # noqa: BLE001
            continue
        times = {t: i for i, t in enumerate(prof["time"])}
        by = {}
        for m in metars:
            if m.get("icaoId") in AIRPORTS and m.get("obsTime"):
                t = dt.datetime.fromtimestamp(m["obsTime"], dt.timezone.utc)
                key = t.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00")
                by.setdefault(key, {}).setdefault(m["icaoId"], S.metar_layers(m.get("clouds"), AIRPORTS[m["icaoId"]]))
        for key, per in by.items():
            i = times.get(key)
            if i is None:
                continue
            runs = model_runs(prof, i, S.SKY_RH_THRESHOLD)
            for ap, lay in per.items():
                truth = [l for l in lay if l["base"] > FLOOR_AGL_M + 100 and l["cov"] >= S.COVER["SCT"]]
                if not truth:
                    continue
                tb = truth[0]["base"]
                if not runs:
                    n_none += 1
                    continue
                others = [v for k, v in per.items() if k != ap]
                base = S.build_table(runs, None, floor_m=FLOOR_AGL_M)
                loo = S.build_table(runs, others, floor_m=FLOOR_AGL_M)
                errs_base.append(abs(base[0]["base"] - tb))
                errs_loo.append(abs(loo[0]["base"] - tb))

    def stat(e):
        e = sorted(e)
        return f"median {statistics.median(e):.0f} m, p90 {e[int(0.9 * (len(e) - 1))]:.0f} m" if e else "no data"

    print(f"airport-hours with an upper layer reported: {len(errs_base) + n_none}; model had a layer: {len(errs_base)}; model missed: {n_none}")
    print("baseline (model only):    ", stat(errs_base))
    print("leave-one-airport-out:    ", stat(errs_loo))


if __name__ == "__main__":
    from microclimate.paths import data_dir
    main(sys.argv[1] if len(sys.argv) > 1 else data_dir() / "sky_inputs")
