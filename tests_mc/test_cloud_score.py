"""Satellite cloud score (guide 04): tallies, rules, rolling file, archive reader, read-only guarantee, never fatal. Synthetic data, no network."""
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from microclimate import replay
from microclimate.live import cloud_score as cs
from microclimate.live import config as C
from microclimate.live import fetch, forcing, outputs
from test_goes_box import fake_lowcloud

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ tallies and rules
class TestTally(unittest.TestCase):
    def setUp(self):
        self.member = np.ones(6, bool)
        self.ok = np.zeros(6, int)

    def test_perfect_model_scores_hit_rate_1_and_error_0(self):
        obs = np.array([1.0, 1.0, 0.0, 0.0, 1.0, 0.0])
        m = cs.metrics([cs.tally(obs, obs.copy(), self.ok, self.member)])
        self.assertEqual((m["hit_rate"], m["mae"], m["false_alarm_ratio"], m["csi"], m["accuracy"]), (1.0, 0.0, 0.0, 1.0, 1.0))
        self.assertEqual((m["hits"], m["misses"], m["false_alarms"], m["correct_clears"]), (3, 0, 0, 3))

    def test_always_clear_model_has_hit_rate_0_and_no_false_alarms(self):
        obs = np.array([1.0, 1.0, 0.0, 0.0, 1.0, 0.0])
        m = cs.metrics([cs.tally(obs, np.zeros(6), self.ok, self.member)])
        self.assertEqual(m["hit_rate"], 0.0)
        self.assertEqual(m["false_alarms"], 0)
        self.assertIsNone(m["false_alarm_ratio"])
        self.assertEqual(m["csi"], 0.0)
        self.assertAlmostEqual(m["mae"], 0.5)

    def test_threshold_is_at_or_above_half(self):
        t = cs.tally(np.array([0.5, 0.49]), np.array([0.5, 0.5]), np.zeros(2, int), np.ones(2, bool))
        self.assertEqual(t[2:6], [1, 0, 1, 0])           # hit, no miss, one false alarm

    def test_high_cloud_hours_are_excluded_and_counted(self):
        low = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
        high = np.array([[0.9, 0.0, 0.0], [0.0, 0.0, 0.0]])
        valid = np.ones((2, 3))
        st = cs.box_status(low, high, valid)
        self.assertEqual(st.tolist(), [[1, 0, 0], [0, 0, 0]])
        t0 = cs.tally(low[0], np.ones(3), st[0], np.ones(3, bool))
        self.assertEqual((t0[0], t0[6]), (2, 1))                        # 2 scored, 1 hidden by high cloud
        whole = cs.box_status(np.zeros(3), np.array([0.5, 0.5, 0.5]), np.ones(3))
        t = cs.tally(np.zeros(3), np.zeros(3), whole, np.ones(3, bool))
        self.assertEqual((t[0], t[6]), (0, 3))
        m = cs.metrics([t, cs.tally(low[1], low[1], st[1], np.ones(3, bool))])
        self.assertEqual((m["hours"], m["unscorable_hours"]["high_cloud"], m["unscorable_box_hours"]["high_cloud"]), (1, 1, 3))

    def test_status_rules(self):
        st = cs.box_status(np.array([0.0, 0.0, 0.0, np.nan, 0.0]), np.array([0.3, 0.31, 0.0, np.nan, 0.0]), np.array([1.0, 1.0, 0.49, 0.0, np.nan]))
        self.assertEqual(st.tolist(), [0, 1, 2, 2, 3])                   # exactly 30 % high is still scorable; fewer than half valid; no scan

    def test_model_without_value_is_counted_apart(self):
        t = cs.tally(np.array([1.0, 0.0]), np.array([np.nan, 0.0]), np.zeros(2, int), np.ones(2, bool))
        self.assertEqual((t[0], t[8]), (1, 1))

    def test_timing(self):
        hours = [T0 + dt.timedelta(hours=k) for k in range(12)]
        obs = np.array([0, 0, 1, 1, 1, 1, 0, 0, 0, np.nan, 0, 0], float)
        mod = np.array([0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 0], float)
        tm = cs.timing(obs, mod, hours)
        self.assertEqual((tm["arrival_err_h"], tm["clear_err_h"]), (2, 2))
        self.assertIsNone(cs.timing(np.zeros(12), mod, hours)["arrival_err_h"])
        self.assertEqual(cs.arrival_clear(np.array([1, np.nan, 1, 1, 0, np.nan, 0, 0], float)), (2, 6))


