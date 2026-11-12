"""M6: validation and ablations of the wetness proxy over all replay nights. Prints PASS/FAIL (or INFO) and writes validation.json.

Expected directions are fixed BEFORE looking at results:
 (a) E-W streets are drier than N-S streets at 09:00 after fog nights: the low morning sun (azimuth ~100-130 deg) runs along an
     E-W street axis and shines down it, while across a N-S street the east-side buildings shade the roadway.
 (b) the side of an E-W segment with less accumulated direct sun is wetter at 09:00 on at least 70 % of segments with a clear
     sun difference (>= 20 min). This is largely built into the model (sun term) and mainly tests the plumbing.
"""
import json
from pathlib import Path

import numpy as np

from . import replay, wetness

DAWN = int(replay.DAWN_STEP_H * 6)  # frame index at 09:00 for 10 min steps
LOC = {"outer_sunset": (37.753, -122.495), "mission": (37.760, -122.419)}
results = []


def rec(name, status, detail, **data):
    results.append(dict(check=name, status=status, detail=detail, **data))
    print(f"[{status}] {name}: {detail}", flush=True)


def seg_groups(P):
    """Index pairs (left, right) of the two side points of each segment, and the segment bearing mod 180."""
    import pandas as pd
    df = pd.DataFrame(dict(way=P["way"], seg=P["seg"], side=P["side"], i=np.arange(len(P["x"]))))
    left = df[df.side == "left"].set_index(["way", "seg"]).i
    right = df[df.side == "right"].set_index(["way", "seg"]).i
    both = left.index.intersection(right.index)
    return left.loc[both].to_numpy(), right.loc[both].to_numpy()


def corr(a, b):
    return float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 0 and np.std(b) > 0 else float("nan")


def surface_tracer_values(data_dir):
    from .street import first_fluid_k
    D = Path(data_dir)
    solid = np.load(D / "grids" / "solid_20m.npz")["solid"]
    k = first_fluid_k(solid)
    c = np.load(D / "library" / "fog_2700_0.npz")["c"]
    return np.take_along_axis(c, np.clip(k, 0, c.shape[2] - 1)[:, :, None], axis=2)[:, :, 0]


