"""The observed/forecast boundary (obs_until) must be the last real observation, not a smoothed carry-forward, and never after build time."""
import datetime as dt
import unittest

import numpy as np

from microclimate.live import forcing


class ObsUntil(unittest.TestCase):
    def test_rolling_median_runs_past_last_observation(self):
        a = np.full(30, np.nan)
        a[:24] = 1.0            # real data for hours 0..23
        r = forcing.rolling_median(a, 3)
        self.assertEqual(int(np.nonzero(np.isfinite(r))[0][-1]), 25)    # the +2 h that used to leak into obs_until
        self.assertEqual(forcing.last_finite_index(a), 23)              # the real last hour

    def test_last_finite_index_empty(self):
        self.assertIsNone(forcing.last_finite_index(np.full(5, np.nan)))

    def test_cap_at_build_time(self):
        t0 = dt.datetime(2026, 11, 11, 20, tzinfo=dt.timezone.utc)
        t_h = [t0 + dt.timedelta(hours=k) for k in range(60)]
        now_h = 22.87           # 18:52 UTC
        self.assertEqual(forcing.cap_obs_until(t_h[25], t_h, now_h), t_h[22])      # 21:00 -> 18:00
        self.assertEqual(forcing.cap_obs_until(t_h[20], t_h, now_h), t_h[20])      # earlier stays
        self.assertIsNone(forcing.cap_obs_until(None, t_h, now_h))


if __name__ == "__main__":
    unittest.main()
