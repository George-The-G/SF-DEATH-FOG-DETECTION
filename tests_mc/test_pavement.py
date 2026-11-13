"""Guide 07: pavement temperature with heat memory (switch pavement). Synthetic forcing only: the constants are textbook values and nothing here is verified
against road measurements."""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import numpy as np

from microclimate import weather, wetness as w
from microclimate.live import compare, config as C, forcing, outputs, state
from tests_mc.test_live_core import fake_data, fake_pts

DT = 1.0 / 6.0
PAV = ("sun_clear", "film", "pavement")


def day(ndays=6, wind=0.5, cloud=0.0, svf=1.0, sun=1.0, tmean=20.0, tamp=5.0, td=8.0, peak=900.0, cd=2.35e5, F0=0.0, porous=1.0, sub=w.PAV_SUBSTEP_MIN):
    """Periodic synthetic days on one point: sunlight sun * peak * sin over 06-18 h (solar noon 12 h), air tmean +- tamp peaking at 15 h, constant dew point,
    constant wind and cloud. -> array of rows (hour, hour of day, Ta, ts, tdeep, S, F, energy J/m2 of the step)."""
    ts = np.array([tmean], float)
    tdeep, F = ts.copy(), np.array([F0], float)
    one = np.ones(1)
    rec = []
    for k in range(int(round(ndays * 24 / DT))):
        h = k * DT
        hod = h % 24
        S = peak * sun * max(np.sin(np.pi * (hod - 6) / 12), 0.0) if 6 < hod < 18 else 0.0
        ta = tmean + tamp * np.sin(2 * np.pi * (hod - 9) / 24)
        ts, tdeep, F, p = w.step_pavement(ts, tdeep, F, svf * one, porous * one, S * one, ta * one, td * one, wind * one, cloud * one, DT, cd * one, sub)
        rec.append((h, hod, ta, ts[0], tdeep[0], S, F[0], p["energy"][0]))
    return np.array(rec)


def last_day(r, d=5):
    return r[r[:, 0] >= d * 24]


