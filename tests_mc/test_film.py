"""Guide 05: slippery versus water depth, rain tracking (switch film)."""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import numpy as np

from microclimate import weather, wetness as w
from microclimate.live import outputs, state
from tests_mc.test_live_core import fake_data, fake_pts


def td_for_vpd(t, vpd):
    """Dew point at which es(t) - es(td) = vpd kPa."""
    lo, hi = -40.0, t
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if w.es_kpa(t) - w.es_kpa(mid) > vpd else (lo, mid)
    return 0.5 * (lo + hi)


def run_steps(F0=0.0, n=6, dt_h=1 / 6, cap=0.5, local_fog=0.0, **f):
    """Film steps on 2 flat asphalt points. Defaults: temperature = dew point (no vapour deficit, no dew), overcast, no sun."""
    ff = dict(temp=10.0, dew=10.0, wind=0.0, cloud_low=100.0, precip=0.0, sw_eff=0.0)
    ff.update(f)
    Fm = np.full(2, F0, np.float32)
    tot = dict(fog=0.0, dew=0.0, rain=0.0, evap=0.0, sun=0.0, runoff=0.0)
    for _ in range(n):
        Fm, parts = w.step_film(Fm, np.full(2, 0.8, np.float32), np.ones(2, np.float32), ff, np.full(2, local_fog), np.full(2, cap, np.float32), dt_h)
        for k in tot:
            tot[k] += float(parts[k][0])
    return float(Fm[0]), tot


def hours_to_dry(F0, vpd, wind, sw_eff, dt_h=1 / 60):
    t = 15.0
    Fm, h = F0, 0.0
    while Fm >= w.DRY_MM and h < 20:
        Fm, _ = run_steps(Fm, 1, dt_h, temp=t, dew=td_for_vpd(t, vpd), wind=wind, sw_eff=sw_eff)
        h += dt_h
    return h


