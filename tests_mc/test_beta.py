"""Beta preview channel: its settings never leak into the public build (and the other way round), the shared fetched inputs, and the GOES downward shortwave
(ranged reads, rolling cache, k_sat preferred over the mask classes)."""
import datetime as dt
import http.server
import json
import os
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import numpy as np

from microclimate import city, sunclear, wetness
from microclimate.live import build, config as C, dsr, forcing
from tests_mc.test_goes_box import fake_lowcloud
from tests_mc.test_sun_clear import lattice_stub

NOW = dt.datetime(2026, 11, 12, 23, 30, tzinfo=dt.timezone.utc)


class Stop(Exception):
    pass


class TestSettings(unittest.TestCase):
    def setUp(self):
        self.saved = (dict(C.BETA), C.PHYSICS, C.NUDGE_ANCHORS, C.CARRY_STATE)

    def tearDown(self):
        C.BETA.clear()
        C.BETA.update(self.saved[0])
        C.PHYSICS, C.NUDGE_ANCHORS, C.CARRY_STATE = self.saved[1:]

    def test_public_settings_are_unchanged_and_stage_1_is_the_beta(self):
        self.assertEqual((C.PHYSICS, C.NUDGE_ANCHORS, C.CARRY_STATE), ((), False, False))
        self.assertEqual(C.BETA, dict(physics=("sun_clear",), nudge_anchors=True, carry_state=False))     # stage 1 (2026-11-12)
        self.assertEqual(wetness.check_physics(C.BETA["physics"]), frozenset({"sun_clear"}))

    def test_beta_settings_never_reach_the_public_build(self):
        before = build.settings(False)
        C.BETA.update(physics=("film",), nudge_anchors=True, carry_state=True)
        self.assertEqual(build.settings(False), before)
        self.assertEqual(before, dict(physics=(), nudge_anchors=False, carry_state=False))

    def test_public_settings_never_reach_the_beta_build(self):
        C.PHYSICS, C.NUDGE_ANCHORS, C.CARRY_STATE = ("film",), True, True
        self.assertEqual(build.settings(True), dict(physics=("sun_clear",), nudge_anchors=True, carry_state=False))
        C.BETA.update(nudge_anchors=False)
        self.assertFalse(build.settings(True)["nudge_anchors"])
        self.assertEqual(build.settings(True, physics=("film",))["physics"], ("film",))          # an explicit argument still wins (tests, command line)

    def test_label_says_what_the_preview_has(self):
        self.assertEqual(build.beta_label(build.settings(True)), "clear-sky sun (GOES sunlight), station anchors")
        C.SUN_DSR_OLD, C.SUN_DSR = C.SUN_DSR, False
        try:
            self.assertEqual(build.beta_label(build.settings(True)), "clear-sky sun, station anchors")
        finally:
            C.SUN_DSR = C.SUN_DSR_OLD
            del C.SUN_DSR_OLD
        self.assertEqual(build.beta_label(dict(physics=(), nudge_anchors=False, carry_state=False)), "no changes")


