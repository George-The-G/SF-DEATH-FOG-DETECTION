"""Live-ready wetness core (PLAN_LIVE_DATA.md step 3): arbitrary windows, tracer table input, spatial forcing, block lag, output cadence."""
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from microclimate import weather, wetness


def fake_data(hours=100, start=datetime(2026, 11, 10, 0, tzinfo=timezone.utc)):
    t = [start + timedelta(hours=h) for h in range(hours)]
    h = np.arange(hours)
    d = {"time": [x.strftime("%Y-%m-%dT%H:%M") for x in t],
         "temperature_2m": list(12 + 2 * np.sin(h / 6.0)), "dew_point_2m": list(11 + np.sin(h / 6.0)),
         "wind_speed_10m": list(5 + np.cos(h / 5.0)), "wind_direction_10m": list(270 + 40 * np.sin(h / 20.0)),
         "cloud_cover": [80.0] * hours, "cloud_cover_low": [70.0] * hours, "precipitation": [0.0] * hours,
         "shortwave_radiation": list(np.maximum(0, 500 * np.sin((h - 6) / 24.0 * 2 * np.pi)))}
    return {"hourly": d}, start


def fake_pts(n=40, seed=0):
    r = np.random.default_rng(seed)
    return dict(x=wetness.X_WEST + r.uniform(0, 12000, n), y=4171500 + r.uniform(0, 9000, n), z=r.uniform(5, 120, n),
                svf=r.uniform(0.3, 1, n).astype(np.float32), porous=np.ones(n, np.float32), wind_ratio=np.ones(n, np.float32),
                rel_wind=np.ones(n, np.float32), horizon=r.uniform(0, 30, (n, 72)).astype(np.float32), tracer=r.uniform(0, 0.04, n).astype(np.float32))


class TestSeries(unittest.TestCase):
    def test_window_length_and_ends(self):
        data, start = fake_data()
        ser = weather.series(data, start + timedelta(hours=3), start + timedelta(hours=3 + 72), 10)
        self.assertEqual(len(ser["temperature_2m"]), 72 * 6 + 1)
        self.assertEqual(ser["t_utc"][0], start + timedelta(hours=3))
        self.assertEqual(ser["t_utc"][-1], start + timedelta(hours=75))
        self.assertEqual(len(ser["wind_dir"]), len(ser["t_utc"]))

    def test_night_series_is_the_16h_wrapper(self):
        data, start = fake_data()
        a = weather.night_series(data, start, 10)
        b = weather.series(data, start, start + timedelta(hours=16), 10)
        self.assertEqual(len(a["t_utc"]), 97)
        for k in a:
            if k == "t_utc":
                self.assertEqual(a[k], b[k])
            elif k != "elevation_m":
                np.testing.assert_array_equal(a[k], b[k])


class TestCore(unittest.TestCase):
    def setUp(self):
        self.data, self.start = fake_data()
        self.ser = weather.series(self.data, self.start, self.start + timedelta(hours=30), 10)
        self.pts = fake_pts()

    def test_table_input_matches_single_tracer_for_west_wind(self):
        ser = dict(self.ser)
        ser["wind_dir"] = np.full(len(ser["wind_dir"]), 270.0)
        P = self.pts
        tab = np.tile(P["tracer"], (9, 1))
        W1, d1 = wetness.simulate(P, ser, 10, tracer=P["tracer"], use_dirs=False)
        W2, d2 = wetness.simulate(P, ser, 10, tracer_dirs=tab)
        np.testing.assert_allclose(W1, W2, atol=1e-4)
        self.assertEqual(d2["dirs_used"], list(wetness.DEFAULT_DIRS))

    def test_table_input_reads_no_library(self):
        saved = wetness.DATA_DIR
        wetness.DATA_DIR = "Z:/does/not/exist"
        try:
            W, _ = wetness.simulate(self.pts, self.ser, 10, tracer_dirs=np.tile(self.pts["tracer"], (9, 1)), keep_lit=False)
        finally:
            wetness.DATA_DIR = saved
        self.assertTrue(np.isfinite(W).all())

    def test_array_forcing_equals_scalar(self):
        P = self.pts
        N = len(P["x"])
        W1, _ = wetness.simulate(P, self.ser, 10, tracer_dirs=np.tile(P["tracer"], (9, 1)))
        W2, _ = wetness.simulate(P, self.ser, 10, tracer_dirs=np.tile(P["tracer"], (9, 1)),
                                 forcing_fn=lambda i, f: {k: np.full(N, v) for k, v in f.items()})
        np.testing.assert_allclose(W1, W2, atol=1e-5)

    def test_spatial_forcing_differs_per_point(self):
        P = self.pts
        N = len(P["x"])
        warm = lambda i, f: {**f, "temp": f["temp"] + np.linspace(-3, 3, N)}  # noqa: E731
        W1, _ = wetness.simulate(P, self.ser, 10, tracer_dirs=np.tile(P["tracer"], (9, 1)))
        W2, _ = wetness.simulate(P, self.ser, 10, tracer_dirs=np.tile(P["tracer"], (9, 1)), forcing_fn=warm)
        self.assertGreater(np.abs(W1 - W2).max(), 0.1)

    def test_block_lag(self):
        P = self.pts
        tab = np.tile(P["tracer"], (9, 1))
        W0, d0 = wetness.simulate(P, self.ser, 10, tracer_dirs=tab)
        Wb, db = wetness.simulate(P, self.ser, 10, tracer_dirs=tab, lag_block_h=3)
        n = len(self.ser["temperature_2m"])
        self.assertEqual(len(db["block_dirs"]), -(-n // 18))
        self.assertGreater(len(set(np.round(db["block_dirs"], 1))), 1)   # direction changes between blocks
        Wl, dl = wetness.simulate(P, self.ser, 10, tracer_dirs=tab, lag_block_h=1000)  # one block = whole window
        np.testing.assert_allclose(Wl, W0, atol=1e-5)

    def test_output_cadence(self):
        P = self.pts
        tab = np.tile(P["tracer"], (9, 1))
        Wf, _ = wetness.simulate(P, self.ser, 10, tracer_dirs=tab)
        Wh, dh = wetness.simulate(P, self.ser, 10, tracer_dirs=tab, out_every=6, keep_lit=False)
        np.testing.assert_allclose(Wh, Wf[::6], atol=1e-6)
        self.assertIsNone(dh["lit"])

    def test_72h_shapes(self):
        ser = weather.series(self.data, self.start, self.start + timedelta(hours=72), 10)
        W, _ = wetness.simulate(self.pts, ser, 10, tracer_dirs=np.tile(self.pts["tracer"], (9, 1)), out_every=6, lag_block_h=3, keep_lit=False)
        self.assertEqual(W.shape, (73, 40))


class TestImports(unittest.TestCase):
    def test_live_path_does_not_import_warp_or_xlb(self):
        code = ("import sys; import microclimate.wetness, microclimate.fogtop, microclimate.dirlib, microclimate.weather, microclimate.sun, "
                "microclimate.city; bad = [m for m in ('warp', 'xlb', 'jax') if m in sys.modules]; assert not bad, bad")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
