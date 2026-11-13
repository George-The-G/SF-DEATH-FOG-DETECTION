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
from . import archive, cloud_score, fetch, forcing, goes, outputs, state as statelib

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


def point_table(S, extra=None):
    """Point dict for wetness.simulate. extra (city.load_extra, guide 06): replaces the bearing and adds slope and surface class (street direction, slope along it, class)."""
    pts = {k: S[k] for k in ("x", "y", "z", "svf", "porous", "wind_ratio", "rel_wind", "horizon")} | {"tracer": S["tracer"][int(np.argmin(np.abs(S["dirs"] - 270.0)))]}
    if extra is not None:
        pts.update(bearing=extra["bearing"], slope=extra["slope"], surf=extra["surf"])
    return pts


def load_extra(static, S, P, st, log=print):
    """live_extra.npz next to the static table, only when a switch needs it (sun_clear, film). A missing or mismatching file = flat asphalt: noted in the status."""
    if not ({"sun_clear", "film"} & set(P)):
        return None
    from .. import city
    ex = city.load_extra(Path(static).parent / C.EXTRA_FILE, len(S["x"]), S["x"])
    if ex is None:
        st.notes.append(f"{C.EXTRA_FILE} missing or not for this point table: flat asphalt streets (no slope, no surface class)")
        log(st.notes[-1])
    return ex


def attach_station_wind(obs, static, S):
    """obs["station_wind"] (guide 03): the simulated wind vectors at the stations, from station_wind.npz next to the static table; None when missing or unreadable."""
    try:
        from . import stations as _stations
        obs["station_wind"] = _stations.load_station_wind(Path(static).parent / C.STATION_WIND_FILE, S["dirs"])
    except Exception:  # noqa: BLE001
        obs["station_wind"] = None


def gather(st, t0, now, cache):
    """Fetch every source once. -> (rrfs, obs, sector_fn, goes_hours). Raises fetch.FetchError when RRFS is unavailable (every other source may fail softly)."""
    obs = {}
    try:
        rr = fetch.rrfs()
        st.ok("rrfs", model=rr["model"], last_valid=rr["last_valid"].isoformat() if rr["last_valid"] else None, note=rr.get("note"))
    except fetch.FetchError as e:
        st.fail("rrfs", e)
        raise
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
    # extra stations (guide 03 step 2): optional; parallel, capped by STATION_FETCH_DEADLINE_S, any failure is recorded and never fails the build
    try:
        from . import stations
        obs["stations"] = stations.collect(obs, t0, now)
        n_ok = sum(1 for d in obs["stations"]["raw"].values() if d)
        st.ok("stations", reporting=n_ok, requests=obs["stations"]["jobs"], seconds=obs["stations"]["seconds"])
    except Exception as e:  # noqa: BLE001
        st.notes.append(f"stations: {e!r}"[:200])
        obs["stations"] = None
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
    return rr, obs, sector_fn, goes_hours


INPUTS_VERSION = 1
PHYSICS_LABEL = {"sun_clear": "clear-sky sun", "film": "water film", "pavement": "pavement heat"}


def settings(beta=False, physics=None, carry_state=None):
    """The settings of one build -> dict(physics (tuple), nudge_anchors, carry_state). The public build reads C.PHYSICS / C.NUDGE_ANCHORS / C.CARRY_STATE; the beta build
    reads ONLY C.BETA (an explicit physics / carry_state argument still wins, for tests and the command line). The two never mix."""
    if beta:
        b = dict(C.BETA)
        return dict(physics=tuple(C.BETA["physics"] if physics is None else physics), nudge_anchors=bool(b.get("nudge_anchors", False)),
                    carry_state=bool(b.get("carry_state", False) if carry_state is None else carry_state))
    return dict(physics=tuple(C.PHYSICS if physics is None else physics), nudge_anchors=bool(C.NUDGE_ANCHORS), carry_state=bool(C.CARRY_STATE if carry_state is None else carry_state))


def beta_label(s):
    """Words for the badge of the preview page, e.g. 'clear-sky sun (GOES sunlight), station anchors'."""
    parts = []
    for p in s["physics"]:
        parts.append(PHYSICS_LABEL.get(p, p) + (" (GOES sunlight)" if p == "sun_clear" and C.SUN_DSR else ""))
    if s["nudge_anchors"]:
        parts.append("station anchors")
    if s["carry_state"]:
        parts.append("carried state")
    return ", ".join(parts) if parts else "no changes"


