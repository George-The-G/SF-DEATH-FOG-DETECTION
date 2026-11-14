"""Guide 10: vapour-deficit evaporation (vpd_evap) and total cloud in the cooling (total_cloud), old step() path."""
import unittest

import numpy as np

from microclimate import wetness as w


def one(temp, rh, physics=(), **kw):
    td = temp - 0.0
    # dew point from RH (Magnus)
    a = 17.625 * temp / (243.04 + temp) + np.log(rh)
    td = 243.04 * a / (17.625 - a)
    f = dict(temp=temp, dew=td, wind=2.0, cloud_low=0.0, precip=0.0, sw=0.0, **kw)
    N = np.ones(3, np.float32)
    _, p = w.step(np.full(3, 50.0, np.float32), N, N, f, np.zeros(3), np.zeros(3, bool), 1.0, physics=frozenset(physics))
    return float(np.ravel(p["evap"])[0])


class TestVpd(unittest.TestCase):
    def test_ratios(self):
        r = lambda t: one(t, 0.7, ("vpd_evap",)) / one(t, 0.7)   # noqa: E731
        self.assertAlmostEqual(r(12.0), 1.0, delta=0.01)
        self.assertTrue(2.2 <= r(25.0) <= 2.3, r(25.0))
        self.assertTrue(0.60 <= r(5.0) <= 0.65, r(5.0))
        self.assertEqual(one(12.0, 1.0, ("vpd_evap",)), 0.0)


class TestCloud(unittest.TestCase):
    def dew(self, low, total, physics):
        N = np.ones(3, np.float32)
        f = dict(temp=10.0, dew=9.5, wind=0.0, cloud_low=low, precip=0.0, sw=0.0)
        if total is not None:
            f["cloud"] = total
        _, p = w.step(np.zeros(3, np.float32), N, N, f, np.zeros(3), np.zeros(3, bool), 1.0, physics=frozenset(physics))
        return float(np.ravel(p["dew"])[0])

    def test_cooling(self):
        sw = ("total_cloud",)
        ref = self.dew(0, 0, ())
        half = self.dew(0, 100, sw)       # effective cloud 50 %: half the clear-sky cooling
        none = self.dew(100, 100, sw)
        self.assertLess(half, ref)
        self.assertEqual(none, self.dew(100, None, ()))
        self.assertEqual(self.dew(0, None, sw), ref)        # total missing: old formula
        self.assertEqual(self.dew(0, 100, ()), ref)         # switch off ignores total cloud


if __name__ == "__main__":
    unittest.main()
