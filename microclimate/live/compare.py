"""Physics compare report (guide 01): run the live model twice on the SAME fetched inputs, once with the current C.PHYSICS and once with the
given switches, and report where and how much the wetness differs.

    python -m microclimate.live compare --physics a,b [--static PATH] [--out reports/physics_compare]

Writes compare_<UTC yyyymmddThhmmZ>.md and .json. Rows = the street columns the page shows (district ribbons first, then city ribbons), frames =
hourly output frames, so "street-hours" are row x frame cells.
"""
import datetime as dt
import json
import time
from pathlib import Path

import numpy as np

from .. import wetness
from . import build, config as C, forcing

BANDS = ((0.0, 50.0), (50.0, 150.0), (150.0, 300.0), (300.0, 1e9))
BAND_NAMES = ("0-50 m", "50-150 m", "150-300 m", ">300 m")
TERMS = ("fog_total", "dew_total", "rain_total", "evap_total")


def parse_physics(text):
    """'a,b' -> ('a', 'b'); 'none' or '' -> ()."""
    t = (text or "").strip()
    if t.lower() in ("", "none"):
        return ()
    return tuple(x.strip() for x in t.split(",") if x.strip())


def rows_of(W, S, Rb):
    """W [F, N] street-point wetness -> (rows [F, R] float, row z [R], row class [R] 0 district / 1 city). Without ribbons: the points themselves."""
    z = np.asarray(S["z"], float)
    if Rb is None:
        return np.asarray(W, float), z, np.zeros(len(z), int)
    a = np.asarray(Rb["A_src"])
    bl, br = np.asarray(Rb["B_srcL"]), np.asarray(Rb["B_srcR"])
    rows = np.concatenate([W[:, a], 0.5 * (W[:, bl] + W[:, br])], axis=1).astype(float)
    zr = np.concatenate([z[a], 0.5 * (z[bl] + z[br])])
    cl = np.concatenate([np.zeros(len(a), int), np.ones(len(bl), int)])
    return rows, zr, cl


def _stats(v):
    if v.size == 0:
        return dict(n=0, mean=None, p90=None, ge20=None, ge50=None)
    return dict(n=int(v.size), mean=float(v.mean()), p90=float(np.percentile(v, 90)), ge20=float((v >= 20).mean()), ge50=float((v >= 50).mean()))


def summarize(rows, zr, cl, k_obs):
    """Statistics of rows [F, R] by elevation band, class and time part (all / observed / forecast frames)."""
    nF = rows.shape[0]
    kb = 0 if k_obs is None else int(np.clip(k_obs, 0, nF))  # no observation boundary: every frame is forecast
    parts = {"all": slice(0, nF), "observed": slice(0, kb), "forecast": slice(kb, nF)}
    out = {}
    groups = {"all rows": np.ones(len(zr), bool), "district rows": cl == 0, "city rows": cl == 1}
    for bn, (lo, hi) in zip(BAND_NAMES, BANDS):
        groups["elev " + bn] = (zr >= lo) & (zr < hi)
    for pn, sl in parts.items():
        out[pn] = {gn: _stats(rows[sl][:, m]) for gn, m in groups.items()}
    return out


def diffs(summ_a, summ_b):
    d = {}
    for pn, grp in summ_a.items():
        d[pn] = {}
        for gn, a in grp.items():
            b = summ_b[pn][gn]
            d[pn][gn] = {k: (None if a[k] is None or b[k] is None else b[k] - a[k]) for k in ("mean", "p90", "ge20", "ge50")}
    return d


def top_street_diffs(Wa, Wb, S, k=10):
    dd = np.abs(np.asarray(Wb, float) - np.asarray(Wa, float))
    mx = dd.max(axis=0)
    order = np.argsort(-mx)[:k]
    return [dict(x=float(S["x"][i]), y=float(S["y"][i]), z=float(S["z"][i]), max_abs_diff=float(mx[i]), mean_abs_diff=float(dd[:, i].mean())) for i in order if mx[i] > 0]


def report(Wa, da, Wb, db, S, Rb, k_obs, pa, pb, now):
    """Pure function: the whole report dict from two simulations."""
    ra, zr, cl = rows_of(Wa, S, Rb)
    rb, _, _ = rows_of(Wb, S, Rb)
    sa, sb = summarize(ra, zr, cl, k_obs), summarize(rb, zr, cl, k_obs)
    terms = {}
    for t in sorted(set(k for k in da if k.endswith("_total")) | set(k for k in db if k.endswith("_total"))):
        va = float(np.mean(da[t])) if t in da else None
        vb = float(np.mean(db[t])) if t in db else None
        terms[t] = dict(a=va, b=vb, diff=None if va is None or vb is None else vb - va)
    return dict(generated_iso=now.isoformat(), physics_a=list(pa), physics_b=list(pb), observed_until_frame=k_obs, frames=int(Wa.shape[0]),
                rows=int(ra.shape[1]), street_points=int(Wa.shape[1]), a=sa, b=sb, diff=diffs(sa, sb), terms=terms,
                top_differences=top_street_diffs(Wa, Wb, S), max_abs_diff_points=float(np.abs(np.asarray(Wb, float) - np.asarray(Wa, float)).max()))


