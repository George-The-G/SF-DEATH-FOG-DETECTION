"""tools/make_bus_schedule.py: GTFS zip -> scheduled passing times near each district (epoch seconds, local calendar, DST-safe)."""
import datetime as dt
import importlib.util
import io
import os
import unittest
import zipfile
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("make_bus_schedule", os.path.join(HERE, "..", "tools", "make_bus_schedule.py"))
mbs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mbs)

BOX = {"19": (37.755, -122.410, 37.770, -122.396)}


def gtfs():
    files = {
        "routes.txt": "route_id,route_short_name\nR19,19\nR38,38\n",
        "trips.txt": "route_id,service_id,trip_id,direction_id\nR19,WK,t1,0\nR19,WK,t2,1\nR19,SA,t3,0\nR38,WK,x1,0\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                        "WK,1,1,1,1,1,0,0,20261101,20261231\nSA,0,0,0,0,0,1,0,20261101,20261231\n",
        "calendar_dates.txt": "service_id,date,exception_type\nWK,20261126,2\nSA,20261127,1\n",  # Thanksgiving: no weekday service, a Saturday timetable instead
        "stops.txt": "stop_id,stop_lat,stop_lon\nA,37.7590,-122.4030\nB,37.7640,-122.4030\nC,37.7675,-122.4030\nFAR,37.7800,-122.4500\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                          "t1,08:00:00,08:00:00,FAR,1\nt1,08:10:00,08:10:00,A,2\nt1,08:12:30,08:12:30,B,3\nt1,08:14:00,08:14:00,C,4\n"
                          "t2,25:10:00,25:10:00,C,1\nt2,25:12:00,25:12:00,B,2\nt2,25:14:00,25:14:00,FAR,3\n"
                          "t3,09:00:00,09:00:00,A,1\nt3,09:05:00,09:05:00,C,2\nx1,08:00:00,08:00:00,A,1\nx1,08:05:00,08:05:00,C,2\n",
    }
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as z:
        for n, c in files.items():
            z.writestr(n, c)
    bio.seek(0)
    return zipfile.ZipFile(bio)


class TestSchedule(unittest.TestCase):
    def test_trips_keep_only_stops_in_the_box_with_local_epochs(self):
        r = mbs.build(gtfs(), BOX, dt.date(2026, 11, 23), 3)  # Mon 23, Tue 24, Wed 25 (all weekdays, no exceptions)
        trips = r["lines"]["19"]["trips"]
        ids = [t["id"] for t in trips]
        self.assertEqual(sorted(ids), sorted([f"{t}@2026-11-{d}" for t in ("t1", "t2") for d in (23, 24, 25)]))  # t3 is Saturday only; x1 is another line
        trips = [t for t in trips if t["id"].startswith("t1")]
        t = trips[0]
        mid = dt.datetime(2026, 11, 23, tzinfo=ZoneInfo(mbs.TZ)).timestamp()
        self.assertEqual([p[0] - int(mid) for p in t["p"]], [8 * 3600 + 600, 8 * 3600 + 750, 8 * 3600 + 840])
        self.assertEqual(t["p"][0][1:], [37.759, -122.403])
        self.assertEqual(t["d"], 0)

    def test_service_exceptions_and_overnight_times(self):
        r = mbs.build(gtfs(), BOX, dt.date(2026, 11, 26), 1)  # Thanksgiving: weekday service removed, nothing else runs
        self.assertEqual(r["lines"]["19"]["trips"], [])
        r = mbs.build(gtfs(), BOX, dt.date(2026, 11, 27), 1)  # Friday: weekday service plus the Saturday timetable added by an exception
        self.assertEqual(sorted(t["id"] for t in r["lines"]["19"]["trips"]), ["t1@2026-11-27", "t2@2026-11-27", "t3@2026-11-27"])
        t2 = [t for t in r["lines"]["19"]["trips"] if t["id"].startswith("t2")][0]
        mid = dt.datetime(2026, 11, 27, tzinfo=ZoneInfo(mbs.TZ)).timestamp()
        self.assertEqual(t2["p"][0][0] - int(mid), 25 * 3600 + 600)  # 25:10 is 01:10 the next day, still counted from the service day's midnight
        self.assertEqual(t2["d"], 1)

    def test_dst_change_uses_local_midnight(self):
        # US DST ended 2026-11-01: a trip at 08:00 that day is 08:00 PST (UTC-8), the day before 08:00 PDT (UTC-7)
        g = gtfs()
        a = mbs.build(g, BOX, dt.date(2026, 10, 30), 1)  # Fri, before the change; calendar starts 11-01 so nothing runs
        self.assertEqual(a["lines"]["19"]["trips"], [])
        b = mbs.build(g, BOX, dt.date(2026, 11, 2), 1)
        t = b["lines"]["19"]["trips"][0]
        self.assertEqual(dt.datetime.fromtimestamp(t["p"][0][0], ZoneInfo(mbs.TZ)).strftime("%H:%M"), "08:10")

    def test_scene_geo_matches_the_viewer(self):
        # busgeo.js: the stop "Rhode Island St & 15th St" (37.76747, -122.40298) is scene (12483.5, -8672.1)
        x, z = mbs.ll_to_scene(37.76747, -122.40298)
        self.assertLess(abs(x - 12483.5) + abs(z + 8672.1), 50)
        lat, lon = mbs.scene_to_ll(x, z)
        self.assertAlmostEqual(lat, 37.76747, places=5)
        self.assertAlmostEqual(lon, -122.40298, places=5)

    def test_district_boxes_from_the_repo_config(self):
        import json
        root = os.path.join(HERE, "..", "site", "v3", "data")
        cfg = json.load(open(os.path.join(root, "bus_config.json"), encoding="utf-8"))
        routes = json.load(open(os.path.join(root, "routes.json"), encoding="utf-8"))
        boxes = mbs.district_boxes(cfg, routes)
        self.assertEqual(sorted(boxes), ["19", "66"])
        la0, lo0, la1, lo1 = boxes["19"]
        self.assertTrue(la0 < 37.7675 < la1 and lo0 < -122.403 < lo1)  # contains Rhode Island & 15th


if __name__ == "__main__":
    unittest.main()
