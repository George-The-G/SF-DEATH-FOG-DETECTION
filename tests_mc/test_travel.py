"""Guide 08 Part A: fog travel (predict, inflow, correct, guide sharpening, convergence, determinism). Synthetic data."""
import unittest

import numpy as np

from microclimate.live import travel as T

H, W = 40, 80   # 80 m cells


def blob(cx, cy, r=4.0):
    rr, cc = np.meshgrid(np.arange(H), np.arange(W), indexing="ij")
    return np.exp(-(((cc - cx) ** 2 + (rr - cy) ** 2) / (2 * r * r))).astype(np.float32)


def com(a):
    rr, cc = np.meshgrid(np.arange(H), np.arange(W), indexing="ij")
    s = a.sum()
    return (cc * a).sum() / s, (rr * a).sum() / s


class TestPredict(unittest.TestCase):
    def test_blob_moves_1500m_east(self):
        A = blob(20, 20)
        u, v = 5.0, 0.0                                   # 5 m/s * 300 s = 1500 m = 18.75 cells
        B = T.predict(A, np.zeros((H, W), np.float32), u, v)
        x0, _ = com(A)
        x1, y1 = com(B)
        self.assertLess(abs((x1 - x0) * 80.0 - 1500.0), 40.0)
        self.assertLess(abs(B.sum() / A.sum() - 1.0), 0.02)    # mass conserved away from edges

    def test_zero_wind_nothing_moves(self):
        A = blob(20, 20)
        self.assertTrue(np.allclose(T.predict(A, np.zeros((H, W), np.float32), 0.0, 0.0), A, atol=1e-6))

    def test_north_wind_moves_south_down_the_rows(self):
        A = blob(40, 10)
        B = T.predict(A, np.zeros((H, W), np.float32), 0.0, -5.0)   # v < 0: wind towards the south, rows increase southward
        self.assertGreater(com(B)[1], com(A)[1])

    def test_wind_dir_convention(self):
        u, v = T.wind_uv(5.0, 270.0, factor=1.0)               # from the west: towards the east
        self.assertAlmostEqual(u, 5.0, places=6)
        self.assertAlmostEqual(v, 0.0, places=6)
        u, v = T.wind_uv(5.0, 0.0, factor=1.0)                 # from the north: towards the south
        self.assertAlmostEqual(v, -5.0, places=6)


class TestInflow(unittest.TestCase):
    def test_fog_crosses_the_grid_in_about_an_hour(self):
        W2 = 214                                           # the real width: 17 km
        prior = np.ones((4, W2), np.float32)
        A = np.zeros((4, W2), np.float32)
        steps = 0
        while A[:, -1].mean() < 0.5 and steps < 40:
            A = T.predict(A, prior, 5.0, 0.0)
            steps += 1
        self.assertTrue(10 <= steps <= 13, steps)          # about 57 minutes (11.4 steps); the edge fills with the inflow value


class TestCorrect(unittest.TestCase):
    def test_two_scans_with_full_confidence(self):
        A, obs = np.zeros((5, 5), np.float32), np.ones((5, 5), np.float32)
        for _ in range(2):
            A = T.correct(A, obs, np.ones((5, 5), np.float32))
        self.assertLess(abs(A.mean() - 0.75), 1e-6)        # KMAX 0.5 twice: 1 - 0.5 * 0.5
        self.assertGreater(A.mean(), 0.7)                  # within 25 % of obs

    def test_no_confidence_changes_nothing(self):
        A = np.random.default_rng(0).random((5, 5)).astype(np.float32)
        self.assertTrue(np.array_equal(T.correct(A, np.ones((5, 5), np.float32), np.zeros((5, 5), np.float32)), A))


class TestGuide(unittest.TestCase):
    def test_box_mean_equals_p_and_zero_where_trb_zero(self):
        rng = np.random.default_rng(2)
        trb = np.clip(np.cumsum(np.cumsum(rng.normal(size=(60, 90)), 0), 1) * 0.01 + 0.5, 0.05, 1).astype(np.float32)   # random smooth field
        trb[:10, :10] = 0.0
        p = np.full((60, 90), 0.4, np.float32)
        obs = T.observation(p, trb)
        self.assertTrue(np.all(obs[:10, :10] == 0))
        sl = (slice(15, 45), slice(15, 75))
        self.assertLess(abs(T.box_mean(obs)[sl].mean() / 0.4 - 1.0), 0.15)

    def test_box_mean_of_constant(self):
        self.assertTrue(np.allclose(T.box_mean(np.full((30, 30), 0.7)), 0.7))