def _f(v, p=2):
    return "-" if v is None else f"{v:.{p}f}"


def to_markdown(r):
    L = [f"# Physics compare {r['generated_iso']}", "",
         f"- A (current C.PHYSICS): `{r['physics_a'] or 'none'}`", f"- B: `{r['physics_b'] or 'none'}`",
         f"- {r['frames']} hourly frames, {r['rows']} street rows ({r['street_points']} model points); observed frames: 0..{r['observed_until_frame']}",
         f"- largest absolute difference at any model point and frame: {_f(r['max_abs_diff_points'], 4)}", ""]
    for pn in ("all", "observed", "forecast"):
        L += [f"## W, {pn} frames", "", "| group | street-hours | mean A | mean B | diff | p90 A | p90 B | diff | >=20 A | >=20 B | >=50 A | >=50 B |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for gn, a in r["a"][pn].items():
            b, d = r["b"][pn][gn], r["diff"][pn][gn]
            L.append(f"| {gn} | {a['n']} | {_f(a['mean'])} | {_f(b['mean'])} | {_f(d['mean'], 4)} | {_f(a['p90'])} | {_f(b['p90'])} | {_f(d['p90'], 4)} | "
                     f"{_f(a['ge20'], 4)} | {_f(b['ge20'], 4)} | {_f(a['ge50'], 4)} | {_f(b['ge50'], 4)} |")
        L.append("")
    L += ["## Terms (mean per point over the window, W units)", "", "| term | A | B | diff |", "|---|---|---|---|"]
    for t, v in r["terms"].items():
        L.append(f"| {t} | {_f(v['a'], 3)} | {_f(v['b'], 3)} | {_f(v['diff'], 4)} |")
    L += ["", "## Ten largest per-street differences", "", "| x | y | z | max abs diff | mean abs diff |", "|---|---|---|---|---|"]
    for t in r["top_differences"]:
        L.append(f"| {t['x']:.0f} | {t['y']:.0f} | {t['z']:.1f} | {t['max_abs_diff']:.3f} | {t['mean_abs_diff']:.3f} |")
    if not r["top_differences"]:
        L.append("| (none: identical) | | | | |")
    return "\n".join(L) + "\n"


def run(physics_b, static=None, ribbons=None, cache=None, out_dir="reports/physics_compare", now=None, log=print):
    """Fetch once, simulate with C.PHYSICS and with physics_b on the same inputs and the same `now`. Returns 0 on success, 2 when no data."""
    from .. import city
    from . import fetch
    now = now or fetch.utcnow()
    pa, pb = wetness.check_physics(C.PHYSICS), wetness.check_physics(physics_b)
    static = Path(static or C.STATIC_DEFAULT)
    ribbons = Path(ribbons or static.parent / "live_ribbons.npz")
    cache = Path(cache or static.parent / "goes_cache")
    start, t0, end = build.window(now)
    n = int(round((end - t0).total_seconds() / 60.0 / C.STEP_MIN)) + 1
    S = city.load_static(static)
    Rb = dict(np.load(ribbons)) if ribbons.exists() else None
    st = build.Status()
    try:
        rr, obs, sector_fn, _ = build.gather(st, t0, now, cache)
    except fetch.FetchError as e:
        log(f"cannot compare: {e}")
        return 2
    res = []
    for P in (pa, pb):
        F = forcing.build(rr, t0, n, now, S["x"], S["y"], obs, sector_fn=sector_fn, log=log, physics=P)
        t = time.time()
        W, diag = wetness.simulate(build.point_table(S), F["ser"], C.STEP_MIN, tracer_dirs=S["tracer"], dirs_deg=S["dirs"], forcing_fn=F["forcing_fn"], lag_block_h=3,
                                   out_every=C.OUT_STEP_MIN // C.STEP_MIN, keep_lit=False, s_series=F["s_series"], fog_top=F["fog_top"], use_dirs=True, physics=P)
        log(f"simulate {sorted(P)}: {time.time() - t:.1f} s")
        res.append((W, diag, F))
    (Wa, da, Fa), (Wb, db, Fb) = res
    obs_until = Fa["info"]["obs_until"]
    k_obs = None if obs_until is None else int((dt.datetime.fromisoformat(obs_until) - t0).total_seconds() // 3600)
    rep = report(Wa, da, Wb, db, S, Rb, k_obs, sorted(pa), sorted(pb), now)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out / f"compare_{stamp}.json").write_text(json.dumps(rep, indent=1))
    md = to_markdown(rep)
    (out / f"compare_{stamp}.md").write_text(md, encoding="utf-8", newline="\n")
    log(md)
    return 0
