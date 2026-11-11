"""Tests for the GitHub-only pieces: env overrides, baseline backfill/replay, form import, site, detector plugins."""
import json
import os
import tempfile
import unittest
from datetime import timedelta

from helpers import IEM_TEXT, NOW, FakeHttp, make_cfg

from deathfog import baseline, collect, db, engine, reports, site  # noqa: E402
from deathfog import sources as _s  # noqa: E402,F401
from deathfog.config import apply_env, baseline_path, db_path  # noqa: E402
from deathfog.detectors import Detector, register_detector  # noqa: E402
from deathfog.model import WeatherState  # noqa: E402


class TestConfigEnv(unittest.TestCase):
    def test_env_overrides_and_ntfy_switch(self):
        cfg = make_cfg()
        apply_env(cfg, {"DEATHFOG_USER_AGENT": "(me, me@x.org)", "DEATHFOG_NTFY_TOPIC": "secret-topic",
                        "DEATHFOG_DATA_DIR": "state", "DEATHFOG_SITE_DIR": "out", "DEATHFOG_FORM_CSV_URL": "https://csv"})
        self.assertEqual(cfg["general"]["user_agent"], "(me, me@x.org)")
        self.assertEqual(cfg["notify"]["type"], "ntfy")
        self.assertEqual(db_path(cfg), os.path.join("state", "deathfog.db"))
        self.assertEqual(baseline_path(cfg), os.path.join("state", "baseline.json"))
        self.assertEqual((cfg["site"]["dir"], cfg["reports"]["form_csv_url"]), ("out", "https://csv"))

    def test_empty_env_values_are_ignored(self):
        cfg = make_cfg()
        apply_env(cfg, {"DEATHFOG_NTFY_TOPIC": "  ", "DEATHFOG_REPORT_URL": ""})
        self.assertEqual(cfg["notify"].get("type", "stdout"), "stdout")
        self.assertEqual(cfg["general"]["report_url"], "https://forms.example/x")


class TestBaseline(unittest.TestCase):
    def run_baseline(self, cfg=None, http=None):
        cfg = cfg or make_cfg()
        conn = db.connect(":memory:")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "baseline.json")
            s = baseline.run_baseline(cfg, conn, http or FakeHttp(cfg["zones"]), NOW, days=30, path=path)
            self.assertTrue(os.path.exists(path))
            self.assertEqual(json.load(open(path))["days"], 30)
        return s, conn

    def test_replay_finds_every_night(self):
        s, _ = self.run_baseline()
        z = s["zones"]["potrero"]
        self.assertEqual(z["episodes"], 30)                           # one per night, 30 nights
        self.assertEqual(z["episode_list"][0]["hours"], 8)
        self.assertAlmostEqual(z["pct_of_hours"]["strong"], 100 * 8 / 24, delta=0.2)
        self.assertEqual(set(z["episode_list"][0]["why"]), {"saturation", "calm", "swing", "no_sun"})
        self.assertIn(z["typical_start_hour_local"], (0, 1))          # midnight-1am local
        self.assertEqual(s["zones"]["control"]["episodes"], 0)        # control zone (x0.3) stays quiet
        self.assertEqual((s["start"], s["end"]), ("2026-10-13", "2026-11-11"))

    def test_warmup_day_is_requested_but_not_scored(self):
        http = FakeHttp(make_cfg()["zones"])
        s, conn = self.run_baseline(http=http)
        hist = [c for c in http.calls if "historical-forecast" in c[0]][0][1]
        self.assertEqual(hist["start_date"], "2026-10-12")            # one extra day for 24 h history
        self.assertEqual(s["zones"]["potrero"]["hours"], 30 * 24)

    def test_model_vs_airport_comparison(self):
        s, _ = self.run_baseline()
        c = s["model_vs_airport"]["potrero"]
        self.assertEqual(c["station"], "KSFO")
        self.assertGreaterEqual(c["n"], 1)
        self.assertAlmostEqual(c["temp_bias_c"], 0.0, delta=0.1)      # fixture obs equal the model at 10:00Z

    def test_airport_failure_is_a_note_not_an_error(self):
        class NoIem(FakeHttp):
            def get_bytes(self, url, params=None, headers=None):
                if "asos.py" in url:
                    raise RuntimeError("boom")
                return super().get_bytes(url, params, headers)
        cfg = make_cfg()
        s, _ = self.run_baseline(cfg, NoIem(cfg["zones"]))
        self.assertEqual(s["zones"]["potrero"]["episodes"], 30)
        self.assertTrue(any("could not fetch airport observations" in n for n in s["notes"]))
        self.assertEqual(s["model_vs_airport"]["potrero"]["n"], 0)

    def test_formatting_lists_episodes(self):
        s, _ = self.run_baseline()
        text = baseline.format_baseline(s)
        self.assertIn("30 episodes", text)
        self.assertIn("Potrero", text)

    def test_iem_parser(self):
        rows = baseline.parse_iem(IEM_TEXT, "KSFO")
        self.assertEqual(len(rows), 2)
        r = rows[0]
        self.assertEqual((r.valid_utc, r.sub, r.source), ("2026-11-10T09:56:00Z", "KSFO", "iem_asos"))
        self.assertAlmostEqual(r.temp_c, 9.0, delta=0.01)
        self.assertAlmostEqual(r.wind_mph, 11.51, delta=0.01)         # 10 knots
        self.assertAlmostEqual(r.precip_mm, 2.54, delta=0.01)         # 0.10 inch
        self.assertIsNone(r.gust_mph)                                 # 'M' = missing
        self.assertIsNone(rows[1].precip_mm)
        csv_style = "station,valid,tmpf,dwpf,relh,sknt,gust,p01i\nSFO,2026-11-10 09:56,50,45,80,5,M,0.00\n"
        self.assertAlmostEqual(baseline.parse_iem(csv_style, "KSFO")[0].temp_c, 10.0, delta=0.01)
        with self.assertRaises(ValueError):
            baseline.parse_iem("<html>error</html>\nmore", "KSFO")


