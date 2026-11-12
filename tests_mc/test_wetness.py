import unittest

import numpy as np

from microclimate import wetness as w

F = dict(temp=10.0, dew=9.0, wind=2.0, cloud_low=80.0, precip=0.0, sw=0.0)


def go(W0=0.0, f=None, fog=0.0, lit=False, n=1, svf=0.8, porous=1.0, ratio=1.0):
    ff = dict(F, **(f or {}))
    W = np.full(2, W0, np.float32)
    for _ in range(n):
        W, _ = w.step(W, np.full(2, svf, np.float32), np.full(2, porous, np.float32), ff, np.full(2, fog), np.full(2, lit), 1 / 6, ratio)
    return float(W[0])


class TestWetness(unittest.TestCase):
    def test_more_fog_wetter(self):
        self.assertLess(go(fog=0.2, n=6), go(fog=0.8, n=6))
        self.assertEqual(go(fog=0.0, f=dict(dew=3.0), n=6), 0.0)

    def test_sunlit_drier(self):
        f = dict(dew=5.0, sw=400.0, cloud_low=0.0)
        self.assertGreater(go(W0=50, f=f, lit=False, n=6), go(W0=50, f=f, lit=True, n=6))

    def test_rain_wets(self):
        self.assertGreater(go(f=dict(precip=3.0), n=3), go(f=dict(precip=0.0, dew=2.0), n=3))
        self.assertGreater(go(f=dict(precip=3.0), n=3), 5.0)

    def test_bounded(self):
        self.assertLessEqual(go(fog=1.0, f=dict(precip=20.0), n=500), 100.0)
        self.assertGreaterEqual(go(W0=100, f=dict(dew=-10.0, sw=1000.0), lit=True, n=500), 0.0)

    def test_porous_less_fog(self):
        self.assertLess(go(fog=0.8, n=6, porous=0.6), go(fog=0.8, n=6, porous=1.0))

    def test_dew_needs_sky_and_calm(self):
        f = dict(temp=10.0, dew=8.0, cloud_low=0.0, wind=0.5)
        self.assertGreater(go(f=f, svf=1.0, n=6), go(f=f, svf=0.2, n=6))
        self.assertGreater(go(f=f, svf=1.0, n=6), go(f=dict(f, wind=6.0), svf=1.0, n=6))

    def test_onshore_weight(self):
        self.assertAlmostEqual(float(w.onshore_weight(270)), 1.0)
        self.assertEqual(float(w.onshore_weight(90)), 0.0)
        self.assertEqual(float(w.onshore_weight(150)), 0.0)
        self.assertAlmostEqual(float(w.onshore_weight(225)), np.cos(np.radians(45)), places=6)

    def test_refined_rel(self):
        r = w.refined_rel([np.nan, 0.0, 0.91, 5.0, 0.455])
        np.testing.assert_allclose(r, [1.0, 0.25, 1.0, 1.0, 0.5], atol=1e-6)

    def test_inflow_offshore_zero(self):
        self.assertEqual(float(w.fog_inflow(10, 10, 100, 90)), 0.0)
        self.assertGreater(float(w.fog_inflow(10, 10, 100, 270)), 0.9)


if __name__ == "__main__":
    unittest.main()
