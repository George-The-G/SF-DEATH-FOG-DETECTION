"""Satellite cloud score (docs/model_guides/04_satellite_cloud_score.md): rate the model's low cloud and fog against GOES-18 every run and keep a rolling history.

Observed = `LowCloud.box_fraction` of the hourly GOES scans (share of low-cloud pixels in a box). Boxes: the 30 lattice cells (about 3 km), an upwind ocean box,
a bay box, and a coast box (the union of the lattice cells that form the coast series s(t)). Model fields scored (fractions 0..1, yes = at or above CLOUD_YES):

  rrfs             raw RRFS low cloud cover of the lattice (before any nudging); ocean / bay boxes: bilinear at the box centre; coast box: mean of the coast points
  source           the fog source s(t) used by the model after the corrections, a coast scalar: scored on the ocean and coast boxes only. It is nudged with
                   the same satellite data, so it measures consistency, not skill
  fog_sheet        the fog tiles drawn on the page (outputs.fog_frames), averaged over the tile cells inside each box. For observed hours the tiles already
                   contain the satellite clear-sky factor: consistency again
  fog_sheet_nosat  the same tiles computed without the satellite factor (extra to the guide): the honest measure of the fog layer

A box-hour is not scorable when no scan exists, fewer than CLOUD_VALID_MIN of its pixels have a mask, or more than CLOUD_HIGH_MAX of the valid pixels are
cloud that is not low (tops above LOW_TOP_M or no height: high cloud hides the fog). Day and night (sun elevation above / below 0) are scored apart.

Rolling file `cloud_score.json` (schema 1): hour-level tallies for the last HOURS_LIVE_H hours (a new run replaces the hours it covers), older hours are folded
once into per-date tallies (local PT dates, kept CLOUD_KEEP_DAYS days); summaries (24 h, 7 d, lead times, timing) are derived from them on every write.
Tally of one (field, hour): [n, abs, hit, miss, fa, cn, u_high, u_valid, u_model, u_sat] = scored box-hours, summed absolute error, yes/no counts and the
unscorable box-hours by reason. Nothing here ever writes to the forcing, fog or wetness arrays it reads, and the whole step is wrapped so it can never
fail a build (`run_safe`).

Lead times need the run archive (guide 02 part B). `read_archive_run` is the one place that knows the archive file format; see its docstring.
"""
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from . import config as C

PT = ZoneInfo("America/Los_Angeles")
FIELDS = ("rrfs", "source", "fog_sheet", "fog_sheet_nosat")
TALLY_KEYS = ("n", "abs", "hit", "miss", "fa", "cn", "u_high", "u_valid", "u_model", "u_sat")
REASONS = ("high_cloud", "few_valid_pixels", "no_model_value", "no_satellite_scan")     # same order as TALLY_KEYS[6:]
SCHEMA = 1
HOURS_LIVE_H = 48                     # hour-level tallies kept (a run rescored the last GOES_CACHE_H = 40 h); older hours are folded into the per-date tallies
SAT_NODATA, SAT_HIGH, SAT_SCALE = 255, 252, 250


# ------------------------------------------------------------------ boxes
def lattice_geometry(lat, lon, x):
    """lat, lon, x: lattice arrays [L] (lat-major, x = UTM easting m). -> dict(names, kinds, geom [nb, 4] = lon0, lat0, lon1, lat1, west [idx of the coast points]).
    The 30 cells come first, then 'upwind' (ocean), 'bay' and 'coast'."""
    lat, lon, x = (np.asarray(a, float) for a in (lat, lon, x))
    dlat = (C.LAT_RANGE[1] - C.LAT_RANGE[0]) / (C.N_LAT - 1)
    dlon = (C.LON_RANGE[1] - C.LON_RANGE[0]) / (C.N_LON - 1)
    cells = np.stack([lon - dlon / 2, lat - dlat / 2, lon + dlon / 2, lat + dlat / 2], axis=1)
    west = x < C.COAST_X_MAX                                     # same rule as forcing.build
    if not west.any():
        west = x <= np.sort(x)[C.N_LAT - 1]
    coast = (cells[west, 0].min(), cells[west, 1].min(), cells[west, 2].max(), cells[west, 3].max())
    geom = np.vstack([cells, np.array([C.UPWIND_BOX, C.BAY_BOX, coast], float)])
    names = [f"cell{k:02d}" for k in range(len(lat))] + ["upwind", "bay", "coast"]
    kinds = ["cell"] * len(lat) + ["ocean", "bay", "coast"]
    return dict(names=names, kinds=kinds, geom=geom, west=np.nonzero(west)[0])


def members(field, kinds):
    """Boolean mask [nb] of the boxes a field is scored on."""
    if field == "source":
        return np.array([k in ("ocean", "coast") for k in kinds])
    return np.array([k in ("cell", "ocean", "bay") for k in kinds])


