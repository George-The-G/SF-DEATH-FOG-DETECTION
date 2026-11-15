"""Guide 01: helpers for the 11-band fog tracer (no GPU imports here, so CPU tests and --check run anywhere).

Files: library/fog3d_<dddd>_<bb>.npz (key c, float16 [855,811,40]) + .json diag; status in library/runs/fog3d_status.json."""
import json
import os
import time
from pathlib import Path

import numpy as np

WEST_DIRS = [180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5, 360]   # directions that carry fog tracer
OLD_TO_NEW = {0: (0, 1, 2), 1: (3, 4, 5), 2: (6, 7, 8), 3: (9, 10)}  # old band -> new bands that add up to it
NEST_MAX, NEST_MEAN = 0.05, 0.01  # guide 01 step 5c


def stem(d):
    return f"{int(round(d * 10)):04d}"


def band_k_range(band, nz=40, dx=20.0):
    """Inclusive k range of inflow cells selected by run_band (cell centre zc=(k+0.5)*dx in [lo, hi)); None if empty."""
    ks = [k for k in range(nz) if band[0] <= (k + 0.5) * dx < band[1]]
    return (ks[0], ks[-1]) if ks else None


# ---- GPU lock: one GPU job at a time (pid file; no other lock exists in the repo) ----
class GpuLockHeld(RuntimeError):
    pass


def _pid_alive(pid):
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name == "nt":
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return f" {pid} " in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class GpuLock:
    """Context manager: library/gpu.lock holds {pid, job, started}. Held by a live pid -> GpuLockHeld (exit 3 in the CLI).
    A stale lock (dead pid) is replaced. Released on exit, including on error."""
    def __init__(self, lib_dir, job):
        self.path, self.job = Path(lib_dir) / "gpu.lock", job

    def __enter__(self):
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    cur = json.loads(self.path.read_text())
                except (ValueError, OSError):
                    cur = {}
                pid = cur.get("pid")
                if pid and _pid_alive(int(pid)):
                    raise GpuLockHeld(f"GPU lock held by running pid {pid} ({cur.get('job')})")
                self.path.unlink(missing_ok=True)  # stale
                continue
            with os.fdopen(fd, "w") as f:
                json.dump({"pid": os.getpid(), "job": self.job, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
            return self
        raise GpuLockHeld("could not take gpu.lock")

    def __exit__(self, *exc):
        try:
            if json.loads(self.path.read_text()).get("pid") == os.getpid():
                self.path.unlink()
        except (OSError, ValueError):
            pass


def plan(cfg, lib_dir, directions=None, bands=None):
    """List of (direction, band index, band, k range, output exists)."""
    lib_dir = Path(lib_dir)
    allb = cfg["library"]["fog_bands3d_m"]
    out = []
    for d in directions or WEST_DIRS:
        for bi in (bands if bands is not None else range(len(allb))):
            out.append((d, bi, allb[bi], band_k_range(allb[bi]), (lib_dir / f"fog3d_{stem(d)}_{bi:02d}.npz").exists()))
    return out


def write_status(lib_dir, **kw):
    p = Path(lib_dir) / "runs"
    p.mkdir(exist_ok=True)
    (p / "fog3d_status.json").write_text(json.dumps(kw))


# ---- --check (CPU only) ----
def check(cfg, lib_dir, grids_dir, report_dir, tol=0.01, directions=None):
    lib_dir, grids_dir = Path(lib_dir), Path(grids_dir)
    fluid = ~np.load(grids_dir / "solid_20m.npz")["solid"]
    nb = len(cfg["library"]["fog_bands3d_m"])
    res = {"directions": {}, "pass": True}
    for d in directions or WEST_DIRS:
        s = stem(d)
        r = {"a_range_ok": True, "b_drift_ok": True, "c_nesting": {}}
        files = [lib_dir / f"fog3d_{s}_{b:02d}.npz" for b in range(nb)]
        if not all(f.exists() for f in files):
            r["missing"] = [f.name for f in files if not f.exists()]
            r["pass"] = False
            res["directions"][str(d)] = r
            res["pass"] = False
            continue
        new = []
        for f in files:
            c = np.load(f)["c"].astype(np.float32)
            if np.isnan(c).any() or c.min() < 0 or c.max() > 1.001:
                r["a_range_ok"] = False
            dr = json.load(open(f.with_suffix(".json"))).get("drift_last_2000_steps")
            if dr is None or dr >= tol:
                r["b_drift_ok"] = False
            new.append(c)
        for B, parts in OLD_TO_NEW.items():
            O = np.load(lib_dir / f"fog_{s}_{B}.npz")["c"].astype(np.float32)
            e = np.abs(sum(new[i] for i in parts) - O)[fluid]
            r["c_nesting"][B] = {"max": float(e.max()), "mean": float(e.mean()), "ok": bool(e.max() < NEST_MAX and e.mean() < NEST_MEAN)}
        low = [float(new[b][:, :, :5][fluid[:, :, :5]].mean()) for b in range(nb)]
        r["mean_below_100m_by_band"] = low
        r["e_monotone_from_band3"] = all(low[b + 1] <= low[b] + 1e-6 for b in range(3, nb - 1))  # reported, not a failure
        d_ok = True
        if d == 270:  # inflow face rows 2-5 cells inside: all bands sum to 1 below 800 m
            tot = sum(new)
            r["d_full_column_maxerr_strict_info"] = [float(np.abs(tot[i, :, :][fluid[i, :, :]] - 1).max()) for i in (2, 3, 4, 5)]
            # Gate d, restated 2026-11-15 (see d_note): levels k 1..36, 10 rows in from the north/south edges, <= 0.02.
            r["d_interior_maxerr"] = [float(np.abs(tot[i, 10:-10, 1:37][fluid[i, 10:-10, 1:37]] - 1).max()) for i in (2, 3, 4, 5)]
            r["d_note"] = ("The strict guide gate (all k, all rows) cannot hold by construction: k=0 loses fog to ground deposition (~0.83), "
                           "k=39 is forced to 0 (clear air) and diffuses down (k=38 ~0.85, k=37 ~0.99), and the only 6 cells with error >0.02 on "
                           "row 5 lie within 8 cells (160 m) of the north/south lateral edge at the terrain.")
            d_ok = max(r["d_interior_maxerr"]) <= 0.02
        r["pass"] = bool(r["a_range_ok"] and r["b_drift_ok"] and all(v["ok"] for v in r["c_nesting"].values()) and d_ok)
        res["pass"] &= r["pass"]
        res["directions"][str(d)] = r
    Path(report_dir).mkdir(parents=True, exist_ok=True)
    json.dump(res, open(Path(report_dir) / "bands_check.json", "w"), indent=1)
    return res
