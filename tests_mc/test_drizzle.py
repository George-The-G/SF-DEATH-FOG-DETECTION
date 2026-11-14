"""Guide 11: drizzle (switch drizzle, needs cloud_base)."""
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from microclimate import fogtop, weather, wetness as w
from microclimate.live import forcing
from tests_mc.test_live_core import fake_data, fake_pts


class TestRate(unittest.TestCase):
    def test_thickness(self):
        r = lambda t: float(w.drizzle_rate(t, 100.0))   # noqa: E731
        self.assertEqual(r(150.0), 0.0)
        self.assertAlmostEqual(r(350.0), 0.5 * w.DRIZZLE_MAX, 6)
        self.assertAlmostEqual(r(600.0), w.DRIZZLE_MAX, 6)

    def test_street(self):
        top, base = 500.0, 300.0
        above = w.drizzle_at_street(0.1, top, base, 700.0, fogtop.fog_top_ramp(top, 700.0))
        self.assertEqual(float(above), 0.0)
        low = w.drizzle_at_street(0.1, 900.0, 600.0, 0.0, fogtop.fog_top_ramp(900.0, 0.0))
        self.assertAlmostEqual(float(low), 0.1 * 0.2, 6)       # 600 m below the base: 20 %

    def test_needs_cloud_base(self):
        with self.assertRaises(ValueError):
            w.check_physics(("drizzle",))


class TestObs(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 11, 10, tzinfo=timezone.utc)
        self.nh = 6
        self.t_h = [self.t0 + timedelta(hours=k) for k in range(self.nh)]
        self.g = np.arange(self.nh * 6) / 6.0

    def run_(self, rows):
        return forcing.drizzle_series({"KSFO": rows}, self.t_h, self.nh, self.g, np.full(len(self.g), 0.02), {})

    def test_dz_raises(self):
        D = self.run_([dict(t=self.t_h[1], ceil_ft=800.0, wx="-DZ BR")])
        self.assertGreaterEqual(D[6], 0.05 - 1e-9)

    def test_ceiling_without_weather_scales(self):
        D = self.run_([dict(t=self.t_h[1], ceil_ft=800.0, wx="")])
        self.assertAlmostEqual(D[6], 0.3 * 0.02, 6)
        D2 = self.run_([dict(t=self.t_h[1], ceil_ft=800.0, wx="BR")])
        self.assertAlmostEqual(D2[6], 0.02, 6)


class TestSimulate(unittest.TestCase):
    def run_(self, rain):
        data, start = fake_data()
        ser = weather.series(data, start, start + timedelta(hours=12), 10)
        ser["precipitation"] = np.full(len(ser["precipitation"]), rain)
        P = fake_pts(); N = len(P["x"])
        fn = lambda i, f: {**f, "drizzle": np.full(N, 0.08, np.float32), "cloud_base": np.full(N, 0.0, np.float32)}   # noqa: E731
        return w.simulate(P, ser, 10, tracer=P["tracer"], keep_lit=False, forcing_fn=fn, physics=("film", "cloud_base", "drizzle"), fog_top=900.0)

    def test_rain_dominates(self):
        _, d_dz = self.run_(2.0)
        data, start = fake_data()
        ser = weather.series(data, start, start + timedelta(hours=12), 10)
        ser["precipitation"] = np.full(len(ser["precipitation"]), 2.0)
        P = fake_pts()
        _, d_r = w.simulate(P, ser, 10, tracer=P["tracer"], keep_lit=False, physics=("film", "cloud_base"), fog_top=900.0)
        np.testing.assert_allclose(d_dz["rain_total"], d_r["rain_total"], rtol=1e-5)

    def test_drizzle_wets_without_rain(self):
        _, d = self.run_(0.0)
        self.assertGreater(d["rain_total"].min(), 0.0)
        self.assertGreater(d["drizzle_total"].min(), 0.9)


if __name__ == "__main__":
    unittest.main()
