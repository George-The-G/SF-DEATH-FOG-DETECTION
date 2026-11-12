import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import numpy as np

from microclimate import fogtop, wetness


def night(n=17, profile=None):
    t0 = datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)
    ser = dict(t_utc=[t0 + timedelta(minutes=10 * i) for i in range(n)], temperature_2m=np.full(n, 11.0), dew_point_2m=np.full(n, 10.6),
               wind_speed_10m=np.full(n, 3.0), cloud_cover_low=np.full(n, 100.0), precipitation=np.zeros(n),
               shortwave_radiation=np.zeros(n), wind_dir=np.full(n, 270.0), elevation_m=34.0)
    ser.update(profile or {})
    return ser


def profile(rh, t, z=(100, 320, 540, 760, 980, 1450)):
    """constant-in-time pressure-level profile; rh has 5 values (1000..900 hPa), t has 6 (1000..850)."""
    d = {}
    for i, l in enumerate((1000, 975, 950, 925, 900, 850)):
        d[f"temperature_{l}hPa"] = np.full(17, float(t[i]))
        d[f"geopotential_height_{l}hPa"] = np.full(17, float(z[i]))
        if i < 5:
            d[f"relative_humidity_{l}hPa"] = np.full(17, float(rh[i]))
    return d


class TestFogHeight(unittest.TestCase):
    def test_blend_bands_interpolates_and_clamps(self):
        b = np.array([[0.1, 0.2, 0.4, 0.8]] * 5, np.float32)
        out = fogtop.blend_bands(b, np.array([0.0, 75.0, 150.0, 400.0, 900.0]))
        np.testing.assert_allclose(out, [0.1, 0.1, 0.15, 0.4, 0.8], atol=1e-6)

    def test_blend_bands_monotone_for_monotone_bands(self):
        b = np.tile(np.array([0.1, 0.2, 0.4, 0.8], np.float32), (200, 1))
        out = fogtop.blend_bands(b, np.linspace(0, 900, 200))
        self.assertTrue(np.all(np.diff(out) >= -1e-7))
        self.assertTrue(out.min() >= 0.1 - 1e-6 and out.max() <= 0.8 + 1e-6)

    def test_ramp_bounded_and_monotone(self):
        z = np.linspace(-100, 600, 300)
        r = fogtop.fog_top_ramp(200.0, z)
        self.assertTrue(np.all((r >= 0) & (r <= 1)))
        self.assertTrue(np.all(np.diff(r) <= 1e-12))        # non-increasing with street elevation
        tops = np.linspace(0, 500, 300)
        r2 = fogtop.fog_top_ramp(tops, 150.0)
        self.assertTrue(np.all(np.diff(r2) >= -1e-12))      # non-decreasing with fog top
        self.assertEqual(float(fogtop.fog_top_ramp(200.0, 200.0)), 0.5)
        h = wetness.FOG_TOP_RAMP_M
        self.assertEqual(float(fogtop.fog_top_ramp(200.0, 200.0 - h)), 1.0)
        self.assertEqual(float(fogtop.fog_top_ramp(200.0, 200.0 + h)), 0.0)

    def test_fog_top_from_profile_shallow_and_deep(self):
        # saturated to 975 hPa, dry from 950 hPa, T inversion above 925: shallow-ish top between 975 and 950 levels
        shallow = night(profile=profile([95, 90, 40, 20, 15], [12, 12.5, 11, 10, 15, 14]))
        top, info = fogtop.estimate_fog_top(shallow)
        self.assertEqual(info["source"], "profile")
        self.assertTrue(np.all(top >= 34 + wetness.FOG_TOP_MIN_M))
        deep = night(profile=profile([97, 97, 96, 92, 40], [12, 11, 10, 9, 8, 12]))
        top_d, _ = fogtop.estimate_fog_top(deep)
        self.assertGreater(top_d[0], top[0] + 200)
        self.assertLessEqual(top_d.max(), wetness.FOG_TOP_MAX_M)

    def test_fog_top_fallback_without_profile(self):
        top, info = fogtop.estimate_fog_top(night())
        self.assertEqual(info["source"], "fallback_constant")
        self.assertTrue(np.all(top == wetness.FOG_TOP_FALLBACK_M))

    def _pts(self, z):
        N = len(z)
        return dict(x=np.full(N, 545000.0), y=np.full(N, 4180000.0), z=np.asarray(z, float), svf=np.full(N, 0.5, np.float32),
                    porous=np.ones(N, np.float32), tracer=np.full(N, 0.03, np.float32), wind_ratio=np.ones(N, np.float32))

    def test_high_point_drier_under_low_fog_top(self):
        pts = self._pts([5.0, 60.0, 150.0, 250.0])
        with mock.patch.object(wetness, "FOG_TOP_FALLBACK_M", 90.0):
            W, dg = wetness.simulate(pts, night(), 10, tracer=pts["tracer"], fog_mode="profile")
        end = W[-1]
        self.assertGreater(end[0], end[2])
        self.assertGreater(end[0], end[3])
        self.assertGreaterEqual(end[1] + 1e-6, end[2])
        self.assertLess(end[3], 1.0)                      # a 250 m summit stays dry above a 90 m fog top
        self.assertEqual(dg["fog_mode"], "profile")

    def test_band0_mode_ignores_fog_top(self):
        pts = self._pts([5.0, 250.0])
        pts["tracer"] = np.full(2, 0.03, np.float32)
        with mock.patch.object(wetness, "FOG_TOP_FALLBACK_M", 90.0):
            W0, _ = wetness.simulate(pts, night(), 10, fog_mode="band0")
        self.assertAlmostEqual(float(W0[-1][0]), float(W0[-1][1]), places=3)

    def test_dry_night_stays_dry(self):
        pts = self._pts([5.0, 60.0])
        ser = night()
        ser["dew_point_2m"] = np.full(17, 2.0)
        W, _ = wetness.simulate(pts, ser, 10, tracer=pts["tracer"], fog_mode="profile")
        self.assertEqual(float(W.max()), 0.0)


if __name__ == "__main__":
    unittest.main()
