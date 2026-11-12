"""Ribbon data contract: site/v3/data/ribbons.bin, streets_r.u8, index.json (needs `python -m microclimate routes` outputs)."""
import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
V3 = ROOT / "site" / "v3"
POINTS = Path.home() / "deathfog-data" / "grids" / "zone" / "points.csv"
HAVE = (V3 / "data" / "ribbons.bin").exists() and POINTS.exists()


def _load(name):
    spec = importlib.util.spec_from_file_location(name, V3 / "tools" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rs = _load("reslice_replay")


class TestSmoothing(unittest.TestCase):
    def test_properties(self):
        rng = np.random.default_rng(1)
        a = np.clip(np.cumsum(rng.integers(-3, 4, (97, 40)), axis=1) + 50, 0, 100).astype(np.uint8)
        a[:, 7] = 255
        out = rs.smooth(a)
        self.assertTrue((out[:, 7] == 255).all())
        self.assertTrue(((out == 255) == (a == 255)).all(), "255 sentinel must survive and nothing else become 255")
        ok = a != 255
        self.assertLessEqual(abs(out[ok].astype(float).mean() - a[ok].astype(float).mean()), 0.5)

    def test_never_across_strips(self):
        rng = np.random.default_rng(2)
        a = rng.integers(0, 101, (97, 40)).astype(np.uint8)
        big = np.concatenate([a, np.full((97, 10), 100, np.uint8)], axis=1)
        rib = {"src": np.arange(50), "stripStart": np.array([0, 40, 50]), "nStrips": 2}
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "s.u8"
            big.tofile(f)
            r = rs.reslice_night(f, 50, rib)
        self.assertTrue((r[:, :40] == rs.smooth(a)).all())
        self.assertTrue((r[:, 40:] == 100).all())

    def test_edge_preserving(self):
        step = np.zeros((1, 20), np.uint8)
        step[:, 10:] = 80
        self.assertTrue((rs.smooth(step) == step).all())


@unittest.skipUnless(HAVE, "ribbon data not generated (python -m microclimate routes)")
class TestRibbonData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rib = rs.read_ribbons(V3 / "data" / "ribbons.bin")
        cls.pts = list(csv.DictReader(open(POINTS, newline="")))
        ss = cls.rib["stripStart"]
        cls.strips = [(int(a), int(b)) for a, b in zip(ss[:-1], ss[1:])]

    def test_header_and_sizes(self):
        rib = self.rib
        S = rib["S"]
        self.assertEqual(rib["version"], 2)
        self.assertTrue(0 < S < 40000)
        self.assertEqual(int(rib["stripStart"][-1]), S)
        self.assertEqual(rib["nIdx"], sum(6 * (b - a - 1) for a, b in self.strips))
        meta = json.load(open(V3 / "data" / "ribbons_meta.json"))
        self.assertEqual((meta["S"], meta["nStrips"], len(meta["strips"])), (S, rib["nStrips"], rib["nStrips"]))
        self.assertLess(int(rib["st"].max()), len(meta["streets"]))
        self.assertEqual((V3 / "data" / "ribbons.bin").stat().st_size % 4, 0)

    def test_strips_have_two_samples_and_bounded_spacing(self):
        P = self.rib["pos"][:, [0, 2]]
        for a, b in self.strips:
            self.assertGreaterEqual(b - a, 2)
            d = np.hypot(*np.diff(P[a:b], axis=0).T)
            # points are L/floor(L/10) apart, i.e. 10-20 m on single-point segments (the 12 m target only holds for L >= 20 m)
            self.assertLessEqual(d.max(), 22.0 if self.rib["cls"][a] == 0 else 46.0, (a, b, d.max()))

    def test_every_route_point_included_and_src_unique(self):
        src, pts = self.rib["src"], self.pts
        self.assertLess(int(src.max()), len(pts))
        route_rows = {int(r["id"]) for r in pts if int(r["route_mask"]) != 0}
        self.assertTrue(route_rows <= set(int(s) for s in src), "route points missing from ribbons")
        seen, dups = {}, []
        cl = self.rib["cls"]
        for i, s in enumerate(src.tolist()):
            key = (s, int(cl[i]))     # a route street is drawn twice: route strip (cls 0) and, underneath, its district strip (cls 1)
            if key in seen:
                dups.append((seen[key], i))
            seen[key] = i
        ends = set()
        for a, b in self.strips:
            ends |= {a, a + 1, b - 2, b - 1}
        self.assertTrue(all(i in ends and j in ends for i, j in dups), "duplicate src away from a strip end")
        for i in range(0, len(src), 5):
            if self.rib["cls"][i] == 0:
                self.assertNotEqual(int(pts[int(src[i])]["route_mask"]), 0)

    def test_district_streets(self):
        """Version 3 data: every street of the zones is a ribbon (cls 1): no mask bits, no fade, roughly 20 m sample spacing."""
        P, cls = self.rib["pos"][:, [0, 2]], self.rib["cls"]
        self.assertGreater(int((cls == 1).sum()), 3 * int((cls == 0).sum()), "district streets should dominate the sample count")
        self.assertTrue((self.rib["mask"][cls == 1] == 0).all())
        self.assertTrue((self.rib["fade"][cls == 1] == 255).all())
        for a, b in self.strips:
            if cls[a] == 1:
                d = np.hypot(*np.diff(P[a:b], axis=0).T)
                self.assertLessEqual(d.max(), 46.0, (a, d.max()))
        self.assertTrue(json.load(open(V3 / "data" / "ribbons_meta.json")).get("district"))

    def test_fade_and_normals(self):
        self.assertEqual(int(self.rib["fade"].max()), 255)
        n = np.hypot(self.rib["nrm"][:, 0], self.rib["nrm"][:, 1])
        self.assertGreaterEqual(float(n.min()), 0.99)
        self.assertLessEqual(float(n.max()), 2.0001)

    def test_replay_files(self):
        idx = json.load(open(V3 / "replay" / "index.json"))
        self.assertEqual(idx["schema"], 2)
        for n in idx["nights"]:
            for k in ("id", "label", "kind", "n_frames", "start_iso", "streets", "fog", "fog_grid", "weather", "w_dawn_by_route"):
                self.assertIn(k, n)
            self.assertEqual(n["streets_r"], f"{n['id']}/streets_r.u8")
            self.assertEqual((n["ribbons"], n["ribbons_n"]), ("data/ribbons.bin", self.rib["S"]))
            sr = V3 / "replay" / n["streets_r"]
            self.assertEqual(sr.stat().st_size, 97 * self.rib["S"])
            W = np.fromfile(sr, np.uint8)
            self.assertTrue(((W <= 100) | (W == 255)).all())
            self.assertTrue((V3 / "replay" / n["streets"]).exists())


if __name__ == "__main__":
    unittest.main()