def run(cfg, data_dir, out_dir):
    results.clear()
    from pyproj import Transformer
    P = replay.load_inputs(data_dir)
    nights = replay.get_nights(cfg, data_dir)
    fog = [n for n in nights if n["kind"] == "fog"]
    dry = [n for n in nights if n["kind"] == "dry"]
    base = {n["id"]: replay.run_night(P, n) for n in nights}
    bearing = P["bearing"] % 180.0
    ew = np.abs(bearing - 90.0) <= 25.0
    ns = (bearing <= 25.0) | (bearing >= 155.0)

    # (a)
    m_ew = np.mean([base[n["id"]][0][DAWN][ew].mean() for n in fog])
    m_ns = np.mean([base[n["id"]][0][DAWN][ns].mean() for n in fog])
    e_ew = np.mean([base[n["id"]][0][-1][ew].mean() for n in fog])
    e_ns = np.mean([base[n["id"]][0][-1][ns].mean() for n in fog])
    pooled = np.concatenate([base[n["id"]][0][DAWN] for n in fog])
    sd = float(pooled.std())
    rec("a_ew_vs_ns", "PASS" if m_ew < m_ns - 0.5 else "FAIL",
        f"expected E-W drier at 09:00 (morning sun runs along the street). Mean W at 09:00 over {len(fog)} fog nights: "
        f"E-W {m_ew:.1f} vs N-S {m_ns:.1f} (diff {m_ew - m_ns:+.1f}, {abs(m_ew - m_ns) / sd:.2f} pooled SD); at 10:00 E-W {e_ew:.1f} vs N-S {e_ns:.1f}. "
        f"n points E-W {int(ew.sum())}, N-S {int(ns.sum())}.",
        ew_0900=float(m_ew), ns_0900=float(m_ns), ew_1000=float(e_ew), ns_1000=float(e_ns))

    # (b)
    L, R = seg_groups(P)
    seg_ew = np.abs((bearing[L] % 180) - 90) <= 25
    L, R = L[seg_ew], R[seg_ew]
    tot, win, ties, per = 0, 0, 0, []
    for n in fog:
        W, dg = base[n["id"]]
        lit_min = dg["lit"][:DAWN + 1].sum(0) * 10.0
        d = lit_min[L] - lit_min[R]
        sel = np.abs(d) >= 20
        wl, wr = W[DAWN][L][sel], W[DAWN][R][sel]
        less_wetter = np.where(d[sel] < 0, wl > wr, wr > wl)  # side with less sun is wetter
        tie = wl == wr
        k, t, ti = int((less_wetter & ~tie).sum()), int((~tie).sum()), int(tie.sum())
        win, tot, ties = win + k, tot + t, ties + ti
        per.append(dict(night=n["id"], segments=int(sel.sum()), n_decided=t, frac=(k / t if t else None)))
    frac = win / tot if tot else float("nan")
    rec("b_less_sun_side_wetter", "PASS" if tot and frac >= 0.7 else "FAIL",
        f"on {tot} E-W segments with a >=20 min sun difference (ties excluded: {ties}) the shadier side is wetter at 09:00 on "
        f"{100 * frac:.1f} % (threshold 70 %). Note the model has this built in via the sun term; sun-minutes accumulated 18:00-09:00.",
        frac=frac, n=tot, ties=ties, per_night=per)

    # (c)
    p95 = {n["id"]: float(np.percentile(base[n["id"]][0], 95, axis=1).max()) for n in dry}
    rec("c_dry_nights_below_20", "PASS" if dry and max(p95.values()) < 20 else "FAIL",
        "max over the night of the 95th percentile of W across points: " + ", ".join(f"{k} {v:.1f}" for k, v in p95.items()), p95=p95)

    # (d)
    tr = P["tracer"]
    hi, lo = tr >= np.percentile(tr, 80), tr <= np.percentile(tr, 20)
    ratios, cs = [], []
    for n in fog:
        f = base[n["id"]][1]["fog_total"]
        ratios.append(float(f[hi].mean() / max(f[lo].mean(), 1e-9)))
        cs.append(corr(f, tr))
    rec("d1_fog_deposition_follows_tracer", "PASS" if min(ratios) > 1.5 and np.nanmin(cs) > 0.5 else "FAIL",
        f"fog deposition at the top-20 % tracer points over the bottom-20 %: x{np.mean(ratios):.1f} (min x{min(ratios):.1f}); "
        f"corr(deposition, tracer) mean {np.nanmean(cs):.2f} (this is largely by construction).", ratios=ratios)
    sp = surface_tracer_values(data_dir)
    tf = Transformer.from_crs("EPSG:4326", "EPSG:32610", always_xy=True)
    vals = {}
    for k, (la, lo_) in LOC.items():
        x, y = tf.transform(lo_, la)
        i, j = int((x - 540100) // 20), int((y - 4171500) // 20)
        vals[k] = float(sp[i - 2:i + 3, j - 2:j + 3].mean())
    rec("d2_city_tracer_sunset_vs_mission", "PASS" if vals["outer_sunset"] > vals["mission"] else "FAIL",
        f"band-0 tracer at street level (5x5 cells): outer Sunset {vals['outer_sunset']:.3f} vs Mission {vals['mission']:.3f}", **vals)
    libs, ok = {}, True
    for b in range(4):
        j = json.load(open(Path(data_dir) / "library" / f"fog_2700_{b}.json"))
        c = np.load(Path(data_dir) / "library" / f"fog_2700_{b}.npz")["c"]
        good = (not j["nan"]) and j["max"] <= 1.0001 and float(c.min()) >= -1e-3 and j["drift_last_2000_steps"] < 0.01
        ok &= bool(good)
        libs[b] = dict(max=j["max"], min=float(c.min()), drift=j["drift_last_2000_steps"], mean_below_100m=j["mean_fluid_below_100m"])
    rec("d3_tracer_bounded_and_converged", "PASS" if ok else "FAIL",
        "all four bands: no NaN, 0 <= c <= 1, steady-state drift over the last 2000 steps < 0.01 (" +
        ", ".join(f"b{b}: drift {v['drift']:.4f}" for b, v in libs.items()) + "). Exact mass conservation is not testable (ground deposition removes tracer).",
        bands=libs)

    # (e) ablations at 09:00 on fog nights
    med_svf = np.full_like(P["svf"], np.median(P["svf"]))
    med_tr = np.full_like(P["tracer"], np.median(P["tracer"]))
    var = {"sun_term_off": dict(use_sun=False), "svf_median": dict(svf=med_svf), "tracer_uniform": dict(tracer=med_tr),
           "refined_wind_off": dict(refined=False),
           "all_four_off": dict(use_sun=False, svf=med_svf, tracer=med_tr, refined=False)}
    table = {"baseline": dict(std=float(np.mean([base[n["id"]][0][DAWN].std() for n in fog])), corr_with_baseline=1.0, mean_abs_diff=0.0)}
    for name, kw in var.items():
        sds, cors, mad = [], [], []
        for n in fog:
            W0 = base[n["id"]][0][DAWN]
            W1 = replay.run_night(P, n, **kw)[0][DAWN]
            sds.append(W1.std())
            cors.append(corr(W0, W1))
            mad.append(np.abs(W0 - W1).mean())
        table[name] = dict(std=float(np.mean(sds)), corr_with_baseline=float(np.nanmean(cors)), mean_abs_diff=float(np.mean(mad)))
    import pandas as pd
    dW = np.mean([replay.run_night(P, n, refined=False)[0][DAWN] - base[n["id"]][0][DAWN] for n in fog], axis=0)
    g = pd.DataFrame(dict(street=P["street"], d=dW)).groupby("street").d.agg(["mean", "size"])
    g = g[g["size"] >= 20]
    chg = g.reindex(g["mean"].abs().sort_values(ascending=False).index).head(8)
    table["refined_wind_off"]["streets_most_changed"] = {k: round(float(v), 1) for k, v in chg["mean"].items()}
    table["refined_wind_off"]["rel_wind_pct"] = [float(x) for x in np.percentile(P["rel_wind"], [5, 50, 95])]
    lines = "; ".join(f"{k}: std {v['std']:.1f}, corr {v['corr_with_baseline']:.2f}, mean|dW| {v['mean_abs_diff']:.1f}" for k, v in table.items() if isinstance(v, dict) and "std" in v)
    lines += "; streets whose 09:00 W changes most when refined wind is OFF (mean dW, W units): " + ", ".join(f"{k} {v:+.1f}" for k, v in table["refined_wind_off"]["streets_most_changed"].items())
    rec("e_ablations", "INFO", f"street-level W pattern at 09:00 (mean over {len(fog)} fog nights): {lines}", table=table)
    rec("f_v2_observed_fog_nights", "SKIPPED", "optional; not attempted in the MVP (needs v2 observations aligned to the replay nights)")

    scored = [r for r in results if r["status"] in ("PASS", "FAIL")]
    allpass = all(r["status"] == "PASS" for r in scored)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation.json").write_text(json.dumps(dict(nights=[n["id"] for n in nights], constants=wetness.CONSTANTS, results=results,
                                                         all_pass=allpass), indent=1, default=float))
    print("ALL PASS" if allpass else "SOME FAIL")
    return allpass

