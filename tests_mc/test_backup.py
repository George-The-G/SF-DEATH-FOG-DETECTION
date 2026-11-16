"""microclimate/live/backup.py: freshness decision, lock, no publish when the build fails (multi-scale guide 10, part C)."""
import datetime as dt
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from microclimate.live import backup

NOW = dt.datetime(2026, 11, 15, 8, 0, tzinfo=dt.timezone.utc)


class Ctx:   # urlopen result needs the context-manager protocol
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def opener2(v2_min, live_min):
    def f(req, timeout=0):
        url = req.full_url
        m = live_min if "/v3/live/" in url else v2_min
        if m is None:
            raise OSError("down")
        key = "generated_iso" if "/v3/live/" in url else "generated_utc"
        return Ctx(json.dumps({key: (NOW - dt.timedelta(minutes=m)).isoformat()}).encode())
    return f


class Decide(unittest.TestCase):
    def test_decisions(self):
        self.assertEqual(backup.decide(8, 20), "fresh")
        self.assertEqual(backup.decide(8, 49), "fresh")          # live builds every 30 min: 49 is still on schedule
        self.assertEqual(backup.decide(31, 20), "stale")
        self.assertEqual(backup.decide(8, 55), "stale")
        self.assertEqual(backup.decide(None, 20), "fresh")       # one unreadable file alone is not an outage
        self.assertEqual(backup.decide(None, None), "unreachable")

    def test_age_reads_both_formats_and_survives_errors(self):
        self.assertAlmostEqual(backup.age_minutes("https://x/status.json", NOW, opener2(12, 5)), 12, places=3)
        self.assertAlmostEqual(backup.age_minutes("https://x/v3/live/status.json", NOW, opener2(12, 5)), 5, places=3)
        self.assertIsNone(backup.age_minutes("https://x/status.json", NOW, opener2(None, None)))


class Runner:
    def __init__(self, fail_public=False):
        self.calls, self.fail_public = [], fail_public

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if "build" in cmd:
            out = Path(cmd[cmd.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            bad = self.fail_public and "--promoted" in cmd
            if not bad:
                (out / "status.json").write_text("{}")
            return SimpleNamespace(returncode=1 if bad else 0)
        return SimpleNamespace(returncode=0)


SECRETS = {k: "x" for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")}


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = self.tmp.name
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, v2, live, runner, env, extra=()):
        return backup.run(["--data", self.data, "--root", self.data, "--static", "s.npz", *extra], now=NOW, urlopen=opener2(v2, live), runner=runner, env=env, log=self.logs.append)

    def test_fresh_exits_at_once(self):
        r = Runner()
        self.assertEqual(self.go(8, 20, r, SECRETS), 0)
        self.assertEqual(r.calls, [])

    def test_stale_builds_both_and_publishes_under_backup(self):
        r = Runner()
        self.assertEqual(self.go(45, 60, r, SECRETS), 0)
        builds = [c for c in r.calls if "build" in c]
        self.assertEqual(len(builds), 2)
        self.assertIn("--promoted", builds[0])
        self.assertIn("--beta", builds[1])
        syncs = [c for c in r.calls if any("r2_sync" in a for a in c)]
        self.assertEqual([c[-1] for c in syncs], ["backup/live/", "backup/live_beta/"])
        self.assertFalse(any("git" in c[0] for c in r.calls), "the PC must never run git here")
        self.assertFalse(Path(self.data, "backup.lock").exists(), "lock released")

    def test_failed_build_exits_nonzero_without_publishing(self):
        r = Runner(fail_public=True)
        self.assertEqual(self.go(45, 60, r, SECRETS), 1)
        self.assertFalse(any(any("r2_sync" in a for a in c) for c in r.calls))
        self.assertFalse(Path(self.data, "backup.lock").exists())

    def test_no_secrets_builds_but_does_not_publish(self):
        r = Runner()
        self.assertEqual(self.go(45, 60, r, {}), 0)
        self.assertFalse(any(any("r2_sync" in a for a in c) for c in r.calls))
        self.assertTrue(any("not published" in m for m in self.logs))

    def test_lock_blocks_second_instance_and_stale_lock_is_taken_over(self):
        lock = Path(self.data, "backup.lock")
        lock.write_text("1")
        r = Runner()
        self.assertEqual(self.go(45, 60, r, SECRETS), 2)
        self.assertEqual(r.calls, [])
        old = time.time() - 3600
        os.utime(lock, (old, old))
        self.assertEqual(self.go(45, 60, r, SECRETS), 0)

    def test_dry_run_and_force(self):
        r = Runner()
        self.assertEqual(self.go(45, 60, r, SECRETS, ["--dry-run"]), 0)
        self.assertEqual(r.calls, [])
        self.assertEqual(self.go(5, 5, r, SECRETS, ["--force"]), 0)
        self.assertTrue(r.calls)


if __name__ == "__main__":
    unittest.main()
