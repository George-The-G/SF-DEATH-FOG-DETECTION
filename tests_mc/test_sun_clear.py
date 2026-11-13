"""Guide 06: clear-sky sun, road tilt, cloudiness (switch sun_clear)."""
import datetime as dt
import math
import unittest
from datetime import timedelta

import numpy as np

from microclimate import city, sunclear, weather, wetness
from microclimate.live import config as C
from microclimate.live import forcing, goes, outputs
from tests_mc.test_goes_box import fake_lowcloud
from tests_mc.test_live_core import fake_data, fake_pts

T_NOON = [dt.datetime(2026, 6, 21, 20, tzinfo=dt.timezone.utc), dt.datetime(2026, 11, 12, 20, tzinfo=dt.timezone.utc)]


def sky(i):
    t = [T_NOON[i]]
    ghi, dni, dhi = (a[0] for a in sunclear.clear_sky(t, wetness.SUN_LAT, wetness.SUN_LON))
    az, el = wetness.sun_position(t)
    return ghi, dni, dhi, float(az[0]), float(el[0])


class TestIrradiance(unittest.TestCase):
    def test_flat_open_street_under_clear_sky_equals_ghi(self):
        for i in (0, 1):
            ghi, dni, dhi, az, el = sky(i)
            nrm = sunclear.normals(np.zeros(1), np.zeros(1))
            S = sunclear.street_irradiance(1.0, ghi, dni, sunclear.cos_incidence(nrm, az, el), np.array([True]), np.ones(1), el)
            self.assertAlmostEqual(float(S[0]) / ghi, 1.0, delta=0.02, msg=(i, S, ghi))

    def test_tilt_matches_the_cosine_formula(self):
        az, el, s = 180.0, 30.0, 0.15
        th = math.atan(s)
        south = sunclear.cos_incidence(sunclear.normals(np.array([0.0]), np.array([s])), az, el)[0]    # rises to the north = faces south
        north = sunclear.cos_incidence(sunclear.normals(np.array([0.0]), np.array([-s])), az, el)[0]   # rises to the south = faces north
        flat = sunclear.cos_incidence(sunclear.normals(np.array([0.0]), np.array([0.0])), az, el)[0]
        self.assertAlmostEqual(flat, 0.5, places=6)
        self.assertAlmostEqual(south, math.sin(math.radians(el) + th), delta=0.01 * math.sin(math.radians(el) + th))
        self.assertAlmostEqual(north, math.sin(math.radians(el) - th), delta=0.01 * math.sin(math.radians(el) - th))
        self.assertGreater(south, flat)
        self.assertLess(north, flat)
        # the same hill seen along an east-west street (bearing 90, rising east) faces east: a due-south sun is unchanged to first order
        east = sunclear.cos_incidence(sunclear.normals(np.array([90.0]), np.array([s])), az, el)[0]
        self.assertAlmostEqual(east, math.cos(th) * 0.5, delta=0.01)
        # rise along the bearing that points the other way is the same surface: bearing 180 with slope -s equals bearing 0 with slope s
        self.assertAlmostEqual(sunclear.cos_incidence(sunclear.normals(np.array([180.0]), np.array([-s])), az, el)[0], south, places=6)

    def test_shade_diffuse_only_and_night_zero(self):
        ghi, dni, dhi, az, el = sky(0)
        nrm = sunclear.normals(np.zeros(1), np.zeros(1))
        ci = sunclear.cos_incidence(nrm, az, el)
        shade = sunclear.street_irradiance(1.0, ghi, dni, ci, np.array([False]), np.array([0.4]), el)[0]
        self.assertAlmostEqual(float(shade), 0.4 * (ghi - dni * math.sin(math.radians(el))), delta=0.5)
        self.assertAlmostEqual(float(shade), 0.4 * dhi, delta=0.03 * dhi)
        self.assertEqual(float(sunclear.street_irradiance(1.0, 0.0, 0.0, ci, np.array([True]), np.ones(1), -5.0)[0]), 0.0)

    def test_k_one_is_clear_and_k_low_has_no_beam(self):
        ghi, dni, dhi, az, el = sky(0)
        ci = sunclear.cos_incidence(sunclear.normals(np.zeros(1), np.zeros(1)), az, el)
        lit = np.array([True])
        s1 = sunclear.street_irradiance(1.0, ghi, dni, ci, lit, np.ones(1), el)[0]
        s_shade_1 = sunclear.street_irradiance(1.0, ghi, dni, ci, ~lit, np.ones(1), el)[0]
        self.assertAlmostEqual(float(s1 - s_shade_1), dni * float(ci[0]), delta=0.5)      # the beam is the difference lit - shaded
        s3 = sunclear.street_irradiance(0.3, ghi, dni, ci, lit, np.ones(1), el)[0]
        s3_shade = sunclear.street_irradiance(0.3, ghi, dni, ci, ~lit, np.ones(1), el)[0]
        self.assertEqual(float(s3), float(s3_shade))                                       # no direct beam at k = 0.3
        self.assertAlmostEqual(float(s3), 0.3 * ghi, delta=0.5)
        self.assertAlmostEqual(float(sunclear.street_irradiance(0.35, ghi, dni, ci, lit, np.ones(1), el)[0]), 0.35 * ghi, delta=0.5)
        self.assertLess(float(sunclear.street_irradiance(0.0, ghi, dni, ci, lit, np.ones(1), el)[0]), 0.06 * ghi)   # k floor 0.05

    def test_k_from_sw_holds_below_5_degrees(self):
        el = np.array([-10.0, 2.0, 10.0, 30.0, 20.0, 4.0, -5.0])
        ghi = np.array([0.0, 10.0, 100.0, 500.0, 300.0, 20.0, 0.0])
        sw = np.array([0.0, 5.0, 50.0, 250.0, 300.0, 5.0, 0.0])
        k = sunclear.k_from_sw(sw, ghi, el)
        np.testing.assert_allclose(k, [0.5, 0.5, 0.5, 0.5, 1.0, 1.0, 1.0])               # first valid value before, held after
        self.assertEqual(sunclear.k_from_sw(np.zeros(3), np.zeros(3), np.full(3, -5.0)).tolist(), [1.0, 1.0, 1.0])   # no daylight at all
        k2 = sunclear.k_from_sw(np.stack([sw, sw * 0.5]), ghi[None, :], el[None, :])
        self.assertEqual(k2.shape, (2, 7))
        np.testing.assert_allclose(k2[1], [0.25, 0.25, 0.25, 0.25, 0.5, 0.5, 0.5])