class TestSanityTargets(unittest.TestCase):
    """The sanity table of guide 07 (the achieved values are in the hand-back)."""

    def test_clear_calm_summer_day(self):
        L = last_day(day())
        diff = L[:, 3] - L[:, 2]
        self.assertGreaterEqual(diff.max(), 20.0)
        self.assertLessEqual(diff.max(), 30.0)
        t_peak = L[np.argmax(L[:, 3]), 1]
        self.assertGreater(t_peak, 12.0 + 1.0 - 1e-9)   # 1 - 2 h after solar noon
        self.assertLessEqual(t_peak, 12.0 + 2.0 + 1e-9)

    def test_clear_night_after_a_clear_day(self):
        L = last_day(day())
        eve = L[L[:, 1] >= 18.0]                         # from sunset to midnight
        self.assertGreater((eve[:, 3] - eve[:, 2]).min(), 0.0)
        dawn = L[np.abs(L[:, 1] - 6.0) < 1e-6][0]
        self.assertLessEqual(dawn[3] - dawn[2], 3.0)
        self.assertGreaterEqual(dawn[3] - dawn[2], -2.0)

    def test_overcast_windy_stays_near_air(self):
        L = last_day(day(wind=8.0, cloud=100.0, peak=150.0))   # thick overcast lets about 150 W/m2 through
        self.assertLess(np.abs(L[:, 3] - L[:, 2]).max(), 2.0)

    def test_shaded_street_cooler_by_day_and_gap_closes(self):
        sunny = last_day(day(svf=0.6))
        shade = last_day(day(svf=0.6, sun=0.15))        # only the diffuse part of the light reaches it
        gap = sunny[:, 3] - shade[:, 3]
        noon = (sunny[:, 1] >= 11.0) & (sunny[:, 1] <= 16.0)
        dawn = np.abs(sunny[:, 1] - 6.0) < 1e-6
        self.assertGreater(gap[noon].min(), 10.0)
        self.assertLess(gap[dawn][0], 0.25 * gap[noon].max())    # by morning most of the gap is gone

    def test_energy_closure(self):
        r = day(ndays=3, F0=0.3, td=12.0)                # wet start: evaporation cooling is part of the flux
        cd = 2.35e5
        c_deep = cd * w.PAV_OMEGA / 2.0 * w.PAV_DEEP_S
        store = cd / 2.0 * r[:, 3] + c_deep * r[:, 4]
        e = r[:, 7]
        # the store after step k minus before it (before step 0: ts = tdeep = the mean air temperature)
        store0 = cd / 2.0 * 20.0 + c_deep * 20.0
        n = int(round(14 / DT))                           # the morning warm-up: large net heating
        d_store = store[n - 1] - store0
        self.assertGreater(abs(d_store), 1e6)
        self.assertLess(abs(d_store - e[:n].sum()), 0.02 * abs(d_store))
        self.assertLess(abs(store[-1] - store0 - e.sum()), 1e-6 * np.abs(e).sum())   # three days: exact up to rounding

    def test_dew_only_below_the_dew_point_and_signs_agree_with_le(self):
        ts, tdeep, F = np.array([14.0]), np.array([14.0]), np.array([0.02])
        one = np.ones(1)
        seen_evap = seen_dew = 0
        for k in range(int(10 / DT)):
            pre, F_pre = float(ts[0]), float(F[0])
            ts, tdeep, F, p = w.step_pavement(ts, tdeep, F, one, one, 0 * one, 12.0 * one, 10.5 * one, 0.3 * one, 0 * one, DT, 2.35e5 * one, 10.0)   # one sub-step
            ev, dw = float(p["evap"][0]), float(p["dew"][0])
            self.assertFalse(ev > 0 and dw > 0)
            if dw > 0:
                self.assertLess(pre, 10.5)                # dew only while the surface is colder than the dew point
                seen_dew += 1
            if ev > 0:
                self.assertGreater(pre, 10.5)             # evaporation only while warmer, and only with water on the street
                self.assertGreater(F_pre, 0.0)
                seen_evap += 1
            if pre > 10.5 and F_pre <= 0.0:
                self.assertEqual((ev, dw), (0.0, 0.0))    # a dry street above the dew point: no latent flux
            self.assertAlmostEqual(float(F[0]), F_pre - ev + dw, places=12)
        self.assertGreater(seen_evap, 0)
        self.assertGreater(seen_dew, 0)

    def test_evaporation_dries_exactly_and_never_goes_negative(self):
        r = day(ndays=1, F0=0.001, tmean=20.0, td=5.0)
        self.assertTrue((r[:, 6] >= 0.0).all())
        ts, tdeep, F = np.array([30.0]), np.array([25.0]), np.array([0.001])
        one = np.ones(1)
        ts, tdeep, F, p = w.step_pavement(ts, tdeep, F, one, one, 800 * one, 25.0 * one, 5.0 * one, 1.0 * one, 0 * one, DT, 2.35e5 * one)
        self.assertEqual(float(F[0]), 0.0)
        self.assertAlmostEqual(float(p["evap"][0]), 0.001, places=9)  # exactly the water that was there

    def test_concrete_swings_less_than_asphalt(self):
        a = last_day(day())
        c = last_day(day(cd=2.9e5))
        self.assertLess(np.ptp(c[:, 3]), np.ptp(a[:, 3]))

    def test_porous_street_takes_no_condensation(self):
        one = np.ones(2)
        ts, tdeep, F, p = w.step_pavement(np.full(2, 8.0), np.full(2, 8.0), np.zeros(2), one, np.array([1.0, 0.0]), 0 * one, 12.0 * one, 12.0 * one, 0.3 * one, 0 * one, DT, 2.35e5 * one)
        self.assertGreater(float(p["dew"][0]), 0.0)
        self.assertEqual(float(p["dew"][1]), 0.0)

    def test_effective_cloud(self):
        self.assertEqual(float(w.effective_cloud_pct(dict(cloud_low=40.0))), 40.0)
        self.assertAlmostEqual(float(w.effective_cloud_pct(dict(cloud_low=40.0, cloud=100.0))), 70.0)
        self.assertAlmostEqual(float(w.effective_cloud_pct(dict(cloud_low=60.0, cloud=20.0))), 60.0)   # total below low: low only


