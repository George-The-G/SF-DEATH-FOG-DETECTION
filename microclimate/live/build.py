"""Live builder: `python -m microclimate.live build --out site/v3/live [--static PATH] [--ribbons PATH] [--cache DIR]` (PLAN_LIVE_DATA.md step 4).

1. window: start = previous 18:00 PT if now is before 18:00 PT, else today 18:00 PT; the model runs from start - 6 h (history) to start + 72 h
2. fetch every source (a failed source is logged and skipped: stations -> RRFS only; no RRFS -> the previous good output is left untouched)
3. spatial forcing + observation nudging (forcing.py), wetness.simulate on the whole-city static table (direction blending always on)
4. write the outputs of section 5 (outputs.py) and status.json
No GPU, no warp / xlb: only numpy, scipy-free numerics, pyproj, pvlib, pandas, requests, h5py, Pillow.
"""
import datetime as dt
import json
import time
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from .. import wetness
from . import config as C
from . import cloud_score, fetch, forcing, goes, outputs

PT = ZoneInfo("America/Los_Angeles")


def window(now):
    """-> (start (18:00 PT of the evening the window is built around), t0 (start - history), end)."""
    loc = now.astimezone(PT)
    d = loc.date() if loc.hour >= 18 else (loc - dt.timedelta(days=1)).date()
    start = dt.datetime(d.year, d.month, d.day, 18, tzinfo=PT).astimezone(dt.timezone.utc)
    return start, start - dt.timedelta(hours=C.HISTORY_H), start + dt.timedelta(hours=C.WINDOW_H)


class Status:
    def __init__(self):
        self.sources, self.failed, self.notes = {}, {}, []

    def ok(self, name, **kw):
        self.sources[name] = kw

    def fail(self, name, err):
        self.failed[name] = str(err)[:300]
        print(f"[source failed] {name}: {err}", flush=True)


def point_table(S):
    return {k: S[k] for k in ("x", "y", "z", "svf", "porous", "wind_ratio", "rel_wind", "horizon")} | {"tracer": S["tracer"][int(np.argmin(np.abs(S["dirs"] - 270.0)))]}


