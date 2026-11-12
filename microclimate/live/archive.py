"""Run archive for later scoring (guide 02, part B).

Every successful live build writes one small file, run_<UTC yyyymmddThhmm>.npz, holding the model output at a FIXED sample of street points for
every frame, plus the inputs a scorer needs (coast series, fog source, fog top, lattice low cloud, GOES fraction, station table when it exists).
refresh.sh adds the file to the `live-archive` branch under yyyy/mm/dd/ with a normal commit (never forced); old files are deleted weekly.

    python -m microclimate.live.archive place REPO_DIR FILE...    copy run files into REPO_DIR/yyyy/mm/dd/
    python -m microclimate.live.archive prune REPO_DIR [--days 120]   weekly clean-up (marker file .pruned)

Best effort everywhere: a failure here is logged and never fails the build.
"""
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np

from . import config as C

MAX_BYTES = 200 * 1024            # one run file must stay below this
DISTRICT_STRIDE = 50              # every 50th district street point (zone potrero / ggh)
CITY_STRIDE = 200                 # every 200th city street point
KEEP_DAYS = 120
PRUNE_EVERY_DAYS = 7
NAME = re.compile(r"^run_(\d{4})(\d{2})(\d{2})T(\d{4})\.npz$")
ROUTES_JSON = Path(__file__).resolve().parents[2] / "site" / "v3" / "data" / "routes.json"


def route_streets(path=ROUTES_JSON):
    """Street names used by the viewer's routes (the live static table has no route flag, so route points = district points on these streets)."""
    try:
        r = json.loads(Path(path).read_text())
        return {s for rt in r.get("routes", []) for s in rt.get("streets", [])}
    except Exception:  # noqa: BLE001
        return set()


def sample_index(S, streets=None, route_stride=1):
    """Fixed sample of street point indices and their kind (0 route point, 1 district, 2 city). Depends only on the static table and routes.json."""
    N = len(S["x"])
    district = np.asarray(S["zone"]) != 2
    kind = np.full(N, 255, np.uint8)
    streets = route_streets() if streets is None else streets
    if streets and "street_names" in S:
        names = np.asarray(S["street_names"])
        route_ids = np.nonzero(np.isin(names, list(streets)))[0]
        kind[district & np.isin(np.asarray(S["street"]), route_ids)] = 0
    if route_stride > 1:                      # thinning of route points (only used when the file would exceed MAX_BYTES)
        r_idx = np.nonzero(kind == 0)[0]
        kind[r_idx[np.arange(len(r_idx)) % route_stride != 0]] = 255
    d_idx = np.nonzero(district)[0]
    c_idx = np.nonzero(~district)[0]
    kind[d_idx[::DISTRICT_STRIDE]] = np.minimum(kind[d_idx[::DISTRICT_STRIDE]], 1)
    kind[c_idx[::CITY_STRIDE]] = 2
    idx = np.nonzero(kind != 255)[0]
    return idx.astype(np.uint32), kind[idx]


