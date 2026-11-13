"""Single-direction wind + fog runner: `python -m microclimate winddir --dir <deg> [--force] [--status] [--validate]`.

Computes ONE wind-FROM direction of the library (rotated-frame XLB wind field via windrot.run_direction, then the four fog
tracer bands via fog.converge_band) and writes library/wind_<dir*10:04d>.npz/.json and fog_<dir*10:04d>_<b>.npz/.json exactly as
`winddirs` does, but:
  * every library file is written under a temporary name and renamed when complete (atomic); json after npz;
  * a status file library/runs/dir_<dir*10:04d>.json tracks state queued / running / done / failed, start / end times, step
    counts, convergence numbers (rho range, speed ratio, fog drift per band), GPU memory peak (nvidia-smi sampling) and the log path;
  * the log is library/runs/dir_<dddd>.log (appended; every start writes a header line);
  * safe to re-run: a direction whose status says done AND whose files are all present is skipped; a failed or interrupted
    (status running but the process is gone) one is restarted cleanly: leftover temp files removed, a finished wind field is
    reused, finished fog bands are reused (bands are cheap; the wind run, 15-25 min, is the expensive part);
  * ONE run at a time on the GPU: library/runs/gpu.lock (pid); a live holder makes this command exit with code 3;
  * existing library directions (files present and no runner status) are never touched (exit 2).
Checkpoints: the XLB solver (wind.run) has no checkpoint / restart support (the distribution functions are 19 x 60 M float32, ~4.5 GB,
and wind.run never saves them), so an interrupted wind run restarts from step 0. Fog bands are saved one by one, so a later failure
does not repeat the wind run.
The fog stage runs in a child process (same command with --stage fog) so the GPU memory of the wind solver is certainly released.
--validate runs validate_dirs (all checks a-h) for this direction; it writes library/runs/validation_<dddd>.json and the manifest
library/wind_dirs_east.json (NOT wind_dirs.json, which dirlib reads), and puts the result in the status file.
"""
import datetime
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np


def stem(d):
    return f"{int(round(float(d) * 10)):04d}"


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _jwrite(path, obj):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    os.replace(tmp, path)


def _jread(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def pid_alive(pid):
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH", "/FO", "CSV"], capture_output=True, text=True, timeout=30).stdout
        return f'"{int(pid)}"' in out
    except Exception:
        return True  # cannot tell: assume alive (the safe answer for a lock)


def gpu_used_mb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20).stdout
        a, b = out.strip().splitlines()[0].split(",")
        return int(a), int(b)
    except Exception:
        return None, None


class GpuWatch(threading.Thread):
    def __init__(self, every=15.0):
        super().__init__(daemon=True)
        self.every, self.peak, self.total, self.stop_ev = every, 0, None, threading.Event()
        self.base, self.total = gpu_used_mb()
        self.peak = self.base or 0

    def run(self):
        while not self.stop_ev.wait(self.every):
            u, t = gpu_used_mb()
            if u is not None:
                self.peak = max(self.peak, u)

    def stop(self):
        self.stop_ev.set()


def files_complete(lib, d, nbands=4):
    s = stem(d)
    need = [lib / f"wind_{s}.npz", lib / f"wind_{s}.json"] + [lib / f"fog_{s}_{b}.{e}" for b in range(nbands) for e in ("npz", "json")]
    if not all(p.exists() for p in need):
        return False
    j = _jread(lib / f"wind_{s}.json")
    if not j or j.get("nan", True):
        return False
    return all(((_jread(lib / f"fog_{s}_{b}.json") or {}).get("nan", True) is False) for b in range(nbands))