FORM_CSV = (
    'Timestamp,"Which zone?","How slick is the road? (0-3)","Is there visible mist?","Skate grip (1-5)",'
    '"Road surface temperature (°F)","Which side of the street?","Anything else?"\n'
    '11/12/2026 6:30:00,Potrero Hill,2 - Slick,Yes,2,48.2,South,<script>alert(1)</script>\n'
    '11/12/2026 6:40:00,Back Side 9 (Sunset),0 - Dry,No,5,,North,\n'
    '11/12/2026 6:50:00,Somewhere else,1,No,3,,,\n')


class TestFormImport(unittest.TestCase):
    def setUp(self):
        self.conn, self.cfg = db.connect(":memory:"), make_cfg()
        self.cfg["zones"]["back_side_9"] = {"label": "Back Side 9 (Sunset)", "lat": 37.75, "lon": -122.47}

    def test_google_form_style_export(self):
        res = reports.import_text(self.conn, FORM_CSV, "America/Los_Angeles", self.cfg["zones"])
        self.assertEqual(res, {"added": 2, "already_had": 0, "skipped": 1})   # unknown zone skipped
        r = self.conn.execute("SELECT * FROM reports ORDER BY ts_utc").fetchall()
        self.assertEqual((r[0]["zone"], r[0]["slick"], r[0]["mist"], r[0]["grip"], r[0]["side"]), ("potrero", 2, 1, 2, "South"))
        self.assertAlmostEqual(r[0]["surface_temp_c"], 9.0, delta=0.01)       # 48.2 F -> 9.0 C
        self.assertEqual((r[1]["zone"], r[1]["slick"]), ("back_side_9", 0))

    def test_reimport_does_not_duplicate(self):
        reports.import_text(self.conn, FORM_CSV, "America/Los_Angeles", self.cfg["zones"])
        res = reports.import_text(self.conn, FORM_CSV, "America/Los_Angeles", self.cfg["zones"])
        self.assertEqual((res["added"], res["already_had"]), (0, 2))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM reports").fetchone()["c"], 2)

    def test_sync_through_collect(self):
        class Csv(FakeHttp):
            def get_bytes(self, url, params=None, headers=None):
                return FORM_CSV.encode() if "sheet" in url else super().get_bytes(url, params, headers)
        self.cfg["reports"] = {"form_csv_url": "https://sheet.example/pub.csv", "interval_min": 30}
        http = Csv(self.cfg["zones"])
        self.assertEqual(collect.sync_reports(self.cfg, self.conn, http, NOW)["added"], 2)
        self.assertIsNone(collect.sync_reports(self.cfg, self.conn, http, NOW + timedelta(minutes=10)))   # not due