class TestSwitchRules(unittest.TestCase):
    def test_needs_sun_clear_and_film(self):
        for ph in (("pavement",), ("film", "pavement"), ("sun_clear", "pavement")):
            with self.assertRaises(ValueError) as cm:
                w.check_physics(ph)
            self.assertIn("needs 'sun_clear' and 'film'", str(cm.exception))
        self.assertEqual(w.check_physics(PAV), frozenset(PAV))

    def test_simulate_build_and_compare_raise(self):
        data, t0 = fake_data()
        ser = weather.series(data, t0, t0 + timedelta(hours=2), 10)
        with self.assertRaises(ValueError):
            w.simulate(fake_pts(), ser, 10, tracer_dirs=np.tile(fake_pts()["tracer"], (9, 1)), physics=("pavement",))
        from microclimate.live import build, __main__ as cli
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                build.run(d, physics=("film", "pavement"))
            with self.assertRaises(ValueError):
                compare.run(("pavement",), out_dir=d)
        with self.assertRaises(SystemExit) as cm:
            cli.main(["build", "--physics", "pavement"])
        self.assertIn("needs", str(cm.exception))
        with self.assertRaises(SystemExit) as cm:
            cli.main(["compare", "--physics", "film,pavement"])
        self.assertIn("needs", str(cm.exception))

    def test_off_in_the_public_config(self):
        self.assertNotIn("pavement", C.PHYSICS)


