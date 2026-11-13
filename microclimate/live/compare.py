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


def film_summary(film_mm, dry_in, S, Rb, k_obs):
    """Water on the road in mm (switch film, run B): mean, p90 and share of street-hours wet (>= wetness.DRY_MM) by band / class / time part; mean hours until dry
    among the wet street-hours (255 = not within the window is left out)."""
    rows, zr, cl = rows_of(np.asarray(film_mm, np.float32), S, Rb)
    dry, _, _ = rows_of(np.asarray(dry_in, np.float32), S, Rb)
    nF = rows.shape[0]
    kb = 0 if k_obs is None else int(np.clip(k_obs, 0, nF))
    parts = {"all": slice(0, nF), "observed": slice(0, kb), "forecast": slice(kb, nF)}
    groups = {"all rows": np.ones(len(zr), bool), "district rows": cl == 0, "city rows": cl == 1}
    for bn, (lo, hi) in zip(BAND_NAMES, BANDS):
        groups["elev " + bn] = (zr >= lo) & (zr < hi)
    out = {}
    for pn, sl in parts.items():
        out[pn] = {}
        for gn, m in groups.items():
            v = rows[sl][:, m]
            dv = dry[sl][:, m]
            wet = v >= wetness.DRY_MM
            ok = wet & (dv < 255)
            out[pn][gn] = dict(n=int(v.size), mean_mm=None if v.size == 0 else float(v.mean()), p90_mm=None if v.size == 0 else float(np.percentile(v, 90)),
                               wet_share=None if v.size == 0 else float(wet.mean()), dry_in_mean_h=float(dv[ok].mean()) if ok.any() else None,
                               not_dry_in_window_share=None if not wet.any() else float((wet & (dv >= 255)).mean() / wet.mean()))
    return out


def dew_frequency(d):
    """Share of model steps with dew on the road (film runs only): dict(mean_step_share over the street points, points_with_dew share) or None."""
    if d.get("dew_steps") is None:
        return None
    n = float(d.get("n_steps") or 1)
    ds = np.asarray(d["dew_steps"], float)
    return dict(step_share=float(ds.mean() / n), points_with_dew=float((ds > 0).mean()), steps=int(n))


def pavement_summary(da, db, S, Rb, k_obs):
    """Road temperature of run B in degC (switch pavement) by elevation band and time part: mean, p90, maximum, plus dew frequency of A and B. None when B has no ts."""
    dfa, dfb = dew_frequency(da), dew_frequency(db)
    if db.get("ts_u8") is None:
        return None if dfb is None and dfa is None else dict(dew_a=dfa, dew_b=dfb)
    rows, zr, cl = rows_of(np.asarray(db["ts_u8"], np.float32) / wetness.TS_U8_PER_K - wetness.TS_U8_OFFSET_C, S, Rb)
    nF = rows.shape[0]
    kb = 0 if k_obs is None else int(np.clip(k_obs, 0, nF))
    parts = {"all": slice(0, nF), "observed": slice(0, kb), "forecast": slice(kb, nF)}
    groups = {"all rows": np.ones(len(zr), bool)}
    for bn, (lo, hi) in zip(BAND_NAMES, BANDS):
        groups["elev " + bn] = (zr >= lo) & (zr < hi)
    out = {}
    for pn, sl in parts.items():
        out[pn] = {}
        for gn, m in groups.items():
            v = rows[sl][:, m]
            out[pn][gn] = dict(n=int(v.size), mean_c=None if v.size == 0 else float(v.mean()), p90_c=None if v.size == 0 else float(np.percentile(v, 90)),
                               max_c=None if v.size == 0 else float(v.max()), min_c=None if v.size == 0 else float(v.min()))
    return dict(ts_b=out, dew_a=dfa, dew_b=dfb)


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
    film = film_summary(db["film_mm"], db["dry_in_h"], S, Rb, k_obs) if db.get("film_mm") is not None else None
    pav = pavement_summary(da, db, S, Rb, k_obs)
    return dict(generated_iso=now.isoformat(), physics_a=list(pa), physics_b=list(pb), observed_until_frame=k_obs, frames=int(Wa.shape[0]),
                rows=int(ra.shape[1]), street_points=int(Wa.shape[1]), a=sa, b=sb, diff=diffs(sa, sb), terms=terms, film_b=film, pavement=pav,
                top_differences=top_street_diffs(Wa, Wb, S), max_abs_diff_points=float(np.abs(np.asarray(Wb, float) - np.asarray(Wa, float)).max()))