class TestCycle(unittest.TestCase):
    def setUp(self):
        self.times = T.window(1_700_000_000.0)
        self.prior = np.full((H, W), 0.3, np.float32)
        self.trb = np.ones((H, W), np.float32)

    def run_(self, scan_fn, A0=None, backward=True):
        return T.run(self.times, lambda t: self.prior, lambda t: self.trb, lambda t: (4.0, 270.0), scan_fn,
                     np.zeros((H, W), np.float32) if A0 is None else A0, backward=backward)

    def test_window_shape(self):
        self.assertEqual(len(self.times), int(8 * 3600 / 300) + 1)
        self.assertTrue(np.all(np.diff(self.times) == 300))

    def test_no_scans_converges_to_prior(self):
        frames, kinds = self.run_(lambda t: None)
        self.assertTrue(np.all(kinds == "forecast"))
        self.assertLess(np.abs(frames[-1] - self.prior).max(), 0.02 + 0.3 * np.exp(-8 * 3600 / T.TAU_PRIOR_S) + 0.02)

    def test_kinds_and_forecast_after_newest(self):
        p = np.full((H, W), 0.9, np.float32)
        newest = self.times[len(self.times) // 2]
        frames, kinds = self.run_(lambda t: (p, np.ones((H, W), np.float32)) if (t <= newest and (t - self.times[0]) % 900 == 0) else None)
        self.assertEqual(kinds[0], "observed")
        self.assertEqual(kinds[-1], "forecast")
        self.assertIn("interpolated", kinds)
        i = int(np.argmax(kinds == "forecast"))
        self.assertTrue(np.all(kinds[i:] == "forecast"))

    def test_scans_pull_towards_obs_and_beat_the_prior(self):
        p = np.full((H, W), 0.9, np.float32)
        frames, _ = self.run_(lambda t: (p, np.ones((H, W), np.float32)))
        self.assertGreater(frames[-1].mean(), 0.7)   # equilibrium of KMAX 0.5 against the relax towards the prior (0.3): about 0.74
        self.assertGreater(frames[-1].mean(), self.prior.mean() + 0.3)

    def test_deterministic(self):
        p = np.random.default_rng(5).random((H, W)).astype(np.float32)
        f1, _ = self.run_(lambda t: (p, np.full((H, W), 0.8, np.float32)))
        f2, _ = self.run_(lambda t: (p, np.full((H, W), 0.8, np.float32)))
        self.assertEqual(f1.tobytes(), f2.tobytes())

    def test_output_range(self):
        p = np.ones((H, W), np.float32)
        frames, _ = self.run_(lambda t: (p, np.ones((H, W), np.float32)))
        self.assertTrue(frames.min() >= 0 and frames.max() <= 1.0)


if __name__ == "__main__":
    unittest.main()


class TestSmoothBlend(unittest.TestCase):
    """Calm the 5-minute series and blend it with the hourly frames (fog jumped forward and back between noisy scans)."""

    def series(self, n=40, seed=3):
        rng = np.random.default_rng(seed)
        base = np.linspace(0.2, 0.6, n)[:, None, None] * np.ones((n, 6, 7), np.float32)
        return np.clip(base + rng.normal(0, 0.15, base.shape), 0, 1).astype(np.float32)

    def test_jitter_drops_and_mean_is_kept(self):
        F = self.series()
        G = T.smooth_blend(F, 30)
        d0, d1 = np.abs(np.diff(F, axis=0)).mean(), np.abs(np.diff(G, axis=0)).mean()
        self.assertLess(d1, 0.5 * d0)
        self.assertAlmostEqual(float(G.mean()), float(F.mean()), delta=0.02)
        self.assertLessEqual(float(G.max()), float(F.max()) + 1e-6)
        self.assertGreaterEqual(float(G.min()), float(F.min()) - 1e-6)

    def test_constant_stays_constant(self):
        F = np.full((30, 4, 4), 0.4, np.float32)
        np.testing.assert_allclose(T.smooth_blend(F, 20), F, atol=1e-6)

    def test_off_and_no_scan_unchanged(self):
        F = self.series()
        np.testing.assert_array_equal(T.smooth_blend(F, 30, sigma=0), F)
        np.testing.assert_array_equal(T.smooth_blend(F, 0), F)

    def test_step_at_newest_scan_is_smoothed(self):
        F = np.concatenate([np.full((20, 3, 3), 0.8, np.float32), np.full((20, 3, 3), 0.2, np.float32)])
        G = T.smooth_blend(F, 19)
        self.assertLess(float(np.abs(np.diff(G, axis=0)).max()), 0.5 * float(np.abs(np.diff(F, axis=0)).max()))

    def test_deterministic_and_shape(self):
        F = self.series()
        a, b = T.smooth_blend(F, 30), T.smooth_blend(F, 30)
        np.testing.assert_array_equal(a, b)
        self.assertEqual(a.shape, F.shape)
        self.assertEqual(a.dtype, np.float32)
