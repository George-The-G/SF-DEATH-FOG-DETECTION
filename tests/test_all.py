import os
import tempfile
import unittest
from datetime import timedelta

from helpers import NOW, FakeHttp, NWS_PAYLOAD, make_cfg

from deathfog import alerts, budget, collect, db, engine, registry, reports  # noqa: E402
from deathfog import sources as _s  # noqa: E402,F401
from deathfog.config import load_config  # noqa: E402
from deathfog.detectors import DETECTORS, build_detector  # noqa: E402
from deathfog.model import WeatherState  # noqa: E402
from deathfog.util import dew_point_c, iso  # noqa: E402


class TestUtil(unittest.TestCase):
    def test_dew_point(self):
        self.assertAlmostEqual(dew_point_c(20, 50), 9.3, delta=0.2)
        self.assertAlmostEqual(dew_point_c(10, 100), 10.0, delta=0.05)


class TestDetector(unittest.TestCase):
    def setUp(self):
        self.d = build_detector({"detector": {}})

    def test_strong(self):
        s = WeatherState(temp_c=9, dewpoint_c=8.5, rh_pct=96, wind_mph=3, solar_wm2=0, high_24h_c=18, temp_3h_ago_c=12.5, precip_3h_mm=0)
        score, reasons = self.d.score(s)
        self.assertEqual(score, 100)
        self.assertEqual({r["trigger"] for r in reasons if r["hit"]}, {"saturation", "calm", "swing", "cooling", "no_sun"})
        self.assertEqual(self.d.level_for(score), "strong")

    def test_dry_windy_day_is_quiet(self):
        s = WeatherState(temp_c=20, dewpoint_c=8, rh_pct=50, wind_mph=15, solar_wm2=600, high_24h_c=21, temp_3h_ago_c=19.5)
        self.assertEqual(self.d.score(s)[0], 0)

    def test_rain_caps_score(self):
        s = WeatherState(temp_c=9, dewpoint_c=8.5, rh_pct=96, wind_mph=3, solar_wm2=0, high_24h_c=18, precip_3h_mm=1.5)
        self.assertEqual(self.d.score(s)[0], 20)

    def test_missing_data_is_not_a_hit(self):
        self.assertEqual(self.d.score(WeatherState(temp_c=9, rh_pct=97))[0], 40)   # dew point derived, nothing else known

    def test_params_come_from_config(self):
        loose = build_detector({"detector": {"spread_hit_c": 8.0}})
        s = WeatherState(temp_c=15, dewpoint_c=9, rh_pct=95)
        self.assertEqual(loose.score(s)[0], 40)
        self.assertLess(self.d.score(s)[0], 40)

    def test_unknown_detector(self):
        with self.assertRaises(KeyError):
            build_detector({"detector": {"type": "nope"}})
        self.assertIn("death_fog_v1", DETECTORS)


class TestSources(unittest.TestCase):
    def test_nws_units(self):
        from deathfog.sources.nws import parse_observation
        r = parse_observation("KSFO", NWS_PAYLOAD, "nws")
        self.assertAlmostEqual(r.wind_mph, 4.97, delta=0.02)   # 8 km/h
        self.assertEqual(r.valid_utc, "2026-11-12T09:56:00Z")
        self.assertIsNone(r.precip_mm)

    def test_openmeteo_multi_location(self):
        cfg = make_cfg()
        src = registry.SOURCES["openmeteo"]("openmeteo", cfg["sources"]["openmeteo"], cfg["zones"])
        out = src.fetch(FakeHttp(cfg["zones"]), NOW)
        self.assertEqual(len(out), 49 * 2)
        self.assertEqual({r.zone for r in out}, {"potrero", "control"})
        self.assertEqual(src.cost(), 2.0)   # 2 zones x 1 (8 variables <= 10)

    def test_openmeteo_default_endpoint_is_best_match(self):
        cfg = make_cfg()
        http = FakeHttp(cfg["zones"])
        registry.SOURCES["openmeteo"]("openmeteo", {}, cfg["zones"]).fetch(http, NOW)
        url, params = http.calls[0]
        self.assertTrue(url.endswith("/v1/forecast"))
        self.assertNotIn("models", params)         # leave model choice to Open-Meteo (RRFS over North America)

    def test_purpleair(self):
        cfg = make_cfg()
        cfg["zones"]["potrero"]["purpleair_sensors"] = [111, 222]
        os.environ["PURPLEAIR_API_KEY"] = "test"
        src = registry.SOURCES["purpleair"]("purpleair", {"enabled": True}, cfg["zones"])
        out = src.fetch(FakeHttp(), NOW)
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(out[0].temp_c, 10.0, delta=0.01)   # 50F
        self.assertEqual(src.cost(), 5 + 3 * 2)

    def test_plugin_source_loads_and_runs(self):
        plugins = os.path.join(os.path.dirname(__file__), "..", "plugins")
        self.assertIn("example_local_csv.py", registry.load_plugins(plugins))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "log.csv")
            with open(p, "w") as f:
                f.write("timestamp,temp_c,rh_pct\n2026-11-12T09:50:00Z,9.0,97\n")
            r = registry.SOURCES["local_csv"]("mine", {"path": p, "zone": "potrero"}, {}).fetch(None, NOW)[0]
            self.assertEqual((r.zone, r.kind, r.temp_c), ("potrero", "obs", 9.0))


