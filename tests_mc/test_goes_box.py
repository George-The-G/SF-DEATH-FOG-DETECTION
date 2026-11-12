"""LowCloud.box_fraction and the inverse GOES navigation, on a synthetic scan (no files, no network)."""
import datetime as dt
import unittest

import numpy as np

from microclimate.live import goes

BOX = (-122.7, 37.6, -122.3, 37.95)


class _Proj:
    attrs = {"semi_major_axis": [6378137.0], "semi_minor_axis": [6356752.31414], "perspective_point_height": [35786023.0],
             "longitude_of_projection_origin": [-137.0]}


def fake_lowcloud(rule, nr=35, nc=40):
    """A LowCloud on a regular scan-angle grid over BOX; rule(lon, lat) -> (low, cloudy, valid) boolean arrays."""
    nav = goes.Nav(_Proj())
    ax, ay = nav.xy(np.array([BOX[0], BOX[2]]), np.array([BOX[1], BOX[3]]))
    x = np.linspace(ax.min(), ax.max(), nc)
    y = np.linspace(ay.max(), ay.min(), nr)            # rows run north to south
    lc = goes.LowCloud.__new__(goes.LowCloud)
    lc.nav, lc.x, lc.y = nav, x, y
    lon, lat = nav.lonlat(*np.meshgrid(x, y))
    low, cloudy, valid = rule(lon, lat)
    lc.low, lc.cloudy, lc.valid = low.astype(np.float32), cloudy.astype(np.float32), valid.astype(bool)
    lc.t = dt.datetime(2026, 11, 12, 12, tzinfo=dt.timezone.utc)
    return lc


class TestNav(unittest.TestCase):
    def test_lonlat_inverts_xy(self):
        nav = goes.Nav(_Proj())
        lon, lat = np.array([-122.5, -122.4, -123.0, -121.9]), np.array([37.7, 37.9, 37.2, 38.3])
        x, y = nav.xy(lon, lat)
        lo, la = nav.lonlat(x, y)
        np.testing.assert_allclose(lo, lon, atol=1e-6)
        np.testing.assert_allclose(la, lat, atol=1e-6)


class TestBoxFraction(unittest.TestCase):
    def test_all_low_all_clear(self):
        east = fake_lowcloud(lambda lo, la: (lo > -122.45, lo > -122.45, np.ones(lo.shape, bool)))
        self.assertEqual(east.box_fraction(-122.40, 37.7, -122.34, 37.8), (1.0, 0.0, 1.0))
        self.assertEqual(east.box_fraction(-122.65, 37.7, -122.55, 37.8), (0.0, 0.0, 1.0))

    def test_straddling_box_matches_a_direct_count(self):
        lc = fake_lowcloud(lambda lo, la: (lo > -122.45, lo > -122.45, np.ones(lo.shape, bool)))
        lon, lat = lc.pixel_lonlat()
        box = (-122.50, 37.70, -122.40, 37.80)
        m = (lon >= box[0]) & (lon <= box[2]) & (lat >= box[1]) & (lat <= box[3])
        want = float((lon[m] > -122.45).mean())
        low, high, valid = lc.box_fraction(*box)
        self.assertAlmostEqual(low, want)
        self.assertGreater(low, 0.2)
        self.assertLess(low, 0.8)
        self.assertEqual((high, valid), (0.0, 1.0))

    def test_high_cloud_is_cloudy_but_not_low(self):
        lc = fake_lowcloud(lambda lo, la: (np.zeros(lo.shape, bool), la < 37.75, np.ones(lo.shape, bool)))
        low, high, valid = lc.box_fraction(-122.6, 37.62, -122.5, 37.70)
        self.assertEqual((low, high, valid), (0.0, 1.0, 1.0))
        low, high, valid = lc.box_fraction(-122.6, 37.80, -122.5, 37.90)
        self.assertEqual((low, high, valid), (0.0, 0.0, 1.0))

    def test_invalid_pixels_reduce_valid_and_are_not_counted_as_clear(self):
        lc = fake_lowcloud(lambda lo, la: (la >= 37.8, la >= 37.8, la >= 37.7))     # no mask south of 37.7; cloud north of 37.8
        low, high, valid = lc.box_fraction(-122.6, 37.62, -122.5, 37.68)
        self.assertTrue(np.isnan(low) and np.isnan(high))
        self.assertEqual(valid, 0.0)
        low, high, valid = lc.box_fraction(-122.6, 37.66, -122.5, 37.76)             # half masked
        self.assertGreater(valid, 0.2)
        self.assertLess(valid, 0.8)
        self.assertEqual(low, 0.0)

    def test_empty_box(self):
        lc = fake_lowcloud(lambda lo, la: (lo > 0, lo > 0, np.ones(lo.shape, bool)))
        low, high, valid = lc.box_fraction(-100.0, 10.0, -99.0, 11.0)
        self.assertTrue(np.isnan(low) and np.isnan(high))
        self.assertEqual(valid, 0.0)

    def test_reads_only(self):
        lc = fake_lowcloud(lambda lo, la: (lo > -122.45, lo > -122.4, np.ones(lo.shape, bool)))
        before = [a.copy() for a in (lc.low, lc.cloudy, lc.valid, lc.x, lc.y)]
        lc.box_fraction(-122.5, 37.7, -122.4, 37.8)
        for b, a in zip(before, (lc.low, lc.cloudy, lc.valid, lc.x, lc.y)):
            np.testing.assert_array_equal(a, b)


if __name__ == "__main__":
    unittest.main()