class TestExtraTable(unittest.TestCase):
    def test_slope_sign_follows_the_direction_of_the_segment(self):
        # a segment heading 20 deg (NNE) that rises 10 %: along bearing 20 the slope is +10 %; heading 200 (the other way) rising 10 % toward SSW: -10 % along bearing 20
        np.testing.assert_allclose(city.slope_along_bearing([0.10, 0.10, 0.5, -0.5], [20.0, 200.0, 20.0, 20.0]), [0.10, -0.10, 0.35, -0.35])

    def test_surface_classes(self):
        self.assertEqual([city.surface_class(s) for s in ("", "asphalt", "paved", "concrete", "concrete:plates", "sett", "cobblestone", "gravel", "metal", "wood")], [0, 0, 0, 1, 1, 2, 2, 2, 3, 3])

    def test_load_extra_checks_the_table(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "live_extra.npz"
            x = np.arange(5.0)
            np.savez_compressed(p, bearing=np.arange(5, dtype=np.float16), slope=np.full(5, 0.1, np.float16), surf=np.arange(5, dtype=np.uint8) % 4, n=np.array(5), x_sum=np.array(float(x.sum())))
            e = city.load_extra(p, 5, x)
            self.assertEqual(e["slope"].dtype, np.float32)
            self.assertIsNone(city.load_extra(p, 6, x))
            self.assertIsNone(city.load_extra(p, 5, x + 1.0))
            self.assertIsNone(city.load_extra(Path(d) / "missing.npz", 5, x))


def lattice_stub(L=30):
    class Lat:
        lat = np.linspace(37.70, 37.83, L)
        lon = np.linspace(-122.52, -122.35, L)
    return Lat


class TestCloudiness(unittest.TestCase):
    def setUp(self):
        self.t0 = dt.datetime(2026, 6, 21, 6, tzinfo=dt.timezone.utc)
        self.n = 6 * 30 + 1
        self.t_utc = [self.t0 + timedelta(minutes=10 * i) for i in range(self.n)]
        self.g = np.arange(self.n) / 6.0
        self.t_h = [self.t0 + timedelta(hours=h) for h in range(31)]
        self.ghi, _, _ = sunclear.clear_sky(self.t_utc, wetness.SUN_LAT, wetness.SUN_LON)

    def lc_all(self, acm_class, lat):
        lc = fake_lowcloud(lambda lo, la: (np.zeros(lo.shape, bool), np.zeros(lo.shape, bool), np.ones(lo.shape, bool)), nr=60, nc=70)
        lc.acm = np.full(lc.low.shape, acm_class, np.uint8)
        return lc

    def test_model_only_without_satellite(self):
        SW = np.vstack([0.3 * self.ghi] * 5)
        info = dict(flags=[], physics=["sun_clear"])
        K, out = forcing.cloudiness(lattice_stub(5), SW, self.g, self.t_h, 12.0, self.t_utc, None, info)
        day = self.ghi > 200
        np.testing.assert_allclose(K[:, day], 0.3, atol=1e-6)
        self.assertIn("no GOES cloud mask: sunlight cloudiness from the model only", info["flags"])

    def test_satellite_clear_moves_k_half_way_then_fades(self):
        SW = np.vstack([0.3 * self.ghi] * 5)
        lat = lattice_stub(5)
        lat.lat, lat.lon = np.full(5, 37.76), np.full(5, -122.45)
        goes_hours = {self.t_h[h]: self.lc_all(0, lat) for h in range(0, 13)}      # clear sky seen in hours 0..12 (observed), nothing later
        info = dict(flags=[], physics=["sun_clear"])
        K, out = forcing.cloudiness(lat, SW, self.g, self.t_h, 12.0, self.t_utc, goes_hours, info)
        np.testing.assert_allclose(out["k_sat_hourly"][:, 8], 1.0)
        i = int(np.argmax(self.ghi * (self.g < 12.0)))                              # a daylight step inside the observed hours
        self.assertAlmostEqual(float(K[0, i]), 0.3 + C.K_SAT_WEIGHT * 0.7, places=5)
        corr = lambda k: float(K[0, k] - 0.3)  # noqa: E731
        k_day = [k for k in range(int(12 * 6) + 1, self.n) if self.ghi[k] > 200]
        if k_day:
            k1 = k_day[0]
            self.assertLess(corr(k1), 0.35)
            self.assertGreater(corr(k1), 0.0)
            self.assertLess(corr(k_day[-1]), corr(k1) + 1e-9)                       # fading, never growing
        self.assertTrue(((K >= wetness.K_MIN) & (K <= 1.0)).all())

    def test_cell_mean_of_classes(self):
        lc = self.lc_all(2, None)
        np.testing.assert_allclose(lc.k_cells(np.array([37.76]), np.array([-122.45]), 0.03, 0.03), [0.6])
        lc.acm[:] = 255
        self.assertTrue(np.isnan(lc.k_cells(np.array([37.76]), np.array([-122.45]), 0.03, 0.03)[0]))
        lc.acm[:30] = 0
        lc.acm[30:] = 3
        v = lc.k_cells(np.array([37.76]), np.array([-122.45]), 0.2, 0.2)[0]
        self.assertGreater(v, 0.35)
        self.assertLess(v, 1.0)


class TestSimulate(unittest.TestCase):
    def setUp(self):
        self.data, self.t0 = fake_data()
        self.ser = weather.series(self.data, self.t0, self.t0 + timedelta(hours=30), 10)
        self.pts = fake_pts()
        self.N = len(self.pts["x"])
        self.tab = np.tile(self.pts["tracer"], (9, 1))

    def run_(self, **kw):
        return wetness.simulate(self.pts, self.ser, 10, tracer_dirs=self.tab, out_every=6, lag_block_h=3, keep_lit=False, use_dirs=True, **kw)

    def test_sun_files_and_flag(self):
        W, d = self.run_(physics=("sun_clear",))
        self.assertEqual(d["sun_u8"].shape, W.shape)
        self.assertEqual(d["sun_u8"].dtype, np.uint8)
        self.assertTrue(d["extra_missing"])                       # no slope table in the fake points: flat
        self.assertEqual(int(d["sun_u8"][d["sun_el"][::6][:W.shape[0]] <= 0].max()), 0)    # night frames are dark
        self.assertGreater(int(d["sun_u8"].max()), 5)
        self.assertLessEqual(int(d["sun_u8"].max()), 250)
        W0, d0 = self.run_()
        self.assertNotIn("sun_u8", d0)
        self.assertFalse(np.array_equal(W, W0))

    def test_slope_changes_the_sunlight(self):
        pts = dict(self.pts, bearing=np.zeros(self.N, np.float32), slope=np.where(np.arange(self.N) % 2 == 0, 0.2, -0.2).astype(np.float32))
        pts["horizon"] = np.zeros_like(pts["horizon"])            # all streets in the open
        self.pts = pts
        _, d = self.run_(physics=("sun_clear",))
        self.assertFalse(d["extra_missing"])
        k = int(np.argmax(d["sun_u8"].astype(int).sum(axis=1)))
        south, north = d["sun_u8"][k, 0::2].astype(int), d["sun_u8"][k, 1::2].astype(int)
        self.assertGreater(south.mean(), north.mean())            # rising north = faces south: more sun at noon

    def test_forcing_k_is_used(self):
        fn = lambda i, f: {**f, "k": np.full(self.N, 0.2, np.float32)}  # noqa: E731
        pts = dict(self.pts, horizon=np.zeros_like(self.pts["horizon"]))
        self.pts = pts
        _, d = self.run_(physics=("sun_clear",), forcing_fn=fn)
        np.testing.assert_allclose(d["sun_k"], 0.2, atol=1e-6)
        _, dc = self.run_(physics=("sun_clear",), forcing_fn=lambda i, f: {**f, "k": np.full(self.N, 1.0, np.float32)})
        self.assertGreater(dc["sun_u8"].max(), d["sun_u8"].max())


class TestSunFiles(unittest.TestCase):
    def test_ribbon_columns_and_index_fields(self):
        import tempfile
        from pathlib import Path
        Rb = dict(A_src=np.array([0, 1]), B_srcL=np.array([2, 3, 4]), B_srcR=np.array([3, 4, 5]))
        sun = np.arange(12, dtype=np.uint8).reshape(2, 6) * 3
        diag = dict(sun_u8=sun, sun_el=np.array([10.0, 20.0, 30.0]), sun_az=np.array([100.0, 150.0, 200.0]), sun_k=np.array([0.5, 0.6], np.float32))
        cols = outputs.ribbon_cols(sun, Rb, "mean")
        self.assertEqual(cols.shape, (2, 5))
        np.testing.assert_array_equal(cols[0], [0, 3, round((6 + 9) / 2), round((9 + 12) / 2), round((12 + 15) / 2)])
        with tempfile.TemporaryDirectory() as d:
            fields, names = outputs.write_extras(Path(d), diag, Rb, [[0, 1], [1, 2]], np.array([0, 1]), None)
            self.assertEqual(names, ["sun0.u8", "sun1.u8"])
            self.assertEqual((Path(d) / "sun0.u8").stat().st_size, 5)
            self.assertEqual(fields["sun"]["files"], names)
            self.assertEqual(fields["sun"]["sun_el"], [10.0, 20.0])
            self.assertNotIn("film", fields)
            self.assertEqual(outputs.write_extras(Path(d), dict(sun_el=[], sun_az=[]), Rb, [[0, 1]], np.array([0]), None), ({}, []))   # switches off: nothing is written


if __name__ == "__main__":
    unittest.main()
