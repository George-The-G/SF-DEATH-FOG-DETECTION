"""Guide 12: in-place fog for any wind direction (switch insitu_fog, needs cloud_base)."""
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from microclimate import weather, wetness as w
from microclimate.live import forcing
from tests_mc.test_live_core import fake_data, fake_pts


def run(wdir, ev, dT, physics, top=500.0, zscale=1.0):
    data, start = fake_data()
    ser = weather.series(data, start, start + timedelta(hours=12), 10)
    ser["wind_dir"] = np.full(len(ser["wind_dir"]), float(wdir))
    P = fake_pts()
    P["z"] = P["z"] * zscale
    N = len(P["x"])
    fn = lambda i, f: {**f, "temp": np.full(N, 10.0, np.float32), "dew": np.full(N, 10.0 - dT, np.float32), "fog_ev": np.full(N, ev, np.float32),   # noqa: E731
                       "cloud_base": np.zeros(N, np.float32)}
    return w.simulate(P, ser, 10, tracer=P["tracer"], keep_lit=False, forcing_fn=fn, physics=physics, fog_top=top)


ON = ("film", "cloud_base", "insitu_fog")
OFF = ("film", "cloud_base")


class TestInsitu(unittest.TestCase):
    def test_east_wind_saturated_foggy(self):
        _, a = run(90, 1.0, 0.0, ON)
        _, b = run(90, 1.0, 0.0, OFF)
        self.assertGreater(a["fog_total"].min(), 0.0)
        self.assertEqual(float(b["fog_total"].max()), 0.0)
        self.assertGreater(a["insitu_share"].min(), 0.99)

    def test_no_evidence_equals_off(self):
        _, a = run(270, 0.0, 0.0, ON)
        _, b = run(270, 0.0, 0.0, OFF)
        np.testing.assert_array_equal(a["fog_total"], b["fog_total"])

    def test_not_saturated(self):
        _, a = run(90, 1.0, 3.0, ON)
        self.assertEqual(float(a["fog_total"].max()), 0.0)

    def test_above_top(self):
        _, a = run(90, 1.0, 0.0, ON, top=-500.0)
        self.assertEqual(float(a["fog_total"].max()), 0.0)

    def test_needs_cloud_base(self):
        with self.assertRaises(ValueError):
            w.check_physics(("insitu_fog",))

    def test_helpers(self):
        self.assertEqual(float(w.vis_evidence(300.0)), 1.0)
        self.assertEqual(float(w.vis_evidence(10000.0)), 0.0)
        self.assertAlmostEqual(float(w.vis_evidence(3000.0)), 0.5, 6)


class TestCorrection(unittest.TestCase):
    def test_obs_raises_evidence(self):
        t0 = datetime(2026, 11, 10, tzinfo=timezone.utc)
        t_h = [t0 + timedelta(hours=k) for k in range(5)]
        g = np.arange(30) / 6.0
        rows = [dict(t=t_h[2], vis_mi=0.5, ceil_ft=np.nan)]
        c = forcing.insitu_correction({"KSFO": rows}, t_h, 5, g, lambda lat, lon: np.zeros(5), {})
        self.assertAlmostEqual(c[12], 0.5, 6)   # (1 - 0) * 0.5
        c0 = forcing.insitu_correction({}, t_h, 5, g, lambda lat, lon: np.zeros(5), {})
        self.assertTrue((c0 == 0).all())


if __name__ == "__main__":
    unittest.main()