class TestRunPlumbing(unittest.TestCase):
    """build.run up to the forcing, with every network and heavy step replaced: what the forcing is called with, and what is left behind."""

    def run_(self, beta, tmp, inputs=None, inputs_out=None):
        rec = {}

        def fake_forcing(rr, t0, n, now, x, y, obs, **kw):
            rec.update(physics=kw["physics"], nudge=C.NUDGE_ANCHORS, dsr=kw.get("dsr_hours"), now=now)
            raise Stop("stop after the settings are known")
        S = dict(x=np.zeros(3), y=np.zeros(3), z=np.zeros(3))
        calls = []
        with mock.patch.object(city, "load_static", lambda p: S), mock.patch.object(build.forcing, "build", fake_forcing), \
                mock.patch.object(build, "gather", lambda st, t0, now, cache: ({"model": "x"}, {}, None, {})), \
                mock.patch.object(build, "fetch_dsr", lambda *a, **k: (calls.append(1), ({}, {"daylight_hours": 0}))[1]):
            rc = build.run(Path(tmp) / ("beta" if beta else "pub"), static=Path(tmp) / "s.npz", now=NOW, beta=beta, inputs=inputs, inputs_out=inputs_out, log=lambda *a: None)
        rec["rc"], rec["dsr_calls"] = rc, len(calls)
        return rec

    def test_public_build_runs_with_the_public_settings(self):
        saved = dict(C.BETA)
        try:
            C.BETA.update(physics=("film",), nudge_anchors=True, carry_state=True)       # whatever the beta says
            with tempfile.TemporaryDirectory() as d:
                rec = self.run_(False, d)
        finally:
            C.BETA.clear()
            C.BETA.update(saved)
        self.assertEqual(rec["physics"], frozenset())
        self.assertFalse(rec["nudge"])
        self.assertIsNone(rec["dsr"])
        self.assertEqual(rec["dsr_calls"], 0)           # no GOES sunlight fetch without sun_clear
        self.assertEqual(rec["rc"], 2)

    def test_beta_build_runs_with_the_beta_settings_and_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as d:
            rec = self.run_(True, d)
        self.assertEqual(rec["physics"], frozenset({"sun_clear"}))
        self.assertTrue(rec["nudge"])
        self.assertEqual(rec["dsr"], {})
        self.assertEqual(rec["dsr_calls"], 1)
        self.assertFalse(C.NUDGE_ANCHORS)               # restored even though the forcing raised
        self.assertEqual(C.PHYSICS, ())

    def test_beta_reuses_the_public_inputs(self):
        rr, obs = {"model": "frozen", "arr": np.arange(4.0)}, {"coops": None, "stations": {"raw": {}}}
        st = build.Status()
        st.sources["goes"] = dict(hours=3)
        later = NOW + timedelta(minutes=40)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "in" / "inputs.pkl"
            self.assertGreater(build.save_inputs(p, NOW, build.snapshot_inputs(rr, obs), {}, st), 0)
            got = build.load_inputs(p)
            self.assertEqual(got["now"], NOW)
            np.testing.assert_array_equal(got["rr"]["arr"], rr["arr"])
            self.assertEqual(got["sources"]["goes"], dict(hours=3))
            with mock.patch.object(build, "gather", side_effect=AssertionError("must not fetch")), mock.patch.object(city, "load_static", lambda p_: dict(x=np.zeros(3))), \
                    mock.patch.object(build.forcing, "build", side_effect=Stop("stop")), mock.patch.object(build, "fetch_dsr", lambda *a, **k: ({}, {})), \
                    mock.patch.object(build.fetch, "utcnow", lambda: later):
                rc = build.run(Path(d) / "beta", static=Path(d) / "s.npz", beta=True, inputs=p, log=lambda *a: None)
            self.assertEqual(rc, 2)                       # it got as far as the forcing: no fetch happened (gather would have raised AssertionError)
            self.assertIsNone(build.load_inputs(Path(d) / "missing.pkl"))
            (Path(d) / "bad.pkl").write_bytes(b"not a pickle")
            self.assertIsNone(build.load_inputs(Path(d) / "bad.pkl"))

    def test_replayed_sector_function_gives_the_same_values(self):
        lc = fake_lowcloud(lambda lo, la: (la > 37.76, np.zeros(lo.shape, bool), np.ones(lo.shape, bool)), nr=60, nc=70)
        t = NOW.replace(minute=0)
        fn = build.replay_sector_fn({t: lc, t - timedelta(hours=1): None}, NOW)
        got = fn([t, t - timedelta(hours=1), t - timedelta(hours=100)], [250.0, 250.0, 250.0])
        self.assertAlmostEqual(got[0], lc.sector_fraction(250.0))
        self.assertEqual(got[1:], [None, None])


