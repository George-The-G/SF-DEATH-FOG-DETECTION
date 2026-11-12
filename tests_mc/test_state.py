"""Guide 02 part A: state carried between runs."""
import datetime as dt
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import numpy as np

from microclimate import weather, wetness
from microclimate.live import config as C
from microclimate.live import state
from tests_mc.test_live_core import fake_data, fake_pts


def run(data, a, b, **kw):
    ser = weather.series(data, a, b, 10)
    ser["cloud_cover_low"] = np.zeros(len(ser["cloud_cover_low"]))   # no fog source: the front lag (which looks back before the window start) plays no part
    P = fake_pts()
    N = len(P["x"])
    tab = np.tile(P["tracer"], (9, 1))
    fn = lambda i, f: {**f, "temp": f["temp"] - 3.0 + np.linspace(-1, 1, N), "dew": f["dew"] + 1.0}  # noqa: E731  (dew above surface temperature: dew term active)
    return wetness.simulate(P, ser, 10, tracer_dirs=tab, forcing_fn=fn, lag_block_h=3, out_every=6, keep_lit=False, use_dirs=True, **kw)


class TestSimulateState(unittest.TestCase):
    def setUp(self):
        self.data, self.t0 = fake_data()

    def test_split_run_equals_one_run(self):
        t1 = self.t0 + timedelta(hours=24)
        full, d = run(self.data, self.t0, self.t0 + timedelta(hours=48), save_at=144)
        self.assertEqual(d["state_at"]["W"].shape, (40,))
        self.assertGreater(d["state_at"]["W"].max(), 0.0)
        part, _ = run(self.data, t1, t1 + timedelta(hours=24), state0=d["state_at"])
        np.testing.assert_array_equal(part, full[24:])
        cold, _ = run(self.data, t1, t1 + timedelta(hours=24))
        self.assertGreater(np.abs(cold - part).max(), 0.5)   # the carried state matters

    def test_save_beyond_window_saves_nothing(self):
        _, d = run(self.data, self.t0, self.t0 + timedelta(hours=10), save_at=10 * 6 + 5)
        self.assertIsNone(d["state_at"])
        _, d = run(self.data, self.t0, self.t0 + timedelta(hours=10))
        self.assertIsNone(d["state_at"])

    def test_state0_shape_checked(self):
        with self.assertRaises(AssertionError):
            run(self.data, self.t0, self.t0 + timedelta(hours=2), state0=dict(W=np.zeros(7)))


class TestStateFiles(unittest.TestCase):
    def test_round_trip_and_size(self):
        r = np.random.default_rng(1)
        W = r.uniform(0, 100, 285809).astype(np.float32)
        t = dt.datetime(2026, 11, 12, 20, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / state.name_for(t)
            self.assertEqual(p.name, "state_20261112T20.npz")
            size = state.save(p, t, ("film",), "live-1", dict(W=W))
            self.assertLess(size, state.MAX_BYTES)
            s = state.load(p)
            self.assertEqual((s["time"], s["physics"], s["model_version"]), (t, ["film"], "live-1"))
            np.testing.assert_allclose(s["arrays"]["W"], W, atol=0.06)        # float16
            old = state.MAX_BYTES
            state.MAX_BYTES = 100
            try:
                with self.assertRaises(AssertionError):
                    state.save(Path(d) / "state_20261113T20.npz", t, (), "live-1", dict(W=W))
            finally:
                state.MAX_BYTES = old
            self.assertFalse(list(Path(d).glob("*.tmp")))
            self.assertFalse((Path(d) / "state_20261113T20.npz").exists())

    def test_wrong_version_physics_time_or_size_is_a_cold_start(self):
        t = dt.datetime(2026, 11, 12, 20, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(state.find_anchor(d, t, (), "live-1", 5)[0], None)           # no file
            state.save(Path(d) / state.name_for(t), t, (), "live-1", dict(W=np.arange(5)))
            a, why = state.find_anchor(d, t, (), "live-1", 5)
            self.assertEqual(why, "ok")
            np.testing.assert_allclose(a["W"], np.arange(5))
            self.assertIsNone(state.find_anchor(d, t, (), "live-2", 5)[0])                # model version
            self.assertIsNone(state.find_anchor(d, t, ("film",), "live-1", 5)[0])         # physics set
            self.assertIsNone(state.find_anchor(d, t, (), "live-1", 6)[0])                # different point table
            (Path(d) / state.name_for(t)).write_bytes(b"not an npz")
            self.assertIn("unreadable", state.find_anchor(d, t, (), "live-1", 5)[1])

    def test_prune_keeps_two_newest(self):
        with tempfile.TemporaryDirectory() as d:
            for h in ("20261110T20", "20261111T20", "20261112T20", "20261112T21"):
                (Path(d) / f"state_{h}.npz").write_bytes(b"x")
            (Path(d) / "day0.u8").write_bytes(b"x")
            self.assertEqual(state.prune(d), ["state_20261112T20.npz", "state_20261112T21.npz"])
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["day0.u8", "state_20261112T20.npz", "state_20261112T21.npz"])

    def test_next_window_start(self):
        start = dt.datetime(2026, 11, 12, 2, tzinfo=dt.timezone.utc)        # 18:00 PST on 11 Nov
        self.assertEqual(state.next_t0(start), dt.datetime(2026, 11, 12, 20, tzinfo=dt.timezone.utc))   # 12:00 PST on 12 Nov
        start = dt.datetime(2026, 11, 1, 1, tzinfo=dt.timezone.utc)         # 18:00 PDT on 31 Oct, DST ends the next night
        self.assertEqual(state.next_t0(start), dt.datetime(2026, 11, 1, 20, tzinfo=dt.timezone.utc))

    def test_default_does_not_carry(self):
        self.assertIs(C.CARRY_STATE, False)
        self.assertEqual(C.PHYSICS, ())


if __name__ == "__main__":
    unittest.main()