def overview(cfg, data_dir, out_dir, night_id=None):
    """PNG: map of W at 09:00 on a fog night over the Potrero points and time series at 3 sample points."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    P = replay.load_inputs(data_dir)
    fog = [n for n in replay.get_nights(cfg, data_dir) if n["kind"] == "fog"]
    n = next((x for x in fog if x["id"] == night_id), None) or max(fog, key=lambda x: replay.run_night(P, x)[0][DAWN].mean())
    W, dg = replay.run_night(P, n)
    fig, ax = plt.subplots(1, 2, figsize=(14, 6), gridspec_kw=dict(width_ratios=[1, 1.2]))
    sc = ax[0].scatter(P["x"], P["y"], c=W[DAWN], s=2, cmap="viridis", vmin=0, vmax=100)
    ax[0].set_aspect("equal")
    ax[0].set_title(f"Wetness index W at 09:00, night of {n['id']}")
    plt.colorbar(sc, ax=ax[0], label="W")
    idx = [int(np.argmax(P["tracer"] * P["svf"])), int(np.argmin(P["tracer"])), int(np.argmin(P["svf"]))]
    names = ["exposed (fog + sky)", "sheltered (low tracer)", "low sky view (canyon)"]
    t = np.arange(W.shape[0]) * 10 / 60 + 18
    for i, nm in zip(idx, names):
        ax[1].plot(t, W[:, i], label=f"{nm}: {P['street'][i]}")
        ax[0].scatter(P["x"][i], P["y"][i], s=60, marker="x", c="red")
    ax[1].plot(t, 100 * dg["s"], "k:", label="fog inflow s(t) x100")
    ax[1].set_xlabel("hours since midnight (18 = 6 pm, 34 = 10 am next day)")
    ax[1].set_ylabel("W")
    ax[1].legend(fontsize=8)
    ax[1].set_title("Wetness through the night at 3 points")
    fig.tight_layout()
    out = Path(out_dir) / "overview.png"
    fig.savefig(out, dpi=80)
    print("wrote", out, "night", n["id"])