# ------------------------------------------------------------------ GOES downward shortwave
def make_dsr_file(path):
    """A small netCDF4-like file with the layout of ABI-L2-DSRF (full-disk grid, DSR uint16 in gzip chunks of 24 full-width rows, DQF, t, projection), noise in the rows
    around San Francisco. -> (raw counts array of the noisy rows, first row, nav)."""
    import h5py
    from microclimate.live.goes import Nav
    n = 5424
    sf, add = 5.6e-5, 0.151844
    rng = np.random.default_rng(7)
    with h5py.File(path, "w") as h:
        pr = h.create_dataset("goes_imager_projection", data=np.int32(0))
        pr.attrs.update(perspective_point_height=np.array([35786023.0]), semi_major_axis=np.array([6378137.0]), semi_minor_axis=np.array([6356752.31414]),
                        longitude_of_projection_origin=np.array([-137.0]))
        x = h.create_dataset("x", data=np.arange(n, dtype=np.int16))
        x.attrs.update(scale_factor=np.array([sf], np.float32), add_offset=np.array([-add], np.float32))
        y = h.create_dataset("y", data=np.arange(n, dtype=np.int16))
        y.attrs.update(scale_factor=np.array([-sf], np.float32), add_offset=np.array([add], np.float32))
        h.create_dataset("t", data=np.float64(NOW.timestamp() - dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc).timestamp()))
        d = h.create_dataset("DSR", shape=(n, n), dtype=np.uint16, chunks=(24, n), compression="gzip", fillvalue=65535)
        d.attrs.update(scale_factor=np.array([0.02289028], np.float32), add_offset=np.array([0.0], np.float32), _FillValue=np.array([65535], np.uint16), units=np.bytes_(b"W m-2"))
        q = h.create_dataset("DQF", shape=(n, n), dtype=np.uint8, chunks=(24, n), compression="gzip", fillvalue=255)
        h.attrs["spatial_resolution"] = np.bytes_(b"2.0km at nadir")
        h.attrs["scene_id"] = np.bytes_(b"Full Disk")
        nav = Nav(pr)
        ax = x[:].astype(np.float64) * sf + (-add)
        ay = y[:].astype(np.float64) * (-sf) + add
        gx, gy = nav.xy(np.array([-122.45]), np.array([37.76]))
        r0 = int(np.argmin(np.abs(ay - gy[0]))) - 72
        r0 -= r0 % 24
        raw = rng.integers(3000, 25000, size=(144, n)).astype(np.uint16)
        d[r0:r0 + 144, :] = raw
        q[r0:r0 + 144, :] = (rng.random((144, n)) < 0.2).astype(np.uint8)
        return raw, r0, nav, ax, ay


class RangeHandler(http.server.BaseHTTPRequestHandler):
    root = None
    served = []

    def log_message(self, *a):
        pass

    def _file(self):
        return (Path(self.root) / self.path.lstrip("/")).read_bytes()

    def do_HEAD(self):
        data = self._file()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()

    def do_GET(self):
        data = self._file()
        rng = self.headers.get("Range")
        if rng:
            a, b = (int(v) for v in rng.split("=")[1].split("-"))
            part = data[a:b + 1]
            self.send_response(206)
        else:
            part = data
            self.send_response(200)
        RangeHandler.served.append(len(part))
        self.send_header("Content-Length", str(len(part)))
        self.end_headers()
        self.wfile.write(part)