class TestGeometry(unittest.TestCase):
    def test_boxes_and_membership(self):
        lat, lon = fetch.lattice_points()
        lat_t = forcing.Lattice(lat, lon)
        b = cs.lattice_geometry(lat, lon, lat_t.x)
        self.assertEqual(len(b["names"]), 33)
        self.assertEqual(b["names"][-3:], ["upwind", "bay", "coast"])
        self.assertEqual(int(cs.members("rrfs", b["kinds"]).sum()), 32)
        self.assertEqual([b["names"][j] for j in np.nonzero(cs.members("source", b["kinds"]))[0]], ["upwind", "coast"])
        g = b["geom"]
        self.assertTrue((g[:, 0] < g[:, 2]).all() and (g[:, 1] < g[:, 3]).all())
        w = cs.box_weights(b, lat_t, 30)
        np.testing.assert_allclose(w.sum(axis=1), 1.0)
        col = np.arange(30, dtype=float)
        self.assertEqual(cs.apply_weights(w, col)[7], 7.0)               # a cell is its own lattice point
        col[3] = np.nan
        out = cs.apply_weights(w, col)
        self.assertTrue(np.isnan(out[3]) and np.isfinite(out[4]))        # NaN stays where it is used


# ------------------------------------------------------------------ rolling file
def rand_row(rng, day=1):
    row = dict(d=day)
    for name in cs.FIELDS + ("rrfs@1", "rrfs@3", "rrfs@6", "rrfs@12", "rrfs@24"):
        n = int(rng.integers(1, 33))
        hit = int(rng.integers(0, n + 1))
        miss = int(rng.integers(0, n - hit + 1))
        fa = int(rng.integers(0, n - hit - miss + 1))
        row[name] = [n, round(float(rng.uniform(0, n)), 3), hit, miss, fa, n - hit - miss - fa, int(rng.integers(0, 4)), 0, 0, 0]
    return row