class TestSite(unittest.TestCase):
    def setUp(self):
        self.conn, self.cfg = db.connect(":memory:"), make_cfg()
        collect.collect(self.cfg, self.conn, FakeHttp(self.cfg["zones"]), NOW)

    def render(self, now=NOW, cfg=None, baseline_summary=None):
        cfg = cfg or self.cfg
        return site.render(cfg, self.conn, engine.evaluate_all(self.conn, cfg, now), baseline_summary, now)

    def test_page_content(self):
        html = self.render()
        for needle in ("SF Death Fog Watch", "Potrero", "Strong", "Next 12 hours", "Open-Meteo", "forms.example/x",
                       "data from nws, openmeteo", "Baseline not built yet"):
            self.assertIn(needle, html)
        self.assertNotIn("Data is stale", html)

    def test_chart_is_first_and_explains_why(self):
        html = self.render()
        self.assertLess(html.index("Death fog chance"), html.index("Next 12 hours"))     # graph sits above the zone cards
        for needle in ('<svg class="chart"', 'id="zs0"', 'id="zs1"', "Calm wind", "No sun", "Why it may happen", ">now<"):
            self.assertIn(needle, html)
        self.assertNotIn("<script", html.lower())

    def test_timeline_parts_add_up_to_score(self):
        for z in self.cfg["zones"]:
            tl = engine.timeline(self.conn, self.cfg, z, NOW)
            self.assertGreater(len(tl), 20)
            self.assertTrue(any(h["t"] < NOW for h in tl) and any(h["t"] > NOW for h in tl))   # past and future both shown
            for h in tl:
                self.assertAlmostEqual(sum(h["parts"].values()), h["score"], places=6)

    def test_chart_without_forecast_data_and_hostile_label(self):
        self.cfg["zones"]["potrero"]["label"] = "<b>x</b>"
        self.conn.execute("DELETE FROM readings WHERE zone='control'")
        html = self.render()
        self.assertIn("No forecast data for Control yet", html)
        self.assertNotIn("<b>x</b>", html)

    def test_stale_banner(self):
        self.assertIn("Data is stale", self.render(now=NOW + timedelta(hours=3)))

    def test_nothing_from_users_or_config_is_injected_raw(self):
        self.cfg["zones"]["potrero"]["label"] = "<script>alert(1)</script>"
        html = self.render()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_baseline_section_and_files(self):
        s, _ = TestBaseline().run_baseline()
        self.assertIn("Last 30 days", self.render(baseline_summary=s))
        with tempfile.TemporaryDirectory() as d:
            bp = os.path.join(d, "b.json")
            json.dump(s, open(bp, "w"))
            out = site.write_site(self.cfg, self.conn, NOW, os.path.join(d, "site"), bp)
            self.assertTrue(out.exists())
            status = json.load(open(os.path.join(d, "site", "status.json")))
            self.assertTrue(any(z["zone"] == "potrero" and z["level"] == "strong" for z in status["zones"]))
            self.assertTrue(os.path.exists(os.path.join(d, "site", "baseline.json")))


class TestSeparation(unittest.TestCase):
    """The point of the layering: a detector only sees WeatherState, whichever source produced the numbers."""

    def test_custom_detector_plugin_is_selected_by_config(self):
        @register_detector("always_watch_test")
        class AlwaysWatch(Detector):
            def score(self, s):
                return 45.0, [{"trigger": "custom", "hit": True, "detail": f"saw {s.temp_c:.1f}C", "points": 45}]
        conn, cfg = db.connect(":memory:"), make_cfg(detector={"type": "always_watch_test"})
        collect.collect(cfg, conn, FakeHttp(cfg["zones"]), NOW)
        ev = {(e["zone"], e["mode"]): e for e in engine.evaluate_all(conn, cfg, NOW)}
        self.assertEqual(ev[("potrero", "now")]["level"], "watch")
        self.assertEqual(ev[("potrero", "now")]["reasons"][0]["detail"], "saw 9.4C")   # NWS temperature, via the merged state

    def test_same_values_same_score_regardless_of_source(self):
        from deathfog.detectors import build_detector
        d = build_detector({})
        vals = dict(temp_c=9, dewpoint_c=8.5, rh_pct=96, wind_mph=3, solar_wm2=0, high_24h_c=18)
        a = WeatherState(**vals, provenance={"temp_c": "nws"})
        b = WeatherState(**vals, provenance={"temp_c": "purpleair"})
        self.assertEqual(d.score(a), d.score(b))

    def test_purpleair_can_replace_nws_without_touching_the_detector(self):
        cfg = make_cfg(state={"obs_sources": ["purpleair", "nws"]})
        cfg["zones"]["potrero"]["purpleair_sensors"] = [111, 222]
        cfg["sources"]["purpleair"] = {"enabled": True, "interval_min": 10}
        os.environ["PURPLEAIR_API_KEY"] = "test"
        conn = db.connect(":memory:")
        collect.collect(cfg, conn, FakeHttp(cfg["zones"]), NOW)
        e = engine.evaluate_zone(conn, cfg, "potrero", NOW)[0]
        self.assertEqual(e["provenance"]["temp_c"], "purpleair")      # sensors outrank the airport
        self.assertEqual(e["provenance"]["wind_mph"], "openmeteo")    # PurpleAir has no wind, so the model fills it


if __name__ == "__main__":
    unittest.main()
