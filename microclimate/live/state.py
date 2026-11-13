"""State carried between live runs (guide 02, part A).

An "anchor" is the full model state at the start of the NEXT window (t0 + about 24 h). A run saves it while it simulates the day, and the run
whose window starts at that time begins from it instead of from dry streets. Files: state_<UTC yyyymmddThh>.npz in the output folder (they are
published with the live files, so the next runner can fetch them). Everything here is best effort: callers treat any failure as a cold start.
"""
import datetime as dt
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from . import config as C

PT = ZoneInfo("America/Los_Angeles")
MAX_BYTES = 3 * 1024 * 1024           # one state file must stay below this
KEEP = 2                              # newest state files kept
PATTERN = re.compile(r"^state_\d{8}T\d{2}\.npz$")
FLOAT16 = ("W", "film", "rain_since_wash", "hours_since_rain")   # arrays stored as float16 (the rest keep their dtype)
CENTI = ("ts", "tdeep")                                           # road temperatures (guide 07): stored as int16 in 0.01 K (float16 would be 0.016 K at 20 degC)


def name_for(t):
    """File name of the anchor valid at aware time t."""
    return f"state_{t.astimezone(dt.timezone.utc):%Y%m%dT%H}.npz"


def next_t0(start):
    """Window start (t0) of the next daily window, given this window's 18:00 PT start: 12:00 PT the next local day."""
    loc = start.astimezone(PT)
    nxt = dt.datetime(loc.year, loc.month, loc.day, 18, tzinfo=PT) + dt.timedelta(days=1)
    nxt = dt.datetime(nxt.year, nxt.month, nxt.day, 18, tzinfo=PT)      # wall-clock 18:00 even across a DST change
    return nxt.astimezone(dt.timezone.utc) - dt.timedelta(hours=C.HISTORY_H)


def save(path, time, physics, model_version, arrays):
    """Write the state atomically as a compressed npz; raises AssertionError above MAX_BYTES."""
    path = Path(path)
    data = {k: (np.asarray(v, np.float16) if k in FLOAT16 else np.clip(np.rint(np.asarray(v, np.float64) * 100.0), -32768, 32767).astype(np.int16) if k in CENTI else np.asarray(v))
            for k, v in arrays.items()}
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        np.savez_compressed(f, _time=np.array(time.astimezone(dt.timezone.utc).isoformat()), _physics=np.array(json.dumps(sorted(physics))),
                            _model_version=np.array(str(model_version)), **data)
    size = tmp.stat().st_size
    if size > MAX_BYTES:
        tmp.unlink()
        raise AssertionError(f"state file would be {size} bytes (limit {MAX_BYTES})")
    tmp.replace(path)
    return size


def load(path):
    """-> dict(time, physics (sorted list), model_version, arrays {name: float32 for the float16 ones}). Raises on a bad file."""
    with np.load(path, allow_pickle=False) as z:
        meta = dict(time=dt.datetime.fromisoformat(str(z["_time"])), physics=json.loads(str(z["_physics"])), model_version=str(z["_model_version"]))
        meta["arrays"] = {k: (z[k].astype(np.float32) if k in FLOAT16 else (z[k].astype(np.float32) / 100.0).astype(np.float32) if k in CENTI else z[k])
                          for k in z.files if not k.startswith("_")}
    return meta


def find_anchor(folder, t0, physics, model_version, n_points):
    """Anchor for a window starting at t0 -> (arrays dict or None, reason). Wrong version / physics / size / time or an unreadable file = cold start."""
    p = Path(folder) / name_for(t0)
    if not p.exists():
        return None, "no anchor file for this window start"
    try:
        s = load(p)
    except Exception as e:  # noqa: BLE001
        return None, f"anchor unreadable: {e!r}"
    if s["model_version"] != model_version:
        return None, f"anchor from model version {s['model_version']}"
    if s["physics"] != sorted(physics):
        return None, f"anchor made with physics {s['physics']}"
    if abs((s["time"] - t0).total_seconds()) > 1:
        return None, "anchor time differs from the window start"
    if "W" not in s["arrays"] or s["arrays"]["W"].shape != (n_points,):
        return None, "anchor does not match the street point table"
    return s["arrays"], "ok"


def prune(folder, keep=KEEP):
    """Delete all but the `keep` newest state files (names sort by time). -> names kept."""
    files = sorted(p for p in Path(folder).glob("state_*.npz") if PATTERN.match(p.name))
    for p in files[:-keep] if keep else files:
        try:
            p.unlink()
        except OSError:
            pass
    return [p.name for p in files[-keep:]] if keep else []
