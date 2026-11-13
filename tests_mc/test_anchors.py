"""Guide 03: stations as anchors. Identity with the flag off, the fit on synthetic truth, per-point direction weights, temperature, stations.json."""
import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from microclimate import dirlib, weather, wetness
from microclimate.live import anchors, config as C, forcing, stations
from tests_mc import anchors_fixture as A
from tests_mc.test_live_core import fake_data, fake_pts

PRE_ANCHOR_FORCING_HASH = "d62e10c9fa5e48b9e645c090a1f852e73f14ef2eefd6867afed510e672275273"   # forcing.build on the fixture, recorded BEFORE any guide 03 change
DIRS = np.array(wetness.DEFAULT_DIRS)


def fake_lib(S, seed=0):
    """uv[D, S, 2]: each station sees the library wind rotated by a station-specific angle and scaled by a station-specific ratio (a stand-in for the real fields)."""
    r = np.random.default_rng(seed)
    k = r.uniform(0.5, 1.1, S)
    rot = r.uniform(-25, 25, S)
    uv = np.zeros((len(DIRS), S, 2))
    for d, th in enumerate(DIRS):
        u, v = anchors.to_uv(1.0, th + rot)
        uv[d, :, 0], uv[d, :, 1] = k * u, k * v
    return uv


def obs_from(U, th, W, uv):
    return anchors.predict(np.atleast_1d(U), np.atleast_1d(th), W, uv, DIRS)


class TestIdentity(unittest.TestCase):
    def test_flag_default_is_off(self):
        self.assertFalse(C.NUDGE_ANCHORS)

    def test_build_with_flag_off_is_bit_identical_to_before(self):
        F, _, _ = A.build()
        self.assertEqual(A.digest(F), PRE_ANCHOR_FORCING_HASH)

    def test_extra_stations_do_not_change_the_model_with_the_flag_off(self):
        obs = A.fake_obs()
        t = [A.T0 + timedelta(hours=h) for h in range(38)]
        obs["stations"] = dict(raw={sid: {} for sid in C.STATIONS}, failed={"x": "y"}, seconds=1.0, newest={}, jobs=3)
        obs["stations"]["raw"]["PXOC1"] = dict(T=(t, np.full(38, 14.0)), wind=(t, np.full(38, 4.0), np.full(38, 250.0)))
        obs["stations"]["raw"]["ONOC1"] = dict(T=(t, np.full(38, 19.0)), Td=(t, np.full(38, 12.0)))
        obs["station_wind"] = None
        F, _, _ = A.build(obs)
        self.assertEqual(A.digest(F), PRE_ANCHOR_FORCING_HASH)
        self.assertIn("stations_json", F)

    def test_flag_on_without_station_data_changes_nothing(self):
        old = C.NUDGE_ANCHORS
        C.NUDGE_ANCHORS = True
        try:
            F, _, _ = A.build()
        finally:
            C.NUDGE_ANCHORS = old
        self.assertEqual(A.digest(F), PRE_ANCHOR_FORCING_HASH)

    def test_dir_w_absent_is_todays_path_and_one_region_equals_it(self):
        data, start = fake_data()
        ser = weather.series(data, start, start + timedelta(hours=30), 10)
        P = fake_pts()
        tab = np.tile(P["tracer"], (9, 1)) * np.linspace(0.5, 1.5, 9)[:, None].astype(np.float32)
        N = len(P["x"])
        W0, _ = wetness.simulate(P, ser, 10, tracer_dirs=tab, lag_block_h=3, keep_lit=False)
        wd = np.asarray(ser["wind_dir"], float)
        Wt = dirlib.weights(wd, DIRS)
        W1, _ = wetness.simulate(P, ser, 10, tracer_dirs=tab, lag_block_h=3, keep_lit=False, forcing_fn=lambda i, f: {**f, "dir_w": np.repeat(Wt[i][:, None], N, axis=1)})
        np.testing.assert_allclose(W1, W0, atol=1e-4)
        # a different direction field per point really changes the result
        Wt2 = np.zeros((9, N))
        Wt2[0] = 1.0
        W2, _ = wetness.simulate(P, ser, 10, tracer_dirs=tab, lag_block_h=3, keep_lit=False, forcing_fn=lambda i, f: {**f, "dir_w": Wt2})
        self.assertGreater(np.abs(W2 - W0).max(), 0.01)