def _f(v, p=2):
    return "-" if v is None else f"{v:.{p}f}"


def _ab_lines(r):
    if r.get("east_compare"):
        return [f"- A: `{r['physics_a'] or 'none'}`, station anchors ON, west wind library (directions clamped to 180..360)", f"- B: same, east wind fields ON (guide 14; 157.5 gap blended, fog tracer rows zero)"]
    if r.get("anchors_compare"):
        return [f"- A: `{r['physics_a'] or 'none'}`, station anchors as configured (C.NUDGE_ANCHORS)", f"- B: `{r['physics_b'] or 'none'}`, station anchors ON"]
    return [f"- A ({'--base' if r.get('base_given') else 'current C.PHYSICS'}): `{r['physics_a'] or 'none'}`", f"- B: `{r['physics_b'] or 'none'}`"]


def to_markdown(r):
    L = [f"# Physics compare {r['generated_iso']}", "",
         *_ab_lines(r),
         f"- {r['frames']} hourly frames, {r['rows']} street rows ({r['street_points']} model points); observed frames: 0..{r['observed_until_frame']}",
         f"- largest absolute difference at any model point and frame: {_f(r['max_abs_diff_points'], 4)}", ""]
    for pn in ("all", "observed", "forecast"):
        L += [f"## W, {pn} frames", "", "| group | street-hours | mean A | mean B | diff | p90 A | p90 B | diff | >=20 A | >=20 B | >=50 A | >=50 B |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for gn, a in r["a"][pn].items():
            b, d = r["b"][pn][gn], r["diff"][pn][gn]
            L.append(f"| {gn} | {a['n']} | {_f(a['mean'])} | {_f(b['mean'])} | {_f(d['mean'], 4)} | {_f(a['p90'])} | {_f(b['p90'])} | {_f(d['p90'], 4)} | "
                     f"{_f(a['ge20'], 4)} | {_f(b['ge20'], 4)} | {_f(a['ge50'], 4)} | {_f(b['ge50'], 4)} |")
        L.append("")
    if r.get("film_b"):
        for pn in ("all", "observed", "forecast"):
            L += [f"## B: water on the road in mm (film), {pn} frames", "", "| group | street-hours | mean mm | p90 mm | wet share | mean h until dry | wet and not dry in window |", "|---|---|---|---|---|---|---|"]
            for gn, a in r["film_b"][pn].items():
                L.append(f"| {gn} | {a['n']} | {_f(a['mean_mm'], 4)} | {_f(a['p90_mm'], 4)} | {_f(a['wet_share'], 4)} | {_f(a['dry_in_mean_h'], 2)} | {_f(a['not_dry_in_window_share'], 4)} |")
            L.append("")
        L += ["(with film, B's W columns above are slip = 100 * (1 - exp(-F / 0.02 mm)); A is today's index W: the two are different scales)", ""]
    L += ["## Terms (mean per point over the window; W units, or mm for a film run)", "", "| term | A | B | diff |", "|---|---|---|---|"]
    for t, v in r["terms"].items():
        L.append(f"| {t} | {_f(v['a'], 3)} | {_f(v['b'], 3)} | {_f(v['diff'], 4)} |")
    if r.get("pavement"):
        pv = r["pavement"]
        if pv.get("ts_b"):
            for pn in ("all", "observed", "forecast"):
                L += [f"## B: road surface temperature in degC (pavement), {pn} frames", "", "| group | street-hours | mean | p90 | min | max |", "|---|---|---|---|---|---|"]
                for gn, a in pv["ts_b"][pn].items():
                    L.append(f"| {gn} | {a['n']} | {_f(a['mean_c'], 1)} | {_f(a['p90_c'], 1)} | {_f(a['min_c'], 1)} | {_f(a['max_c'], 1)} |")
                L.append("")
        L += ["## Dew frequency (share of 10-minute steps with dew on the road, mean over the street points)", "", "| run | step share | points with any dew |", "|---|---|---|"]
        for nm, k in (("A", "dew_a"), ("B", "dew_b")):
            v = pv.get(k)
            L.append(f"| {nm} | {_f(v['step_share'], 4) if v else '-'} | {_f(v['points_with_dew'], 4) if v else '-'} |")
        L += ["", "(unverified: textbook constants, no road-temperature measurements)", ""]
    L += ["", "## Ten largest per-street differences", "", "| x | y | z | max abs diff | mean abs diff |", "|---|---|---|---|---|"]
    for t in r["top_differences"]:
        L.append(f"| {t['x']:.0f} | {t['y']:.0f} | {t['z']:.1f} | {t['max_abs_diff']:.3f} | {t['mean_abs_diff']:.3f} |")
    if not r["top_differences"]:
        L.append("| (none: identical) | | | | |")
    return "\n".join(L) + "\n"


def run(physics_b, static=None, ribbons=None, cache=None, out_dir="reports/physics_compare", now=None, log=print, anchors=False, base=None, east=False):
    """Fetch once, simulate with C.PHYSICS and with physics_b on the same inputs and the same `now`. Returns 0 on success, 2 when no data.
    anchors=True (guide 03): A and B both run with physics_b (None / empty = C.PHYSICS), A with C.NUDGE_ANCHORS as configured, B with the station anchors on."""
    from .. import city
    from . import fetch
    now = now or fetch.utcnow()
    pa, pb = wetness.check_physics(C.PHYSICS if base is None else base), wetness.check_physics(physics_b)
    if anchors or east:     # east=True (guide 14): A and B both with station anchors on and physics_b; A the west library, B the east fields (live_east.npz)
        pa = pb = wetness.check_physics(tuple(physics_b or ()) or C.PHYSICS)
    static = Path(static or C.STATIC_DEFAULT)
    ribbons = Path(ribbons or static.parent / "live_ribbons.npz")
    cache = Path(cache or static.parent / "goes_cache")
    start, t0, end = build.window(now)
    n = int(round((end - t0).total_seconds() / 60.0 / C.STEP_MIN)) + 1
    S = city.load_static(static)
    Rb = dict(np.load(ribbons)) if ribbons.exists() else None
    st = build.Status()
    try:
        rr, obs, sector_fn, goes_hours = build.gather(st, t0, now, cache)
    except fetch.FetchError as e:
        log(f"cannot compare: {e}")
        return 2
    res = []
    nudge0, east0 = C.NUDGE_ANCHORS, C.WIND_EAST
    S_west, S_east = S, None
    if east:      # guide 14: A = station anchors with the west library, B = the same with the east fields (live_east.npz); everything else equal
        from . import east as _east
        S_east, einfo = _east.apply(S, static.parent / C.EAST_FILE)
        if S_east is None:
            log(f"cannot compare east on/off: {einfo['reason']}")
            return 2
    for P, nudge, e_on in ((pa, True if east else nudge0, False), (pb, True if (anchors or east) else nudge0, bool(east))):
        S = S_east if e_on else S_west
        build.attach_station_wind(obs, static, S, east=e_on)
        C.NUDGE_ANCHORS, C.WIND_EAST = nudge, e_on
        try:
            F = forcing.build(rr, t0, n, now, S["x"], S["y"], obs, sector_fn=sector_fn, log=log, physics=P, goes_hours=goes_hours, z=S["z"])
        finally:
            C.NUDGE_ANCHORS, C.WIND_EAST = nudge0, east0
        t = time.time()
        W, diag = wetness.simulate(build.point_table(S, build.load_extra(static, S, P, st, log)), F["ser"], C.STEP_MIN, tracer_dirs=S["tracer"], dirs_deg=S["dirs"], forcing_fn=F["forcing_fn"], lag_block_h=3,
                                   out_every=C.OUT_STEP_MIN // C.STEP_MIN, keep_lit=False, s_series=F["s_series"], fog_top=F["fog_top"], use_dirs=True, physics=P, ts0=F.get("pav_ts0"))
        log(f"simulate {sorted(P)}: {time.time() - t:.1f} s")
        res.append((W, diag, F))
    (Wa, da, Fa), (Wb, db, Fb) = res
    obs_until = Fa["info"]["obs_until"]
    k_obs = None if obs_until is None else int((dt.datetime.fromisoformat(obs_until) - t0).total_seconds() // 3600)
    rep = report(Wa, da, Wb, db, S, Rb, k_obs, sorted(pa), sorted(pb), now)
    rep["anchors_compare"] = bool(anchors)
    rep["east_compare"] = bool(east)
    rep["base_given"] = base is not None
    if anchors:
        rep["anchors_info"] = dict(a=Fa["info"].get("anchors"), b=Fb["info"].get("anchors"))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out / f"compare_{stamp}.json").write_text(json.dumps(rep, indent=1))
    md = to_markdown(rep)
    (out / f"compare_{stamp}.md").write_text(md, encoding="utf-8", newline="\n")
    log(md)
    return 0
