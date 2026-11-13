"""Guide 14: east wind directions behind the WIND_EAST switch. 16-direction blend weights (157.5 gap, north wrap), the carried direction range of the
anchors, live_east.npz applied to a static table (fog tracer rows zero), the switch in the build settings. No GPU, no data files."""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from microclimate import dirlib, wetness
from microclimate.live import anchors, build, config as C, east

WEST = [180.0, 202.5, 225.0, 247.5, 270.0, 292.5, 315.0, 337.5, 360.0]
EAST = [22.5, 45.0, 67.5, 90.0, 112.5, 135.0]
D15 = np.array(EAST + WEST)          # 157.5 is the accepted gap


def w_at(angle, dirs=D15):
    return dirlib.weights([angle], dirs)[0]


class TestWeights(unittest.TestCase):
    def pairs(self, angle):
        w = w_at(angle)
        return {float(D15[i]): float(w[i]) for i in np.nonzero(w)[0]}

    def test_every_angle_sums_to_one_and_only_neighbours(self):
        for a in np.arange(0.0, 360.0, 2.5):
            w = w_at(a)
            self.assertAlmostEqual(w.sum(), 1.0)
            self.assertLessEqual(np.count_nonzero(w), 2)
            self.assertTrue((w >= 0).all())

    def test_0_is_360(self):
        self.assertEqual(self.pairs(0.0), {360.0: 1.0})
        self.assertEqual(self.pairs(360.0), {360.0: 1.0})

    def test_10_wraps_north_between_360_and_22_5(self):
        p = self.pairs(10.0)
        self.assertEqual(sorted(p), [22.5, 360.0])
        self.assertAlmostEqual(p[22.5], 10.0 / 22.5)
        self.assertAlmostEqual(p[360.0], 12.5 / 22.5)

    def test_100_between_90_and_112_5(self):
        p = self.pairs(100.0)
        self.assertAlmostEqual(p[112.5], 10.0 / 22.5)
        self.assertAlmostEqual(p[90.0], 12.5 / 22.5)

    def test_gap_150_157_5_170_blend_135_and_180_linearly_in_angle(self):
        for a, t in ((150.0, 15.0 / 45.0), (157.5, 0.5), (170.0, 35.0 / 45.0)):
            p = self.pairs(a)
            self.assertEqual(sorted(p), [135.0, 180.0])
            self.assertAlmostEqual(p[180.0], t)
            self.assertAlmostEqual(p[135.0], 1.0 - t)

    def test_200_350_359(self):
        p = self.pairs(200.0)
        self.assertEqual(sorted(p), [180.0, 202.5])
        self.assertAlmostEqual(p[202.5], 20.0 / 22.5)
        p = self.pairs(350.0)
        self.assertEqual(sorted(p), [337.5, 360.0])
        self.assertAlmostEqual(p[360.0], 12.5 / 22.5)
        p = self.pairs(359.0)
        self.assertAlmostEqual(p[360.0], 21.5 / 22.5)

    def test_exact_directions_are_one_hot(self):
        for k, d in enumerate(D15):
            w = w_at(d)
            self.assertEqual(w[k], 1.0)

    def test_west_only_set_is_unchanged(self):
        """No wrap, no east blending for the 9 west directions: 10 and 150 degrees stay on the nearest end field (offshore, zero fog weight anyway)."""
        dirs = np.array(WEST)
        self.assertEqual(dirlib.weights([10.0], dirs)[0].argmax(), 0)       # nearest end (180)
        self.assertEqual(dirlib.weights([150.0], dirs)[0, 0], 1.0)
        self.assertEqual(dirlib.weights([0.0], dirs)[0, -1], 1.0)
        self.assertAlmostEqual(dirlib.weights([350.0], dirs)[0, -1], 12.5 / 22.5)

    def test_gap_deg(self):
        self.assertEqual(dirlib.gap_deg(157.5, D15), 22.5)
        self.assertEqual(dirlib.gap_deg(10.0, D15), 10.0)       # 360 is 10 degrees away across north


