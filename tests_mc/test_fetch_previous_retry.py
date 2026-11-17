"""live_ci/fetch_previous.py retries transient errors per file (stubbed urlopen, no sleeping, no network)."""
import io
import json
import runpy
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SCRIPT = str(Path(__file__).resolve().parent.parent / "live_ci" / "fetch_previous.py")
IDX = json.dumps({"weather": "w.json", "frame_files": [], "fog_files": []}).encode()


class R(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


def run_script(behaviour):
    """behaviour(name, call_no) -> bytes or raises. Returns (exit code or None, per-name call counts, sleeps)."""
    calls, sleeps = {}, []

    def urlopen(req, timeout=None):
        name = req.full_url.split("?")[0].rsplit("/", 1)[1]
        calls[name] = calls.get(name, 0) + 1
        return R(behaviour(name, calls[name]))

    with tempfile.TemporaryDirectory() as d, mock.patch("urllib.request.urlopen", urlopen), mock.patch("time.sleep", sleeps.append), \
            mock.patch.object(sys, "argv", ["fetch_previous.py", "http://x/live", d]):
        code = None
        try:
            runpy.run_path(SCRIPT, run_name="__main__")
        except SystemExit as e:
            code = e.code
        files = sorted(p.name for p in Path(d).iterdir())
    return code, calls, sleeps, files


class FetchPreviousRetry(unittest.TestCase):
    def test_503_then_ok_is_retried(self):
        def b(name, n):
            if name == "index.json":
                return IDX
            if name == "status.json" and n < 3:
                raise http(503)
            return b"{}"
        code, calls, sleeps, files = run_script(b)
        self.assertIsNone(code)
        self.assertEqual(calls["status.json"], 3)
        self.assertEqual(len(sleeps), 2)
        self.assertIn("status.json", files)

    def test_timeout_and_reset_retried(self):
        def b(name, n):
            if name == "index.json":
                return IDX
            if name != "status.json":
                return b"{}"
            if n == 1:
                raise TimeoutError("t")
            if n == 2:
                raise ConnectionResetError("reset")
            return b"{}"
        code, calls, _, _ = run_script(b)
        self.assertIsNone(code)
        self.assertEqual(calls["status.json"], 3)

    def test_404_not_retried(self):
        def b(name, n):
            if name == "index.json":
                return IDX
            if name != "status.json":
                return b"{}"
            raise http(404)
        code, calls, sleeps, files = run_script(b)
        self.assertEqual(code, 1)
        self.assertEqual((calls["status.json"], sleeps, files), (1, [], []))

    def test_persistent_503_bounded_and_writes_nothing(self):
        def b(name, n):
            if name == "index.json":
                return IDX
            if name != "status.json":
                return b"{}"
            raise http(503)
        code, calls, sleeps, files = run_script(b)
        self.assertEqual(code, 1)
        self.assertEqual(calls["status.json"], 4)
        self.assertLessEqual(sum(sleeps), 30)
        self.assertEqual(files, [])


if __name__ == "__main__":
    unittest.main()