def snapshot_inputs(rr, obs):
    """Bytes of the fetched RRFS and observations, taken right after gather() and before the forcing touches them. None when they cannot be pickled."""
    import pickle
    try:
        return pickle.dumps((rr, obs), protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:  # noqa: BLE001
        print(f"inputs not snapshotted: {e!r}", flush=True)
        return None


def save_inputs(path, now, snap, goes_hours, st):
    """Write the fetched inputs of this run (snapshot from snapshot_inputs, the GOES scans the forcing read, the source bookkeeping) so the beta build of the same job
    reuses them: same data, the external APIs are not hit twice. Never fatal; returns the file size or None."""
    import pickle
    try:
        if snap is None:
            return None
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(path) + ".tmp")
        with open(tmp, "wb") as f:
            pickle.dump(dict(version=INPUTS_VERSION, model_version=C.MODEL_VERSION, now=now, snap=snap, goes_hours=goes_hours, sources=st.sources, failed=st.failed, notes=st.notes),
                        f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)
        return Path(path).stat().st_size
    except Exception as e:  # noqa: BLE001
        print(f"inputs not saved: {e!r}", flush=True)
        return None


def load_inputs(path):
    """-> dict(now, rr, obs, goes_hours, sources, failed, notes) as save_inputs wrote it, or None (missing, unreadable, other version)."""
    import pickle
    try:
        with open(path, "rb") as f:
            d = pickle.load(f)
        if d.get("version") != INPUTS_VERSION or d.get("model_version") != C.MODEL_VERSION:
            return None
        d["rr"], d["obs"] = pickle.loads(d.pop("snap"))
        return d
    except Exception as e:  # noqa: BLE001
        print(f"inputs not loaded: {e!r}", flush=True)
        return None


def replay_sector_fn(goes_hours, now):
    """The sector function of gather() over the already read GOES scans (no download): same hours, same values."""
    def fn(hours, dirs):
        lim = now - dt.timedelta(hours=C.GOES_CACHE_H)
        return [(goes_hours[h].sector_fraction(d) if h >= lim and goes_hours.get(h) is not None else None) for h, d in zip(hours, dirs)]
    return fn


def fetch_dsr(st, t0, now, out, log=print):
    """GOES downward shortwave of the observed daylight hours (dsr.py; rolling cache in the output folder). -> ({hour: Dsr}, stats). Never fatal."""
    try:
        from .. import sunclear
        from . import dsr
        nobs = int(np.floor((now - t0).total_seconds() / 3600.0)) + 1
        hours = [t0 + dt.timedelta(hours=k) for k in range(max(nobs, 0))]
        hours = [h for h in hours if h >= now - dt.timedelta(hours=C.GOES_CACHE_H)]
        ghi, _, _ = sunclear.clear_sky(hours, wetness.SUN_LAT, wetness.SUN_LON)
        day = [h for h, g in zip(hours, ghi) if g >= C.DSR_MIN_GHI]
        res, stats = dsr.hourly(day, Path(out) / C.DSR_CACHE_FILE, now, log=lambda m: st.notes.append(m))
        stats["daylight_hours"] = len(day)
        if res:
            st.ok("goes_dsr", hours=len(res), of=len(day), newest=max(d.t for d in res.values()).isoformat(), product=stats.get("product"))
        elif day:
            st.fail("goes_dsr", "no DSR scan could be read" + (f" ({st.notes[-1]})" if st.notes else ""))
        log(f"dsr: {stats}")
        return res, stats
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        st.fail("goes_dsr", repr(e))
        return None, dict(error=repr(e)[:200])


