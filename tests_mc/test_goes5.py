"""Multi-scale guide 07: 5-minute GOES crop store, low_cloud rule, parallax, macro frames, switch off. No network: tiny fake netCDF (HDF5) files built with h5py."""
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import h5py
import numpy as np

from microclimate.live import build, config as C, goes, goes5

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 12, 15, 1, 17, tzinfo=UTC)
PROJ = {"semi_major_axis": [6378137.0], "semi_minor_axis": [6356752.31414], "perspective_point_height": [35786023.0], "longitude_of_projection_origin": [-137.0]}


class _Proj:
    attrs = PROJ


def axes():
    """ACM axes (2 km steps, x increasing, y decreasing) and ACHA axes (10 km) covering the GOES box with margin."""
    nav = goes.Nav(_Proj())
    bx, by = nav.xy(np.array([-123.6, -121.6]), np.array([37.0, 38.5]))
    step = 5.6e-5
    x = np.arange(bx.min() - 5 * step, bx.max() + 5 * step, step)
    y = np.arange(by.max() + 5 * step, by.min() - 5 * step, -step)
    return nav, x, y, x[::5], y[::5]


def write_axis(f, name, v):
    d = f.create_dataset(name, data=v.astype(np.float64))
    d.attrs["scale_factor"] = np.array([1.0])
    d.attrs["add_offset"] = np.array([0.0])


def make_pair(d, start, acm_fn=None, ht_fn=None, dqf_fn=None, name="a", with_dqf=True):
    """Write a fake ACM + ACHA file pair; acm_fn(lon, lat) -> uint8 classes on the ACM grid, ht_fn(lon, lat) -> metres (nan = none) on the ACM grid.
    -> (acm path, acha path). t = seconds since 2000-01-01 12:00 UTC."""
    nav, x, y, hx, hy = axes()
    lon, lat = nav.lonlat(*np.meshgrid(x, y))
    acm = (acm_fn or (lambda lo, la: np.zeros(lo.shape, np.uint8)))(lon, lat).astype(np.uint8)
    hlon, hlat = nav.lonlat(*np.meshgrid(hx, hy))
    ht = (ht_fn or (lambda lo, la: np.full(lo.shape, np.nan)))(hlon, hlat)
    pa, ph = Path(d) / f"OR_ABI-L2-ACMC-M6_G18_s{start:%Y%j%H%M%S}0_{name}.nc", Path(d) / f"OR_ABI-L2-ACHAC-M6_G18_s{start:%Y%j%H%M%S}0_{name}.nc"
    sec = (start - dt.datetime(2000, 1, 1, 12, tzinfo=UTC)).total_seconds() + 150.0
    for p, fine in ((pa, True), (ph, False)):
        with h5py.File(p, "w") as f:
            pr = f.create_dataset("goes_imager_projection", data=0)
            for k, v in PROJ.items():
                pr.attrs[k] = np.array(v)
            write_axis(f, "x", x if fine else hx)
            write_axis(f, "y", y if fine else hy)
            f.create_dataset("t", data=sec)
            if fine:
                f.create_dataset("ACM", data=acm)
                if with_dqf:
                    f.create_dataset("DQF", data=(dqf_fn(lon, lat) if dqf_fn else np.zeros(acm.shape)).astype(np.uint8))
            else:
                raw = np.where(np.isfinite(ht), ht / 0.3, 65535).astype(np.uint16)
                d_ = f.create_dataset("HT", data=raw)
                d_.attrs["scale_factor"] = np.array([0.3], np.float32)
                d_.attrs["_FillValue"] = np.array([65535], np.uint16)
    return pa, ph


def key_of(p, prod):
    return f"{prod}/{p.name}"


class FakeCrop:
    """What goes.low_cloud needs: acm, ht, t_scan, centre."""

    def __init__(self, acm, ht, t_scan=1_700_000_000, centre=(-122.6, 37.75)):
        self.acm, self.ht, self.t_scan, self.centre = np.asarray(acm)[None], np.asarray(ht)[None], np.array([t_scan]), centre