class TestRegions(unittest.TestCase):
    def test_weights_sum_to_one_everywhere(self):
        r = np.random.default_rng(1)
        x, y = r.uniform(-5e4, 5e4, 200), r.uniform(-5e4, 5e4, 200)
        w = anchors.region_weights(x, y, [0, 3000, 6000, -4000], [0, 2000, -3000, 5000], 3000.0)
        np.testing.assert_allclose(w.sum(axis=0), 1.0, atol=1e-12)
        self.assertTrue((w >= 0).all())

    def test_direction_weights_sum_to_one(self):
        th = np.array([[200.0, 260.0, 359.0], [90.0, 400.0, 370.0]])
        w = anchors.dir_weights(th, DIRS)
        self.assertEqual(w.shape, (2, 3, 9))
        np.testing.assert_allclose(w.sum(axis=-1), 1.0, atol=1e-12)


class TestWindFit(unittest.TestCase):
    def setUp(self):
        self.S = 5
        self.uv = fake_lib(self.S)

    def test_one_region_recovers_the_inflow(self):
        W = np.ones((1, self.S))
        for U, th in ((7.0, 250.0), (3.5, 300.0), (9.0, 215.0)):
            # forecast wrong by 1 m/s and 10 degrees (a typical miss; a forecast far off is only partly corrected by design, see test_far_forecast_is_pulled_part_way)
            o = anchors.fit_hour(obs_from(U, th, W, self.uv), W, self.uv, DIRS, np.array([U - 1.0]), np.array([th + 10.0]), [])
            self.assertAlmostEqual(o["U"][0], U, delta=0.3)
            self.assertLess(abs(o["th"][0] - th), 5.0)
            self.assertFalse(o["offshore"])

    def test_far_forecast_is_pulled_part_way(self):
        W = np.ones((1, self.S))
        o = anchors.fit_hour(obs_from(7.0, 215.0, W, self.uv), W, self.uv, DIRS, np.array([5.0]), np.array([270.0]), [])
        self.assertLess(abs(o["th"][0] - 215.0), 15.0)
        self.assertLess(abs(o["th"][0] - 215.0), abs(270.0 - 215.0) / 3)

    def test_two_regions_recover_their_own(self):
        S = 8
        uv = fake_lib(S, 4)
        W = np.zeros((2, S))
        W[0, :4], W[1, 4:] = 1.0, 1.0
        truth_U, truth_th = np.array([8.0, 6.0]), np.array([240.0, 285.0])
        obs = obs_from(truth_U, truth_th, W, uv)
        o = anchors.fit_hour(obs, W, uv, DIRS, np.array([7.2, 6.8]), np.array([250.0, 275.0]), [(0, 1)])
        self.assertEqual(o["mode"], "regional")
        np.testing.assert_allclose(o["U"], truth_U, atol=0.3)
        np.testing.assert_allclose(o["th"], truth_th, atol=5.0)

    def test_region_with_one_anchor_takes_the_citywide_fit(self):
        S = 4
        uv = fake_lib(S, 5)
        W = np.zeros((2, S))
        W[0, :3], W[1, 3] = 1.0, 1.0           # region 1 has a single anchor
        obs = obs_from([6.0, 6.0], [255.0, 255.0], W, uv)
        o = anchors.fit_hour(obs, W, uv, DIRS, np.array([5.0, 5.0]), np.array([270.0, 270.0]), [(0, 1)])
        self.assertEqual(list(o["active"]), [True, False])
        self.assertAlmostEqual(o["U"][1], 6.0, delta=0.4)

    def test_no_stations_returns_the_forecast(self):
        W = np.ones((1, self.S))
        o = anchors.fit_hour(np.full((self.S, 2), np.nan), W, self.uv, DIRS, np.array([5.0]), np.array([271.0]), [])
        self.assertEqual(o["mode"], "forecast")
        self.assertEqual(o["U"][0], 5.0)
        self.assertEqual(o["th"][0], 271.0)

    def test_outlier_is_dropped(self):
        W = np.ones((1, self.S))
        obs = obs_from(6.0, 260.0, W, self.uv)
        bad = obs.copy()
        bad[2] += np.array([12.0, -9.0])
        keep, dropped = anchors.qc_mask(bad)
        self.assertEqual(dropped, 1)
        self.assertFalse(keep[2])
        o = anchors.fit_hour(bad, W, self.uv, DIRS, np.array([5.0]), np.array([270.0]), [])
        self.assertAlmostEqual(o["U"][0], 6.0, delta=0.4)
        self.assertEqual(o["dropped"], 1)

    def test_offshore_hour_is_flagged_and_clamped(self):
        W = np.ones((1, self.S))
        u, v = anchors.to_uv(5.0, 110.0)           # wind from 110 degrees: the library cannot represent it
        obs = np.tile([u, v], (self.S, 1))
        o = anchors.fit_hour(obs, W, self.uv, DIRS, np.array([5.0]), np.array([270.0]), [])
        self.assertTrue(o["offshore"])
        w = anchors.dir_weights(o["th"], DIRS)
        np.testing.assert_allclose(w.sum(), 1.0)

    def test_series_fits_each_hour(self):
        W = np.ones((1, self.S))
        truth = [(6.0, 250.0), (7.0, 255.0), (5.0, 262.0)]
        obs = np.stack([obs_from(U, th, W, self.uv) for U, th in truth])
        o = anchors.fit_series(obs, W, self.uv, DIRS, np.full((3, 1), 5.0), np.full((3, 1), 270.0), [])
        np.testing.assert_allclose(o["U"][:, 0], [t[0] for t in truth], atol=0.3)


