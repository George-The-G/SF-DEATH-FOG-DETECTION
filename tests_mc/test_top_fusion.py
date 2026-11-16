"""Fog top fusion (guide 06, switch TOP_FUSION): Kalman filter + RTS smoother on synthetic series, and the forcing integration. No network."""
import unittest
from datetime import timedelta

import numpy as np

from microclimate.live import config as C, forcing, topfusion
from tests_mc import anchors_fixture as A

OFF_HASH = "1b5d0aa0d83cd7bb0df0e5f5e7f5475a8d6c092f430c0854a4a6b3021364a52a"   # fog_top of the saved-inputs fixture on origin/v3-dev da494de


def nanv(n):
    return np.full(n, np.nan)


def src(n, val=None, sig=None, at=None):
    z, s = nanv(n), nanv(n)
    for k in (range(n) if at is None else at):
        z[k] = val[k] if hasattr(val, "__len__") else val
        s[k] = sig
    return z, s


class Fuse(unittest.TestCase):
    def test_one_source_equals_it(self):
        n = 30
        top, sd, info = topfusion.fuse({"model": src(n, 400.0, 120.0)})
        self.assertTrue(np.allclose(top[3:], 400.0, atol=1.0))
        self.assertTrue(np.all(sd > 0) and np.all(sd < 150))
        self.assertEqual(info["model"]["rejected"], 0)

    def test_equal_sigma_gives_mean(self):
        obs = {"model": src(1, 300.0, 100.0), "ceiling": src(1, 500.0, 100.0)}
        top, _, _ = topfusion.fuse(obs, p0=1e6)   # flat prior, one hour: precision-weighted mean of the two
        self.assertAlmostEqual(float(top[0]), 400.0, delta=1.0)

    def test_unequal_sigma_follows_precise(self):
        n = 1
        obs = {"model": src(n, 300.0, 250.0), "sounding": src(n, 700.0, 50.0)}
        top, _, _ = topfusion.fuse(obs, p0=1e3)   # flat prior: weights 1/250^2 and 1/50^2
        frac = (top[0] - 300.0) / 400.0
        self.assertGreater(frac, 0.95)   # within 4 % of the way to the precise source

    def test_sounding_pulls_earlier_hours_less_further_away(self):
        n = 16
        model = (np.full(n, 300.0), np.full(n, 120.0))
        obs = {"model": model, "sounding": src(n, 480.0, 50.0, at=[12])}
        top, _, info = topfusion.fuse(obs)
        self.assertEqual(info["sounding"]["used"], 1)
        pull = top - topfusion.fuse({"model": model})[0]
        self.assertTrue(pull[12] > pull[11] > pull[10] > pull[9] > 0, pull[8:13])

    def test_hours_after_last_nonmodel_observation_keep_forward_result(self):
        n = 16
        model = (np.array([300.0 + 10 * k for k in range(n)]), np.full(n, 120.0))
        obs = {"model": model, "ceiling": src(n, 400.0, 120.0, at=[5])}
        top, _, _ = topfusion.fuse(obs)
        fwd, _, _ = topfusion.fuse({"model": model, "ceiling": src(n, 400.0, 120.0, at=[5])}, q=C.TF_Q_M)
        self.assertTrue(np.allclose(top[6:], fwd[6:]))   # no backward pass beyond hour 5
        self.assertTrue(np.isfinite(top).all())

    def test_outlier_rejected_and_counted(self):
        n = 24
        z = np.full(n, 300.0)
        z[12] = 2000.0
        top, _, info = topfusion.fuse({"model": (z, np.full(n, 120.0))})
        self.assertEqual(info["model"]["rejected"], 1)
        self.assertLess(abs(top[12] - 300.0), 5.0)

    def test_lower_bound(self):
        n = 12
        lower = nanv(n)
        lower[[3, 4, 9]] = 700.0
        top, _, _ = topfusion.fuse({"model": src(n, 300.0, 120.0)}, lower=lower)
        self.assertTrue(np.all(top[[3, 4, 9]] >= 700.0))

    def test_no_nan_with_gaps(self):
        n = 24
        z, s = src(n, 350.0, 120.0, at=[0, 1, 10, 11, 20])
        obs = {"model": (z, s), "ceiling": src(n, 450.0, 120.0, at=[5])}
        top, sd, _ = topfusion.fuse(obs)
        self.assertTrue(np.isfinite(top).all() and np.isfinite(sd).all())
        top2, sd2, _ = topfusion.fuse({"model": (nanv(n), nanv(n))})   # nothing at all: the start value (the guide's 300 m) carries
        self.assertTrue(np.isfinite(top2).all())

    def test_sd_grows_without_data(self):
        n = 24
        z, s = src(n, 350.0, 120.0, at=[0, 1, 2])
        _, sd, _ = topfusion.fuse({"model": (z, s)})
        self.assertTrue(sd[20] > sd[2])


def snd_obs(valid, base_z=400.0):
    z = np.arange(20.0, 1500.0, 40.0)
    t = np.where(z < base_z, 14.0 - 0.006 * z, 14.0 - 0.006 * base_z + 0.012 * (z - base_z))
    return dict(z=z, t=t, valid=valid)


class Integration(unittest.TestCase):
    def setUp(self):
        self.tf0 = C.TOP_FUSION

    def tearDown(self):
        C.TOP_FUSION = self.tf0

    def test_switch_off_is_byte_identical(self):
        import hashlib
        C.TOP_FUSION = False
        F, _, _ = A.build()
        self.assertEqual(hashlib.sha256(np.asarray(F["fog_top"], float).tobytes()).hexdigest(), OFF_HASH)
        self.assertNotIn("fog_top_sigma", F)
        self.assertNotIn("top_fusion", F["info"])

    def test_on_gives_series_sigma_lower_bound(self):
        obs = A.fake_obs()
        for st in ("KHAF", "KSFO"):
            for r in obs["metar"][st]:
                if 10 <= (r["t"] - A.T0).total_seconds() / 3600 <= 14:
                    r["ceil_ft"] = 800.0
        obs["sounding"] = snd_obs(A.T0 + timedelta(hours=12))
        C.TOP_FUSION = True
        F, _, _ = A.build(obs=obs)
        top, sd = np.asarray(F["fog_top"], float), np.asarray(F["fog_top_sigma"], float)
        self.assertTrue(np.isfinite(top).all() and np.isfinite(sd).all())
        self.assertEqual(len(top), A.N_STEPS)
        ti = F["info"]["top_fusion"]
        self.assertGreater(ti["sounding"]["used"], 0)
        self.assertGreater(ti["ceiling"]["used"], 0)
        self.assertEqual(F["info"]["fog_top_source"], "fusion")
        elev = F["ser"]["elevation_m"]
        for k in range(10, 15):   # ceiling lower bound (ceiling ASL + 30 m) at hours with a ceiling
            i = k * 6
            self.assertGreaterEqual(top[i], 800.0 * 0.3048 + C.METAR_ELEV_M["KHAF"] + C.TF_CEIL_LOWER_M - 1.0)
        self.assertTrue((top >= elev + 50.0 - 1e-6).all())
        self.assertTrue(np.all(sd > 0))


if __name__ == "__main__":
    unittest.main()