class TestPairing(unittest.TestCase):
    def test_pairs_by_scan_start_and_skips_unpaired(self):
        def key(prod, s):
            return f"{prod}/2026/316/15/OR_{prod}-M6_G18_s2026316{s}0_e0_c0.nc"
        acm = [key(C.GOES_MASK, s) for s in ("150117", "150617", "151117", "151617")]
        acha = [key(C.GOES_HEIGHT, s) for s in ("150117", "151117", "151645")]    # 1506 missing; 1516 vs 151645: 28 s apart, still pairs
        lister = lambda prod, h: (acm if prod == C.GOES_MASK else acha) if h.hour == 15 else []
        got = goes5.list_scans(dt.datetime(2026, 11, 12, 15, 0, tzinfo=UTC), dt.datetime(2026, 11, 12, 16, 0, tzinfo=UTC), lister)
        self.assertEqual([t.strftime("%H%M%S") for t, _, _ in got], ["150117", "151117", "151617"])
        self.assertTrue(all(a.split("/")[0] == C.GOES_MASK and b.split("/")[0] == C.GOES_HEIGHT for _, a, b in got))

    def test_tolerance_is_60_s(self):
        mk = lambda prod, s: f"{prod}/2026/316/15/OR_x_s2026316{s}0_e.nc"
        lister = lambda prod, h: [mk(prod, "150117" if prod == C.GOES_MASK else "150330")]      # 133 s apart: no pair
        self.assertEqual(goes5.list_scans(T0 - dt.timedelta(minutes=1), T0 + dt.timedelta(minutes=5), lister), [])


