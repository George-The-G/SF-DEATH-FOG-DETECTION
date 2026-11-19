"""PC backup runner (multi-scale guide 10, part C). python -m microclimate.live backup [--data DIR] [--static PATH] [--stale-min 30] [--live-stale-min 50] [--force] [--dry-run]

When the published data is stale or unreachable, build the same public and beta outputs the GitHub Action builds (CPU only; mirrors live_ci/refresh.sh: public `build --promoted`,
then `build --beta --inputs`) and publish them to R2 under backup/live/ and backup/live_beta/ with live_ci/r2_sync.py. The PC NEVER pushes to the repository and never deploys Pages.
Without the R2 secrets nothing is published and the result stays in the local data folder (log line says so).
Exit codes: 0 = fresh (nothing to do) or built and published/kept; 1 = build failed (nothing published); 2 = another instance holds the lock.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PAGES = "https://george-the-g.github.io/SF-DEATH-FOG-DETECTION"
LOCK_MAX_AGE_S = 15 * 60       # a lock older than this is a crashed run
BUILD_TIMEOUT_S = 7 * 60       # the scheduled task stops at 8 minutes


def opt(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def _parse(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def age_minutes(url, now, urlopen=urllib.request.urlopen):
    """Age in minutes of the build time in a published status.json (generated_utc, or generated_iso for the v3 live file); None when unreachable or unreadable."""
    try:
        req = urllib.request.Request(f"{url}?t={int(now.timestamp())}", headers={"User-Agent": "deathfog-pc-backup", "Cache-Control": "no-cache"})
        with urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
        t = _parse(d.get("generated_utc") or d["generated_iso"])
        return (now - t).total_seconds() / 60.0
    except Exception:
        return None


def decide(v2_age, live_age, stale_min=30.0, live_stale_min=50.0):
    """'fresh' | 'stale' | 'unreachable'. v2 status.json is rebuilt every 10 min (the workflow is alive); the v3 live status.json is built every 30 min, so it gets a longer limit.
    Unreachable = neither file could be read (GitHub Pages down, or no network on the PC: then publishing would fail too, but R2 may still be reachable, so it is treated as stale)."""
    if v2_age is None and live_age is None:
        return "unreachable"
    if v2_age is not None and v2_age > stale_min:
        return "stale"
    if live_age is not None and live_age > live_stale_min:
        return "stale"
    return "fresh"   # every readable file is young enough (one unreadable file alone is not an outage)


class Lock:
    """Lock file; stale after LOCK_MAX_AGE_S. Released on exit."""
    def __init__(self, path, now=time.time):
        self.path, self.now, self.held = Path(path), now, False

    def acquire(self):
        try:
            if self.path.exists() and self.now() - self.path.stat().st_mtime < LOCK_MAX_AGE_S:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(str(os.getpid()))
            self.held = True
            return True
        except OSError:
            return False

    def release(self):
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.held = False


def _cmd(py, *a):
    return [py, "-m", "microclimate.live", "build", *a]


def build_all(data, static, py=sys.executable, runner=subprocess.run, root=".", log=print):
    """Public (promoted) then beta build of the same fetched inputs. Returns True when the public build succeeded (the beta build may fail without failing the run)."""
    pub, beta, inputs = data / "live", data / "live_beta", data / "inputs.pkl"
    for d in (pub, beta):
        d.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(root))
    new_pub, new_beta = data / "live_new", data / "live_beta_new"
    import shutil
    for d in (new_pub, new_beta):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        src = pub if d is new_pub else beta
        for f in src.glob("state_*.npz"):        # carried state, like the Action
            shutil.copy2(f, d / f.name)
    r = runner(_cmd(py, "--promoted", "--out", str(new_pub), "--static", str(static), "--save-inputs", str(inputs)), cwd=root, env=env, timeout=BUILD_TIMEOUT_S)
    if r.returncode != 0:
        log(f"backup: public build failed (exit {r.returncode}); nothing published")
        return False
    r = runner(_cmd(py, "--beta", "--inputs", str(inputs), "--out", str(new_beta), "--static", str(static)), cwd=root, env=env, timeout=BUILD_TIMEOUT_S)
    ok_beta = r.returncode == 0
    if not ok_beta:
        log(f"backup: beta build failed (exit {r.returncode}); the public backup is still published")
    # promote the new output folders to the local 'last good' folders
    for new, cur, good in ((new_pub, pub, True), (new_beta, beta, ok_beta)):
        if good:
            shutil.rmtree(cur, ignore_errors=True)
            new.rename(cur)
    return True


def publish(data, root, runner=subprocess.run, py=sys.executable, env=os.environ, log=print):
    """Upload to R2 under backup/. r2_sync never fails; without secrets it only logs."""
    if not all(env.get(k) for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")):
        log(f"backup: R2 secrets absent: built output stays in {data} (not published)")
        return False
    for sub in ("live", "live_beta"):
        if (data / sub).is_dir():
            runner([py, str(Path(root) / "live_ci" / "r2_sync.py"), str(data / sub), f"backup/{sub}/"], cwd=root, timeout=90)
    return True


def run(argv, now=None, urlopen=urllib.request.urlopen, runner=subprocess.run, env=os.environ, log=print):
    from ..paths import data_dir
    now = now or dt.datetime.now(dt.timezone.utc)
    root = Path(opt(argv, "--root", Path(__file__).resolve().parents[2]))
    dd = data_dir()
    data = Path(opt(argv, "--data", dd / "backup"))
    static = Path(opt(argv, "--static", dd / "live" / "live_static.npz"))
    pages = opt(argv, "--pages", PAGES).rstrip("/")
    stale_min, live_stale_min = float(opt(argv, "--stale-min", 30)), float(opt(argv, "--live-stale-min", 50))
    v2 = age_minutes(f"{pages}/status.json", now, urlopen)
    live = age_minutes(f"{pages}/v3/live/status.json", now, urlopen)
    verdict = decide(v2, live, stale_min, live_stale_min)
    fmt = lambda a: "unreadable" if a is None else f"{a:.0f} min"
    log(f"backup: v2 status {fmt(v2)}, live status {fmt(live)}: {verdict}")
    if verdict == "fresh" and "--force" not in argv:
        return 0
    if "--dry-run" in argv:
        log("backup: dry run, no build")
        return 0
    lock = Lock(data / "backup.lock")
    if not lock.acquire():
        log("backup: another instance is running (lock held)")
        return 2
    try:
        if not build_all(data, static, runner=runner, root=root, log=log):
            return 1
        publish(data, root, runner=runner, env=env, log=log)
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
