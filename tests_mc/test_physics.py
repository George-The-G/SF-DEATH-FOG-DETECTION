"""Guide 01: physics switches and the compare report. The hash below was recorded from the model BEFORE the switches existed:
with no switch the live model must stay bit-identical."""
import hashlib
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from microclimate import weather, wetness
from microclimate.live import compare
from tests_mc.test_live_core import fake_data, fake_pts

PRE_SWITCH_HASH = "874cc34cdf1ab1ce9474ca8d86e5704631cc6a6bcd2e98cb005dfb7f2e43ac97"   # sha256 of W + the four diag totals, recorded pre-change


def case(**kw):
    data, start = fake_data()
    ser = weather.series(data, start, start + timedelta(hours=72), 10)
    P = fake_pts()
    tab = np.tile(P["tracer"], (9, 1)) * np.linspace(0.5, 1.5, 9)[:, None].astype(np.float32)
    N = len(P["x"])
    fn = lambda i, f: {**f, "temp": f["temp"] + np.linspace(-2, 2, N)}  # noqa: E731
    return wetness.simulate(P, ser, 10, tracer_dirs=tab, forcing_fn=fn, lag_block_h=3, out_every=6, keep_lit=False, use_dirs=True, **kw)


def digest(W, d):
    h = hashlib.sha256(np.ascontiguousarray(W).tobytes())
    for k in ("fog_total", "dew_total", "rain_total", "evap_total"):
        h.update(np.ascontiguousarray(d[k]).tobytes())
    return h.hexdigest()


class TestSwitches(unittest.TestCase):
    def test_no_switch_is_bit_identical_to_before(self):
        for kw in ({}, {"physics": None}, {"physics": ()}, {"physics": []}):
            W, d = case(**kw)
            self.assertEqual(digest(W, d), PRE_SWITCH_HASH, kw)

    def test_unknown_switch_raises(self):
        with self.assertRaises(ValueError):
            case(physics=("no_such_switch",))
        with self.assertRaises(ValueError):
            wetness.check_physics(["film", "typo"])

    def test_known_names(self):
        self.assertEqual(set(wetness.KNOWN_PHYSICS), {"cloud_base", "lag_uniform", "vpd_evap", "total_cloud", "drizzle", "insitu_fog", "sun_clear", "film", "pavement", "grease"})
        built = {"sun_clear", "film"}                # guides 06 and 05 are built: they change the result (tests_mc/test_sun_clear.py, test_film.py)
        ph = tuple(k for k in wetness.KNOWN_PHYSICS if k not in built)
        W, d = case(physics=ph)                      # accepted; the others change nothing yet
        self.assertEqual(d["physics"], sorted(ph))
        self.assertEqual(digest(W, d), PRE_SWITCH_HASH)
        for k in built:
            Wk, dk = case(physics=(k,))
            self.assertNotEqual(digest(Wk, dk), PRE_SWITCH_HASH, k)
            self.assertTrue(np.isfinite(Wk).all())

    def test_parse_physics(self):
        self.assertEqual(compare.parse_physics("none"), ())
        self.assertEqual(compare.parse_physics(""), ())
        self.assertEqual(compare.parse_physics("film, drizzle"), ("film", "drizzle"))


class TestCompare(unittest.TestCase):
    def fake_static(self, n):
        r = np.random.default_rng(3)
        S = dict(x=wetness.X_WEST + r.uniform(0, 12000, n), y=4171500 + r.uniform(0, 9000, n), z=r.uniform(0, 400, n))
        Rb = dict(A_src=np.arange(0, n // 2), B_srcL=np.arange(n // 2, n - 1), B_srcR=np.arange(n // 2 + 1, n))
        return S, Rb

    def test_identical_sets_report_zero(self):
        Wa, da = case()
        Wb, db = case(physics=())
        S, Rb = self.fake_static(Wa.shape[1])
        rep = compare.report(Wa, da, Wb, db, S, Rb, 20, (), (), datetime(2026, 11, 12, tzinfo=timezone.utc))
        self.assertEqual(rep["max_abs_diff_points"], 0.0)
        self.assertEqual(rep["top_differences"], [])
        for part in rep["diff"].values():
            for g in part.values():
                self.assertTrue(all(v in (0.0, None) for v in g.values()), g)
        for t in rep["terms"].values():
            self.assertEqual(t["diff"], 0.0)
        md = compare.to_markdown(rep)
        self.assertIn("Ten largest", md)
        self.assertIn("elev >300 m", md)

    def test_differences_are_reported(self):
        Wa, da = case()
        Wb = Wa.copy()
        Wb[:, 3] += 10.0
        S, Rb = self.fake_static(Wa.shape[1])
        rep = compare.report(Wa, da, Wb, da, S, Rb, None, (), ("film",), datetime(2026, 11, 12, tzinfo=timezone.utc))
        self.assertEqual(rep["top_differences"][0]["z"], float(S["z"][3]))
        self.assertGreater(rep["diff"]["all"]["all rows"]["mean"], 0.0)
        self.assertEqual(rep["observed_until_frame"], None)
        self.assertEqual(rep["a"]["observed"]["all rows"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