# ------------------------------------------------------------------ observation
def observe(goes_hours, hours, geom):
    """goes_hours {hour: LowCloud or None}; hours: list of aware datetimes; geom [nb, 4]. -> low, high, valid arrays [nh, nb]; valid is NaN for an hour with no scan."""
    nh, nb = len(hours), len(geom)
    low, high, valid = (np.full((nh, nb), np.nan) for _ in range(3))
    for i, h in enumerate(hours):
        lc = goes_hours.get(h) if goes_hours else None
        if lc is None:
            continue
        for j in range(nb):
            low[i, j], high[i, j], valid[i, j] = lc.box_fraction(*geom[j])
    return low, high, valid


def box_status(low, high, valid):
    """0 scorable, 1 high cloud hides the box, 2 too few valid pixels, 3 no satellite scan. Arrays of any shape."""
    low, high, valid = (np.asarray(a, float) for a in (low, high, valid))
    st = np.zeros(low.shape, int)
    with np.errstate(invalid="ignore"):
        st[~np.isfinite(valid)] = 3
        st[(st == 0) & ((valid < C.CLOUD_VALID_MIN) | ~np.isfinite(low))] = 2
        st[(st == 0) & (high > C.CLOUD_HIGH_MAX)] = 1
    return st


def tally(obs_low, model, status, member):
    """One hour, one field -> [n, abs, hit, miss, fa, cn, u_high, u_valid, u_model, u_sat] over the member boxes (see the module docstring)."""
    t = [0] * 10
    for j in np.nonzero(member)[0]:
        s = status[j]
        if s == 3:
            t[9] += 1
        elif s == 2:
            t[7] += 1
        elif s == 1:
            t[6] += 1
        elif not np.isfinite(model[j]):
            t[8] += 1
        else:
            o, m = float(obs_low[j]), float(model[j])
            t[0] += 1
            t[1] += abs(m - o)
            yo, ym = o >= C.CLOUD_YES, m >= C.CLOUD_YES
            t[2 if (yo and ym) else 3 if yo else 4 if ym else 5] += 1
    t[1] = round(t[1], 3)
    return t


def add_tally(a, b):
    return [round(x + y, 3) if i == 1 else x + y for i, (x, y) in enumerate(zip(a, b))]


def metrics(tallies):
    """list of hourly tallies -> dict(box_hours, hours, mae, hits, misses, false_alarms, correct_clears, hit_rate, false_alarm_ratio, csi, accuracy,
    unscorable_box_hours {reason: n}, unscorable_hours {reason: n}); numbers are None where undefined."""
    tot = [0] * 10
    h_ok, h_un = 0, [0, 0, 0, 0]
    for t in tallies:
        tot = add_tally(tot, t)
        if t[0] > 0:
            h_ok += 1
        elif sum(t[6:]) > 0:
            h_un[int(np.argmax(t[6:]))] += 1
    return _metrics_from(tot, h_ok, h_un)


def _metrics_from(tot, h_ok, h_un):
    n, ab, hit, miss, fa, cn = tot[:6]
    r = lambda v: None if v is None else round(v, 3)  # noqa: E731
    return dict(box_hours=int(n), hours=int(h_ok), mae=r(ab / n) if n else None, hits=int(hit), misses=int(miss), false_alarms=int(fa), correct_clears=int(cn),
                hit_rate=r(hit / (hit + miss)) if hit + miss else None, false_alarm_ratio=r(fa / (hit + fa)) if hit + fa else None,
                csi=r(hit / (hit + miss + fa)) if hit + miss + fa else None, accuracy=r((hit + cn) / n) if n else None,
                unscorable_box_hours=dict(zip(REASONS, (int(v) for v in tot[6:]))), unscorable_hours=dict(zip(REASONS, (int(v) for v in h_un))))


# ------------------------------------------------------------------ model fields
def box_weights(boxes, lattice, n_lat_pts):
    """Matrix [nb, L]: how each box value is built from the lattice (cell: its own point; ocean / bay: bilinear at the centre; coast: mean of the coast points)."""
    geom = boxes["geom"]
    Wm = np.zeros((len(geom), n_lat_pts))
    ncell = sum(1 for k in boxes["kinds"] if k == "cell")
    for j in range(len(geom)):
        k = boxes["kinds"][j]
        if k == "cell":
            Wm[j, j] = 1.0
        elif k == "coast":
            Wm[j, boxes["west"]] = 1.0 / len(boxes["west"])
        else:
            lon_c, lat_c = 0.5 * (geom[j, 0] + geom[j, 2]), 0.5 * (geom[j, 1] + geom[j, 3])
            idx, w = lattice.weights(np.array([lat_c]), np.array([lon_c]))
            for kk in range(4):
                Wm[j, idx[0, kk]] += w[0, kk]
    assert ncell == n_lat_pts
    return Wm