class TestAvailable(unittest.TestCase):
    def test_east_manifest_only_with_east_true(self):
        with tempfile.TemporaryDirectory() as t:
            lib = Path(t)
            (lib / "wind_dirs.json").write_text('{"validated": [180.0, 360.0]}')
            (lib / "wind_dirs_east.json").write_text('{"validated": [22.5, 135.0, 157.5]}')
            for d in (22.5, 135.0, 157.5, 180.0, 270.0, 360.0):
                s = dirlib._stem(d)
                (lib / f"wind_{s}.npz").write_bytes(b"x")
                for b in range(4):
                    if d != 157.5:                      # 157.5: wind file but no fog files = not available
                        (lib / f"fog_{s}_{b}.npz").write_bytes(b"x")
            self.assertEqual(dirlib.available(lib), [180.0, 270.0, 360.0])
            self.assertEqual(dirlib.available(lib, east=True), [22.5, 135.0, 180.0, 270.0, 360.0])


class TestAnchorRange(unittest.TestCase):
    def setUp(self):
        self.e0 = C.WIND_EAST

    def tearDown(self):
        C.WIND_EAST = self.e0

    def test_carried_range_off_is_unchanged(self):
        C.WIND_EAST = False
        self.assertEqual(anchors.carry_range(), (90.0, 450.0))
        np.testing.assert_allclose(anchors.wrap_dir([10.0, 89.0, 90.0, 150.0, 359.0]), [370.0, 449.0, 90.0, 150.0, 359.0])
        np.testing.assert_allclose(anchors.eff_dir([100.0, 270.0, 400.0]), [180.0, 270.0, 360.0])

    def test_carried_range_on(self):
        C.WIND_EAST = True
        self.assertEqual(anchors.carry_range(), (180.0, 540.0))
        np.testing.assert_allclose(anchors.wrap_dir([10.0, 150.0, 179.0, 180.0, 359.0]), [370.0, 510.0, 539.0, 180.0, 359.0])
        np.testing.assert_allclose(anchors.eff_dir([100.0, 270.0, 400.0, 600.0]), [180.0, 270.0, 400.0, 540.0])
        w = anchors.dir_weights(np.array([370.0, 510.0, 540.0, 180.0]), D15)
        np.testing.assert_allclose(w.sum(axis=-1), 1.0)
        self.assertAlmostEqual(w[0, 0], 10.0 / 22.5)       # 370 = 10 degrees: 22.5 and 360
        self.assertAlmostEqual(w[1, list(D15).index(180.0)], 15.0 / 45.0)     # 510 = 150
        self.assertEqual(w[2, list(D15).index(180.0)], 1.0)

    def test_fit_recovers_an_east_wind_with_the_east_library(self):
        lib = np.zeros((len(D15), 4, 2))
        for k, d in enumerate(D15):
            lib[k, :, 0], lib[k, :, 1] = anchors.to_uv(1.0, d)
        W = np.ones((1, 4))
        u, v = anchors.to_uv(6.0, 100.0)
        obs = np.tile([u, v], (4, 1))
        C.WIND_EAST = True
        o = anchors.fit_hour(obs, W, lib, D15, np.array([5.0]), anchors.wrap_dir(np.array([105.0])), [])
        self.assertLess(abs(o["th"][0] - 460.0), 4.0)
        self.assertAlmostEqual(o["U"][0], 6.0, delta=0.3)
        self.assertTrue(o["offshore"])                      # east wind stays flagged offshore (no advected fog)

    def test_fit_in_the_157_5_gap_is_sane(self):
        lib = np.zeros((len(D15), 4, 2))
        for k, d in enumerate(D15):
            lib[k, :, 0], lib[k, :, 1] = anchors.to_uv(1.0, d)
        W = np.ones((1, 4))
        u, v = anchors.to_uv(6.0, 157.5)
        C.WIND_EAST = True
        o = anchors.fit_hour(np.tile([u, v], (4, 1)), W, lib, D15, np.array([6.0]), anchors.wrap_dir(np.array([157.5])), [])
        self.assertLess(abs(o["th"][0] - 517.5), 6.0)
        self.assertLess(o["U"][0], 6.0 / np.cos(np.radians(22.5)) + 0.4)   # the linear blend of the two neighbours loses 8 % of the speed at mid-gap: the fit may raise U by that


def static_table(n=7):
    r = np.random.default_rng(3)
    return dict(x=np.arange(n, dtype=float), dirs=np.array(WEST, np.float32), tracer=r.random((9, n)).astype(np.float32) + 0.1,
                tr80=r.random((9, 4, 5)).astype(np.float32) + 0.1, svf=np.ones(n, np.float32))