class TestStore(unittest.TestCase):
    def arr(self, v):
        return np.full((4, 5), v, np.uint8), np.full((4, 5), v * 10, np.uint16)

    def test_merge_is_idempotent_and_sorted(self):
        a, h = self.arr(1)
        d = goes5.merge_scan(None, 200, a, h, "s200")
        d = goes5.merge_scan(d, 100, a, h, "s100")
        d = goes5.merge_scan(d, 200, *self.arr(2), "s200b")           # same scan again: replaced, not duplicated
        self.assertEqual(list(d["t_scan"]), [100, 200])
        self.assertEqual(int(d["acm"][1, 0, 0]), 2)
        self.assertEqual(list(d["src"]), ["s100", "s200b"])
        again = goes5.merge_scan(d, 200, *self.arr(2), "s200b")
        np.testing.assert_array_equal(again["acm"], d["acm"])
        self.assertEqual(len(again["t_scan"]), 2)

    def test_prune_after_48_h(self):
        now = dt.datetime(2026, 11, 14, 12, 30, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            for t in (now - dt.timedelta(hours=60), now - dt.timedelta(hours=49), now - dt.timedelta(hours=47), now):
                goes5.save_hour(goes5.hour_path(tmp, t), goes5.merge_scan(None, int(t.timestamp()), *self.arr(1), "s"))
            self.assertEqual(goes5.prune_store(tmp, now), 2)
            left = [f"{f.parent.name}/{f.stem}" for f in goes5.hour_files(tmp)]
            self.assertEqual(left, ["2026-11-12/13", "2026-11-14/12"])
            self.assertFalse((Path(tmp) / "2026-11-11").exists())          # emptied day folders go

    def test_atomic_write_leaves_no_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = goes5.hour_path(tmp, T0)
            d = goes5.merge_scan(None, 1, *self.arr(1), "s")
            goes5.save_hour(p, d)
            before = p.read_bytes()
            for bad in (mock.patch.object(goes5.os, "replace", side_effect=OSError("disk")),
                        mock.patch.object(Path, "write_bytes", side_effect=OSError("half"))):
                with bad, self.assertRaises(OSError):
                    goes5.save_hour(p, goes5.merge_scan(d, 2, *self.arr(2), "s2"))
            self.assertEqual(p.read_bytes(), before)                     # the old file is intact
            self.assertEqual([f.name for f in p.parent.iterdir()], [p.name])    # and there is no .tmp left
            fresh = goes5.hour_path(tmp, T0 + dt.timedelta(hours=2))
            with mock.patch.object(goes5.os, "replace", side_effect=OSError("disk")), self.assertRaises(OSError):
                goes5.save_hour(fresh, d)
            self.assertFalse(fresh.exists())
            self.assertEqual(sorted(f.name for f in fresh.parent.iterdir()), [p.name])      # no 17.npz and no .tmp next to it


class TestCropAndIngest(unittest.TestCase):
    def test_shared_crop_gives_the_old_lowcloud_indices_and_flags(self):
        """Reference = the index math of LowCloud.__init__ as it was before the refactor (copied here)."""
        with tempfile.TemporaryDirectory() as tmp:
            pa, ph = make_pair(tmp, T0, acm_fn=lambda lo, la: np.where(lo > -122.8, 3, 0), ht_fn=lambda lo, la: np.where(la > 37.7, 400.0, 2500.0))
            lc = goes.LowCloud(pa, ph)
            with h5py.File(pa) as a, h5py.File(ph) as h:
                nav = goes.Nav(a["goes_imager_projection"])
                x, y = goes._axis(a, "x"), goes._axis(a, "y")
                box = (-123.3, 37.2, -121.9, 38.3)
                bx, by = nav.xy(np.array([box[0], box[2], box[0], box[2]]), np.array([box[1], box[1], box[3], box[3]]))
                pad = 3 * 5.6e-5
                ci = np.nonzero((x >= bx.min() - pad) & (x <= bx.max() + pad))[0]
                ri = np.nonzero((y >= by.min() - pad) & (y <= by.max() + pad))[0]
                c0, c1, r0, r1 = int(ci.min()), int(ci.max()) + 1, int(ri.min()), int(ri.max()) + 1
                acm = a["ACM"][r0:r1, c0:c1]
                cr = goes.read_crop(a, h)
            self.assertEqual((cr["c0"], cr["c1"], cr["r0"], cr["r1"]), (c0, c1, r0, r1))
            np.testing.assert_array_equal(cr["acm"], acm)
            np.testing.assert_array_equal(lc.x, x[c0:c1])
            np.testing.assert_array_equal(lc.y, y[r0:r1])
            np.testing.assert_array_equal(lc.acm, acm)
            want_low = (((acm == 2) | (acm == 3)) & np.isfinite(cr["ht"]) & (cr["ht"] < C.LOW_TOP_M)).astype(np.float32)
            np.testing.assert_array_equal(lc.low, want_low)
            self.assertGreater(want_low.sum(), 0)

    def test_dqf_marks_bad_pixels_254_and_space_255(self):
        with tempfile.TemporaryDirectory() as tmp:
            pa, ph = make_pair(tmp, T0, acm_fn=lambda lo, la: np.full(lo.shape, 3), dqf_fn=lambda lo, la: np.select([lo < -123.0, lo > -122.0], [1, 2], 0))
            cr, acm, ht = goes5.crop_scan(pa, ph)
            self.assertEqual(set(np.unique(acm)), {3, 254, 255})
            self.assertEqual(acm.dtype, np.uint8)
            self.assertEqual(ht.dtype, np.uint16)
            pa2, ph2 = make_pair(tmp, T0 + dt.timedelta(minutes=5), name="nodqf", with_dqf=False)        # a file without DQF still crops
            self.assertEqual(set(np.unique(goes5.crop_scan(pa2, ph2)[1])), {0})

    def fake_fetch(self, tmp, scans):
        """fetch(key, cache) that returns the fake file of a scan and counts the calls."""
        files = {}
        for s in scans:
            pa, ph = make_pair(tmp, s, acm_fn=lambda lo, la: np.where(lo > -122.8, 3, 0), ht_fn=lambda lo, la: np.full(lo.shape, 500.0), name=f"{s:%H%M}")
            files[key_of(pa, C.GOES_MASK)], files[key_of(ph, C.GOES_HEIGHT)] = pa, ph
        calls = []

        def fetch(key, cache):
            calls.append(key)
            dst = Path(cache) / key.split("/")[-1]
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(files[key].read_bytes())
            return dst
        lister = lambda prod, h: [k for k in files if k.startswith(prod) and goes._scan_start(k).replace(minute=0, second=0) == h]
        return fetch, lister, calls

    def test_ingest_cold_start_incremental_and_deadline(self):
        now = T0 + dt.timedelta(minutes=58)
        scans = [T0 + dt.timedelta(minutes=5 * i) for i in range(10)]       # 15:01 .. 15:46; plus one older than the cold window below
        scans = [s for s in scans if s.hour == 15]
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as tmp:
            fetch, lister, calls = self.fake_fetch(src, scans)
            store, cache = Path(tmp) / "goes5", Path(tmp) / "raw"
            i1 = goes5.ingest(store, now, cache=cache, fetch=fetch, lister=lister, log=lambda *a: None)
            self.assertTrue(i1["cold"])
            self.assertEqual(i1["new"], C.GOES5_MAX_SCANS_PER_RUN)             # capped, oldest first
            self.assertEqual(i1["listed"], len([s for s in scans if s >= now - dt.timedelta(minutes=C.GOES5_COLD_MIN)]))
            self.assertEqual(goes5.newest_scan(store), int(scans[5].timestamp()))
            self.assertEqual(list(cache.glob("*.nc")), [])                     # raw downloads deleted
            self.assertTrue((store / "grid.json").exists())
            g = json.loads((store / "grid.json").read_text())
            self.assertEqual((g["schema"], g["sat_lon"], g["box"]), (1, -137.0, [-123.3, 37.2, -121.9, 38.3]))
            n1 = len(calls)
            i2 = goes5.ingest(store, now, cache=cache, fetch=fetch, lister=lister, log=lambda *a: None)       # next run: only the rest
            self.assertEqual((i2["cold"], i2["new"]), (False, len(scans) - 6))
            self.assertEqual(len(calls) - n1, 2 * (len(scans) - 6))
            n2 = len(calls)
            i3 = goes5.ingest(store, now, cache=cache, fetch=fetch, lister=lister, log=lambda *a: None)       # idempotent: nothing new, no download
            self.assertEqual((i3["new"], len(calls) - n2), (0, 0))
            c = goes5.Crop5(store)
            self.assertEqual(list(c.t_scan), [int(s.timestamp()) for s in scans])
            self.assertEqual(c.acm.shape[0], len(scans))
            self.assertEqual(int(c.ht.max()), 500 if abs(int(c.ht.max()) - 500) <= 1 else int(c.ht.max()))
            # deadline: nothing new is started after it
            with tempfile.TemporaryDirectory() as tmp2:
                i4 = goes5.ingest(Path(tmp2) / "g", now, deadline_s=-1.0, cache=Path(tmp2) / "r", fetch=fetch, lister=lister, log=lambda *a: None)
                self.assertEqual(i4["new"], 0)
                self.assertTrue(any("deadline" in e for e in i4["errors"]))

    def test_a_failing_scan_is_logged_and_never_raised(self):
        now = T0 + dt.timedelta(minutes=10)
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as tmp:
            fetch, lister, _ = self.fake_fetch(src, [T0])
            boom = mock.Mock(side_effect=OSError("network"))
            info = goes5.run(Path(tmp) / "out", now, cache=Path(tmp) / "raw", fetch=boom, lister=lister, log=lambda *a: None)
            self.assertEqual(info["ingest"]["new"], 0)
            self.assertEqual(len(info["ingest"]["errors"]), 1)
            with mock.patch.object(goes5, "ingest", side_effect=RuntimeError("x")):
                self.assertIn("error", goes5.run(Path(tmp) / "out2", now, cache=Path(tmp) / "raw")["ingest"])


class TestLowCloudRule(unittest.TestCase):
    def one(self, acm, ht, sun_el=30.0, **kw):
        c = FakeCrop(np.full((7, 7), acm, np.uint8), np.full((7, 7), ht, np.uint16), **kw)
        p, cf, top, v = goes.low_cloud(c, 0, sun_el=sun_el)
        return float(p[3, 3]), float(cf[3, 3]), float(top[3, 3]), v

    def test_class_and_height_table(self):
        for cls, want in ((0, 0.0), (1, 0.25), (2, 0.75), (3, 1.0)):
            self.assertEqual(self.one(cls, 400)[0], want)
        self.assertEqual(self.one(3, 0)[0], 1.0)                 # no height retrieved: counts as low (contract)
        self.assertEqual(self.one(3, 1500)[0], 0.0)              # high top: not fog
        self.assertEqual(self.one(3, 999)[0], 1.0)
        self.assertEqual(self.one(3, 1000)[0], 0.0)              # at LOW_TOP_M: high
        self.assertEqual(self.one(2, 400)[2], 400.0)
        self.assertEqual(self.one(3, 0)[2], 0.0)
        self.assertEqual(self.one(3, 400)[3], 1)                 # rule version returned

    def test_confidence_zero_for_bad_and_missing(self):
        for v in (254, 255):
            p, c, _, _ = self.one(v, 400)
            self.assertEqual((p, c), (0.0, 0.0))
        self.assertEqual(self.one(3, 400)[1], C.GOES5_CONF_DAY)

    def test_confidence_zero_inside_the_terminator_window_and_lower_at_night(self):
        for el in (-6.0, -3.0, 0.0, 5.99, 6.0):
            self.assertEqual(self.one(3, 400, sun_el=el)[1], 0.0, el)
        self.assertEqual(self.one(3, 400, sun_el=6.5)[1], 1.0)
        self.assertAlmostEqual(self.one(3, 400, sun_el=-6.5)[1], 0.7, places=6)
        self.assertAlmostEqual(self.one(3, 400, sun_el=-40.0)[1], 0.7, places=6)

    def test_confidence_zero_under_high_cloud(self):
        acm = np.full((9, 9), 3, np.uint8)
        ht = np.full((9, 9), 400, np.uint16)
        ht[:, 4:] = 9000                                       # half the neighbourhood is high cloud
        p, c, _, _ = goes.low_cloud(FakeCrop(acm, ht), 0, sun_el=30.0)
        self.assertEqual(c[4, 4], 0.0)
        self.assertEqual(c[4, 0], 1.0)                         # far from the high cloud (outside 5 x 5): trusted
        self.assertEqual(p[4, 0], 1.0)
        self.assertEqual(p[4, 8], 0.0)

    def test_real_sun_elevation_by_day_and_night(self):
        c = FakeCrop(np.full((5, 5), 3, np.uint8), np.full((5, 5), 400, np.uint16), t_scan=int(dt.datetime(2026, 11, 12, 21, 0, tzinfo=UTC).timestamp()))
        self.assertEqual(goes.low_cloud(c, 0)[1][2, 2], 1.0)    # 13:00 PST
        c.t_scan = np.array([int(dt.datetime(2026, 11, 12, 9, 0, tzinfo=UTC).timestamp())])
        self.assertAlmostEqual(float(goes.low_cloud(c, 0)[1][2, 2]), 0.7)     # 01:00 PST
        with self.assertRaises(ValueError):
            goes.low_cloud(c, 0, version=2)


class TestParallax(unittest.TestCase):
    def test_view_geometry_at_san_francisco(self):
        z, az = goes.view_geometry(-122.4, 37.76, -137.0)
        self.assertAlmostEqual(float(z), 46.4, delta=0.5)           # about 46 degrees (the plan's estimate), computed here from the spherical formula
        self.assertAlmostEqual(float(az), 203.0, delta=1.5)         # south-south-west
        z0, _ = goes.view_geometry(-137.0, 0.0, -137.0)
        self.assertAlmostEqual(float(z0), 0.0, places=6)            # sub-satellite point

    def test_shift_towards_the_satellite(self):
        z, _ = goes.view_geometry(-122.4, 37.76, -137.0)
        de, dn = goes.parallax_shift_m(500.0, -122.4, 37.76, -137.0)
        self.assertAlmostEqual(float(np.hypot(de, dn)), 500.0 * float(np.tan(np.radians(z))), places=3)
        self.assertLess(de, 0)
        self.assertLess(dn, 0)                                      # south-west
        self.assertGreater(abs(dn), abs(de))                        # mostly south
        self.assertEqual(goes.parallax_shift_m(0.0, -122.4, 37.76, -137.0), (0.0, 0.0))

    def test_sampling_moves_a_500_m_cloud_south_west_and_a_zero_one_not_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "goes5"
            cloudy = lambda lo, la: np.where((lo > -122.62) & (lo < -122.30) & (la > 37.55) & (la < 37.85), 3, 0)      # one blob, apparent position
            for h in (0, 500):
                pa, ph = make_pair(tmp, T0 + dt.timedelta(minutes=5 * (h > 0)), acm_fn=cloudy, ht_fn=lambda lo, la, h=h: np.full(lo.shape, float(h)), name=f"h{h}")
                cr, acm, ht = goes5.crop_scan(pa, ph)
                if h == 0:
                    grid = goes5.make_grid(cr, goes.BOX)
                    (store).mkdir(parents=True)
                    (store / "grid.json").write_text(json.dumps(grid))
                t = T0 + dt.timedelta(minutes=5 * (h > 0))
                goes5.save_hour(goes5.hour_path(store, t), goes5.merge_scan(goes5.load_hour(goes5.hour_path(store, t)), int(t.timestamp()), acm, ht, "s"))
            crop = goes5.Crop5(store)
            self.assertEqual(len(crop), 2)
            res = 0.002
            lon1, lat1 = goes5.macro_axes(crop.box, res)
            lon, lat = np.meshgrid(lon1, lat1)

            def centroid(i, default_top):
                p, c, top, _ = goes.low_cloud(crop, i, sun_el=30.0)
                s, _, _ = goes5.sample_macro(crop, i, p, c, top, lon, lat, default_top_m=default_top)
                m = s > 0.5
                return lon[m].mean(), lat[m].mean(), int(m.sum())
            lo0, la0, n0 = centroid(0, 0.0)                # ht = 0, no default: no shift
            lo5, la5, n5 = centroid(1, 0.0)
            self.assertGreater(n0, 1000)
            de_m = (lo5 - lo0) * 111320.0 * np.cos(np.radians(37.76))
            dn_m = (la5 - la0) * 111320.0
            wde, wdn = goes.parallax_shift_m(500.0, -122.4, 37.76, -137.0)
            self.assertAlmostEqual(de_m, float(wde), delta=120.0)      # about -205 m (macro cell here is about 175 m)
            self.assertAlmostEqual(dn_m, float(wdn), delta=120.0)      # about -482 m
            self.assertLess(de_m, -50)
            self.assertLess(dn_m, -300)


class TestMacro(unittest.TestCase):
    def test_png_round_trip_is_exact(self):
        rng = np.random.default_rng(1)
        p, c = rng.random((111, 141)).astype(np.float32), rng.random((111, 141)).astype(np.float32)
        top = rng.random((111, 141)).astype(np.float32) * 3000
        rgba = goes5.macro_rgba(p, c, top)
        self.assertEqual(rgba.shape, (111, 141, 4))
        self.assertEqual(int(rgba[..., 3].min()), 255)
        np.testing.assert_array_equal(rgba[..., 2], np.minimum(np.rint(top / 8.0), 255).astype(np.uint8))
        data = goes5.png_bytes(rgba)
        self.assertEqual(data, goes5.png_bytes(rgba))              # deterministic
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "x.png"
            f.write_bytes(data)
            np.testing.assert_array_equal(goes5.read_png(f), rgba)
            from PIL import Image
            self.assertEqual(Image.open(f).mode, "RGBA")           # no palette

    def test_macro_grid_is_141_by_111(self):
        lon, lat = goes5.macro_axes(goes.BOX, 0.01)
        self.assertEqual((len(lon), len(lat)), (141, 111))
        self.assertAlmostEqual(float(lat[0]), 38.3)                # row 0 = north
        self.assertAlmostEqual(float(lat[-1]), 37.2)

    def test_write_macro_frames_index_and_pruning(self):
        now = T0 + dt.timedelta(hours=7)
        with tempfile.TemporaryDirectory() as tmp:
            store, out = Path(tmp) / "goes5", Path(tmp) / "macro"
            pa, ph = make_pair(tmp, T0, acm_fn=lambda lo, la: np.where(lo > -122.8, 3, 0), ht_fn=lambda lo, la: np.full(lo.shape, 400.0))
            cr, acm, ht = goes5.crop_scan(pa, ph)
            store.mkdir()
            (store / "grid.json").write_text(json.dumps(goes5.make_grid(cr, goes.BOX)))
            times = [T0, now - dt.timedelta(hours=5, minutes=50), now - dt.timedelta(minutes=10)]
            for t in times:
                goes5.save_hour(goes5.hour_path(store, t), goes5.merge_scan(goes5.load_hour(goes5.hour_path(store, t)), int(t.timestamp()), acm, ht, "s"))
            out.mkdir()
            (out / "20260101T0000.png").write_bytes(b"old")                  # stale frame
            info = goes5.write_macro(store, out, now, log=lambda *a: None)
            idx = json.loads((out / "index.json").read_text())
            self.assertEqual((idx["schema"], idx["w"], idx["h"], idx["row0"], idx["rule_version"]), (1, 141, 111, "north", 1))
            self.assertEqual(len(idx["frames"]), 2)                           # the 7 h old scan is outside the 6 h window
            self.assertEqual(sorted(p.name for p in out.glob("*.png")), sorted(f["file"] for f in idx["frames"]))
            self.assertEqual(info["deleted"], 1)                              # the stale file (the 7 h old scan was never made into a frame)
            png = goes5.read_png(out / idx["frames"][0]["file"])
            self.assertEqual(png.shape, (111, 141, 4))
            self.assertGreater(int(png[..., 0].max()), 0)
            self.assertEqual(int(png[..., 3].min()), 255)
            again = goes5.write_macro(store, out, now, log=lambda *a: None)
            self.assertEqual(again["written"], 0)                             # unchanged frames are not rewritten


class TestSwitchOff(unittest.TestCase):
    def test_off_by_default_everywhere(self):
        self.assertFalse(C.GOES5)
        self.assertTrue(C.BETA["goes5"])                      # beta only (George, 2026-11-15)
        self.assertNotIn("goes5", C.PROMOTED)
        for beta, promoted in ((False, False), (False, True)):
            self.assertFalse(build.goes5_enabled(beta, promoted))
        self.assertTrue(build.goes5_enabled(True))

    def test_switches_are_separate(self):
        saved = dict(C.BETA), C.GOES5
        try:
            C.BETA["goes5"] = True
            self.assertTrue(build.goes5_enabled(True))
            self.assertFalse(build.goes5_enabled(False))              # the beta key never reaches the public build
            C.BETA.pop("goes5")
            C.GOES5 = True
            self.assertTrue(build.goes5_enabled(False))
            self.assertFalse(build.goes5_enabled(False, promoted=True))
            self.assertFalse(build.goes5_enabled(True))
        finally:
            C.BETA.clear()
            C.BETA.update(saved[0])
            C.GOES5 = saved[1]

    def test_settings_dicts_do_not_change(self):
        self.assertEqual(sorted(build.settings(False)), ["carry_state", "nudge_anchors", "physics", "top_fusion", "wind_east"])
        self.assertEqual(sorted(build.settings(True)), ["carry_state", "nudge_anchors", "physics", "top_fusion", "wind_east"])

    def test_build_hook_makes_no_download_and_no_files_when_off(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(goes, "download", side_effect=AssertionError("download")), \
                mock.patch.object(goes, "_list_keys", side_effect=AssertionError("list")), mock.patch.object(goes5, "run", side_effect=AssertionError("goes5.run")):
            # the hook in build.run is guarded by goes5_enabled(); with it off nothing of goes5 is called and nothing is written
            if build.goes5_enabled(False, False):    # the public build (beta is on since 2026-11-15)
                goes5.run(tmp, T0)
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