class Run:
    def __init__(self, cfg, data, d):
        self.cfg, self.data, self.d = cfg, Path(data), float(d)
        self.lib = self.data / "library"
        self.runs = self.lib / "runs"
        self.s = stem(d)
        self.sf = self.runs / f"dir_{self.s}.json"
        self.logf = self.runs / f"dir_{self.s}.log"
        self.lock = self.runs / "gpu.lock"
        self.st = _jread(self.sf) or {}

    def log(self, msg):
        line = f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, flush=True)
        with open(self.logf, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def save(self, **kw):
        self.st.update(kw)
        _jwrite(self.sf, self.st)

    def cleanup_tmp(self):
        for p in list(self.lib.glob(f"wind_{self.s}.*.part")) + list(self.lib.glob(f"fog_{self.s}_*.part")) + list(self.lib.glob(f"*{self.s}*.tmp")):
            try:
                p.unlink()
                self.log(f"removed leftover temp file {p.name}")
            except OSError:
                pass

    def acquire(self):
        self.runs.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} dir={self.d}\n".encode())
                os.close(fd)
                return True
            except FileExistsError:
                try:
                    pid = int(open(self.lock).read().split()[0])
                except Exception:
                    pid = -1
                if pid > 0 and pid_alive(pid):
                    print(f"GPU lock held by running pid {pid} ({self.lock}); not starting direction {self.d}")
                    return False
                print(f"stale GPU lock (pid {pid} gone), taking it over")
                try:
                    self.lock.unlink()
                except OSError:
                    pass
        return False

    def release(self):
        try:
            self.lock.unlink()
        except OSError:
            pass

    # ------------------------------------------------------------------ stages
    def wind_stage(self):
        from . import windrot
        wf, wj = self.lib / f"wind_{self.s}.npz", self.lib / f"wind_{self.s}.json"
        j = _jread(wj)
        if wf.exists() and j and not j.get("nan", True):
            self.log(f"wind field {wf.name} already complete, reused")
            self.save(wind=dict(j, reused=True))
            return True
        solid = np.load(self.data / "grids" / "solid_20m.npz")["solid"]
        self.log(f"wind run: direction {self.d}, 40000 steps, average over the last 8000")
        t0 = time.time()
        u, diag = windrot.run_direction(solid, self.d, steps=40000, avg_steps=8000, log=self.log)
        diag["run_wall_seconds"] = round(time.time() - t0)
        if u is None or diag.get("nan"):
            self.save(wind=diag)
            self.log(f"wind run aborted (NaN or |u| > 0.5): {json.dumps(diag)}")
            return False
        part = self.lib / f"wind_{self.s}.npz.part"
        with open(part, "wb") as f:
            np.savez_compressed(f, u=u)
        os.replace(part, wf)
        _jwrite(wj, diag)
        self.save(wind=diag)
        self.log(f"wind field written: {json.dumps(diag)}")
        return True

    def fog_stage(self):
        from . import fog
        grids = self.data / "grids"
        spec = json.load(open(grids / "grid_spec.json"))
        solid = np.load(grids / "solid_20m.npz")["solid"]
        lib = self.cfg["library"]
        u = np.load(self.lib / f"wind_{self.s}.npz")["u"]
        bands = self.st.get("fog", {})
        for bi, band in enumerate(lib["fog_bands_m"]):
            f, fj = self.lib / f"fog_{self.s}_{bi}.npz", self.lib / f"fog_{self.s}_{bi}.json"
            j = _jread(fj)
            if f.exists() and j and not j.get("nan", True):
                self.log(f"fog band {bi} already complete, reused")
                bands[str(bi)] = dict(j, reused=True)
                continue
            t0 = time.time()
            c, diag, hist = fog.converge_band(u, solid, spec, self.d, band, tol=0.01, schedule=(3, 6, 12, 24, 48), speed_ms=lib["ref_speed_ms"],
                                              kd=lib["eddy_diffusivity_m2s"], vd=lib["deposition_velocity_ms"], loss_per_km=lib["fog_loss_per_km"])
            diag["total_seconds_incl_retries"] = round(time.time() - t0, 1)
            diag["attempts"] = [(h["transits"], h["drift_last_2000_steps"]) for h in hist]
            part = self.lib / f"fog_{self.s}_{bi}.npz.part"
            with open(part, "wb") as fh:
                np.savez_compressed(fh, c=c)
            os.replace(part, f)
            _jwrite(fj, diag)
            bands[str(bi)] = diag
            self.save(fog=bands)
            self.log(f"fog band {bi} written: {json.dumps(diag)}")
            if diag.get("nan") or (diag.get("drift_last_2000_steps") or 1) >= 0.01:
                self.log(f"fog band {bi} NOT converged or NaN (drift {diag.get('drift_last_2000_steps')}); validation criterion g will fail")
        self.save(fog=bands)
        return True

    # ------------------------------------------------------------------ main
    def go(self, force=False):
        lib = self.lib
        d = self.d
        if not (0.0 <= d < 360.0) or d == 0.0:
            print("direction must be in (0, 360); 0 is the same as 360, which already exists")
            return 2
        self.runs.mkdir(parents=True, exist_ok=True)
        existing = (lib / f"wind_{self.s}.npz").exists() or any((lib / f"fog_{self.s}_{b}.npz").exists() for b in range(4))
        if existing and not self.st:
            print(f"library files for direction {d} exist and were not made by this runner: not touching them")
            return 2
        if self.st.get("state") == "done" and files_complete(lib, d) and not force:
            print(f"direction {d}: done, files present, nothing to do")
            return 0
        if force and files_complete(lib, d):
            print("--force would overwrite finished library files; refusing (move them aside by hand if you really want that)")
            return 2
        if not self.acquire():
            return 3
        gw = GpuWatch()
        gw.start()
        try:
            prev = self.st.get("state")
            self.logf.parent.mkdir(exist_ok=True)
            self.log(f"=== start direction {d} (previous state: {prev}, attempt {int(self.st.get('attempts', 0)) + 1}) pid {os.getpid()}")
            if prev == "running":
                self.log("previous run did not finish (interrupted): restarting cleanly")
            self.cleanup_tmp()
            t0 = time.time()
            self.st = {"direction_deg": d, "state": "running", "start": now(), "end": None, "pid": os.getpid(), "attempts": int(self.st.get("attempts", 0)) + 1,
                       "log": str(self.logf), "gpu_mem_baseline_mb": gw.base, "gpu_mem_total_mb": gw.total, "wind": self.st.get("wind") if _jread(lib / f"wind_{self.s}.json") else None,
                       "fog": self.st.get("fog", {}), "steps": {"wind": 40000, "wind_avg": 8000}, "checkpoint": "none (XLB solver has no restart; wind restarts from step 0, finished fog bands are reused)"}
            self.save()
            ok = self.wind_stage()
            self.save(gpu_mem_peak_mb=gw.peak)
            if ok:
                self.log("starting fog stage in a child process")
                with open(self.logf, "a", encoding="utf-8") as lf:
                    rc = subprocess.run([sys.executable, "-m", "microclimate", "winddir", "--dir", str(d), "--stage", "fog"], stdout=subprocess.DEVNULL, stderr=lf).returncode  # the child logs through Run.log itself
                self.st = _jread(self.sf) or self.st
                ok = rc == 0
                if not ok:
                    self.log(f"fog stage failed (exit code {rc})")
            ok = ok and files_complete(lib, d)
            gw.stop()
            wall = round(time.time() - t0)
            self.save(state="done" if ok else "failed", end=now(), wall_seconds=wall, gpu_mem_peak_mb=gw.peak, pid=None)
            self.log(f"=== {'DONE' if ok else 'FAILED'} direction {d} in {wall} s, GPU memory peak {gw.peak} MiB of {gw.total}")
            return 0 if ok else 1
        except BaseException as e:  # noqa: BLE001 (also KeyboardInterrupt: record it, then re-raise nothing)
            gw.stop()
            self.log("EXCEPTION " + traceback.format_exc())
            self.save(state="failed", end=now(), error=f"{type(e).__name__}: {e}", gpu_mem_peak_mb=gw.peak, pid=None)
            return 1
        finally:
            gw.stop()
            self.release()


