"""Bounded retry of transient network errors in microclimate.live.fetch.get (stubbed requests, no sleeping, no network)."""
import unittest
from unittest import mock

import requests

from microclimate.live import config as C, fetch


class Resp:
    def __init__(self, status=200, headers=None):
        self.status_code, self.headers = status, headers or {}


class Stub:
    def __init__(self, seq):
        self.seq, self.calls = list(seq), 0

    def __call__(self, *a, **k):
        self.calls += 1
        x = self.seq[min(self.calls - 1, len(self.seq) - 1)]
        if isinstance(x, Exception):
            raise x
        return x


class RetryTests(unittest.TestCase):
    def run_get(self, seq):
        stub, sleeps, logs = Stub(seq), [], []
        with mock.patch.object(fetch.requests, "get", stub), mock.patch.object(fetch, "_sleep", sleeps.append), mock.patch.object(fetch, "_log", logs.append):
            try:
                out = fetch.get("http://x", None, "rrfs")
            except fetch.FetchError as e:
                out = e
        return out, stub.calls, sleeps, logs

    def test_fails_then_succeeds(self):
        ok = Resp(200)
        out, calls, sleeps, logs = self.run_get([requests.ReadTimeout("t"), requests.ConnectionError("reset"), ok])
        self.assertIs(out, ok)
        self.assertEqual((calls, sleeps), (3, [2.0, 6.0]))
        self.assertEqual(len(logs), 2)
        self.assertIn("ReadTimeout", logs[0])

    def test_5xx_and_429_retry_with_retry_after(self):
        ok = Resp(200)
        out, calls, sleeps, _ = self.run_get([Resp(503), Resp(429, {"Retry-After": "7"}), Resp(429, {"Retry-After": "999"}), ok])
        self.assertIs(out, ok)
        self.assertEqual(sleeps, [2.0, 7.0, C.RETRY_AFTER_MAX_S])

    def test_always_fails_bounded(self):
        out, calls, sleeps, _ = self.run_get([requests.ReadTimeout("t")])
        self.assertIsInstance(out, fetch.FetchError)
        self.assertIn("ReadTimeout", str(out))
        self.assertEqual(calls, C.RETRIES)
        self.assertLessEqual(sum(sleeps), 60)

    def test_4xx_not_retried(self):
        for code in (400, 404, 403):
            out, calls, sleeps, logs = self.run_get([Resp(code)])
            self.assertIsInstance(out, fetch.FetchError)
            self.assertIn(f"HTTP {code}", str(out))
            self.assertEqual((calls, sleeps, logs), (1, [], []))

    def test_success_first_time_no_sleep(self):
        ok = Resp(200)
        out, calls, sleeps, logs = self.run_get([ok])
        self.assertEqual((out, calls, sleeps, logs), (ok, 1, [], []))


if __name__ == "__main__":
    unittest.main()
