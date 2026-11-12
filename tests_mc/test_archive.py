"""Guide 02 part B: run archive."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path

import numpy as np

from microclimate.live import archive


class Lat:
    lat = np.linspace(37.7, 37.8, 30)
    lon = np.linspace(-122.5, -122.35, 30)


def fake(n=4000, nF=79, seed=0):
    r = np.random.default_rng(seed)
    S = dict(x=np.arange(n, dtype=float), zone=np.where(np.arange(n) < n // 5, 0, 2).astype(np.uint8), street=r.integers(0, 4, n).astype(np.uint32),
             street_names=np.array(["Kansas Street", "Alameda Street", "Other", "More"]))
    n6 = nF * 6
    ser = {k: r.uniform(0, 10, n6) for k in ("temperature_2m", "dew_point_2m", "wind_speed_10m", "wind_dir", "cloud_cover_low", "precipitation", "shortwave_radiation")}
    t0 = dt.datetime(2026, 11, 12, 20, tzinfo=dt.timezone.utc)
    F = dict(ser=ser, s_series=r.uniform(0, 1, n6), s_model=r.uniform(0, 1, n6), fog_top=r.uniform(100, 500, n6), goes_frac=np.full(nF, np.nan), khaf=np.zeros(nF),
             obs_until=t0 + dt.timedelta(hours=26), lattice_cl=r.uniform(0, 100, (30, n6)), lattice=Lat)
    W = np.clip(r.normal(40, 30, (nF, n)), 0, 100).astype(np.float32)
    return W, F, S, t0


class TestArchive(unittest.TestCase):
    def test_sample_is_fixed_and_has_the_three_kinds(self):
        W, F, S, t0 = fake()
        i1, k1 = archive.sample_index(S, {"Kansas Street"})
        i2, k2 = archive.sample_index(S, {"Kansas Street"})
        np.testing.assert_array_equal(i1, i2)
        self.assertEqual(set(np.unique(k1)), {0, 1, 2})
        d = np.nonzero(S["zone"] != 2)[0]
        route = i1[k1 == 0]
        self.assertTrue(np.isin(route, d).all())                                  # route points are district points on route streets
        self.assertTrue(np.isin(S["street"][route], [0]).all())
        self.assertEqual(int((k1 == 2).sum()), len(np.nonzero(S["zone"] == 2)[0][::archive.CITY_STRIDE]))
        self.assertEqual(len(archive.sample_index(S, {"Kansas Street"}, route_stride=2)[0]) < len(i1), True)

    def test_round_trip_and_size(self):
        W, F, S, t0 = fake()
        now = dt.datetime(2026, 11, 12, 22, 40, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            name, size = archive.write_run(d, W, {}, F, S, t0, now, ("film",), "live-1", 6)
            self.assertEqual(name, "run_20261112T2240.npz")
            self.assertLess(size, archive.MAX_BYTES)
            r = archive.load(Path(d) / name)
            idx = r["sample_index"]
            np.testing.assert_array_equal(r["w"], np.rint(W[:, idx]).astype(np.uint8))
            self.assertEqual(r["w"].shape[0], 79)
            self.assertEqual(int(r["observed_until_frame"]), 26)
            self.assertEqual(str(r["model_version"]), "live-1")
            self.assertEqual(str(r["physics"]), '["film"]')
            self.assertEqual(r["lattice_cloud_low"].shape, (30, 79))
            self.assertEqual(r["s_series"].shape, (79,))
            self.assertNotIn("cloud_base", r)                                      # only when the model produces it
            self.assertFalse(list(Path(d).glob("*.tmp")))

    def test_size_assertion_and_thinning(self):
        W, F, S, t0 = fake()
        now = dt.datetime(2026, 11, 12, 22, 40, tzinfo=dt.timezone.utc)
        old = archive.MAX_BYTES
        try:
            archive.MAX_BYTES = 100
            with tempfile.TemporaryDirectory() as d, self.assertRaises(AssertionError):
                archive.write_run(d, W, {}, F, S, t0, now, (), "live-1", 6)
                self.fail("unreachable")
            with tempfile.TemporaryDirectory() as d:
                try:
                    archive.write_run(d, W, {}, F, S, t0, now, (), "live-1", 6)
                except AssertionError:
                    pass
                self.assertEqual(list(Path(d).iterdir()), [])                      # nothing left behind
        finally:
            archive.MAX_BYTES = old

    def test_place_and_weekly_prune_leave_git_alone(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src:
            root = Path(d)
            (root / ".git" / "refs" / "heads").mkdir(parents=True)                 # empty folders of a git repo must survive
            files = []
            for n in ("run_20250101T0000.npz", "run_20261110T1200.npz"):
                (Path(src) / n).write_bytes(b"x")
                files.append(Path(src) / n)
            (Path(src) / "other.txt").write_bytes(b"x")
            self.assertEqual(archive.place(root, files + [Path(src) / "other.txt"]), ["2025/01/01/run_20250101T0000.npz", "2026/11/10/run_20261110T1200.npz"])
            today = dt.date(2026, 11, 12)
            self.assertEqual(archive.prune(root, 120, today), 1)
            self.assertFalse((root / "2025").exists())
            self.assertTrue((root / "2026/11/10/run_20261110T1200.npz").exists())
            self.assertTrue((root / ".git" / "refs" / "heads").exists())
            self.assertIsNone(archive.prune(root, 120, dt.date(2026, 11, 15)))      # not due within a week
            self.assertEqual(archive.prune(root, 120, dt.date(2026, 11, 20)), 0)

    def test_one_file_per_utc_hour(self):
        from microclimate.live import config as C
        self.assertEqual(C.ARCHIVE_EVERY_H, 1)
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src:
            root = Path(d)

            def mk(n):
                (Path(src) / n).write_bytes(b"x")
                return Path(src) / n
            self.assertEqual(archive.place(root, [mk("run_20261112T2210.npz")]), ["2026/11/12/run_20261112T2210.npz"])
            self.assertEqual(archive.place(root, [mk("run_20261112T2240.npz")]), [])                      # same hour as the predecessor: skipped
            self.assertEqual(archive.place(root, [mk("run_20261112T2310.npz"), mk("run_20261112T2340.npz")]), ["2026/11/12/run_20261112T2310.npz"])   # one per hour within a call too
            self.assertEqual(archive.place(root, [mk("run_20261113T0010.npz")]), ["2026/11/13/run_20261113T0010.npz"])   # next day, hour 0
            self.assertEqual(archive.place(root, [mk("run_20261112T2210.npz")]), [])                      # already there
            self.assertEqual(len(list(root.glob("*/*/*/run_*.npz"))), 3)
            self.assertEqual(len(archive.place(root, [mk("run_20261112T2250.npz")], every_h=0)), 1)       # 0 = keep every run
            self.assertEqual(archive.place(root, [mk("run_20261114T0200.npz"), mk("run_20261114T0330.npz")], every_h=2), ["2026/11/14/run_20261114T0200.npz"])
            self.assertEqual(archive.place(root, [mk("run_20261114T0400.npz")], every_h=2), ["2026/11/14/run_20261114T0400.npz"])


if __name__ == "__main__":
    unittest.main()