class TestTemperature(unittest.TestCase):
    def test_offset_and_lapse_recovered(self):
        dz = np.array([0.0, 0.02, 0.05, 0.3, 0.4, 0.6, 0.0])
        a, b = 1.3, 3.0
        r = a + b * dz + np.random.default_rng(2).normal(0, 0.05, len(dz))
        fa, fb = anchors.fit_T_hour(r, dz)
        self.assertAlmostEqual(fa, a, delta=0.3)
        self.assertAlmostEqual(fb, b, delta=1.0)

    def test_no_height_spread_means_no_lapse_adjustment(self):
        fa, fb = anchors.fit_T_hour(np.array([1.2, 1.4, 1.3, 1.1]), np.array([0.0, 0.01, 0.03, 0.045]))
        self.assertEqual(fb, 0.0)
        self.assertAlmostEqual(fa, 1.25, delta=0.1)

    def test_fewer_than_three_stations_gives_none(self):
        self.assertIsNone(anchors.fit_T_hour(np.array([1.0, 1.1, np.nan]), np.array([0.0, 0.1, 0.2])))

    def test_horizontal_is_the_residual_at_a_lone_station_and_fades_away(self):
        r = np.array([2.0])
        at = anchors.horizontal(r, [0.0], [0.0], [0.0, 3000.0, 30000.0], [0.0, 0.0, 0.0])
        self.assertAlmostEqual(at[0], 2.0)
        self.assertTrue(0.0 < at[1] < 2.0)
        self.assertLess(at[2], 1e-4)


