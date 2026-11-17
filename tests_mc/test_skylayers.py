"""Guide 13: sky layer table (microclimate/live/skylayers.py)."""
import unittest

from microclimate.live import skylayers as S


class TestRuns(unittest.TestCase):
    def test_edges_interpolated(self):
        runs = S.humidity_runs([0, 500, 1000, 1500, 2000], [50, 70, 100, 100, 60], thr=90)
        self.assertEqual(len(runs), 1)
        b, t = runs[0]
        self.assertAlmostEqual(b, 500 + (90 - 70) / 30 * 500, places=3)
        self.assertAlmostEqual(t, 1500 + (90 - 100) / (60 - 100) * 500, places=3)

    def test_two_layers_and_thin_dropped(self):
        runs = S.humidity_runs([0, 400, 800, 1200, 3000, 3100, 3300], [40, 95, 40, 40, 99, 99, 40], thr=90)
        self.assertEqual(len(runs), 2)
        thin = S.humidity_runs([0, 100, 200], [40, 95, 40], thr=90)
        self.assertEqual(thin, [])

    def test_floor_drops_fog_layer(self):
        runs = S.humidity_runs([0, 100, 600, 3000], [99, 99, 40, 40], thr=90, floor_m=200)
        self.assertEqual(runs, [])

    def test_unsorted_and_missing(self):
        runs = S.humidity_runs([2000, None, 500, 1000], [99, 99, float("nan"), 99], thr=90)
        self.assertEqual(len(runs), 1)


class TestMetar(unittest.TestCase):
    def test_conversion(self):
        L = S.metar_layers([{"cover": "OVC", "base": 3000}, {"cover": "FEW", "base": 1000}, {"cover": "CLR"}], elev_m=10)
        self.assertEqual([round(l["cov"], 2) for l in L], [0.19, 1.0])
        self.assertAlmostEqual(L[1]["base"], 3000 * 0.3048 + 10)


class TestTable(unittest.TestCase):
    def test_airport_nudge_is_partial(self):
        runs = [(1000.0, 1400.0)]
        t = S.build_table(runs, [[dict(base=1200.0, cov=0.75)]])
        self.assertAlmostEqual(t[0]["base"], 1100.0)
        self.assertEqual(t[0]["src"], "model+metar")
        self.assertAlmostEqual(t[0]["cov"], 0.75)

    def test_far_airport_layer_added_only_if_scattered(self):
        t = S.build_table([(1000.0, 1400.0)], [[dict(base=4000.0, cov=0.19)]])
        self.assertEqual(len(t), 1)
        t = S.build_table([(1000.0, 1400.0)], [[dict(base=4000.0, cov=0.75)]])
        self.assertEqual([l["src"] for l in t], ["model", "metar"])

    def test_satellite_moves_top_only_when_confident(self):
        r = [(1000.0, 1400.0)]
        self.assertEqual(S.build_table(r, None, sat_top=2000, sat_conf=0.2)[0]["top"], 1400.0)
        self.assertAlmostEqual(S.build_table(r, None, sat_top=2000, sat_conf=0.9)[0]["top"], 1400 + 0.6 * 600)

    def test_max_three(self):
        runs = [(1000.0 + 500 * i, 1100.0 + 500 * i) for i in range(5)]
        self.assertEqual(len(S.build_table(runs)), 3)

    def test_never_below_floor(self):
        t = S.build_table([(50.0, 900.0)], floor_m=300)
        self.assertGreaterEqual(t[0]["base"], 300)


class TestThinDeck(unittest.TestCase):
    def test_deck(self):
        d = S.thin_deck(700, 0.9, 0.8, floor_m=300)
        self.assertEqual((d["base"], d["top"], d["src"]), (550, 700, "sat"))

    def test_floor_and_ground(self):
        d = S.thin_deck(400, 0.9, 0.8, floor_m=350)
        self.assertEqual(d["base"], 350)
        self.assertGreater(d["base"], 0)

    def test_rejected(self):
        self.assertIsNone(S.thin_deck(700, 0.3, 0.8, 300))      # not sure it is cloud
        self.assertIsNone(S.thin_deck(700, 0.9, 0.2, 300))      # low confidence
        self.assertIsNone(S.thin_deck(8000, 0.9, 0.9, 300))     # cirrus
        self.assertIsNone(S.thin_deck(None, 0.9, 0.9, 300))


class TestHeightRule(unittest.TestCase):
    def test_order(self):
        self.assertEqual(S.cloud_height_m(800, 400), 800)
        self.assertEqual(S.cloud_height_m(None, 400), 400)
        self.assertEqual(S.cloud_height_m(float("nan"), None), 500)
        self.assertEqual(S.cloud_height_m(0, 0), 500)


if __name__ == "__main__":
    unittest.main()
