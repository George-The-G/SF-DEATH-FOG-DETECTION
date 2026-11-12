"""Route resolver tests on a synthetic street grid (no network, no city data): intersection lookup, waypoint snapping, routing."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from microclimate import routes

LAT0, LON0 = 37.75, -122.47
DLAT, DLON = 0.0009, 0.0011  # ~100 m x ~97 m blocks


def make_dem(path):
    """Elevation 100 m at the north edge falling 0.1 m per metre toward the south (lat gradient), EPSG:4326."""
    n = 60
    tf = from_origin(LON0 - 0.01, LAT0 + 0.02, 0.0007, 0.0007)
    lat = LAT0 + 0.02 - 0.0007 * (np.arange(n) + 0.5)
    z = (100.0 + (lat - (LAT0 + 0.02)) * 111000 * 0.1).astype("float32")
    arr = np.tile(z[:, None], (1, n))
    with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=1, dtype="float32", crs="EPSG:4326", transform=tf) as dst:
        dst.write(arr, 1)


def make_osm():
    """3 avenues (1st..3rd Avenue, north-south) x 3 streets (A..C Street, east-west); node id = 100*row + col. Intersection
    (row 1, col 1) is doubled (ids 11 and 111) to mimic dual-carriageway nodes."""
    el, names_v, names_h = [], ["1st Avenue", "2nd Avenue", "3rd Avenue"], ["A Street", "B Street", "C Street"]
    for r in range(3):
        for c in range(3):
            el.append({"type": "node", "id": 100 * r + c, "lat": LAT0 - r * DLAT, "lon": LON0 + c * DLON})
    el.append({"type": "node", "id": 111, "lat": LAT0 - DLAT - 0.00002, "lon": LON0 + DLON + 0.00002})
    wid = 1
    for c in range(3):  # avenues
        nodes = [100 * r + c for r in range(3)]

        el.append({"type": "way", "id": wid, "nodes": nodes, "tags": {"highway": "residential", "name": names_v[c]}})
        wid += 1
    for r in range(3):  # streets
        nodes = [100 * r + c for c in range(3)]
        el.append({"type": "way", "id": wid, "nodes": nodes, "tags": {"highway": "residential", "name": names_h[r]}})
        wid += 1
    el.append({"type": "way", "id": wid, "nodes": [101, 111], "tags": {"highway": "residential", "name": "2nd Avenue"}})
    wid += 1
    el.append({"type": "way", "id": wid, "nodes": [111, 102], "tags": {"highway": "residential", "name": "B Street"}})
    wid += 1
    # a stairway and an alley must never be used
    el.append({"type": "way", "id": 900, "nodes": [0, 202], "tags": {"highway": "steps", "name": "Steps"}})
    el.append({"type": "way", "id": 901, "nodes": [0, 200], "tags": {"highway": "service", "name": "Alley"}})
    return {"elements": el}


class RouteResolver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        dem = Path(cls.tmp.name) / "dem.tif"
        make_dem(dem)
        cls.G = routes.StreetGraph(make_osm(), dem)

    @classmethod
    def tearDownClass(cls):
        cls.G.Z.dem.close()
        cls.tmp.cleanup()

    def test_name_keys(self):
        self.assertEqual(routes.name_key("9th Ave"), routes.name_key("9th Avenue"))
        self.assertEqual(routes.name_key("Kansas St"), routes.name_key("Kansas Street"))
        self.assertNotEqual(routes.name_key("Kansas Street"), routes.name_key("Arkansas Street"))
        self.assertNotEqual(routes.name_key("19th Avenue"), routes.name_key("9th Avenue"))
        self.assertEqual(routes.name_key("Laguna Honda Blvd"), "laguna honda")

    def test_split_intersection(self):
        self.assertEqual(routes.split_intersection("9th Ave & Judah St"), ["9th Ave", "Judah St"])
        self.assertEqual(routes.split_intersection("9th Ave and Judah St"), ["9th Ave", "Judah St"])
        self.assertIsNone(routes.split_intersection("37.7,-122.4"))
        self.assertEqual(routes.parse_latlon("37.7621, -122.4662"), (37.7621, -122.4662))
        self.assertIsNone(routes.parse_latlon("9th Ave & Judah St"))

    def test_intersection_lookup(self):
        self.assertEqual(self.G.candidates("1st Ave & A St"), [0])
        self.assertEqual(self.G.candidates("C Street & 3rd Ave"), [202])  # order and abbreviations do not matter
        self.assertEqual(sorted(self.G.candidates("2nd Avenue & B Street")), [101, 111])  # both dual nodes returned
        with self.assertRaises(ValueError) as cm:
            self.G.candidates("1st Ave & Z St")
        self.assertIn("not found", str(cm.exception))

    def test_waypoint_snapping(self):
        n = self.G.candidates(f"{LAT0 - DLAT + 0.00001:.6f},{LON0 + 0.00002:.6f}")  # a few metres from node 100
        self.assertEqual(n, [100])
        with self.assertRaises(ValueError):
            self.G.candidates(f"{LAT0 + 0.01:.6f},{LON0:.6f}")  # >60 m from any street

    def test_route_and_describe(self):
        path, nodes = self.G.route(["1st Ave & A St", "1st Ave & C St", "3rd Ave & C St"])
        self.assertEqual(nodes, [0, 200, 202])
        self.assertEqual(path, [0, 100, 200, 201, 202])
        d = self.G.describe(path)
        self.assertAlmostEqual(d["length_m"], 2 * DLAT * 111200 + 2 * DLON * 111320 * np.cos(np.radians(LAT0)), delta=6)
        self.assertGreater(d["drop_m"], 15)  # DEM falls ~0.1 m per m to the south
        self.assertEqual(d["climb_m"], 0.0)
        self.assertEqual([s[0] for s in d["streets"]], ["1st Avenue", "C Street"])

    def test_dual_node_choice_is_nearest(self):
        path, nodes = self.G.route(["2nd Ave & B Street", "2nd Ave & C St"])
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[1], 201)
        self.assertIn(nodes[0], (101, 111))

    def test_steps_and_service_not_routable(self):
        path, _ = self.G.route(["1st Ave & A St", "3rd Ave & C St"])
        way_names = {self.G.edge_name.get((a, b)) for a, b in zip(path, path[1:])}
        self.assertTrue(way_names <= {"1st Avenue", "2nd Avenue", "3rd Avenue", "A Street", "B Street", "C Street"})
        self.assertLess(self.G.describe(path)["length_m"], 500)


class RoutesToml(unittest.TestCase):
    def test_repo_file_parses(self):
        rt = routes.load_toml()
        ids = [r["id"] for r in rt["routes"]]
        self.assertEqual(ids[0], "potrero")  # route #1 stays the Potrero route
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len([r for r in rt["routes"] if r["zone"] == "ggh"]), 5)
        for r in rt["routes"]:
            self.assertIn(r["zone"], rt["zones"])
            if not r.get("auto"):
                self.assertGreaterEqual(len(routes.waypoint_list(r)), 2)

    def test_start_via_end_form(self):
        self.assertEqual(routes.waypoint_list({"start": "A & B", "via": ["C & D"], "end": "E & F"}), ["A & B", "C & D", "E & F"])

    def test_bad_toml_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.toml"
            p.write_text('[zone.z]\nname="Z"\nbuilder="corridor"\n[[route]]\nid="a"\nname="A"\nzone="nope"\ncolor="#fff"\nwaypoints=["x & y","z & w"]\n')
            with self.assertRaises(ValueError):
                routes.load_toml(p)
            p.write_text('[zone.z]\nname="Z"\nbuilder="corridor"\n[[route]]\nid="a"\nname="A"\nzone="z"\ncolor="#fff"\n')
            with self.assertRaises(ValueError):
                routes.load_toml(p)

    def test_baseline_file(self):
        bl = routes.baseline()
        self.assertIsNotNone(bl)
        self.assertEqual(bl["n_points"], 14116)
        self.assertEqual(len(bl["points_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