def write_east(path, static_dirs=WEST, dirs=D15):
    uv = np.zeros((len(dirs), 3, 2), np.float32)
    np.savez_compressed(path, ids=np.array(["A", "B", "C"]), dirs=np.array(dirs, np.float32), uv=uv, z_used=np.full(3, 50.0, np.float32), x=np.zeros(3), y=np.zeros(3),
                        layer_floor=np.full(3, 50.0, np.float32), static_dirs=np.array(static_dirs, np.float32), east_dirs=np.array(EAST, np.float32))


class TestApply(unittest.TestCase):
    def test_east_rows_are_zero_and_west_rows_unchanged(self):
        S = static_table()
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "live_east.npz"
            write_east(p)
            S2, info = east.apply(S, p)
        self.assertTrue(info["applied"])
        self.assertEqual(S2["dirs"].tolist(), D15.tolist())
        self.assertEqual(S2["tracer"].shape, (15, 7))
        self.assertEqual(S2["tr80"].shape, (15, 4, 5))
        self.assertEqual(float(np.abs(S2["tracer"][:6]).max()), 0.0)        # the east fields add no fog
        self.assertEqual(float(np.abs(S2["tr80"][:6]).max()), 0.0)
        np.testing.assert_array_equal(S2["tracer"][6:], S["tracer"])
        np.testing.assert_array_equal(S2["tr80"][6:], S["tr80"])
        self.assertEqual(S["tracer"].shape, (9, 7))                          # the input table is untouched
        self.assertEqual(len(S["dirs"]), 9)
        self.assertIs(S2["svf"], S["svf"])

    def test_no_fog_weight_at_east_wind(self):
        """onshore_weight is zero for wind from 0..180: no advected fog, whatever the east tracer rows are."""
        d = np.arange(0.0, 180.0, 1.0)
        self.assertEqual(float(wetness.onshore_weight(d).max()), 0.0)
        S = static_table()
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "live_east.npz"
            write_east(p)
            S2, _ = east.apply(S, p)
        for a in (10.0, 100.0, 150.0, 170.0):                                 # blends that only involve east rows or the 135/180 pair carry no fog
            self.assertEqual(float(wetness.onshore_weight(np.array([a]))[0]), 0.0)
        w = dirlib.weights([100.0], S2["dirs"])[0]
        self.assertEqual(float((w @ S2["tracer"]).max()), 0.0)               # 90 / 112.5 blend of zero rows

    def test_mismatch_missing_and_broken_files_are_not_applied(self):
        S = static_table()
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "live_east.npz"
            write_east(p, static_dirs=WEST[:-1])
            S2, info = east.apply(S, p)
            self.assertIsNone(S2)
            self.assertFalse(info["applied"])
            self.assertIn("static directions", info["reason"])
            S2, info = east.apply(S, Path(t) / "missing.npz")
            self.assertIsNone(S2)
            (Path(t) / "bad.npz").write_bytes(b"not a zip")
            self.assertIsNone(east.apply(S, Path(t) / "bad.npz")[0])


class TestSwitch(unittest.TestCase):
    def setUp(self):
        self.saved = (dict(C.BETA), C.WIND_EAST)

    def tearDown(self):
        C.BETA.clear()
        C.BETA.update(self.saved[0])
        C.WIND_EAST = self.saved[1]

    def test_off_for_public_on_for_beta(self):
        self.assertFalse(C.WIND_EAST)
        self.assertTrue(C.BETA["wind_east"])
        self.assertFalse(build.settings(False)["wind_east"])
        self.assertTrue(build.settings(True)["wind_east"])
        C.BETA["wind_east"] = False
        self.assertFalse(build.settings(True)["wind_east"])
        C.WIND_EAST = True
        self.assertTrue(build.settings(False)["wind_east"])
        self.assertFalse(build.settings(True)["wind_east"])                 # the two never mix

    def test_label_names_the_switch(self):
        s = build.settings(True)
        self.assertIn("east wind", build.beta_label(s))
        s["wind_east"] = False
        self.assertNotIn("east wind", build.beta_label(s))


if __name__ == "__main__":
    unittest.main()