def run(out_dir, static=None, ribbons=None, cache=None, now=None, log=print, physics=None, carry_state=None, archive_dir=None, beta=False, inputs=None, inputs_out=None):
    """One live run. beta=True: the beta preview channel (settings from C.BETA only, never writes the run archive, status gets "channel": "beta").
    inputs: path of a pickle written by an earlier (public) run of the same job (save_inputs): its fetched data is reused instead of fetching again; a missing or unreadable
    file means the beta run fetches for itself (noted in the status). inputs_out: path to write this run's fetched inputs to (the public run, for the beta run)."""
    from .. import city
    t_run = time.time()
    loaded = load_inputs(inputs) if (inputs and beta) else None
    now = (loaded["now"] if loaded else None) or now or fetch.utcnow()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    static = Path(static or C.STATIC_DEFAULT)
    ribbons = Path(ribbons or static.parent / "live_ribbons.npz")
    cache = Path(cache or static.parent / "goes_cache")
    S_set = settings(beta, physics, carry_state)
    P = wetness.check_physics(S_set["physics"])
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
    inp_info = None
    snap = None
    try:
        if loaded:
            rr, obs, goes_hours = loaded["rr"], loaded["obs"], loaded["goes_hours"]
            st.sources.update(loaded["sources"])
            st.failed.update(loaded["failed"])
            st.notes.extend(loaded["notes"])
            sector_fn = replay_sector_fn(goes_hours, now)
            inp_info = dict(source="public build of the same job (fetched once)", fetched_iso=now.isoformat())
        else:
            rr, obs, sector_fn, goes_hours = gather(st, t0, now, cache)
            if beta:
                inp_info = dict(source="own fetch", reason="no inputs file given" if not inputs else "inputs file missing, unreadable or from another model version")
            if inputs_out and not beta:
                snap = snapshot_inputs(rr, obs)
    except fetch.FetchError as e:
        return _abort(out, st, now, t_run, "RRFS unavailable: previous good output kept")
    # ---- GOES downward shortwave (sun_clear runs only: the public build has none)
    dsr_hours, dsr_stats = None, None
    if "sun_clear" in P and C.SUN_DSR:
        dsr_hours, dsr_stats = fetch_dsr(st, t0, now, out, log)
    # ---- forcing
    nudge0 = C.NUDGE_ANCHORS
    try:
        extra = load_extra(static, S, P, st, log)
        attach_station_wind(obs, static, S)
        C.NUDGE_ANCHORS = S_set["nudge_anchors"]      # this build's setting only; restored below (the beta build must never leave its value behind)
        F = forcing.build(rr, t0, n, now, S["x"], S["y"], obs, sector_fn=sector_fn, log=log, physics=P, goes_hours=goes_hours, z=S["z"], dsr_hours=dsr_hours)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return _abort(out, st, now, t_run, f"forcing failed: {e!r}")
    finally:
        C.NUDGE_ANCHORS = nudge0
    if inputs_out and not beta:
        sz = save_inputs(inputs_out, now, snap, goes_hours, st)
        log(f"fetched inputs for the beta build: {sz} bytes")
    # ---- carried state (guide 02 A): never fatal, any problem is a cold start
    carry = S_set["carry_state"]
    sinfo = dict(carry_state=bool(carry), start="cold", anchor_time=None, saved=None, anchor=None)
    state0, save_at, next_t0 = None, None, None
    try:
        arrays, why = statelib.find_anchor(out, t0, P, C.MODEL_VERSION, len(S["x"]))
        sinfo["anchor"] = why
        if arrays is not None and carry:
            state0 = arrays
            sinfo.update(start="anchor", anchor_time=t0.isoformat())
        elif carry:
            F["info"]["flags"].append(f"cold start ({why})")
        next_t0 = statelib.next_t0(start)
        k = int(round((next_t0 - t0).total_seconds() / 60.0 / C.STEP_MIN))
        if F["obs_until"] is not None and next_t0 <= F["obs_until"] and 0 <= k < n:
            save_at = k
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        sinfo["error"] = repr(e)[:200]
        state0, save_at = None, None
    # ---- pavement heat (guide 07): the length of the carried chain of road temperatures; the flag stays until PAV_SPINUP_DAYS days are in it
    pavinfo, pav_chain = None, 0
    if "pavement" in P:
        carried = state0 is not None and "ts" in state0 and "tdeep" in state0
        pav_chain = int(np.asarray(state0.get("pav_days", 0)).reshape(-1)[0]) if carried else 0
        pavinfo = dict(carried=bool(carried), days=pav_chain, spin_up=pav_chain < C.PAV_SPINUP_DAYS, cold_start_air_mean=F["info"].get("pavement"))
        if pavinfo["spin_up"]:
            F["info"]["flags"].append("pavement spin-up")
    # ---- model
    t_sim = time.time()
    W, diag = wetness.simulate(point_table(S, extra), F["ser"], C.STEP_MIN, tracer_dirs=S["tracer"], dirs_deg=S["dirs"], forcing_fn=F["forcing_fn"], lag_block_h=3,
                               out_every=C.OUT_STEP_MIN // C.STEP_MIN, keep_lit=False, s_series=F["s_series"], fog_top=F["fog_top"], use_dirs=True, physics=P,
                               state0=state0, save_at=save_at, ts0=F.get("pav_ts0"))
    if pavinfo is not None and diag.get("state_at") is not None and "ts" in diag["state_at"]:
        diag["state_at"]["pav_days"] = np.array(pav_chain + 1, np.int16)
    sim_s = time.time() - t_sim
    log(f"simulate {sim_s:.1f} s, frames {W.shape}")
    # ---- outputs
    cs_hook = cloud_score.Hook()
    try:
        meta = outputs.write(out, W, diag, F, S, Rb, t0, start, now, goes_hours, static.parent, log=log, tiles_cb=cs_hook.tiles_cb)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return _abort(out, st, now, t_run, f"writing outputs failed: {e!r}")
    if beta:   # the index says what it is, so a copied link or a file listing cannot be taken for the public data
        try:
            idx_p = out / "index.json"
            idx = json.loads(idx_p.read_text())
            idx.update(channel="beta", label=f"Beta preview ({beta_label(S_set)}): " + str(idx.get("label", "")))
            idx_p.write_text(json.dumps(idx, indent=1))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            st.notes.append(f"beta label not written: {e!r}"[:160])
    # stations.json (guide 03 step 4): read-only data for the page; never fatal
    sinfo_st = dict(file=None)
    try:
        sj = F.get("stations_json")
        if sj is not None:
            (out / C.STATIONS_FILE).write_text(json.dumps(sj, separators=(",", ":"), default=str))
            sinfo_st = dict(file=C.STATIONS_FILE, bytes=(out / C.STATIONS_FILE).stat().st_size, nudge_anchors=bool(S_set["nudge_anchors"]),
                            fetch_failed=(obs.get("stations") or {}).get("failed", {}), fetch_seconds=(obs.get("stations") or {}).get("seconds"),
                            newest=(obs.get("stations") or {}).get("newest", {}), wind=(F["info"].get("anchors") or {}).get("wind"))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        sinfo_st = dict(file=None, error=repr(e)[:200])
    # satellite cloud score (guide 04): reads only, never fatal; any problem is logged into the status notes
    cs_notes = []
    cs_info = cloud_score.run_safe(out, cs_hook, rr, F, S, diag, goes_hours, t0, now, cs_notes, log=log)
    try:
        if diag.get("state_at") is not None:
            name = statelib.name_for(next_t0)
            size = statelib.save(out / name, next_t0, P, C.MODEL_VERSION, diag["state_at"])
            sinfo.update(saved=name, saved_bytes=size)
        keep = statelib.prune(out)
        idx_p = out / "index.json"
        idx = json.loads(idx_p.read_text())
        idx["state_files"] = keep
        idx_p.write_text(json.dumps(idx, indent=1))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        sinfo["error"] = repr(e)[:200]
    log(f"state: {sinfo}")
    ainfo = None
    if archive_dir and C.ARCHIVE and not beta:      # the beta channel never writes the run archive
        try:
            name, size = archive.write_run(archive_dir, W, diag, F, S, t0, now, P, C.MODEL_VERSION, C.OUT_STEP_MIN // C.STEP_MIN)
            ainfo = dict(file=name, bytes=size)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            ainfo = dict(error=repr(e)[:200])
        log(f"archive: {ainfo}")
    status = dict(ok=True, model_version=C.MODEL_VERSION, physics=sorted(P), state=sinfo, archive=ainfo, generated_iso=now.isoformat(), run_seconds=round(time.time() - t_run, 1), simulate_seconds=round(sim_s, 1),
                  window=dict(start_18pt_iso=start.isoformat(), t0_iso=t0.isoformat(), end_iso=end.isoformat(), step_min=C.STEP_MIN, out_step_min=C.OUT_STEP_MIN),
                  observed_until_iso=F["info"]["obs_until"], forecast_from_iso=F["info"]["obs_until"], rrfs_model=rr["model"],
                  sources_used=sorted(st.sources), sources_failed=st.failed, data_as_of=st.sources, notes=st.notes[:20] + cs_notes + F["info"]["flags"],
                  forcing=F["info"], mode=("rrfs_only" if not any(obs[k] is not None for k in ("coops", "metar")) else "observations_nudged"),
                  points=int(len(S["x"])), outputs=meta, cloud_score=cs_info, stations=sinfo_st)
    if pavinfo is not None:
        status["pavement"] = pavinfo
    if beta:   # the preview channel says so in its own status (the page badge reads "beta")
        status["channel"] = "beta"
        status["beta"] = dict(label=beta_label(S_set), physics=list(S_set["physics"]), nudge_anchors=S_set["nudge_anchors"], carry_state=S_set["carry_state"],
                              sun_dsr=bool(C.SUN_DSR and "sun_clear" in P), inputs=inp_info)
        if dsr_stats is not None:
            status["dsr"] = dsr_stats
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