def build_record(W, diag, F, S, t0, now, physics, model_version, hourly_stride, route_stride=1):
    """-> dict of arrays for np.savez_compressed."""
    idx, kind = sample_index(S, route_stride=route_stride)
    nF = W.shape[0]
    hi = np.arange(nF) * hourly_stride
    ser = F["ser"]
    rec = dict(
        sample_index=idx, sample_kind=kind, w=np.rint(np.clip(W[:, idx], 0, 100)).astype(np.uint8),
        t0_iso=np.array(t0.isoformat()), frame_min=np.array(C.OUT_STEP_MIN), route_stride=np.array(route_stride), generated_iso=np.array(now.isoformat()),
        observed_until_frame=np.array(-1 if F.get("obs_until") is None else int((F["obs_until"] - t0).total_seconds() // 3600)),
        physics=np.array(json.dumps(sorted(physics))), model_version=np.array(str(model_version)),
        coast_temp=np.asarray(ser["temperature_2m"], np.float32)[hi], coast_dew=np.asarray(ser["dew_point_2m"], np.float32)[hi],
        coast_wind=np.asarray(ser["wind_speed_10m"], np.float32)[hi], coast_wind_dir=np.asarray(ser["wind_dir"], np.float32)[hi],
        coast_cloud_low=np.asarray(ser["cloud_cover_low"], np.float32)[hi], coast_precip=np.asarray(ser["precipitation"], np.float32)[hi],
        coast_sw=np.asarray(ser["shortwave_radiation"], np.float32)[hi],
        s_series=np.asarray(F["s_series"], np.float32)[hi], s_model=np.asarray(F["s_model"], np.float32)[hi], fog_top=np.asarray(F["fog_top"], np.float32)[hi],
        goes_frac=np.asarray(F["goes_frac"], np.float32)[:nF], khaf=np.asarray(F["khaf"], np.float32)[:nF])
    if F.get("cloud_base") is not None:        # guide 08
        rec["cloud_base"] = np.asarray(F["cloud_base"], np.float32)[hi]
    if F.get("lattice_cl") is not None:        # lattice low cloud for the satellite score (guide 04)
        rec["lattice_cloud_low"] = np.asarray(F["lattice_cl"], np.float16)[:, hi]
        rec["lattice_lat"] = np.asarray(F["lattice"].lat, np.float32)
        rec["lattice_lon"] = np.asarray(F["lattice"].lon, np.float32)
    if F.get("stations") is not None:          # guide 03: station table (rows of numbers/strings the scorer wrote)
        rec["stations"] = np.array(json.dumps(F["stations"], default=str))
    return rec


def save(path, record):
    """Write the record; raises AssertionError above MAX_BYTES (nothing is left behind)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        np.savez_compressed(f, **record)
    size = tmp.stat().st_size
    if size > MAX_BYTES:
        tmp.unlink()
        raise AssertionError(f"archive file would be {size} bytes (limit {MAX_BYTES})")
    tmp.replace(path)
    return size


def file_name(now):
    return f"run_{now.astimezone(dt.timezone.utc):%Y%m%dT%H%M}.npz"


def write_run(folder, W, diag, F, S, t0, now, physics, model_version, hourly_stride):
    """Build and save the run file in `folder`; when it would exceed MAX_BYTES the route points are thinned (every 2nd, 4th, 8th). -> (file name, bytes)."""
    p = Path(folder) / file_name(now)
    for stride in (1, 2, 4, 8):
        try:
            return p.name, save(p, build_record(W, diag, F, S, t0, now, physics, model_version, hourly_stride, stride))
        except AssertionError:
            if stride == 8:
                raise


def load(path):
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


# ------------------------------------------------------------------ branch helpers (used by refresh.sh)
def place(repo_dir, files):
    """Copy run files into repo_dir/yyyy/mm/dd/ (by the date in the file name). -> list of repo-relative paths."""
    out = []
    for f in files:
        f = Path(f)
        m = NAME.match(f.name)
        if not m:
            continue
        rel = Path(m.group(1)) / m.group(2) / m.group(3) / f.name
        (Path(repo_dir) / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, Path(repo_dir) / rel)
        out.append(rel.as_posix())
    return out


def prune(repo_dir, days=KEEP_DAYS, today=None, every=PRUNE_EVERY_DAYS):
    """Weekly: delete run files older than `days` days (by file-name date) and empty folders. Returns the number deleted, or None when it was not due."""
    root = Path(repo_dir)
    today = today or dt.datetime.now(dt.timezone.utc).date()
    marker = root / ".pruned"
    try:
        last = dt.date.fromisoformat(marker.read_text().strip())
        if (today - last).days < every:
            return None
    except Exception:  # noqa: BLE001
        pass
    n = 0
    for f in root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/run_*.npz"):
        m = NAME.match(f.name)
        if m and (today - dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))).days > days:
            f.unlink()
            n += 1
    for d in sorted(root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]"), reverse=True) + sorted(root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]"), reverse=True) + sorted(root.glob("[0-9][0-9][0-9][0-9]"), reverse=True):  # never touches .git
        try:
            d.rmdir()          # only succeeds when empty
        except OSError:
            pass
    marker.write_text(today.isoformat() + "\n")
    return n


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "place" and len(argv) >= 3:
        for r in place(argv[1], argv[2:]):
            print(r)
    elif cmd == "prune" and len(argv) >= 2:
        days = int(argv[argv.index("--days") + 1]) if "--days" in argv else KEEP_DAYS
        n = prune(argv[1], days)
        print("not due" if n is None else f"deleted {n} files older than {days} days")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