class TestSimulate(unittest.TestCase):
    def setUp(self):
        self.data, self.t0 = fake_data()
        self.pts = fake_pts()
        self.N = len(self.pts["x"])
        self.tab = np.tile(self.pts["tracer"], (9, 1))

    def run_(self, a, b, physics=PAV, rain=None, **kw):
        ser = weather.series(self.data, a, b, 10)
        ser["cloud_cover_low"] = np.zeros(len(ser["cloud_cover_low"]))     # no fog source: the fog lag does not depend on the window length
        fn = lambda i, f: {**f, "dew": f["temp"] - 1.0, "k": 0.7, "precip": 0.0 if rain is None else rain(i)}  # noqa: E731
        return w.simulate(self.pts, ser, 10, tracer_dirs=self.tab, forcing_fn=fn, out_every=6, keep_lit=False, use_dirs=True, physics=physics, **kw)

    def test_state_keys_and_cold_start(self):
        _, d = self.run_(self.t0, self.t0 + timedelta(hours=24), save_at=0)
        st = d["state_at"]
        self.assertEqual(sorted(st), ["W", "film", "hours_since_rain", "rain_hist", "rain_since_wash", "tdeep", "ts"])
        ser = weather.series(self.data, self.t0, self.t0 + timedelta(hours=24), 10)
        np.testing.assert_allclose(st["ts"], np.mean(ser["temperature_2m"][:144]), atol=1e-5)     # default: the mean air temperature of the first 24 h
        np.testing.assert_array_equal(st["ts"], st["tdeep"])
        _, d = self.run_(self.t0, self.t0 + timedelta(hours=24), save_at=0, ts0=7.5)
        np.testing.assert_allclose(d["state_at"]["ts"], 7.5)
        _, d = self.run_(self.t0, self.t0 + timedelta(hours=24), save_at=0, ts0=np.linspace(5, 9, self.N))
        np.testing.assert_allclose(d["state_at"]["tdeep"], np.linspace(5, 9, self.N), atol=1e-5)
        _, d = self.run_(self.t0, self.t0 + timedelta(hours=24), physics=("sun_clear", "film"), save_at=0)
        self.assertEqual(sorted(d["state_at"]), ["W", "film", "hours_since_rain", "rain_hist", "rain_since_wash"])   # switch off: the state is what it was

    def test_outputs(self):
        W, d = self.run_(self.t0, self.t0 + timedelta(hours=24))
        self.assertEqual(d["ts_u8"].shape, W.shape)
        self.assertEqual(d["ts_u8"].dtype, np.uint8)
        np.testing.assert_array_equal(d["ts_u8"][-1], np.clip(np.rint((d["ts_end"] + 10.0) * 4.0), 0, 255).astype(np.uint8))   # 24 h = 145 steps: the last frame is the last step
        self.assertTrue(np.isfinite(d["ts_end"]).all())
        self.assertEqual(d["dew_steps"].shape, (self.N,))
        self.assertEqual(d["n_steps"], 145)
        self.assertEqual(d["physics"], sorted(PAV))
        W0, d0 = self.run_(self.t0, self.t0 + timedelta(hours=24), physics=("sun_clear", "film"))
        self.assertNotIn("ts_u8", d0)
        self.assertGreater(np.abs(d["film_mm"].astype(float) - d0["film_mm"].astype(float)).max(), 0.0)

    def test_split_run_with_carried_temperature(self):
        t1 = self.t0 + timedelta(hours=12)
        rain = lambda i: 0.4 if 20 <= i < 50 else 0.0   # noqa: E731
        full, d = self.run_(self.t0, self.t0 + timedelta(hours=24), rain=rain, save_at=72)
        part, dp = self.run_(t1, t1 + timedelta(hours=12), rain=lambda i: rain(i + 72), state0=d["state_at"])
        np.testing.assert_array_equal(part, full[12:])
        np.testing.assert_array_equal(dp["ts_u8"], d["ts_u8"][12:])
        np.testing.assert_array_equal(dp["ts_end"], d["ts_end"])
        np.testing.assert_array_equal(dp["tdeep_end"], d["tdeep_end"])
        same, ds = self.run_(t1, t1 + timedelta(hours=12), rain=lambda i: rain(i + 72), state0=d["state_at"], ts0=-5.0)
        np.testing.assert_array_equal(ds["ts_end"], d["ts_end"])                              # a carried state wins over ts0
        cold, dc = self.run_(t1, t1 + timedelta(hours=12), rain=lambda i: rain(i + 72), ts0=-5.0)
        self.assertGreater(np.abs(dc["ts_u8"][0].astype(int) - d["ts_u8"][12].astype(int)).max(), 40)   # a cold start from -5 degC is far off at the start

    def test_state_file_round_trip_in_0_01_k(self):
        import datetime as dt
        full, d = self.run_(self.t0, self.t0 + timedelta(hours=24), save_at=72)
        t = dt.datetime(2026, 11, 10, 12, tzinfo=dt.timezone.utc)
        st = dict(d["state_at"], pav_days=np.array(2, np.int16))
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / state.name_for(t)
            state.save(p, t, PAV, "live-1", st)
            s = state.load(p)
            np.testing.assert_allclose(s["arrays"]["ts"], st["ts"], atol=0.0051)
            np.testing.assert_allclose(s["arrays"]["tdeep"], st["tdeep"], atol=0.0051)
            self.assertEqual(s["arrays"]["ts"].dtype, np.float32)
            self.assertEqual(int(s["arrays"]["pav_days"]), 2)
            with np.load(p) as z:
                self.assertEqual(z["ts"].dtype, np.int16)
            arrays, why = state.find_anchor(tmp, t, PAV, "live-1", self.N)
            self.assertEqual(why, "ok")
            self.assertIsNone(state.find_anchor(tmp, t, ("sun_clear", "film"), "live-1", self.N)[0])   # an anchor with road temperatures is never used without the switch
        t1 = self.t0 + timedelta(hours=12)
        _, dp = self.run_(t1, t1 + timedelta(hours=12), state0=arrays)
        np.testing.assert_allclose(dp["ts_end"], d["ts_end"], atol=0.05)                           # only the 0.01 K and float16 water rounding differ

    def test_tsurf_file_and_index_fields(self):
        Rb = dict(A_src=np.array([0, 1]), B_srcL=np.array([2, 3]), B_srcR=np.array([3, 4]))
        ts = np.array([[20.0, 30.0, -12.0, 70.0, 12.3]])
        diag = dict(ts_u8=np.clip(np.rint((ts + 10.0) * 4.0), 0, 255).astype(np.uint8))
        with tempfile.TemporaryDirectory() as d:
            fields, names = outputs.write_extras(Path(d), diag, Rb, [[0, 1]], np.array([0]), None)
            self.assertEqual(names, ["tsurf0.u8"])
            raw = np.frombuffer((Path(d) / "tsurf0.u8").read_bytes(), np.uint8)
            self.assertEqual(raw.tolist(), [120, 160, 128, 172])   # district points as they are (20 and 30 degC); city samples = mean of left / right: (0 + 255) / 2, (255 + 89) / 2
            self.assertEqual(fields["tsurf"]["files"], ["tsurf0.u8"])
            self.assertIn("u8 / 4 - 10", fields["tsurf"]["formula"])


