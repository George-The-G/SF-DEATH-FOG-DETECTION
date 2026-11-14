"""Guide 09: fog-front lag speed (switch lag_uniform)."""
import unittest
from datetime import timedelta

import numpy as np

from microclimate import weather, wetness
from tests_mc.test_live_core import fake_data, fake_pts


def lag_of(physics, rel):
    data, start = fake_data()
    ser = weather.series(data, start, start + timedelta(hours=30), 10)
    ser["wind_dir"] = np.full(len(ser["wind_dir"]), 270.0)
    P = fake_pts(4)
    P["x"] = np.full(4, wetness.X_WEST + 2000.0)
    P["y"] = np.full(4, 4175000.0)
    P["rel_wind"] = np.array([0.5, 1.0, 0.5, 1.0], np.float32)
    P["wind_ratio"] = np.ones(4, np.float32)
    _, d = wetness.simulate(P, ser, 10, tracer=P["tracer"], keep_lit=False, physics=physics, fog_mode="profile", fog_top=500.0, refined=True)
    return d["lag_steps"]


class TestLag(unittest.TestCase):
    def test_same_lag_with_switch_different_without(self):
        on = np.asarray(lag_of(("lag_uniform",), None))
        off = np.asarray(lag_of((), None))
        self.assertEqual(on[0], on[1])
        self.assertAlmostEqual(off[0] / off[1], 2.0, 1)
        self.assertLess(on[0], off[0])

    def test_linear_and_capped(self):
        x = np.array([wetness.X_WEST, wetness.X_WEST + 1000.0, wetness.X_WEST + 2000.0, wetness.X_WEST + 1e7])
        lag = wetness.lag_seconds(x, 6.5)
        self.assertEqual(lag[0], 0.0)
        self.assertAlmostEqual(lag[2], 2 * lag[1], 6)
        self.assertEqual(lag[3], wetness.LAG_MAX_H * 3600.0)


if __name__ == "__main__":
    unittest.main()