class TestRates(unittest.TestCase):
    def test_mass_balance_rain(self):
        F, tot = run_steps(0.0, 6, precip=0.2)                      # 1 h of 0.2 mm/h on flat asphalt
        self.assertAlmostEqual(F, 0.2, places=5)
        self.assertAlmostEqual(tot["rain"], 0.2, places=5)
        F, tot = run_steps(0.0, 60, precip=0.2)                     # 10 h: capped at 0.5 mm, the rest runs off
        self.assertAlmostEqual(F, 0.5, places=5)
        self.assertAlmostEqual(tot["runoff"], 2.0 - 0.5, places=4)
        self.assertAlmostEqual(tot["rain"], 2.0, places=4)

    def test_porous_takes_less(self):
        Fm, _ = w.step_film(np.zeros(2, np.float32), np.ones(2), np.array([1.0, 0.6]), dict(temp=10.0, dew=10.0, wind=0.0, cloud_low=100.0, precip=1.0, sw_eff=0.0), np.zeros(2), np.full(2, 0.5), 1.0)
        np.testing.assert_allclose(Fm, [0.5, 0.5])                  # both exceed the cap in an hour; use a short step instead
        Fm, _ = w.step_film(np.zeros(2, np.float32), np.ones(2), np.array([1.0, 0.6]), dict(temp=10.0, dew=10.0, wind=0.0, cloud_low=100.0, precip=1.0, sw_eff=0.0), np.zeros(2), np.full(2, 0.5), 0.1)
        np.testing.assert_allclose(Fm, [0.1, 0.06], atol=1e-6)

    def test_calm_fog_three_hours(self):
        F, tot = run_steps(0.0, 18, local_fog=1.0, wind=0.0)
        self.assertGreater(F, 0.04)
        self.assertLess(F, 0.05)
        self.assertGreater(float(w.slip_of(F)), 87.0)
        self.assertLess(float(w.slip_of(F)), 92.0)
        F5, _ = run_steps(0.0, 6, local_fog=1.0, wind=5.0)          # 5 m/s: about 0.03 mm/h
        self.assertAlmostEqual(F5, 0.2 * (0.02 + 0.004 * 5.0) * 3.6, places=5)

    def test_dew_is_limited(self):
        F, tot = run_steps(0.0, 6, temp=10.0, dew=10.0, cloud_low=0.0, wind=0.0)   # clear calm night: surface 3.2 K colder than the air
        self.assertGreater(tot["dew"], 0.0)
        self.assertLessEqual(tot["dew"], w.DEW_MAX * 1.0 + 1e-9)
        self.assertAlmostEqual(tot["dew"], min(w.DEW_MM_PER_K * w.DT_MAX * 0.8, w.DEW_MAX), places=6)

    def test_drying_times(self):
        h = hours_to_dry(0.1, 0.5, 2.0, 0.0)
        self.assertGreater(h, 0.8)
        self.assertLess(h, 1.0)
        self.assertLess(hours_to_dry(0.1, 0.5, 2.0, 800.0), 10.0 / 60.0)    # with 800 W/m2: under ten minutes

    def test_slip_and_depth_values(self):
        np.testing.assert_allclose(w.slip_of([0.0, 0.01, 0.02, 0.05, 0.1]), [0.0, 39.35, 63.21, 91.79, 99.33], atol=0.01)
        self.assertEqual(round(float(w.slip_of(0.3))), round(float(w.slip_of(0.6))))      # slippery does not care about more water ...
        self.assertEqual(round(float(w.slip_of(0.3))), 100)
        self.assertEqual(w.depth_u8([0.0, 0.01, 1.0, 5.0]).tolist(), [0, 38, 255, 255])   # ... but the depth file does
        self.assertGreater(int(w.depth_u8(0.6)), int(w.depth_u8(0.3)))

    def test_storage_cap(self):
        c = w.storage_cap([0, 1, 2, 3, 0, 0], [0.0, 0.0, 0.0, 0.0, 0.15, -0.30])
        np.testing.assert_allclose(c, [0.5, 0.3, 0.2, 0.5, 0.25, 0.25], atol=1e-6)

    def test_no_negative_water(self):
        F, tot = run_steps(0.002, 6, temp=15.0, dew=td_for_vpd(15.0, 1.0), wind=3.0, sw_eff=800.0)
        self.assertEqual(F, 0.0)
        self.assertLessEqual(tot["evap"] + tot["sun"], 0.002 + 1e-9)        # no more removed than there was


