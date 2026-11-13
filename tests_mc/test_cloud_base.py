"""Guide 08: cloud base. In-cloud weight per street, street temperature, airport ceiling offset, switch off = unchanged."""
import unittest
from datetime import timedelta

import numpy as np

from microclimate import fogtop, wetness
from microclimate.live import config as C, forcing
from tests_mc import anchors_fixture as fx


class TestWeight(unittest.TestCase):
    def test_base_200_top_400(self):
        z = np.array([50.0, 170.0, 200.0, 300.0, 500.0])
        w = fogtop.cloud_base_ramp(200.0, z) * fogtop.fog_top_ramp(400.0, z)
        self.assertEqual(w[0], 0.0)
        self.assertAlmostEqual(float(w[1]), 0.5 * float(fogtop.fog_top_ramp(400.0, 170.0)), 6)
        self.assertAlmostEqual(float(fogtop.cloud_base_ramp(200.0, 170.0)), 0.5, 6)
        self.assertEqual(float(fogtop.cloud_base_ramp(200.0, 200.0)), 1.0)
        self.assertGreater(float(w[3]), 0.99)
        self.assertLess(float(w[4]), 0.05)

    def test_ground_fog_full_weight(self):   # T = Td: base at ground, every street at or above the model terrain gets 1
        z = np.linspace(20, 300, 15)
        self.assertTrue(np.allclose(fogtop.cloud_base_ramp(z.min(), z), 1.0) or fogtop.cloud_base_ramp(z.min(), z).min() > 0.99)

    def test_monotone(self):
        z = np.linspace(0, 500, 101)
        self.assertTrue((np.diff(fogtop.cloud_base_ramp(200.0, z)) >= -1e-12).all())
        b = np.linspace(0, 500, 101)
        self.assertTrue((np.diff(fogtop.cloud_base_ramp(b, 250.0)) <= 1e-12).all())


class TestTemperature(unittest.TestCase):
    def test_lapse_pieces(self):
        zm, base, top = 30.0, 130.0, 330.0
        z = np.array([30.0, 130.0, 230.0, 330.0, 500.0])
        t, td = fogtop.street_temperature(np.full(5, 12.0), np.full(5, 10.0), z, np.full(5, zm), np.full(5, base), np.full(5, top))
        self.assertAlmostEqual(float(t[0]), 12.0, 5)
        self.assertAlmostEqual(float(t[1]), 12.0 - 0.0098 * 100, 4)
        self.assertAlmostEqual(float(t[2]), 12.0 - 0.98 - 0.6, 4)
        self.assertAlmostEqual(float(t[3]), 12.0 - 0.98 - 1.2, 4)
        self.assertAlmostEqual(float(t[4]), float(t[3]), 5)   # constant above the top
        self.assertTrue((td <= t + 1e-6).all())

    def test_clip_and_below_terrain(self):
        t, _ = fogtop.street_temperature(np.array([10.0]), np.array([8.0]), np.array([-100.0]), np.array([50.0]), np.array([300.0]), np.array([600.0]))
        self.assertAlmostEqual(float(t[0]), 10.0 + 0.0098 * 150.0, 4)


class TestOffset(unittest.TestCase):
    def test_600ft_one_kelvin(self):
        t_h = [fx.T0 + timedelta(hours=k) for k in range(6)]
        rows = [dict(t=t_h[2], temp=10.0, dewp=9.0, ceil_ft=600.0)]
        info = dict(flags=[])
        off = forcing.base_offset_hourly({"KSFO": rows}, t_h, 6, info)
        raw_expected = 600 * 0.3048 - 125.0
        self.assertAlmostEqual(raw_expected, 57.88, 1)
        self.assertAlmostEqual(float(off[2]), raw_expected, 3)
        self.assertEqual(info["cloud_base"]["stations_used"], ["KSFO"])

    def test_clip_and_high_ceiling_ignored(self):
        t_h = [fx.T0 + timedelta(hours=k) for k in range(4)]
        rows = [dict(t=t_h[1], temp=10.0, dewp=10.0, ceil_ft=2900.0), dict(t=t_h[2], temp=10.0, dewp=10.0, ceil_ft=5000.0)]
        off = forcing.base_offset_hourly({"KHAF": rows}, t_h, 4, dict(flags=[]))
        self.assertEqual(float(off[1]), 300.0)       # 884 m clipped
        self.assertFalse(np.isfinite(off[0]))


class TestSwitch(unittest.TestCase):
    def test_forcing_off_equals_before(self):
        F0, _, _ = fx.build()
        F1, _, _ = fx.build(physics=())
        self.assertEqual(fx.digest(F0), fx.digest(F1))
        self.assertNotIn("cloud_base", F0["info"])

    def test_on_gives_base_and_lower_streets_drier(self):
        F, x, y = fx.build(physics=("cloud_base",), z=np.linspace(5, 200, 50))
        f = F["forcing_fn"](100, {"temp": 0.0})
        self.assertIn("cloud_base", f)
        self.assertEqual(f["cloud_base"].shape, (50,))
        self.assertTrue((f["dew"] <= f["temp"] + 1e-5).all())
        self.assertIn("cloud_base", F["info"])
        self.assertEqual(len(F["cloud_base_coast"]), fx.N_STEPS)

    def test_simulate_in_cloud_hours(self):
        def run(P):
            def fn(i, f):
                N = 40
                return {**f, "cloud_base": np.full(N, 150.0, np.float32)}
            import tests_mc.test_physics as tp
            from microclimate import weather
            from datetime import timedelta
            from tests_mc.test_live_core import fake_data, fake_pts
            data, start = fake_data()
            ser = weather.series(data, start, start + timedelta(hours=30), 10)
            pts = fake_pts()
            pts["z"] = np.linspace(10, 300, 40)
            return wetness.simulate(pts, ser, 10, forcing_fn=fn, keep_lit=False, physics=P, fog_top=400.0, tracer=pts["tracer"])
        _, d0 = run(("film",))
        _, d1 = run(("film", "cloud_base"))
        h = d1["in_cloud_hours"]
        self.assertEqual(h[0], 0.0)           # 10 m street far below the base
        self.assertGreater(h[-1], 29.0)       # 300 m street in cloud the whole time
        self.assertNotIn("in_cloud_hours", d0)
        self.assertLess(d1["fog_total"][:5].sum(), d0["fog_total"][:5].sum())


if __name__ == "__main__":
    unittest.main()