def run(out_dir, static=None, ribbons=None, cache=None, now=None, log=print):
    from .. import city
    t_run = time.time()
    now = now or fetch.utcnow()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    static = Path(static or C.STATIC_DEFAULT)
    ribbons = Path(ribbons or static.parent / "live_ribbons.npz")
    cache = Path(cache or static.parent / "goes_cache")
    st = Status()
    start, t0, end = window(now)
    n = int(round((end - t0).total_seconds() / 60.0 / C.STEP_MIN)) + 1
    log(f"now {now:%Y-%m-%d %H:%M}Z  window {t0:%Y-%m-%d %H:%M}Z .. {end:%Y-%m-%d %H:%M}Z ({n} steps), start (18:00 PT) {start:%Y-%m-%d %H:%M}Z")
    try:
        S = city.load_static(static)
        Rb = dict(np.load(ribbons)) if ribbons.exists() else None
    except Exception as e:  # noqa: BLE001
        return _abort(out, st, now, t_run, f"static table unreadable: {e!r}")
    # ---- sources
    obs = {}
    try:
        rr = fetch.rrfs()
        st.ok("rrfs", model=rr["model"], last_valid=rr["last_valid"].isoformat() if rr["last_valid"] else None, note=rr.get("note"))
    except fetch.FetchError as e:
        st.fail("rrfs", e)
        return _abort(out, st, now, t_run, "RRFS unavailable: previous good output kept")
    begin = t0 - dt.timedelta(hours=1)
    for name, fn in (("coops", lambda: fetch.coops(begin, now)), ("metar", lambda: fetch.metar(48)), ("sounding", lambda: fetch.sounding(now))):
        try:
            obs[name] = fn()
        except fetch.FetchError as e:
            st.fail(name, e)
            obs[name] = None
        except Exception as e:  # noqa: BLE001
            st.fail(name, repr(e))
            obs[name] = None
    try:
        obs["nws"] = {sid: fetch.nws(sid) for sid in C.NWS_STATIONS}
    except fetch.FetchError as e:
        st.fail("nws", e)
        obs["nws"] = {}
    if obs["coops"] is not None:
        st.ok("coops", newest=max(obs["coops"]["t"]).isoformat(), n=len(obs["coops"]["t"]))
    if obs["metar"] is not None:
        st.ok("metar", newest={k: (max(x["t"] for x in r).isoformat() if r else None) for k, r in obs["metar"].items()})
    if obs["sounding"] is not None:
        st.ok("sounding", valid=obs["sounding"]["valid"].isoformat(), levels=int(len(obs["sounding"]["z"])))
    if obs["nws"]:
        st.ok("nws", newest={k: max(x["t"] for x in r).isoformat() for k, r in obs["nws"].items()})
    goes_hours = {}

    def sector_fn(hours, dirs):
        want = [h for h in hours if h >= now - dt.timedelta(hours=C.GOES_CACHE_H)]
        res = goes.hourly(want, cache, log=lambda m: st.notes.append(m))
        goes_hours.update(res)
        got = [h for h in want if res.get(h) is not None]
        if not got:
            st.fail("goes", "no GOES scan could be read" + (f" ({st.notes[-1]})" if st.notes else ""))
        else:
            st.ok("goes", hours=len(got), of=len(want), newest=max(res[h].t for h in got).isoformat(), products=[C.GOES_MASK, C.GOES_HEIGHT])
        return [(res[h].sector_fraction(d) if res.get(h) is not None else None) for h, d in zip(hours, dirs)]
    # ---- forcing
    try:
        F = forcing.build(rr, t0, n, now, S["x"], S["y"], obs, sector_fn=sector_fn, log=log)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return _abort(out, st, now, t_run, f"forcing failed: {e!r}")
    # ---- model
    t_sim = time.time()
    W, diag = wetness.simulate(point_table(S), F["ser"], C.STEP_MIN, tracer_dirs=S["tracer"], dirs_deg=S["dirs"], forcing_fn=F["forcing_fn"], lag_block_h=3,
                               out_every=C.OUT_STEP_MIN // C.STEP_MIN, keep_lit=False, s_series=F["s_series"], fog_top=F["fog_top"], use_dirs=True)
    sim_s = time.time() - t_sim
    log(f"simulate {sim_s:.1f} s, frames {W.shape}")
    # ---- outputs
    cs_hook = cloud_score.Hook()
    try:
        meta = outputs.write(out, W, diag, F, S, Rb, t0, start, now, goes_hours, static.parent, log=log, tiles_cb=cs_hook.tiles_cb)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return _abort(out, st, now, t_run, f"writing outputs failed: {e!r}")
    # satellite cloud score (guide 04): reads only, never fatal; any problem is logged into the status notes
    cs_notes = []
    cs_info = cloud_score.run_safe(out, cs_hook, rr, F, S, diag, goes_hours, t0, now, cs_notes, log=log)
    status = dict(ok=True, model_version=C.MODEL_VERSION, generated_iso=now.isoformat(), run_seconds=round(time.time() - t_run, 1), simulate_seconds=round(sim_s, 1),
                  window=dict(start_18pt_iso=start.isoformat(), t0_iso=t0.isoformat(), end_iso=end.isoformat(), step_min=C.STEP_MIN, out_step_min=C.OUT_STEP_MIN),
                  observed_until_iso=F["info"]["obs_until"], forecast_from_iso=F["info"]["obs_until"], rrfs_model=rr["model"],
                  sources_used=sorted(st.sources), sources_failed=st.failed, data_as_of=st.sources, notes=st.notes[:20] + cs_notes + F["info"]["flags"],
                  forcing=F["info"], mode=("rrfs_only" if not any(obs[k] is not None for k in ("coops", "metar")) else "observations_nudged"),
                  points=int(len(S["x"])), outputs=meta, cloud_score=cs_info)
    (out / "status.json").write_text(json.dumps(status, indent=1, default=str))
    log(f"done in {status['run_seconds']} s; sources used {status['sources_used']}, failed {list(st.failed)}")
    return 0


def _abort(out, st, now, t_run, msg):
    """Leave the previous good output in place; record the failure in status.json (merged into an existing one)."""
    p = out / "status.json"
    prev = {}
    if p.exists():
        try:
            prev = json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            prev = {}
    prev.update(dict(last_run_failed=True, last_failure_iso=now.isoformat(), last_failure=msg, sources_failed=st.failed,
                     last_run_seconds=round(time.time() - t_run, 1)))
    if "ok" not in prev:
        prev["ok"] = False
    p.write_text(json.dumps(prev, indent=1, default=str))
    print("ABORT:", msg, flush=True)
    return 2