class TestSimulate(unittest.TestCase):
    def setUp(self):
        self.data, self.t0 = fake_data()
        self.pts = fake_pts()
        self.N = len(self.pts["x"])
        self.tab = np.tile(self.pts["tracer"], (9, 1))

    def run_(self, a, b, rain=None, **kw):
        ser = weather.series(self.data, a, b, 10)
        ser["cloud_cover_low"] = np.zeros(len(ser["cloud_cover_low"]))
        if rain is None:
            fn = lambda i, f: {**f, "dew": f["temp"]}  # noqa: E731
        else:
            fn = lambda i, f: {**f, "dew": f["temp"], "precip": rain(i)}  # noqa: E731
        return w.simulate(self.pts, ser, 10, tracer_dirs=self.tab, forcing_fn=fn, out_every=6, keep_lit=False, use_dirs=True, physics=("film",), **kw)

    def test_outputs_shapes_and_slip(self):
        W, d = self.run_(self.t0, self.t0 + timedelta(hours=24), rain=lambda i: 1.0 if 30 <= i < 48 else 0.0)
        self.assertEqual(W.shape, (25, self.N))
        self.assertEqual(d["film_mm"].shape, W.shape)
        np.testing.assert_allclose(W, w.slip_of(d["film_mm"].astype(np.float32)), atol=0.2)
        self.assertTrue(d["extra_missing"])
        self.assertGreater(float(d["film_mm"].max()), 0.1)
        self.assertGreater(float(d["rain_total"].mean()), 2.0)
        self.assertGreater(float(d["runoff_total"].mean()), 0.0)             # 3 h of 1 mm/h exceeds the 0.5 mm storage
        self.assertEqual(sorted(d["rain_track"]), ["hours_since_rain", "rain_mm_24h", "rain_since_wash"])

    def test_dry_in_is_read_from_the_future_frames(self):
        rain = lambda i: 0.8 if 12 <= i < 24 else 0.0      # 2 h of rain from hour 2
        W, d = w.simulate(self.pts, weather.series(self.data, self.t0, self.t0 + timedelta(hours=20), 10), 10, tracer_dirs=self.tab,
                          forcing_fn=lambda i, f: {**f, "dew": f["temp"], "precip": rain(i)}, out_every=1, keep_lit=False, use_dirs=True, physics=("film",))
        film = d["film_mm"].astype(np.float32)
        wet = film >= w.DRY_MM
        clear = np.abs(film - w.DRY_MM) > 2e-4                               # float16 frames: skip the values within rounding of the threshold
        n = film.shape[0]
        checked = 0
        for j in range(0, n, 7):
            for p in range(self.N):
                later = np.nonzero(~wet[j:, p])[0]
                if not clear[j:, p][:(later[0] + 1) if len(later) else n].all():
                    continue
                exp = 255 if len(later) == 0 else min(254, int(np.ceil(later[0] / 6.0 - 1e-9)))
                self.assertEqual(int(d["dry_in_h"][j, p]), exp, (j, p))
                checked += 1
        self.assertGreater(checked, 100)
        self.assertTrue((d["dry_in_h"][:6] > 0).any())                       # wet streets exist in the first frames or later
        self.assertGreater(int((d["dry_in_h"] == 0).sum()), 0)               # dry streets say 0
        wet_late = d["film_mm"][-1] >= w.DRY_MM
        if wet_late.any():
            self.assertTrue((d["dry_in_h"][-1][wet_late] == 255).all())      # still wet at the end and never dry in the window

    def test_cause(self):
        W, d = self.run_(self.t0, self.t0 + timedelta(hours=12), rain=lambda i: 1.0 if 12 <= i < 18 else 0.0)
        self.assertEqual(int(d["cause"][0].max()), 0)                        # nothing wets the street at the start
        self.assertTrue((d["cause"][4] == 4).all())                          # 1 mm/h is rain, not drizzle
        W, d = self.run_(self.t0, self.t0 + timedelta(hours=12), rain=lambda i: 0.1 if 12 <= i < 18 else 0.0)
        self.assertTrue((d["cause"][4] == 3).all())                          # 0.1 mm/h is drizzle
        self.assertFalse(np.isin(d["cause"][-1], (3, 4)).any())              # six hours after it stopped: no rain or drizzle in the last 3 h (dew from the cold surface may remain)

    def test_rain_tracking(self):
        W, d = self.run_(self.t0, self.t0 + timedelta(hours=12), rain=lambda i: 1.0 if 12 <= i < 24 else 0.0)   # 2 mm in hours 2-4 (12 steps = 2 h at 1 mm/h)
        t = d["rain_track"]
        np.testing.assert_allclose(t["rain_mm_24h"], 2.0, atol=0.1)
        np.testing.assert_allclose(t["hours_since_rain"], 12 - 4 + 1 / 6 * 0, atol=0.2)                          # last rain step ends at hour 4, the window ends at hour 12
        self.assertAlmostEqual(float(t["rain_since_wash"].max()), 0.0, places=5)                                 # hours with >= 1 mm washed the street
        W, d = self.run_(self.t0, self.t0 + timedelta(hours=12), rain=lambda i: 0.3 if 12 <= i < 24 else 0.0)
        t = d["rain_track"]
        np.testing.assert_allclose(t["rain_since_wash"], 0.6, atol=0.05)                                         # light rain only accumulates
        W, d = self.run_(self.t0, self.t0 + timedelta(hours=12))
        np.testing.assert_allclose(d["rain_track"]["hours_since_rain"], w.HSR_MAX_H)

    def test_split_run_with_carried_film_state(self):
        rain = lambda i: 0.5 if 20 <= i < 80 else 0.0
        t1 = self.t0 + timedelta(hours=12)
        full, d = self.run_(self.t0, self.t0 + timedelta(hours=24), rain=rain, save_at=72)
        st = d["state_at"]
        self.assertEqual(sorted(st), ["W", "film", "hours_since_rain", "rain_hist", "rain_since_wash"])
        self.assertEqual(st["rain_hist"].dtype, np.uint8)
        part, _ = self.run_(t1, t1 + timedelta(hours=12), rain=lambda i: rain(i + 72), state0=st)
        np.testing.assert_array_equal(part, full[12:])
        cold, _ = self.run_(t1, t1 + timedelta(hours=12), rain=lambda i: rain(i + 72))
        self.assertGreater(np.abs(cold - part).max(), 1.0)

    def test_state_file_round_trip_and_off_state_unchanged(self):
        import datetime as dt
        _, d = self.run_(self.t0, self.t0 + timedelta(hours=12), rain=lambda i: 1.0 if 12 <= i < 24 else 0.0, save_at=60)
        t = dt.datetime(2026, 11, 12, 20, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / state.name_for(t)
            state.save(p, t, ("film",), "live-1", d["state_at"])
            s = state.load(p)
            np.testing.assert_allclose(s["arrays"]["film"], d["state_at"]["film"], atol=1e-3)
            np.testing.assert_array_equal(s["arrays"]["rain_hist"], d["state_at"]["rain_hist"])
            self.assertEqual(s["arrays"]["rain_hist"].dtype, np.uint8)
            a, why = state.find_anchor(tmp, t, ("film",), "live-1", self.N)
            self.assertEqual(why, "ok")
            self.assertIsNone(state.find_anchor(tmp, t, (), "live-1", self.N)[0])           # a film anchor is never used by a run without the switch
        ser = weather.series(self.data, self.t0, self.t0 + timedelta(hours=12), 10)
        _, d0 = w.simulate(self.pts, ser, 10, tracer_dirs=self.tab, out_every=6, keep_lit=False, use_dirs=True, save_at=60)
        self.assertEqual(sorted(d0["state_at"]), ["W"])                                      # switch off: the state holds what it held before


class TestFilmFiles(unittest.TestCase):
    def test_write_extras(self):
        Rb = dict(A_src=np.array([0, 1]), B_srcL=np.array([2, 3]), B_srcR=np.array([3, 4]))
        film = np.zeros((2, 5), np.float16)
        film[1] = [0.0, 0.01, 0.02, 0.4, 1.0]
        diag = dict(film_mm=film, dry_in_h=np.array([[0, 0, 0, 0, 0], [0, 3, 5, 255, 7]], np.uint8), cause=np.array([[0] * 5, [0, 1, 2, 3, 4]], np.uint8))
        with tempfile.TemporaryDirectory() as d:
            fields, names = outputs.write_extras(Path(d), diag, Rb, [[0, 2]], np.array([0, 1]), None)
            self.assertEqual(sorted(names), ["cause0.u8", "depth0.u8", "dry0.u8"])
            self.assertEqual(fields["film"]["files"]["depth"], ["depth0.u8"])
            self.assertEqual((Path(d) / "depth0.u8").stat().st_size, 2 * 4)
            dry = np.frombuffer((Path(d) / "dry0.u8").read_bytes(), np.uint8).reshape(2, 4)
            self.assertEqual(dry[1].tolist(), [0, 3, 255, 255])                               # city samples: the later of the two street sides
            depth = np.frombuffer((Path(d) / "depth0.u8").read_bytes(), np.uint8).reshape(2, 4)
            self.assertEqual(depth[1, 1], int(w.depth_u8(0.01)))
            self.assertEqual(fields["film"]["cause_codes"][3], "drizzle")
            self.assertNotIn("sun", fields)


if __name__ == "__main__":
    unittest.main()