def apply_weights(Wm, col):
    """Wm [nb, L] @ col [L] with NaN only where a used (non-zero weight) point is NaN."""
    used = Wm > 0
    prod = np.where(used, Wm * np.where(np.isfinite(col), col, 0.0)[None, :], 0.0)
    bad = (used & ~np.isfinite(col)[None, :]).any(axis=1)
    out = prod.sum(axis=1)
    out[bad] = np.nan
    return out


def tile_box_index(geom, lon, lat):
    """Flat indices of the fog-tile cells inside every box (lon, lat: 2-D tile centres) -> list of int arrays."""
    lo, la = lon.ravel(), lat.ravel()
    return [np.nonzero((lo >= g[0]) & (lo <= g[2]) & (la >= g[1]) & (la <= g[3]))[0] for g in geom]


def tile_means(tile, idx):
    """tile uint8 [H, W] -> fraction 0..1 averaged inside each box ([nb], NaN where the box has no tile cell). Reads only."""
    flat = np.asarray(tile).ravel()
    return np.array([flat[i].mean() / 255.0 if len(i) else np.nan for i in idx])


# ------------------------------------------------------------------ timing
def arrival_clear(yes):
    """yes: array [nh] of 1.0 / 0.0 / NaN (unknown). Arrival = first hour k with yes at k and k+1; clearing = first hour after it with no at k and k+1.
    -> (arrival index or None, clearing index or None)."""
    y = np.asarray(yes, float)
    arr = next((k for k in range(len(y) - 1) if y[k] == 1 and y[k + 1] == 1), None)
    if arr is None:
        return None, None
    clr = next((k for k in range(arr + 1, len(y) - 1) if y[k] == 0 and y[k + 1] == 0), None)
    return arr, clr


def timing(obs_yes, model_yes, hours):
    """-> dict(arrival_obs, arrival_model, arrival_err_h, clear_obs, clear_model, clear_err_h): iso hours or None; err = model - observed in hours, only when both exist."""
    ao, co = arrival_clear(obs_yes)
    am, cm = arrival_clear(model_yes)
    iso = lambda k: None if k is None else hours[k].isoformat()  # noqa: E731
    return dict(arrival_obs=iso(ao), arrival_model=iso(am), arrival_err_h=None if ao is None or am is None else int(am - ao),
                clear_obs=iso(co), clear_model=iso(cm), clear_err_h=None if co is None or cm is None else int(cm - co))


# ------------------------------------------------------------------ archive (guide 02 part B) and lead times
ARCHIVE_FRAME_KEYS = ("frame_times_iso", "frame_times", "times")
ARCHIVE_LOWCLOUD_KEYS = ("lattice_low_cloud", "lattice_cloud_low", "cloud_low_lattice")


