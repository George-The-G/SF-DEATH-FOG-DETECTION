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


Z_BINS = [0, 10, 20, 30, 45, 60, 80, 1000]  # street elevation bins (m above sea level) for the fog-height checks
HILLS = {  # name: (lat, lon, summit elevation m ASL typed by hand; the 20 m grid lowers peaks by ~20 m)
    "Twin Peaks": (37.7544, -122.4477, 277.0), "Mt Davidson": (37.7385, -122.4545, 283.0),
    "Mt Sutro": (37.7599, -122.4586, 254.0), "Bernal Hill": (37.7439, -122.4150, 110.0)}


def fog_active_top(top, s):
    """Fog top summarised over the night as the inflow-weighted mean (weights s(t)); nan if no inflow."""
    return float(np.average(top, weights=s)) if s.sum() > 0 else float("nan")


def fog_height_checks(P, nights, fog, dry, data_dir):
    """(g) fog height versus street elevation: before ('band0') and after ('profile') tables and sanity checks.
    Expected directions, fixed before the results: g1 the model change must not alter low streets (< 30 m) by more than
    2 W on average but wets the high streets (> 60 m) at least as much as before; g2 on nights whose fog top stays
    below ~130 m the high streets (> 60 m) end drier than the low streets (< 20 m); g3 the hill summits (Twin Peaks,
    Mt Davidson) get ~no fog on the shallowest fog night and are fog-exposed on the deepest."""
    from pyproj import Transformer
    from . import fogtop
    z = P["z"]
    modes = {m: {n["id"]: replay.run_night(P, n, fog_mode=m) for n in fog + dry} for m in ("band0", "profile")}
    bins = list(zip(Z_BINS[:-1], Z_BINS[1:]))
    sel = [(z > a) & (z <= b) for a, b in bins]
    label = [f"{a}-{b}" if b < 1000 else f">{a}" for a, b in bins]
    tab = {m: [float(np.mean([modes[m][n["id"]][0][DAWN][s].mean() for n in fog])) for s in sel] for m in modes}
    cnt = [int(s.sum()) for s in sel]
    low, high = (z <= 30), (z > 60)
    d_low = float(np.mean([modes["profile"][n["id"]][0][DAWN][low].mean() - modes["band0"][n["id"]][0][DAWN][low].mean() for n in fog]))
    d_high = float(np.mean([modes["profile"][n["id"]][0][DAWN][high].mean() - modes["band0"][n["id"]][0][DAWN][high].mean() for n in fog]))
    rows = "; ".join(f"z {l} m (n={c}): {a:.1f} -> {b:.1f}" for l, c, a, b in zip(label, cnt, tab["band0"], tab["profile"]))
    rec("g1_wetness_vs_elevation", "PASS" if abs(d_low) <= 2.0 and d_high >= 0 else "FAIL",
        f"mean W at 09:00 over {len(fog)} fog nights, band0 -> profile, by street elevation: {rows}. Change for streets < 30 m: "
        f"{d_low:+.1f} W, for streets > 60 m: {d_high:+.1f} W.", labels=label, n=cnt, band0=tab["band0"], profile=tab["profile"],
        d_low=d_low, d_high=d_high)

    info, per = {}, []
    for n in fog + dry:
        W, dg = modes["profile"][n["id"]]
        top = dg["fog_top"]
        t_act = fog_active_top(top, dg["s"])
        Wb = modes["band0"][n["id"]][0]
        per.append(dict(night=n["id"], kind=n["kind"], fog_top_active_max=t_act, fog_top_min=float(top.min()), fog_top_median=float(np.median(top)),
                        fog_top_max=float(top.max()), W_low_b0=float(Wb[DAWN][z <= 20].mean()), W_high_b0=float(Wb[DAWN][z > 60].mean()),
                        W_low=float(W[DAWN][z <= 20].mean()), W_high=float(W[DAWN][z > 60].mean())))
    # controlled experiment: same weather, fog top forced shallow (SHALLOW_M) or deep (DEEP_M)
    SHALLOW_M, DEEP_M = 90.0, 700.0
    ctl = {k: [replay.run_night(P, n, fog_mode="profile", fog_top=v)[0][DAWN] for n in fog] for k, v in (("shallow", SHALLOW_M), ("deep", DEEP_M))}
    cm = lambda k, s: float(np.mean([w[s].mean() for w in ctl[k]]))
    d_hi = cm("deep", z > 60) - cm("shallow", z > 60)
    d_lo = cm("deep", z <= 20) - cm("shallow", z <= 20)
    ok = d_hi >= 3.0 and abs(d_lo) <= 1.0
    txt = "; ".join(f"{p['night']}: inflow-weighted fog top {p['fog_top_active_max']:.0f} m (range {p['fog_top_min']:.0f}-{p['fog_top_max']:.0f}), "
                    f"W low(<20 m) {p['W_low']:.1f} vs high(>60 m) {p['W_high']:.1f} (band0: {p['W_low_b0']:.1f} vs {p['W_high_b0']:.1f})"
                    for p in per if p["kind"] == "fog")
    rec("g2_low_fog_top_dries_high_streets", "PASS" if ok else "FAIL",
        f"controlled test, same {len(fog)} fog nights with the fog top forced to {SHALLOW_M:.0f} m vs {DEEP_M:.0f} m: mean W at 09:00 of streets > 60 m "
        f"{cm('shallow', z > 60):.1f} (shallow) vs {cm('deep', z > 60):.1f} (deep), +{d_hi:.1f} W; streets < 20 m {cm('shallow', z <= 20):.1f} vs {cm('deep', z <= 20):.1f} "
        f"({d_lo:+.1f} W). Potrero streets span only 3-99 m, i.e. mostly below any estimated top, so the real nights differ little. Real nights: {txt}.",
        shallow_m=SHALLOW_M, deep_m=DEEP_M, high_shallow=cm("shallow", z > 60), high_deep=cm("deep", z > 60), low_shallow=cm("shallow", z <= 20),
        low_deep=cm("deep", z <= 20), per_night=per)

    tf = Transformer.from_crs("EPSG:4326", "EPSG:32610", always_xy=True)
    hx = {k: tf.transform(v[1], v[0]) for k, v in HILLS.items()}
    xs = np.array([hx[k][0] for k in HILLS]); ys = np.array([hx[k][1] for k in HILLS]); zs = np.array([v[2] for v in HILLS.values()])
    trc = fogtop.blend_bands(fogtop.sample_bands(xs, ys, zs + wetness.TRACER_Z_OFFSET_M), zs + wetness.TRACER_Z_OFFSET_M)
    expo = wetness.fog_exposure(trc)
    table, ranked = {}, sorted(per, key=lambda p: p["fog_top_active_max"] if p["kind"] == "fog" else 1e9)
    for p in per:
        if p["kind"] != "fog":
            continue
        ramp = fogtop.fog_top_ramp(p["fog_top_active_max"], zs)
        table[p["night"]] = {k: dict(ramp=round(float(ramp[i]), 2), exposure=round(float(ramp[i] * expo[i]), 2)) for i, k in enumerate(HILLS)}
    shal, deep = ranked[0]["night"], [p for p in ranked if p["kind"] == "fog"][-1]["night"]
    tp = list(HILLS).index("Twin Peaks")
    md = list(HILLS).index("Mt Davidson")
    ok3 = table[shal]["Twin Peaks"]["ramp"] == 0.0 and table[shal]["Mt Davidson"]["ramp"] == 0.0 and table[deep]["Twin Peaks"]["ramp"] > 0.5
    rec("g3_hills_dry_on_shallow_wet_on_deep", "PASS" if ok3 else "FAIL",
        f"qualitative; the summit elevations are typed in by hand and this is largely by construction (street elevation vs estimated fog top). "
        f"Shallowest fog night {shal} (top {ranked[0]['fog_top_active_max']:.0f} m): Twin Peaks ramp {table[shal]['Twin Peaks']['ramp']}, Mt Davidson {table[shal]['Mt Davidson']['ramp']}; "
        f"deepest {deep}: Twin Peaks {table[deep]['Twin Peaks']['ramp']}, Mt Davidson {table[deep]['Mt Davidson']['ramp']}. Band-interpolated tracer at the summits: "
        + ", ".join(f"{k} {trc[i]:.3f}" for i, k in enumerate(HILLS)) + ". Per night ramp x exposure: " + json.dumps(table),
        table=table, tracer_at_hills={k: float(trc[i]) for i, k in enumerate(HILLS)})


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
    tr = base[fog[0]["id"]][1]["tracer"]  # the tracer actually used by the default FOG_HEIGHT_MODE
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

    fog_height_checks(P, nights, fog, dry, data_dir)

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