class TestCollect(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.cfg = make_cfg()
        self.http = FakeHttp(self.cfg["zones"])

    def statuses(self, now, **kw):
        return {x["source"]: x["status"] for x in collect.collect(self.cfg, self.conn, self.http, now, **kw)}

    def test_due_and_budget(self):
        self.assertEqual(self.statuses(NOW), {"nws": "ok", "openmeteo": "ok"})
        self.assertEqual(self.statuses(NOW + timedelta(minutes=10)), {"nws": "ok", "openmeteo": "not_due"})
        self.assertEqual(self.statuses(NOW + timedelta(minutes=30))["openmeteo"], "ok")

    def test_budget_blocks(self):
        self.cfg["sources"]["openmeteo"]["daily_budget"] = 3     # one fetch costs 2, so the second is refused
        collect.collect(self.cfg, self.conn, self.http, NOW)
        self.assertEqual(self.statuses(NOW + timedelta(minutes=30))["openmeteo"], "over_budget")

    def test_duplicate_obs_ignored_model_history_kept(self):
        collect.collect(self.cfg, self.conn, self.http, NOW)
        collect.collect(self.cfg, self.conn, self.http, NOW + timedelta(minutes=30))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM readings WHERE source='nws'").fetchone()["c"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(DISTINCT fetched_utc) c FROM readings WHERE kind='model'").fetchone()["c"], 2)

    def test_one_failing_source_does_not_stop_others(self):
        class Boom(FakeHttp):
            def get_json(self, url, params=None, headers=None):
                if "weather.gov" in url:
                    raise RuntimeError("nws down")
                return super().get_json(url, params, headers)
        r = {x["source"]: x["status"] for x in collect.collect(self.cfg, self.conn, Boom(self.cfg["zones"]), NOW)}
        self.assertEqual(r, {"nws": "error", "openmeteo": "ok"})

    def test_prune_keeps_history_sources(self):
        collect.collect(self.cfg, self.conn, self.http, NOW)
        self.conn.execute("INSERT INTO readings(source,kind,zone,valid_utc,fetched_utc) VALUES ('openmeteo_hist','model','potrero','2026-10-01T00:00:00Z','2026-11-01T00:00:00Z')")
        db.prune_old(self.conn, NOW + timedelta(days=3))
        srcs = {r["source"] for r in self.conn.execute("SELECT DISTINCT source FROM readings")}
        self.assertEqual(srcs, {"nws", "openmeteo_hist"})     # old live model issue pruned; backfill and observations kept