class TestStationsJson(unittest.TestCase):
    def lone(self):
        """One temperature station whose observation is the lattice value plus a constant: the model after the fit equals it."""
        rr = A.fake_rrfs()
        lat_t = forcing.Lattice(rr["lat"], rr["lon"])
        th = forcing.hours_rel(rr["times"], A.T0)
        nh = int(np.floor((A.N_STEPS - 1) / 6.0)) + 1
        Tg = forcing.to_grid(rr["vars"]["temperature_2m"], th, np.arange(nh, dtype=float))
        m = C.STATIONS["PXOC1"]
        i, w = lat_t.weights(np.array([m["lat"]]), np.array([m["lon"]]))
        zl = forcing.interp_at(np.asarray(rr["elev"], float), i, w)[0]
        Tl = forcing.interp_at(Tg, i, w)[0]
        T = Tl + C.LAPSE_T_K_PER_KM * (m["elev_m"] - zl) / 1000.0 + 1.7
        ts = [A.T0 + timedelta(hours=h) for h in range(30)]
        obs = dict(coops=None, metar=None, nws={}, sounding=None, station_wind=None)
        obs["stations"] = dict(raw={sid: {} for sid in C.STATIONS}, failed={}, seconds=0.0, newest={}, jobs=0)
        obs["stations"]["raw"]["PXOC1"] = dict(T=(ts, T[:30]))
        return obs, T

    def test_schema_and_lone_station_matches_its_observation(self):
        obs, T = self.lone()
        F, _, _ = A.build(obs)
        sj = F["stations_json"]
        for k in ("version", "generated_iso", "hours_iso", "stations", "mae_columns", "nudge_anchors"):
            self.assertIn(k, sj)
        row = sj["stations"]["PXOC1"]
        for k in ("name", "kind", "lat", "lon", "x", "y", "z", "obs", "model_before", "model_after", "model_city", "mae_24h"):
            self.assertIn(k, row)
        n = len(sj["hours_iso"])
        self.assertGreater(n, 10)
        for series in list(row["obs"].values()) + list(row["model_before"].values()) + list(row["model_after"].values()):
            self.assertEqual(len(series), n)
        obsT, aft = np.array(row["obs"]["T"], float), np.array(row["model_after"]["T"], float)
        ok = np.isfinite(obsT) & np.isfinite(aft)
        self.assertGreater(ok.sum(), 10)
        self.assertLess(np.abs(obsT[ok] - aft[ok]).max(), 0.2)
        self.assertLess(row["mae_24h"]["T"][1], 0.2)
        json.dumps(sj, allow_nan=False)        # strict JSON: no NaN anywhere
        self.assertIsNotNone(F["stations"])
        self.assertLess(len(json.dumps(F["stations"])), 60000)

    def test_flag_on_uses_the_station_for_the_street_temperature(self):
        obs, T = self.lone()
        base, _, _ = A.build(obs)
        old = C.NUDGE_ANCHORS
        C.NUDGE_ANCHORS = True
        try:
            on, x, y = A.build(obs)
        finally:
            C.NUDGE_ANCHORS = old
        f0 = {"temp": 0.0, "dew": 0.0, "wind": 0.0, "cloud_low": 0.0, "precip": 0.0, "sw": 0.0}
        a, b = base["forcing_fn"](30, f0), on["forcing_fn"](30, f0)
        self.assertTrue(np.isfinite(b["temp"]).all() and np.isfinite(b["dew"]).all())
        self.assertGreater(np.abs(a["temp"] - b["temp"]).max(), 0.05)


class TestFetchIsSafe(unittest.TestCase):
    def test_blocked_sources_fail_softly_and_fast(self):
        os.environ["DEATHFOG_LIVE_BLOCK"] = "coops_extra,iem,ndbc,nws_extra"
        try:
            t = time.time()
            r = stations.collect(A.fake_obs(), A.T0, A.NOW)
            dt_ = time.time() - t
        finally:
            del os.environ["DEATHFOG_LIVE_BLOCK"]
        self.assertLess(dt_, 3.0)
        self.assertGreater(len(r["failed"]), 5)
        self.assertTrue(r["raw"]["KSFO"])               # stations the build already fetched are reused
        self.assertIn("T", r["raw"]["SFOC1"])
        self.assertFalse(r["raw"]["GGBC1"])


if __name__ == "__main__":
    unittest.main()