class TestRolling(unittest.TestCase):
    def test_keeps_14_days_stays_under_the_limit_and_loses_no_counts(self):
        rng = np.random.default_rng(1)
        now0 = dt.datetime(2026, 10, 1, tzinfo=UTC)
        prev, final = {}, {}
        for step in range(40 * 8):                       # 40 days, one run every 3 h, each covering the last 27 h
            now = now0 + dt.timedelta(hours=3 * step)
            rows = {}
            for h in range(27):
                t = now - dt.timedelta(hours=h)
                rows[cs.hour_key(t)] = rand_row(rng, day=1 if 15 <= t.hour <= 23 or t.hour < 1 else 0)
            final.update(rows)
            roll = cs.merge_rolling(prev, rows, now)
            roll["events"] = cs.merge_events(prev.get("events"), [[(now - dt.timedelta(hours=5)).isoformat(), "arrival", "rrfs", 1]], now)
            prev = json.loads(json.dumps(roll))
        sm = cs.summaries(roll, now)
        doc = dict(schema=1, headline=sm["headline"], summary=sm["summary"], daily=sm["daily"], events=roll["events"], hours=roll["hours"], days=roll["days"])
        txt = cs.dumps_limited(doc)
        self.assertLess(len(txt.encode()), C.CLOUD_MAX_BYTES)
        self.assertNotIn("truncated", doc)
        dates = sorted(set(roll["days"]) | {cs.day_key(k) for k in roll["hours"]})
        self.assertEqual(len(dates), C.CLOUD_KEEP_DAYS)
        self.assertEqual(dates[-1], now.astimezone(cs.PT).strftime("%Y-%m-%d"))
        self.assertLessEqual(len(roll["hours"]), cs.HOURS_LIVE_H + 1)
        first = dates[0]
        want = sum(row["rrfs"][0] for hk, row in final.items() if cs.day_key(hk) >= first and hk <= cs.hour_key(now))
        got = sum(r["fields"]["rrfs"]["all"]["n"] for r in sm["daily"] if r["date"] >= first)
        self.assertEqual(got, want)
        self.assertIsNotNone(sm["headline"])

    def test_oversize_file_drops_the_oldest_days(self):
        doc = dict(days={f"2026-11-{d:02d}": {"x": "y" * 400} for d in range(1, 20)}, daily=[dict(date=f"2026-11-{d:02d}") for d in range(1, 20)])
        txt = cs.dumps_limited(doc, limit=6000)
        self.assertLessEqual(len(txt.encode()), 6000)
        self.assertEqual(doc["truncated"][0], "2026-11-01")

    def test_a_new_run_replaces_its_hours_but_a_run_without_scan_does_not_clobber(self):
        now = T0 + dt.timedelta(hours=10)
        hk = cs.hour_key(T0 + dt.timedelta(hours=8))
        good = dict(d=0, rrfs=[10, 2.0, 1, 1, 1, 7, 0, 0, 0, 0])
        r1 = cs.merge_rolling({}, {hk: good}, now)
        none = dict(d=0, rrfs=[0, 0, 0, 0, 0, 0, 0, 0, 0, 33])       # this run had no scan for that hour
        r2 = cs.merge_rolling(r1, {hk: none}, now)
        self.assertEqual(r2["hours"][hk]["rrfs"], good["rrfs"])
        better = dict(d=0, rrfs=[12, 3.0, 2, 1, 1, 8, 0, 0, 0, 0])
        r3 = cs.merge_rolling(r2, {hk: better}, now)
        self.assertEqual(r3["hours"][hk]["rrfs"], better["rrfs"])

    def test_previous_file_handling(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cloud_score.json"
            self.assertEqual(cs.read_prev(p)[0], {})
            p.write_text("{not json")
            js, note = cs.read_prev(p)
            self.assertEqual(js, {})
            self.assertIn("unreadable", note)
            p.write_text(json.dumps(dict(schema=1, hours={}, days={})))
            self.assertEqual(cs.read_prev(p)[1], None)


# ------------------------------------------------------------------ archive
def write_archive(folder, run_time, n_frames=30, percent=True, key="lattice_low_cloud", time_key="frame_times_iso", value=0.9):
    times = np.array([(run_time + dt.timedelta(hours=k)).isoformat() for k in range(n_frames)])
    low = np.full((n_frames, 30), value * (100.0 if percent else 1.0))
    p = Path(folder) / f"run_{run_time:%Y%m%dT%H%M}.npz"
    np.savez_compressed(p, **{time_key: times, key: low, "other": np.zeros(3)})
    return p


class TestArchive(unittest.TestCase):
    def test_reader_round_trip_and_alternatives(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_archive(d, T0 + dt.timedelta(hours=1))
            r = cs.read_archive_run(p)
            self.assertEqual(r["run_time"], T0 + dt.timedelta(hours=1))
            self.assertEqual(r["frame_times"][3], T0 + dt.timedelta(hours=4))
            self.assertAlmostEqual(float(r["low"][0, 0]), 0.9)             # percent converted to a fraction
            r2 = cs.read_archive_run(write_archive(d, T0 + dt.timedelta(hours=2), percent=False))
            self.assertAlmostEqual(float(r2["low"][0, 0]), 0.9)
            self.assertIsNone(cs.read_archive_run(write_archive(d, T0 + dt.timedelta(hours=3), key="something_else")))
            bad = Path(d) / "run_20261112T0500.npz"
            bad.write_bytes(b"not an npz")
            self.assertIsNone(cs.read_archive_run(bad))
            self.assertIsNone(cs.read_archive_run(Path(d) / "other.npz"))

    def test_no_archive_yet(self):
        hrs = [T0 + dt.timedelta(hours=k) for k in range(5)]
        self.assertEqual(cs.lead_models(None, hrs, T0, 30)[0], "no archive yet")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cs.lead_models(d, hrs, T0, 30)[0], "no archive yet")
            self.assertEqual(cs.lead_models(Path(d) / "missing", hrs, T0, 30)[0], "no archive yet")

    def test_leads_pick_the_closest_run(self):
        hrs = [T0 + dt.timedelta(hours=k) for k in range(2, 12)]
        with tempfile.TemporaryDirectory() as d:
            write_archive(d, T0 + dt.timedelta(hours=1), value=0.2)          # lead 3 for 04:00
            write_archive(d, T0 + dt.timedelta(hours=1, minutes=30), value=0.8)   # lead 2.5 for 04:00: farther from 3 than the first
            status, per, counts = cs.lead_models(d, hrs, T0 + dt.timedelta(hours=12), 30)
            self.assertEqual(status, "ok")
            self.assertAlmostEqual(float(per[3][T0 + dt.timedelta(hours=4)][0]), 0.2)
            self.assertIn(1, per)
            self.assertNotIn(24, per)                                          # no frame 24 h ahead of any run inside the hours
            self.assertEqual(counts["usable"], 2)


# ------------------------------------------------------------------ whole step on a synthetic run
def synth(n_frames=8):
    lat, lon = fetch.lattice_points()
    lat_t = forcing.Lattice(lat, lon)
    times = [T0 + dt.timedelta(hours=k) for k in range(n_frames + 4)]
    rng = np.random.default_rng(3)
    rr = dict(lat=lat, lon=lon, times=times, vars=dict(cloud_cover_low=rng.uniform(0, 100, (30, len(times)))))
    F = dict(lattice=lat_t, s_series=rng.uniform(0, 1, n_frames * 6))
    t_hours = [T0 + dt.timedelta(hours=k) for k in range(n_frames)]
    tiles = rng.integers(0, 256, (n_frames, replay.FOG_H, replay.FOG_W)).astype(np.uint8)
    # the sea and the bay low cloud; high cloud over everything at hour 5
    def rule(k):
        return lambda lo, la: ((lo < -122.45) & (k != 5), (lo < -122.45) | (k == 5), np.ones(lo.shape, bool))
    goes_hours = {t: fake_lowcloud(rule(k)) for k, t in enumerate(t_hours)}
    goes_hours[t_hours[7]] = None                                              # a missing scan
    sun = np.where(np.arange(n_frames) < 4, -10.0, 20.0)
    return rr, F, t_hours, tiles, goes_hours, sun


def freeze(*objs):
    for o in objs:
        if isinstance(o, np.ndarray):
            o.setflags(write=False)


class TestScoreRun(unittest.TestCase):
    def test_scoring_does_not_modify_forcing_fog_or_scan_arrays(self):
        rr, F, t_hours, tiles, goes_hours, sun = synth()
        arrays = [rr["vars"]["cloud_cover_low"], F["s_series"], tiles, F["lattice"].x, F["lattice"].y, sun, rr["lat"], rr["lon"]]
        for lc in goes_hours.values():
            if lc is not None:
                arrays += [lc.low, lc.cloudy, lc.valid, lc.x, lc.y]
        before = [a.copy() for a in arrays]
        freeze(*arrays)                                    # any write would now raise
        nosat = tiles.copy()
        nosat.setflags(write=False)
        now = T0 + dt.timedelta(hours=7, minutes=20)
        res = cs.score_current(rr, F, goes_hours, t_hours, sun, tiles, nosat, now)
        for a, b in zip(arrays, before):
            np.testing.assert_array_equal(a, b)
        self.assertEqual(len(res["rows"]), 8)

    def test_rows_hold_the_expected_unscorable_reasons(self):
        rr, F, t_hours, tiles, goes_hours, sun = synth()
        now = T0 + dt.timedelta(hours=7, minutes=20)
        res = cs.score_current(rr, F, goes_hours, t_hours, sun, tiles, tiles, now)
        r5 = res["rows"][cs.hour_key(t_hours[5])]
        self.assertEqual(r5["rrfs"][0], 0)                  # high cloud everywhere at hour 5
        self.assertEqual(r5["rrfs"][6], 32)
        r7 = res["rows"][cs.hour_key(t_hours[7])]
        self.assertEqual((r7["rrfs"][0], r7["rrfs"][9]), (0, 32))        # no scan
        self.assertEqual((r7["source"][0], r7["source"][9]), (0, 2))     # the source is scored on two boxes
        r2 = res["rows"][cs.hour_key(t_hours[2])]
        self.assertEqual(r2["rrfs"][0], 32)
        self.assertEqual(r2["d"], 0)
        self.assertEqual(res["rows"][cs.hour_key(t_hours[6])]["d"], 1)
        self.assertEqual(res["lead"]["status"], "no archive yet")
        self.assertIn("rrfs", res["table"])
        self.assertEqual(res["table"]["rrfs"]["all"]["unscorable_hours"]["no_satellite_scan"], 1)

    def test_lead_times_come_from_the_archive(self):
        rr, F, t_hours, tiles, goes_hours, sun = synth()
        now = T0 + dt.timedelta(hours=7, minutes=20)
        with tempfile.TemporaryDirectory() as d:
            write_archive(d, T0 - dt.timedelta(hours=2), value=1.0)                 # lead 3 h: frame at T0 + 1 h, lead 6 h: T0 + 4 h
            res = cs.score_current(rr, F, goes_hours, t_hours, sun, tiles, tiles, now, archive_dir=d)
        self.assertEqual(res["lead"]["status"], "ok")
        row = res["rows"][cs.hour_key(T0 + dt.timedelta(hours=1))]
        self.assertIn("rrfs@3", row)
        self.assertNotIn("rrfs@3", res["rows"][cs.hour_key(T0 + dt.timedelta(hours=2))])
        self.assertIn("rrfs@6", res["rows"][cs.hour_key(T0 + dt.timedelta(hours=4))])
        self.assertEqual(row["rrfs@3"][0], 30)                                    # the 30 lattice cells

    def test_run_safe_writes_the_files_and_is_idempotent(self):
        rr, F, t_hours, tiles, goes_hours, sun = synth()
        now = T0 + dt.timedelta(hours=7, minutes=20)
        diag = dict(sun_el=np.repeat(sun, 6))
        hook = cs.Hook()
        hook.tiles_cb(tiles, t_hours)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(outputs, "fog_frames", return_value=np.zeros_like(tiles)):
            out = Path(d)
            (out / "index.json").write_text(json.dumps(dict(frame_ranges=[[0, 4], [4, 8]], fog_files=["fog0.png", "fog1.png"])))
            notes = []
            info = cs.run_safe(out, hook, rr, F, None, diag, goes_hours, T0, now, notes, log=lambda m: None)
            self.assertTrue(info["ok"], info)
            self.assertEqual(notes, ["cloud score: no previous cloud_score.json: history starts now"])
            doc1 = json.loads((out / "cloud_score.json").read_text())
            self.assertLess((out / "cloud_score.json").stat().st_size, C.CLOUD_MAX_BYTES)
            for fn in ("sat0.png", "sat1.png"):
                self.assertTrue((out / fn).exists())
            idx = json.loads((out / "index.json").read_text())
            self.assertEqual(idx["sat_score"]["satellite"]["files"], ["sat0.png", "sat1.png"])
            self.assertEqual(idx["sat_score"]["satellite"]["hours_with_data"], 7)
            self.assertFalse(idx["sat_score"]["loaded_at_start"])
            from PIL import Image
            img = np.array(Image.open(out / "sat0.png"))
            self.assertEqual(img.shape, (replay.FOG_H, replay.FOG_COLS * replay.FOG_W))
            tile0 = img[:replay.FOG_H, :replay.FOG_W]                      # hour 0: low cloud west of -122.45, none east of it
            self.assertGreater(int((tile0 >= 200).sum()), 0)
            self.assertGreater(int((tile0 == 0).sum()), 0)
            self.assertTrue((np.array(Image.open(out / "sat1.png"))[:replay.FOG_H, 3 * replay.FOG_W:4 * replay.FOG_W] == cs.SAT_NODATA).all())   # hour 7: no scan
            # a second run with the first run's file as history must not double count
            notes2 = []
            with mock.patch.dict("os.environ", {C.CLOUDSCORE_PREV_ENV: str(out / "cloud_score.json")}):
                info2 = cs.run_safe(out, hook, rr, F, None, diag, goes_hours, T0, now + dt.timedelta(minutes=30), notes2, log=lambda m: None)
            self.assertTrue(info2["ok"])
            self.assertEqual(notes2, [])
            doc2 = json.loads((out / "cloud_score.json").read_text())
            self.assertEqual(doc1["summary"], doc2["summary"])
            self.assertEqual(doc1["hours"], doc2["hours"])

    def test_a_failure_never_raises_and_is_noted(self):
        notes = []
        info = cs.run_safe(tempfile.gettempdir(), cs.Hook(), {}, {}, None, {}, {}, T0, T0, notes, log=lambda m: None)
        self.assertFalse(info["ok"])
        self.assertTrue(notes and notes[0].startswith("cloud score failed"))
        rr, F, t_hours, tiles, goes_hours, sun = synth()
        hook = cs.Hook()
        hook.tiles_cb(tiles, t_hours)
        notes = []
        with tempfile.TemporaryDirectory() as d:                          # no index.json: fails late, after the first file was written
            info = cs.run_safe(d, hook, rr, F, None, dict(sun_el=np.repeat(sun, 6)), goes_hours, T0, T0 + dt.timedelta(hours=7), notes, log=lambda m: None)
        self.assertFalse(info["ok"])
        self.assertTrue(any(n.startswith("cloud score failed") for n in notes))

    def test_cli_prints_a_summary(self):
        rr, F, t_hours, tiles, goes_hours, sun = synth()
        hook = cs.Hook()
        hook.tiles_cb(tiles, t_hours)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(outputs, "fog_frames", return_value=np.zeros_like(tiles)):
            (Path(d) / "index.json").write_text(json.dumps(dict(frame_ranges=[[0, 8]])))
            cs.run_safe(d, hook, rr, F, None, dict(sun_el=np.repeat(sun, 6)), goes_hours, T0, T0 + dt.timedelta(hours=7), [], log=lambda m: None)
            lines = []
            cs.print_summary(json.loads((Path(d) / "cloud_score.json").read_text()), out=lines.append)
            self.assertTrue(any("last 24 h" in x for x in lines))
            self.assertTrue(any(x.startswith("rrfs") for x in lines))
            self.assertEqual(cs.run_cli(["--file", str(Path(d) / "nope.json")]), 1)


if __name__ == "__main__":
    unittest.main()