class TestEngineAndAlerts(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.cfg = make_cfg()
        collect.collect(self.cfg, self.conn, FakeHttp(self.cfg["zones"]), NOW)

    def test_scores_zones(self):
        ev = {(e["zone"], e["mode"]): e for e in engine.evaluate_all(self.conn, self.cfg, NOW)}
        self.assertEqual(ev[("potrero", "now")]["level"], "strong")
        self.assertEqual(ev[("control", "now")]["level"], "none")       # 100 x 0.3 = 30
        self.assertEqual(ev[("potrero", "now")]["inputs"]["basis"], "obs+model")
        self.assertEqual(ev[("potrero", "ahead")]["score"], 100)
        self.assertEqual(len(ev[("potrero", "ahead")]["hourly"]), 12)

    def test_provenance_shows_which_source_supplied_what(self):
        prov = engine.evaluate_zone(self.conn, self.cfg, "potrero", NOW)[0]["provenance"]
        self.assertEqual(prov["temp_c"], "nws")          # measured
        self.assertEqual(prov["solar_wm2"], "openmeteo") # not measured by the airport, so the model fills it
        self.assertEqual(prov["high_24h_c"], "openmeteo")

    def test_ahead_sees_a_future_event(self):
        ev = {(e["zone"], e["mode"]): e for e in engine.evaluate_all(self.conn, self.cfg, NOW - timedelta(hours=8))}
        self.assertEqual(ev[("potrero", "ahead")]["level"], "strong")

    def test_store_evaluations_is_throttled(self):
        evals = engine.evaluate_all(self.conn, self.cfg, NOW)
        n1 = engine.store_evaluations(self.conn, evals, NOW)
        n2 = engine.store_evaluations(self.conn, evals, NOW + timedelta(minutes=10))   # same level, too soon
        n3 = engine.store_evaluations(self.conn, evals, NOW + timedelta(minutes=40))   # old enough
        self.assertEqual((n1, n2, n3), (4, 0, 4))

    def test_alert_once_then_cooldown(self):
        sent = []
        notifier = lambda title, body, prio, click: sent.append((title, body, prio, click))  # noqa: E731
        evals = engine.evaluate_all(self.conn, self.cfg, NOW)
        alerts.process_alerts(self.conn, self.cfg, evals, NOW, notifier)
        self.assertEqual(len(sent), 1)                                  # potrero only; 'ahead' suppressed, control quiet
        self.assertIn("Potrero", sent[0][0])
        self.assertEqual(sent[0][2], "high")
        self.assertIn("https://forms.example/x", sent[0][1])
        alerts.process_alerts(self.conn, self.cfg, evals, NOW + timedelta(minutes=10), notifier)
        self.assertEqual(len(sent), 1)                                  # inside cooldown
        alerts.process_alerts(self.conn, self.cfg, evals, NOW + timedelta(hours=5), notifier)
        self.assertEqual(len(sent), 2)                                  # cooldown over


class TestVerify(unittest.TestCase):
    def test_matrix(self):
        conn, cfg = db.connect(":memory:"), make_cfg()
        collect.collect(cfg, conn, FakeHttp(cfg["zones"]), NOW)
        engine.store_evaluations(conn, engine.evaluate_all(conn, cfg, NOW), NOW)
        reports.add_report(conn, iso(NOW + timedelta(minutes=10)), "potrero", slick=3)      # hit
        reports.add_report(conn, iso(NOW + timedelta(minutes=15)), "potrero", slick=0)      # false alarm
        reports.add_report(conn, iso(NOW + timedelta(minutes=10)), "control", slick=0)      # correct quiet
        reports.add_report(conn, iso(NOW + timedelta(minutes=12)), "control", slick=2)      # miss
        reports.add_report(conn, iso(NOW + timedelta(days=2)), "potrero", slick=2)          # no coverage
        r = reports.verify(conn, days=30, now=NOW + timedelta(days=3))
        self.assertEqual((r["zones"]["potrero"]["tp"], r["zones"]["potrero"]["fp"]), (1, 1))
        self.assertEqual((r["zones"]["control"]["tn"], r["zones"]["control"]["fn"]), (1, 1))
        self.assertEqual(r["uncovered_reports"], 1)
        self.assertEqual(r["total"]["precision"], 0.5)
        self.assertEqual(r["trigger_hits_in_real_events"].get("saturation"), 2)   # both positive reports saw it
        self.assertIn("Overall", reports.format_verify(r))

    def test_import_csv_local_time(self):
        conn = db.connect(":memory:")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.csv")
            with open(p, "w") as f:
                f.write("Timestamp,Zone,Slick,Mist,Surface_temp_c\n11/12/2026 6:30:00,potrero,2,yes,8.5\n")
            self.assertEqual(reports.import_csv(conn, p, "America/Los_Angeles"), 1)
        row = conn.execute("SELECT * FROM reports").fetchone()
        self.assertEqual((row["slick"], row["mist"], row["surface_temp_c"]), (2, 1, 8.5))
        self.assertEqual(row["ts_utc"], "2026-11-12T14:30:00Z")       # 6:30 PST = 14:30Z


class TestBudget(unittest.TestCase):
    def test_estimates(self):
        cfg = make_cfg()
        rows = {r["source"]: r for r in budget.estimate(cfg)}
        self.assertEqual(rows["openmeteo"]["daily"], 96)               # 2 zones x 48 runs
        for s in cfg["sources"].values():
            s["interval_min"] = 10
        rows = {r["source"]: r for r in budget.estimate(cfg)}
        self.assertEqual(rows["openmeteo"]["daily"], 288)              # 2 zones x 144 runs
        self.assertLess(rows["openmeteo"]["pct_of_limit"], 3)
        cfg["zones"]["potrero"]["purpleair_sensors"] = list(range(10))
        cfg["sources"]["purpleair"] = {"enabled": True, "interval_min": 10}
        self.assertEqual({r["source"]: r for r in budget.estimate(cfg)}["purpleair"]["daily"], 35 * 144)


class TestConfigFile(unittest.TestCase):
    def test_shipped_config_loads(self):
        cfg = load_config(os.path.join(os.path.dirname(__file__), "..", "config.toml"), environ={})
        self.assertIn("potrero_hill", cfg["zones"])
        self.assertEqual([s.name for s in collect.build_sources(cfg)], ["nws", "openmeteo"])
        self.assertEqual(build_detector(cfg).type_name, "death_fog_v1")


if __name__ == "__main__":
    unittest.main()