def fog_only(cfg, data, d):
    r = Run(cfg, data, d)
    r.st = _jread(r.sf) or {}
    try:
        r.fog_stage()
        return 0
    except BaseException:  # noqa: BLE001
        r.log("EXCEPTION in fog stage " + traceback.format_exc())
        return 1


def status(data, d=None):
    runs = Path(data) / "library" / "runs"
    for p in sorted(runs.glob("dir_*.json")):
        if d is not None and p.name != f"dir_{stem(d)}.json":
            continue
        j = _jread(p) or {}
        st = j.get("state")
        alive = pid_alive(j["pid"]) if j.get("pid") else None
        extra = ""
        if st == "running" and alive is False:
            extra = "  (process gone: interrupted, re-run restarts it)"
        print(f"{p.name}: state={st} start={j.get('start')} end={j.get('end')} wall_s={j.get('wall_seconds')} gpu_peak_mb={j.get('gpu_mem_peak_mb')} pid={j.get('pid')}{extra}")
        lg = j.get("log")
        if lg and Path(lg).exists():
            tail = Path(lg).read_text(encoding="utf-8", errors="replace").splitlines()[-3:]
            for t in tail:
                print("    " + t[:200])
    lk = runs / "gpu.lock"
    print("gpu.lock:", lk.read_text().strip() if lk.exists() else "free")


def validate(data, d):
    from . import validate_dirs
    runs = Path(data) / "library" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    out = runs / f"validation_{stem(d)}.json"
    res = validate_dirs.run(data, [d], out=out, manifest=Path(data) / "library" / "wind_dirs_east.json")
    r = res.get(str(d)) or res.get(str(float(d)))
    sf = runs / f"dir_{stem(d)}.json"
    st = _jread(sf) or {}
    if r is not None:
        st["validation"] = {"pass": bool(r["pass"]), **{k: bool(v) for k, v in r.items() if k.endswith("_pass")}, "report": str(out), "time": now()}
        _jwrite(sf, st)
    ok = bool(r and r["pass"])
    print(f"VALIDATION direction {d}: {'PASS' if ok else 'FAIL'}  ->  {out}")
    return 0 if ok else 1


def main(cfg, data, argv):
    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default
    d = opt("--dir")
    if "--status" in argv:
        status(data, float(d) if d else None)
        return 0
    if d is None:
        print("usage: python -m microclimate winddir --dir <deg> [--force] [--validate] | --status [--dir <deg>]")
        return 2
    d = float(d)
    if "--validate" in argv:
        return validate(data, d)
    if opt("--stage") == "fog":
        return fog_only(cfg, data, d)
    return Run(cfg, data, d).go(force="--force" in argv)
