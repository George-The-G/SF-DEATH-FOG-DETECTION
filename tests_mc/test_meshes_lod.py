"""Plan B building LOD: hidden-wall mask, block-merged far mesh (microclimate/meshes_lod.py)."""
import struct
import unittest
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import box

from microclimate import meshes_lod as L

ROOT = Path(__file__).resolve().parent.parent
BLD = ROOT / "site" / "v3" / "data" / "buildings.bin"


def boxes(spec):
    """spec: [(cx, cy, w, d, ang, base, top)] axis-aligned unless ang given -> Bld with no polygons."""
    a = np.array(spec, dtype=float)
    return L.Bld(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], a[:, 6], [], np.zeros(0), np.zeros(0))


# local +x axis is east for ang 0; local +z points to (sin a, -cos a) = south for ang 0
PZ, MZ, PX, MX = 1, 2, 4, 8


class TestMask(unittest.TestCase):
    def test_row_houses(self):
        # three 8x10 houses side by side along east; the middle one has both x walls hidden
        b = boxes([(100 + 8 * i, 100, 8, 10, 0, 0, 9) for i in range(3)])
        m = L.hidden_mask(b)
        self.assertEqual(m[1], PX | MX)
        self.assertEqual(m[0] & PX, PX)
        self.assertEqual(m[0] & MX, 0)
        self.assertEqual(m[2], MX)

    def test_tall_beside_short(self):
        b = boxes([(100, 100, 8, 10, 0, 0, 20), (108, 100, 8, 10, 0, 0, 8)])
        m = L.hidden_mask(b)
        self.assertEqual(m[0] & PX, 0)   # tall building's wall next to the short one stays visible
        self.assertEqual(m[1] & MX, MX)  # short one's wall is hidden by the tall one

    def test_gap_of_one_metre(self):
        b = boxes([(100, 100, 8, 10, 0, 0, 9), (109, 100, 8, 10, 0, 0, 9)])
        self.assertEqual(L.hidden_mask(b).tolist(), [0, 0])

    def test_partial_cover_not_hidden(self):
        # neighbour covers only half of the wall
        b = boxes([(100, 100, 8, 10, 0, 0, 9), (108, 105, 8, 10, 0, 0, 9)])
        self.assertEqual(L.hidden_mask(b)[0] & PX, 0)

    def test_rotated_frame(self):
        # rotate the row by 90 deg: shared wall is now along local z
        a = np.pi / 2
        b = boxes([(100, 100, 8, 10, a, 0, 9), (100, 108, 8, 10, a, 0, 9)])
        m = L.hidden_mask(b)
        self.assertEqual(bin(int(m[0])).count("1"), 1)
        self.assertEqual(bin(int(m[1])).count("1"), 1)

    def test_file_roundtrip(self):
        m = np.array([0, 5, 15], dtype=np.uint8)
        self.assertEqual(L.read_mask(L.mask_bytes(m)).tolist(), [0, 5, 15])


class TestFar(unittest.TestCase):
    def test_merge_and_height(self):
        foot = np.array([box(0, 0, 10, 10), box(10.5, 0, 20, 10), box(100, 100, 104, 104)], dtype=object)
        base = np.array([0.0, 1.0, 0.0])
        top = np.array([9.0, 10.0, 30.0])
        parts = L.merge_tile(foot, base, top)
        # the first two close up into one part (gap 0.5 m); the 16 m2 shed is dropped
        self.assertEqual(len(parts), 1)
        poly, b, t = parts[0]
        self.assertEqual(b, 0.0)
        self.assertAlmostEqual(t, (9 * 100 + 10 * 95) / 195, 6)

    def test_extrude_counts(self):
        V, T, I = L.extrude([(box(0, 0, 10, 10), 0.0, 8.0)])
        self.assertEqual(len(V), 8)
        self.assertEqual(len(I) // 3, 4 * 2 + 2)  # 4 walls x 2 + 2 roof triangles
        self.assertLess(I.max(), len(V))

    def test_deterministic(self):
        rng = np.random.default_rng(3)
        foot = np.array([box(x, y, x + 8, y + 8) for x, y in rng.uniform(0, 300, (60, 2))], dtype=object)
        h = rng.uniform(4, 40, 60)
        a = L.extrude(L.merge_tile(foot, np.zeros(60), h))
        c = L.extrude(L.merge_tile(foot, np.zeros(60), h))
        self.assertTrue(all(np.array_equal(x, y) for x, y in zip(a, c)))


@unittest.skipUnless(BLD.exists(), "buildings.bin missing")
class TestReal(unittest.TestCase):
    def test_decode_matches_header(self):
        buf = BLD.read_bytes()
        b = L.decode(buf)
        nb, npl, _ = struct.unpack_from("<III", buf, 4)
        self.assertEqual((b.nbox, len(b.rings)), (nb, npl))
        self.assertTrue((b.top > b.base).all())

    def test_one_tile(self):
        d = L.decode(BLD.read_bytes())
        b, mask, recs, rep = L.build(BLD, "unused", only=L.tile_key(d.cx[0], d.cy[0]))
        self.assertEqual(len(mask), b.nbox)
        self.assertEqual(L.check(b, mask, recs), [])
        self.assertLess(rep["far_tris"], rep["near_tris"])