class TestRangedRead(unittest.TestCase):
    def test_city_box_read_with_range_requests(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "ABI-L2-DSRF" / "2026" / "316" / "23"
            sub.mkdir(parents=True)
            raw, r0, nav, ax, ay = make_dsr_file(sub / "OR_ABI-L2-DSRF-M6_G18_s20263162300208_e1_c1.nc")
            size = (sub / "OR_ABI-L2-DSRF-M6_G18_s20263162300208_e1_c1.nc").stat().st_size
            RangeHandler.root, RangeHandler.served = d, []
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            old_env = os.environ.get("NO_PROXY")
            os.environ["NO_PROXY"] = "127.0.0.1"
            try:
                with mock.patch.object(C, "GOES_BUCKET", f"http://127.0.0.1:{srv.server_port}/"):
                    sc = dsr.read_scan("ABI-L2-DSRF/2026/316/23/OR_ABI-L2-DSRF-M6_G18_s20263162300208_e1_c1.nc")
            finally:
                srv.shutdown()
                if old_env is None:
                    os.environ.pop("NO_PROXY", None)
                else:
                    os.environ["NO_PROXY"] = old_env
        self.assertLess(sc["bytes"], 0.5 * size)                        # a part of the file, not the file
        self.assertEqual(sc["attrs"]["units"], "W m-2")
        self.assertEqual(sc["t"], NOW.replace(microsecond=0))
        # every pixel of the subset carries the value written for that pixel (found again through the navigation)
        gx, gy = nav.xy(sc["lon"].astype(float), sc["lat"].astype(float))
        col = np.rint(np.interp(gx, ax, np.arange(len(ax)))).astype(int)
        row = np.rint(np.interp(gy, ay[::-1], np.arange(len(ay))[::-1])).astype(int)
        self.assertTrue(((row - r0 >= 0) & (row - r0 < raw.shape[0])).all())
        np.testing.assert_allclose(sc["dsr"], raw[row - r0, col] * 0.02289028, atol=0.05)
        self.assertGreater(sc["dsr"].size, 100)
        self.assertTrue(np.isfinite(sc["dsr"]).all())
        self.assertTrue(set(np.unique(sc["dqf"])) <= {0, 1})
        self.assertTrue(((sc["lon"] >= C.DSR_BOX[0] - 0.2) & (sc["lon"] <= C.DSR_BOX[2] + 0.2)).all())       # the box plus two pixels of padding


def grid_dsr(t, value, nr=8, nc=8, qual=0):
    lon, lat = np.meshgrid(np.linspace(-122.60, -122.30, nc), np.linspace(37.65, 37.90, nr))
    return dsr.Dsr(t, lon, lat, np.full(lon.shape, value, np.float32), np.full(lon.shape, qual, np.uint8))


class TestDsrCache(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.hours = [dt.datetime(2026, 11, 12, h, tzinfo=dt.timezone.utc) for h in (18, 19, 20, 21, 22, 23)]
        self.reads = []

    def fake_scan(self, key, box=None, block=None):
        self.reads.append(key)
        h = int(key[-2:])
        d = grid_dsr(dt.datetime(2026, 11, 12, h, 5, tzinfo=dt.timezone.utc), 100.0 + h)
        return dict(t=d.t, lon=d.lon, lat=d.lat, dsr=d.dsr, dqf=d.dqf, bytes=1000, requests=3, attrs=dict(units="W m-2"))

    def fake_nearest(self, h):
        return "ABI-L2-DSRF", f"x/{h:%H}", h

    def test_only_new_hours_are_fetched_and_the_cache_is_small(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(dsr, "read_scan", self.fake_scan), mock.patch.object(dsr, "nearest_scan", self.fake_nearest):
            p = Path(d) / C.DSR_CACHE_FILE
            res, s1 = dsr.hourly(self.hours, p, self.now, log=lambda m: None)
            self.assertEqual((s1["fetched"], s1["cached"], len(res)), (6, 0, 6))
            self.assertEqual(s1["bytes"], 6000)
            self.assertLess(p.stat().st_size, 20000)
            np.testing.assert_allclose(res[self.hours[2]].dsr, 120.0)
            self.reads.clear()
            res2, s2 = dsr.hourly(self.hours, p, self.now + timedelta(minutes=30), log=lambda m: None)
            self.assertEqual(self.reads, [])                      # same scans: nothing downloaded, the older hours are final, the newest ones only looked up
            self.assertGreaterEqual(s2["cached"], 6)
            later = self.now + timedelta(hours=1)
            res3, s3 = dsr.hourly(self.hours + [self.hours[-1] + timedelta(hours=1)], p, later, log=lambda m: None)
            self.assertEqual(self.reads, ["x/00"])                # exactly the one new hour
            self.assertEqual(len(res3), 7)

    def test_failures_and_night_hours_mean_no_dsr_for_that_hour(self):
        def boom(key, box=None, block=None):
            raise OSError("network")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(dsr, "read_scan", boom), mock.patch.object(dsr, "nearest_scan", self.fake_nearest):
            res, s = dsr.hourly(self.hours[:2], Path(d) / "c.json", self.now, log=lambda m: None)
            self.assertEqual((res, s["failed"]), ({}, 2))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(dsr, "nearest_scan", lambda h: None):
            res, s = dsr.hourly(self.hours[:2], Path(d) / "c.json", self.now, log=lambda m: None)
            self.assertEqual((res, s["no_scan"]), ({}, 2))

    def test_a_scan_too_far_from_the_hour_is_not_used(self):
        far = lambda h: ("ABI-L2-DSRF", "x/00", h + timedelta(minutes=C.DSR_MAX_SCAN_OFFSET_MIN + 5))  # noqa: E731
        with tempfile.TemporaryDirectory() as d, mock.patch.object(dsr, "nearest_scan", far):
            res, s = dsr.hourly(self.hours[:1], Path(d) / "c.json", self.now, log=lambda m: None)
            self.assertEqual((res, s["no_scan"]), ({}, 1))

    def test_unreadable_or_foreign_cache_is_an_empty_cache(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text("{not json")
            self.assertEqual(dsr.load_cache(p)["hours"], {})
            p.write_text(json.dumps(dict(schema=1, hours={"a": 1})))
            self.assertEqual(dsr.load_cache(p)["hours"], {})

    def test_another_pixel_grid_drops_the_old_hours(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(dsr, "nearest_scan", self.fake_nearest):
            p = Path(d) / "c.json"
            with mock.patch.object(dsr, "read_scan", self.fake_scan):
                dsr.hourly(self.hours[:2], p, self.now, log=lambda m: None)

            def other(key, box=None, block=None):
                sc = self.fake_scan(key)
                return dict(sc, lon=sc["lon"][:4], lat=sc["lat"][:4], dsr=sc["dsr"][:4], dqf=sc["dqf"][:4])
            with mock.patch.object(dsr, "read_scan", other):
                res, s = dsr.hourly([self.hours[2]], p, self.now, log=lambda m: None)
            c = dsr.load_cache(p)
            self.assertEqual(list(c["hours"]), [self.hours[2].isoformat()])
            self.assertEqual(c["grid"]["shape"], [4, 8])


class TestDsrCells(unittest.TestCase):
    def test_cell_means_and_quality_preference(self):
        d = grid_dsr(NOW, 300.0)
        d.dsr[:, :] = np.where(d.lon > -122.45, 500.0, 300.0)
        d.dqf[:, :] = 1
        v = d.cells(np.array([37.78, 37.78]), np.array([-122.55, -122.35]), 0.0325, 0.034)
        np.testing.assert_allclose(v, [300.0, 500.0])                     # degraded pixels are used when the box has no good ones
        d.dqf[0, 0] = 0
        d.dsr[0, 0] = 111.0
        v = d.cells(np.array([37.65]), np.array([-122.60]), 0.0325, 0.034)
        np.testing.assert_allclose(v, [111.0])                            # a good pixel in the box wins over the degraded ones
        d.dsr[3, 3] = np.nan
        self.assertTrue(np.isfinite(d.city_mean()))
        self.assertTrue(np.isnan(d.cells(np.array([40.0]), np.array([-120.0]), 0.03, 0.03)[0]))

    def test_k_from_dsr_uses_the_clear_sky_of_the_scan_time(self):
        t = dt.datetime(2026, 6, 21, 19, 5, tzinfo=dt.timezone.utc)
        ghi, _, _ = sunclear.clear_sky([t], wetness.SUN_LAT, wetness.SUN_LON)
        d = grid_dsr(t, 0.4 * float(ghi[0]))
        k = dsr.k_cells(d, float(ghi[0]), np.array([37.78]), np.array([-122.45]), 0.0325, 0.034)
        np.testing.assert_allclose(k, [0.4], atol=1e-4)
        self.assertTrue(np.isnan(dsr.k_cells(d, 10.0, np.array([37.78]), np.array([-122.45]), 0.0325, 0.034)[0]))      # low sun: no k
        self.assertEqual(dsr.k_cells(grid_dsr(t, 2.0 * float(ghi[0])), float(ghi[0]), np.array([37.78]), np.array([-122.45]), 0.0325, 0.034)[0], 1.2)


class TestCloudinessWithDsr(unittest.TestCase):
    def setUp(self):
        self.t0 = dt.datetime(2026, 6, 21, 6, tzinfo=dt.timezone.utc)
        self.n = 6 * 30 + 1
        self.t_utc = [self.t0 + timedelta(minutes=10 * i) for i in range(self.n)]
        self.g = np.arange(self.n) / 6.0
        self.t_h = [self.t0 + timedelta(hours=h) for h in range(31)]
        self.ghi, _, _ = sunclear.clear_sky(self.t_utc, wetness.SUN_LAT, wetness.SUN_LON)
        self.lat = lattice_stub(5)
        self.lat.lat, self.lat.lon = np.full(5, 37.76), np.full(5, -122.45)
        self.SW = np.vstack([0.3 * self.ghi] * 5)
        lc = fake_lowcloud(lambda lo, la: (np.zeros(lo.shape, bool), np.zeros(lo.shape, bool), np.ones(lo.shape, bool)), nr=60, nc=70)
        lc.acm = np.zeros(lc.low.shape, np.uint8)                          # the mask says clear (k 1.0) in hours 0..12
        self.goes = {self.t_h[h]: lc for h in range(0, 13)}

    def go(self, dsr_hours):
        info = dict(flags=[], physics=["sun_clear"])
        K, out = forcing.cloudiness(self.lat, self.SW, self.g, self.t_h, 12.0, self.t_utc, self.goes, info, dsr_hours=dsr_hours)
        return K, out, info

    def test_dsr_is_preferred_and_the_mask_fills_the_other_hours(self):
        h = 10
        scan = self.t_h[h] + timedelta(minutes=5)
        ghi_s, _, _ = sunclear.clear_sky([scan], wetness.SUN_LAT, wetness.SUN_LON)
        K, out, info = self.go({self.t_h[h]: grid_dsr(scan, 0.5 * float(ghi_s[0]))})
        np.testing.assert_allclose(out["k_dsr_hourly"][:, h], 0.5, atol=1e-4)
        np.testing.assert_allclose(out["k_sat_hourly"][:, h], 0.5, atol=1e-4)      # DSR wins over the mask's 1.0 ...
        np.testing.assert_allclose(out["k_sat_hourly"][:, 9], 1.0)                 # ... and the mask is used where there is no DSR
        np.testing.assert_allclose(out["k_mask_hourly"][:, h], 1.0)
        self.assertEqual((info["sun_k"]["dsr_hours"], info["sun_k"]["mask_hours"]), (1, 13))
        row = [r for r in info["sun_k"]["table"] if r["hour"] == self.t_h[h].isoformat()][0]
        self.assertAlmostEqual(row["k_dsr"], 0.5, places=3)
        self.assertEqual(row["k_mask"], 1.0)
        self.assertAlmostEqual(row["k_model"], 0.3, places=3)
        i = h * 6
        self.assertAlmostEqual(float(K[0, i]), 0.3 + C.K_SAT_WEIGHT * (0.5 - 0.3), places=3)    # the model k moves halfway to the DSR value

    def test_switch_off_or_no_dsr_gives_the_mask_result(self):
        scan = self.t_h[10]
        ghi_s, _, _ = sunclear.clear_sky([scan], wetness.SUN_LAT, wetness.SUN_LON)
        Kn, _, _ = self.go(None)
        Ke, _, _ = self.go({})
        old = C.SUN_DSR
        C.SUN_DSR = False
        try:
            Ko, out, _ = self.go({self.t_h[10]: grid_dsr(scan, 0.5 * float(ghi_s[0]))})
        finally:
            C.SUN_DSR = old
        np.testing.assert_array_equal(Kn, Ke)
        np.testing.assert_array_equal(Kn, Ko)
        self.assertTrue(np.isnan(out["k_dsr_hourly"]).all())

    def test_future_hours_are_not_taken_from_dsr(self):
        h = 14                                                           # after now_h = 12
        scan = self.t_h[h]
        ghi_s, _, _ = sunclear.clear_sky([scan], wetness.SUN_LAT, wetness.SUN_LON)
        _, out, _ = self.go({self.t_h[h]: grid_dsr(scan, 0.5 * float(ghi_s[0]))})
        self.assertTrue(np.isnan(out["k_dsr_hourly"][:, h]).all())


if __name__ == "__main__":
    unittest.main()