def read_archive_run(path):
    """THE ONLY PLACE THAT KNOWS THE ARCHIVE FORMAT. Read one `run_<UTC yyyymmddThhmm>.npz` of the run archive (guide 02 part B) -> dict(run_time, frame_times,
    low) or None when the file does not carry what is needed.
      run_time     aware UTC datetime parsed from the file name
      frame_times  list of aware UTC datetimes, one per saved frame (stored as ISO strings, or as epoch seconds)
      low          float array [n_frames, L] of the lattice low cloud cover as a fraction (stored as a fraction or in percent; 30 lattice points, lat-major)
    Adjust ARCHIVE_FRAME_KEYS / ARCHIVE_LOWCLOUD_KEYS or this function when the archive writer settles on other names."""
    p = Path(path)
    m = re.match(r"run_(\d{8})T(\d{4})", p.name)
    if not m:
        return None
    try:
        rt = dt.datetime.strptime(m[1] + m[2], "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
        with np.load(p, allow_pickle=False) as z:
            fk = next((k for k in ARCHIVE_FRAME_KEYS if k in z.files), None)
            lk = next((k for k in ARCHIVE_LOWCLOUD_KEYS if k in z.files), None)
            if fk is None or lk is None:
                return None
            ft, low = z[fk], np.asarray(z[lk], float)
        if ft.dtype.kind in "US" or ft.dtype == object:
            times = [dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")) for s in ft]
            times = [t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc) for t in times]
        else:
            times = [dt.datetime.fromtimestamp(float(s), dt.timezone.utc) for s in ft]
        if low.ndim != 2 or low.shape[0] != len(times):
            return None
        if np.nanmax(low) > 1.5:           # percent
            low = low / 100.0
        return dict(run_time=rt, frame_times=times, low=low)
    except Exception:  # noqa: BLE001  (an unreadable file is skipped, never fatal)
        return None


def lead_models(archive_dir, hours, now, n_cells, leads=None, tol_h=None):
    """Lattice low cloud of earlier runs for the scored hours. -> (status text, {lead_h: {hour: low array [n_cells]}}, counts dict).
    For every (hour, lead) the archive run whose lead is closest to the wanted one (within tol_h; the later run on a tie) is used."""
    leads = C.CLOUD_LEADS_H if leads is None else leads
    tol_h = C.CLOUD_LEAD_TOL_H if tol_h is None else tol_h
    if not archive_dir or not Path(archive_dir).is_dir():
        return "no archive yet", {}, dict(files=0)
    files = sorted(Path(archive_dir).rglob("run_*.npz"))
    if not files:
        return "no archive yet", {}, dict(files=0)
    h0, h1 = min(hours), max(hours)
    best = {}                                  # (lead, hour) -> (|lead error|, run_time, row)
    read = usable = 0
    for f in files:
        m = re.match(r"run_(\d{8})T(\d{4})", f.name)
        if not m:
            continue
        rt = dt.datetime.strptime(m[1] + m[2], "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
        if rt > h1 or rt < h0 - dt.timedelta(hours=max(leads) + tol_h):
            continue                           # cannot reach any scored hour
        run = read_archive_run(f)
        read += 1
        if run is None or run["low"].shape[1] != n_cells:
            continue
        usable += 1
        for k, t in enumerate(run["frame_times"]):
            if not (h0 <= t <= h1) or t.minute != 0:
                continue
            ld = (t - run["run_time"]).total_seconds() / 3600.0
            for L in leads:
                err = abs(ld - L)
                if err <= tol_h and ((L, t) not in best or (err, -rt.timestamp()) < (best[(L, t)][0], -best[(L, t)][1].timestamp())):
                    best[(L, t)] = (err, rt, run["low"][k])
    out = {}
    for (L, t), (_, _, row) in best.items():
        out.setdefault(L, {})[t] = row
    counts = dict(files=len(files), read=read, usable=usable, pairs=len(best))
    if usable == 0:
        return "archive present but no file with lattice low cloud could be read", {}, counts
    return ("ok" if best else "archive has no frame at the wanted lead times inside the scored hours"), out, counts


# ------------------------------------------------------------------ rolling file
def day_key(hour_iso):
    t = dt.datetime.strptime(hour_iso, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc)
    return t.astimezone(PT).strftime("%Y-%m-%d")


def hour_key(t):
    return t.strftime("%Y-%m-%dT%H")


def _fold_record(rec, tallies_by_name, d):
    """Add one hour (day/night d) into a per-date record: {name: {'d'|'n': [tally(10) + h_ok + h_un(4)]}}."""
    for name, t in tallies_by_name.items():
        slot = rec.setdefault(name, {}).setdefault("d" if d else "n", [0] * 10 + [0] + [0] * 4)
        base = add_tally(slot[:10], t)
        ok = slot[10] + (1 if t[0] > 0 else 0)
        un = slot[11:15]
        if t[0] == 0 and sum(t[6:]) > 0:
            un = list(un)
            un[int(np.argmax(t[6:]))] += 1
        slot[:] = base + [ok] + list(un)


def _slot_metrics(slot):
    return _metrics_from(slot[:10], slot[10], slot[11:15])


def _compact(m):
    """The short form used in the daily rows (the full numbers per slot are in `days`): n box-hours, h scorable hours, mae, hit rate, false-alarm ratio, csi, accuracy."""
    return dict(n=m["box_hours"], h=m["hours"], mae=m["mae"], hit=m["hit_rate"], far=m["false_alarm_ratio"], csi=m["csi"], acc=m["accuracy"])


def merge_rolling(prev, new_hours, now, keep_days=None):
    """prev: previous rolling dict (or {}); new_hours {hour_iso: {'d': 0/1, name: tally}} from this run. -> new rolling dict without summaries (hours, days, events)."""
    keep_days = C.CLOUD_KEEP_DAYS if keep_days is None else keep_days
    hours = {k: v for k, v in (prev.get("hours") or {}).items()}
    days = json.loads(json.dumps(prev.get("days") or {}))
    for hk, row in new_hours.items():
        old = hours.get(hk)
        if old is None:
            hours[hk] = row
            continue
        merged = dict(old)
        merged["d"] = row["d"]
        for name, t in row.items():
            if name == "d":
                continue
            has_scan = sum(t[:1] + t[6:9]) > 0      # this run saw a scan; otherwise keep what an earlier run scored
            if has_scan or name not in old:
                merged[name] = t
        hours[hk] = merged
    cut = hour_key(now - dt.timedelta(hours=HOURS_LIVE_H))
    for hk in sorted(k for k in hours if k < cut):          # fold hours that left the live window, once
        row = hours.pop(hk)
        _fold_record(days.setdefault(day_key(hk), {}), {n: t for n, t in row.items() if n != "d"}, row["d"])
    first = (now.astimezone(PT) - dt.timedelta(days=keep_days - 1)).strftime("%Y-%m-%d")
    days = {k: v for k, v in days.items() if k >= first}
    hours = {k: v for k, v in hours.items() if day_key(k) >= first}
    return dict(hours=dict(sorted(hours.items())), days=dict(sorted(days.items())))


def _live_by_day(hours):
    """Fold copies of the live hour rows into per-date records (for display); does not touch the stored structures."""
    rec = {}
    for hk, row in hours.items():
        _fold_record(rec.setdefault(day_key(hk), {}), {n: t for n, t in row.items() if n != "d"}, row["d"])
    return rec


def _combine(a, b):
    for name, dn in b.items():
        for k, slot in dn.items():
            cur = a.setdefault(name, {}).setdefault(k, [0] * 15)
            cur[:] = add_tally(cur[:10], slot[:10]) + [cur[10] + slot[10]] + [x + y for x, y in zip(cur[11:15], slot[11:15])]
    return a


def summaries(rolling, now):
    """-> dict(daily rows, summary 24h / 7d, headline) derived from a rolling dict."""
    hours, days = rolling["hours"], rolling["days"]
    live = _live_by_day(hours)
    per_day = {}
    for d in sorted(set(days) | set(live)):
        rec = {}
        _combine(rec, days.get(d, {}))
        _combine(rec, live.get(d, {}))
        per_day[d] = rec

    def block(rec_by_name, compact=False):
        out = {}
        for name, dn in rec_by_name.items():
            if compact and name not in FIELDS:
                continue
            ent = {}
            allslot = None
            for k, label in (("d", "day"), ("n", "night")):
                if k in dn:
                    ent[label] = _slot_metrics(dn[k])
                    allslot = dn[k][:] if allslot is None else add_slot(allslot, dn[k])
            if allslot is not None:
                ent["all"] = _slot_metrics(allslot)
            out[name] = {k: _compact(v) for k, v in ent.items()} if compact else ent
        return out

    def add_slot(a, b):
        return add_tally(a[:10], b[:10]) + [a[10] + b[10]] + [x + y for x, y in zip(a[11:15], b[11:15])]

    daily = [dict(date=d, fields=block(rec, compact=True)) for d, rec in per_day.items()]
    cut24 = hour_key(now - dt.timedelta(hours=24))
    rec24 = {}
    for hk, row in hours.items():
        if hk > cut24:
            _fold_record(rec24, {n: t for n, t in row.items() if n != "d"}, row["d"])
    first7 = (now.astimezone(PT) - dt.timedelta(days=6)).strftime("%Y-%m-%d")
    rec7 = {}
    for d, rec in per_day.items():
        if d >= first7:
            _combine(rec7, rec)
    s24, s7 = block(rec24), block(rec7)
    head = None
    for nm in ("fog_sheet", "rrfs"):
        m = (s24.get(nm) or {}).get("all")
        if m and m["accuracy"] is not None:
            head = dict(field=nm, match_pct_24h=round(100 * m["accuracy"]), scorable_hours_24h=m["hours"], text=f"Cloud match, last 24 h: {round(100 * m['accuracy'])} % ({m['hours']} scorable hours)")
            break
    return dict(daily=daily, summary=dict(h24=s24, d7=s7), headline=head)


def merge_events(prev_events, run_events, now, keep_days=None):
    keep_days = C.CLOUD_KEEP_DAYS if keep_days is None else keep_days
    cut = (now - dt.timedelta(days=keep_days)).isoformat()
    ev = {(e[0], e[1], e[2]): e for e in (prev_events or [])}
    for e in run_events:
        ev[(e[0], e[1], e[2])] = e
    return sorted((e for e in ev.values() if e[0] >= cut), key=lambda e: (e[0], e[1], e[2]))


def read_prev(path):
    """(dict, note or None). A missing file is a normal first run; an unreadable one starts a new history."""
    if not path or not Path(path).exists():
        return {}, "no previous cloud_score.json: history starts now"
    try:
        js = json.loads(Path(path).read_text(encoding="utf-8"))
        if js.get("schema") != SCHEMA or "hours" not in js or "days" not in js:
            return {}, "previous cloud_score.json has another schema: history restarted"
        return js, None
    except Exception as e:  # noqa: BLE001
        return {}, f"previous cloud_score.json unreadable ({e!r}): history restarted"[:200]


def dumps_limited(doc, limit=None):
    """Compact JSON of the rolling file; drops the oldest per-date records until it fits under the size limit (and says so in doc['truncated'])."""
    limit = C.CLOUD_MAX_BYTES if limit is None else limit
    txt = json.dumps(doc, separators=(",", ":"))
    while len(txt.encode("utf-8")) > limit and doc["days"]:
        oldest = sorted(doc["days"])[0]
        del doc["days"][oldest]
        doc["daily"] = [r for r in doc["daily"] if r["date"] != oldest]
        doc["truncated"] = doc.get("truncated", []) + [oldest]
        txt = json.dumps(doc, separators=(",", ":"))
    return txt


# ------------------------------------------------------------------ satellite tiles
def sat_frames(goes_hours, t_hours, lon, lat):
    """Observed low cloud as tiles uint8 [nF, H, W] on the fog tile grid: 0..250 = low-cloud share x 250, 252 = cloud that is not low (high cloud / no height),
    255 = no data (no scan, or no mask)."""
    nF = len(t_hours)
    out = np.full((nF,) + lon.shape, SAT_NODATA, np.uint8)
    for f in range(nF):
        lc = goes_hours.get(t_hours[f]) if goes_hours else None
        if lc is None:
            continue
        low, cl, va = (lc.sample(lon, lat, k) for k in ("low", "cloudy", "valid"))
        v = np.where(np.isfinite(low), np.clip(low, 0, 1), np.nan)
        tile = np.rint(SAT_SCALE * v)
        tile = np.where(np.isfinite(cl) & ((cl - np.nan_to_num(low)) >= 0.5), SAT_HIGH, tile)
        tile = np.where(~np.isfinite(low) | ~(va >= 0.5), SAT_NODATA, tile)
        out[f] = np.nan_to_num(tile, nan=SAT_NODATA).astype(np.uint8)
    return out


SAT_ENCODING = (f"uint8: 0..{SAT_SCALE} = share of low cloud (cloudy, top below {C.LOW_TOP_M:.0f} m) x {SAT_SCALE}; {SAT_HIGH} = cloud that is not low (top higher or unknown, "
                f"the satellite cannot see fog under it); {SAT_NODATA} = no data (no scan or no cloud mask)")


# ------------------------------------------------------------------ the run
class Hook:
    """Receives the fog tiles from outputs.write (a reference only: nothing is copied or changed)."""

    def __init__(self):
        self.tiles = None
        self.t_hours = None

    def tiles_cb(self, tiles, t_hours):
        self.tiles, self.t_hours = tiles, t_hours


def score_current(rr, F, goes_hours, t_hours, sun_el_hourly, tiles, tiles_nosat, now, log=print, archive_dir=None):
    """Score the hours t_hours of the current run that have a satellite scan (or should have). Reads rr, F, tiles and the scans only.
    -> dict(rows {hour_iso: {'d':, field: tally}}, events [(obs_event_iso, kind, field, err_h)], run table, boxes, lead status, counts)."""
    spf = C.OUT_STEP_MIN // C.STEP_MIN
    lat_t = F["lattice"]
    boxes = lattice_geometry(rr["lat"], rr["lon"], lat_t.x)
    geom, kinds = boxes["geom"], boxes["kinds"]
    sel = [f for f, h in enumerate(t_hours) if now - dt.timedelta(hours=C.GOES_CACHE_H) <= h <= now]
    hrs = [t_hours[f] for f in sel]
    nb = len(geom)
    out = dict(rows={}, events=[], boxes=dict(names=boxes["names"], kinds=kinds, geom=np.round(geom, 4).tolist()))
    if not sel:
        out.update(table={}, lead=dict(status="no hours to score"), notes=["no hour of the window lies in the last %d h" % C.GOES_CACHE_H])
        return out
    low, high, valid = observe(goes_hours, hrs, geom)
    stat = box_status(low, high, valid)
    # --- model matrices [nsel, nb]
    Wm = box_weights(boxes, lat_t, len(rr["lat"]))
    cl = np.asarray(rr["vars"]["cloud_cover_low"], float) / 100.0
    tmap = {t: i for i, t in enumerate(rr["times"])}
    M = {name: np.full((len(sel), nb), np.nan) for name in FIELDS}
    from . import outputs
    _, _, lon_t, lat_tt = outputs.tile_lonlat()
    tidx = tile_box_index(geom, lon_t, lat_tt)
    s_series = np.asarray(F["s_series"], float)
    src_box = np.array([k in ("ocean", "coast") for k in kinds])
    for i, f in enumerate(sel):
        c = tmap.get(hrs[i])
        if c is not None:
            M["rrfs"][i] = apply_weights(Wm, cl[:, c])
        M["source"][i, src_box] = s_series[min(f * spf, len(s_series) - 1)]
        M["fog_sheet"][i] = tile_means(tiles[f], tidx)
        if tiles_nosat is not None:
            M["fog_sheet_nosat"][i] = tile_means(tiles_nosat[f], tidx)
    mem = {name: members(name, kinds) for name in FIELDS}
    day = [bool(sun_el_hourly[f] > 0) for f in sel]
    for i, h in enumerate(hrs):
        row = dict(d=1 if day[i] else 0)
        for name in FIELDS:
            if name == "fog_sheet_nosat" and tiles_nosat is None:
                continue
            row[name] = tally(low[i], M[name][i], stat[i], mem[name])
        out["rows"][hour_key(h)] = row
    # --- run table (this run only), per field and day/night
    table = {}
    for name in FIELDS:
        if name == "fog_sheet_nosat" and tiles_nosat is None:
            continue
        ent = {}
        for label, want in (("day", True), ("night", False), ("all", None)):
            ts = [out["rows"][hour_key(h)][name] for i, h in enumerate(hrs) if want is None or day[i] == want]
            ent[label] = metrics(ts)
        table[name] = ent
    # --- timing at the coast box
    cj = kinds.index("coast")
    obs_yes = np.where(stat[:, cj] == 0, (low[:, cj] >= C.CLOUD_YES).astype(float), np.nan)
    for name in FIELDS:
        if name == "fog_sheet_nosat" and tiles_nosat is None:
            continue
        mv = M[name][:, cj]
        my = np.where(np.isfinite(mv), (mv >= C.CLOUD_YES).astype(float), np.nan)
        tm = timing(obs_yes, my, hrs)
        table[name]["timing"] = tm
        for kind in ("arrival", "clear"):
            if tm[f"{kind}_obs"] is not None and tm[f"{kind}_err_h"] is not None:
                out["events"].append([tm[f"{kind}_obs"], kind, name, tm[f"{kind}_err_h"]])
    out["table"] = table
    # --- lead times from the archive
    status, lead_models_, counts = lead_models(archive_dir, hrs, now, int(sum(1 for k in kinds if k == "cell")))
    out["lead"] = dict(status=status, **counts)
    ci = [j for j, k in enumerate(kinds) if k == "cell"]
    hpos = {h: i for i, h in enumerate(hrs)}
    for L, per in lead_models_.items():
        for h, vec in per.items():
            i = hpos.get(h)
            if i is None:
                continue
            m = np.full(nb, np.nan)
            m[ci] = vec
            mem_l = np.array([k == "cell" for k in kinds])
            out["rows"][hour_key(h)][f"rrfs@{L}"] = tally(low[i], m, stat[i], mem_l)
    return out


def run_safe(out, hook, rr, F, S, diag, goes_hours, t0, now, notes, static_dir=None, archive_dir=None, log=print):
    """Whole cloud-score step after outputs.write. Never raises: any exception is logged into `notes` (status.json) and the build continues.
    Writes <out>/cloud_score.json and <out>/sat{d}.png, adds the key `sat_score` to <out>/index.json. -> info dict for status.json."""
    t_start = time.time()
    try:
        if C.blocked("cloudscore"):
            raise RuntimeError("blocked for testing (DEATHFOG_LIVE_BLOCK)")
        if hook is None or hook.tiles is None:
            raise RuntimeError("no fog tiles were handed over by outputs.write")
        from . import outputs
        out = Path(out)
        spf = C.OUT_STEP_MIN // C.STEP_MIN
        tiles, t_hours = hook.tiles, hook.t_hours
        sun_el = np.asarray(diag["sun_el"], float)
        sun_h = np.array([sun_el[min(f * spf, len(sun_el) - 1)] for f in range(len(t_hours))])
        t_n = time.time()
        try:
            nosat = outputs.fog_frames(S, diag, F, None, t_hours, spf)
        except Exception as e:  # noqa: BLE001
            nosat = None
            notes.append(f"cloud score: fog tiles without the satellite factor not built: {e!r}"[:200])
        res = score_current(rr, F, goes_hours, t_hours, sun_h, tiles, nosat, now, log=log, archive_dir=archive_dir or C.ARCHIVE_DIR)
        prev, pnote = read_prev(os.environ.get(C.CLOUDSCORE_PREV_ENV) or (out / C.CLOUD_SCORE_FILE))
        if pnote:
            notes.append("cloud score: " + pnote)
        roll = merge_rolling(prev, res["rows"], now)
        roll["events"] = merge_events(prev.get("events"), res["events"], now)
        sm = summaries(roll, now)
        doc = dict(schema=SCHEMA, generated_iso=now.isoformat(), model_version=C.MODEL_VERSION, window_hours_live=HOURS_LIVE_H, keep_days=C.CLOUD_KEEP_DAYS,
                   headline=sm["headline"], summary=sm["summary"], daily=sm["daily"], this_run=dict(hours_scored=len(res["rows"]), table=res["table"], lead=res.get("lead")),
                   events=roll["events"], hours=roll["hours"], days=roll["days"],
                   definition=dict(fields=list(FIELDS), yes_at=C.CLOUD_YES, high_cloud_max=C.CLOUD_HIGH_MAX, valid_min=C.CLOUD_VALID_MIN, day="sun elevation > 0",
                                   tally=list(TALLY_KEYS), slot="tally + [scorable hours, unscorable hours by reason " + "/".join(REASONS) + "]",
                                   notes=["source and fog_sheet are nudged with the same satellite data: consistency, not skill; fog_sheet_nosat and rrfs are not",
                                          "7 d = the last 7 local (PT) dates including today; 24 h = hours after now - 24 h",
                                          "timing: first of two consecutive yes hours at the coast box, model minus observed in hours"]),
                   boxes=res["boxes"])
        txt = dumps_limited(doc)
        (out / C.CLOUD_SCORE_FILE).write_text(txt, encoding="utf-8", newline="\n")
        # --- satellite tiles
        _, _, lon_t, lat_t_ = outputs.tile_lonlat()
        sat = sat_frames(goes_hours, t_hours, lon_t, lat_t_)
        idx_path = out / "index.json"
        index = json.loads(idx_path.read_text(encoding="utf-8"))
        files, rows = [], []
        sat_bytes = 0
        for d, (a, b) in enumerate(index["frame_ranges"]):
            img, nrows = outputs.sheet(sat[a:b])
            fn = C.SAT_FILE.format(d=d)
            img.save(out / fn, optimize=True)
            files.append(fn)
            rows.append(nrows)
            sat_bytes += (out / fn).stat().st_size
        with_data = sum(1 for f in range(len(t_hours)) if goes_hours and goes_hours.get(t_hours[f]) is not None)
        index["sat_score"] = dict(cloud_score=C.CLOUD_SCORE_FILE, satellite=dict(files=files, rows=rows, hours_with_data=with_data, encoding=SAT_ENCODING,
                                                                                    layout="same tile grid, frame ranges and row layout as fog_grid / fog_files"),
                                  loaded_at_start=False)
        idx_path.write_text(json.dumps(index, indent=1), encoding="utf-8", newline="\n")
        info = dict(ok=True, seconds=round(time.time() - t_start, 1), hours_scored=len(res["rows"]), cloud_score_bytes=len(txt.encode("utf-8")), sat_bytes=sat_bytes,
                    lead=res.get("lead"), headline=sm["headline"], truncated=doc.get("truncated"))
        log("cloud score: " + json.dumps({k: v for k, v in info.items() if k != "lead"}))
        return info
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        msg = f"cloud score failed (build continues): {e!r}"[:300]
        notes.append(msg)
        return dict(ok=False, error=msg, seconds=round(time.time() - t_start, 1))


# ------------------------------------------------------------------ CLI: python -m microclimate.live cloudscore
def print_summary(doc, since_days=14, out=print):
    """Human-readable summary of a rolling cloud_score.json."""
    out(f"cloud score file generated {doc.get('generated_iso')} (model {doc.get('model_version')})")
    hd = doc.get("headline")
    if hd:
        out(hd["text"])
    lead = (doc.get("this_run") or {}).get("lead")
    if lead:
        out(f"lead-time scoring: {lead.get('status')}")
    fmt = lambda v: "  -  " if v is None else f"{v:5.2f}"  # noqa: E731

    def table(title, flds):
        out(title)
        out(f"{'field':16s}{'time':6s}{'mae':>6s}{'hit':>6s}{'far':>6s}{'csi':>6s}{'acc':>6s}{'boxh':>6s}{'hours':>6s}  unscorable hours")
        for name in FIELDS:
            ent = flds.get(name) or {}
            for label in ("day", "night"):
                m = ent.get(label)
                if m:
                    un = ", ".join(f"{k} {v}" for k, v in m["unscorable_hours"].items() if v) or "none"
                    out(f"{name:16s}{label:6s}{fmt(m['mae'])}{fmt(m['hit_rate'])}{fmt(m['false_alarm_ratio'])}{fmt(m['csi'])}{fmt(m['accuracy'])}{m['box_hours']:6d}{m['hours']:6d}  {un}")
    table("last 24 h", doc["summary"]["h24"])
    table("last 7 local dates", doc["summary"]["d7"])
    cut = (dt.date.today() - dt.timedelta(days=since_days)).isoformat()
    out(f"daily rows since {cut}:")
    for r in doc.get("daily", []):
        if r["date"] >= cut:
            m = (r["fields"].get("fog_sheet") or {}).get("all")
            out(f"  {r['date']}  fog_sheet accuracy {fmt(m['acc']) if m else '-'}  scorable hours {m['h'] if m else 0}")
    ev = [e for e in doc.get("events", []) if e[0][:10] >= cut]
    if ev:
        out("timing events (obs hour, kind, field, model minus observed h):")
        for e in ev[-12:]:
            out("  " + ", ".join(str(x) for x in e))


def run_cli(argv):
    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default
    root = Path(__file__).resolve().parents[2]
    f = Path(opt("--file", root / "site" / "v3" / "live" / C.CLOUD_SCORE_FILE))
    if not f.exists():
        print(f"{f} not found (pass --file PATH, e.g. the folder of a local build)")
        return 1
    doc = json.loads(f.read_text(encoding="utf-8"))
    print_summary(doc, int(str(opt("--since", "14d")).rstrip("d")))
    arch = opt("--archive", C.ARCHIVE_DIR)
    if arch:
        n = len(list(Path(arch).rglob("run_*.npz"))) if Path(arch).is_dir() else 0
        print(f"archive {arch}: {n} run files" + ("" if n else " (no archive yet)"))
    return 0