class TestForcingColdStart(unittest.TestCase):
    def test_full_past_day(self):
        th = np.arange(-30, 20, dtype=float)                       # RRFS: 30 past hours
        T = np.tile(th, (3, 1)) * 0.0 + np.array([[10.0], [12.0], [14.0]])
        T[:, th < -24] = 99.0                                      # older than 24 h: not used
        T[:, th >= 0] = -50.0                                      # the window itself: not used when 24 past hours exist
        m, info = forcing.pavement_cold_start(T, th)
        np.testing.assert_allclose(m, [10.0, 12.0, 14.0])
        self.assertEqual(info, dict(past_hours=24, window_hours=0))

    def test_short_past_adds_the_first_hours_of_the_window(self):
        th = np.arange(-15, 20, dtype=float)                       # only 15 past hours
        T = np.where(th < 0, 10.0, 20.0)[None, :]
        m, info = forcing.pavement_cold_start(T, th)
        self.assertEqual(info, dict(past_hours=15, window_hours=min(C.HISTORY_H, 9)))
        n_w = info["window_hours"]
        np.testing.assert_allclose(m, (15 * 10.0 + n_w * 20.0) / (15 + n_w))

    def test_no_past_at_all_uses_the_window(self):
        th = np.arange(0, 30, dtype=float)
        m, info = forcing.pavement_cold_start(np.full((2, 30), 11.0), th)
        np.testing.assert_allclose(m, 11.0)
        self.assertEqual(info["past_hours"], 0)
        self.assertEqual(info["window_hours"], C.HISTORY_H)


class TestCompareParts(unittest.TestCase):
    def test_dew_frequency_and_pavement_summary(self):
        self.assertIsNone(compare.dew_frequency(dict()))
        d = dict(dew_steps=np.array([0, 10, 20, 0]), n_steps=100)
        r = compare.dew_frequency(d)
        self.assertAlmostEqual(r["step_share"], 0.075)
        self.assertAlmostEqual(r["points_with_dew"], 0.5)
        S = dict(x=np.zeros(4), y=np.zeros(4), z=np.array([10.0, 60.0, 200.0, 400.0]))
        ts_u8 = np.full((3, 4), 120, np.uint8)
        pv = compare.pavement_summary(dict(d), dict(d, ts_u8=ts_u8), S, None, 1)
        self.assertAlmostEqual(pv["ts_b"]["all"]["all rows"]["mean_c"], 20.0)
        self.assertEqual(pv["ts_b"]["observed"]["all rows"]["n"], 4)
        self.assertEqual(pv["ts_b"]["all"]["elev >300 m"]["n"], 3)

    def test_markdown_has_pavement_and_dew_sections(self):
        data, t0 = fake_data()
        ser = weather.series(data, t0, t0 + timedelta(hours=12), 10)
        pts = fake_pts()
        tab = np.tile(pts["tracer"], (9, 1))
        fn = lambda i, f: {**f, "k": 0.7}  # noqa: E731
        res = [w.simulate(pts, ser, 10, tracer_dirs=tab, forcing_fn=fn, out_every=6, keep_lit=False, use_dirs=True, physics=ph) for ph in (("sun_clear", "film"), PAV)]
        (Wa, da), (Wb, db) = res
        n = Wa.shape[1]
        S = dict(x=pts["x"], y=pts["y"], z=pts["z"])
        Rb = dict(A_src=np.arange(0, n // 2), B_srcL=np.arange(n // 2, n - 1), B_srcR=np.arange(n // 2 + 1, n))
        import datetime as dt
        rep = compare.report(Wa, da, Wb, db, S, Rb, 4, ("film", "sun_clear"), sorted(PAV), dt.datetime(2026, 11, 12, tzinfo=dt.timezone.utc))
        md = compare.to_markdown(rep)
        self.assertIn("road surface temperature", md)
        self.assertIn("Dew frequency", md)
        self.assertIsNotNone(rep["pavement"]["dew_a"])
        self.assertIsNotNone(rep["pavement"]["dew_b"])


if __name__ == "__main__":
    unittest.main()
