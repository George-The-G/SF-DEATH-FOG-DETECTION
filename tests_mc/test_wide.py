"""Guide 09 part A: wide terrain file (header round trip, sea = 0, grid covers the box). The Mount Tamalpais check needs the real tiles and is skipped without them."""
import unittest
from pathlib import Path

import numpy as np

from microclimate import wide
from microclimate.paths import raw_dir

RAW = raw_dir()


class TestWide(unittest.TestCase):
    def test_round_trip(self):
        spec = dict(x0=400000.0, y0=4300000.0, nx=5, ny=4, cell=400.0)
        h = np.array([[0, 1.2, 100.26, 650.0, -3.0]] * 4, np.float32)
        h[0, 0] = np.nan
        data = wide.encode(h, spec)
        self.assertEqual(data[:4], b"TWD1")
        self.assertEqual(len(data), 32 + 5 * 4 * 2)
        s2, h2 = wide.decode(data)
        self.assertEqual((s2["nx"], s2["ny"], s2["x0"], s2["y0"], s2["cell"], s2["z_step"]), (5, 4, 400000.0, 4300000.0, 400.0, 0.5))
        self.assertEqual(h2[0, 0], 0.0)                       # NaN -> 0
        self.assertEqual(h2[1, 4], 0.0)                       # below sea level -> 0
        self.assertTrue(np.allclose(h2[1, :4], [0, 1.0, 100.5, 650.0], atol=0.26))   # 0.5 m steps

    def test_bad_magic(self):
        with self.assertRaises(ValueError):
            wide.decode(b"XXXX" + b"\0" * 40)

    def test_grid_covers_the_box_and_size(self):
        s = wide.grid_spec()
        self.assertTrue(250 < s["nx"] < 340 and 250 < s["ny"] < 340)
        self.assertLess(s["nx"] * s["ny"] * 2, 200_000)       # under 200 KB raw

    def test_mount_tamalpais_and_ocean(self):
        if not all((RAW / f"dem_{t}.tif").exists() for t in wide.TILES):
            self.skipTest("3DEP tiles not downloaded")
        spec = wide.grid_spec()
        h = np.nan_to_num(wide.build_heights(RAW, spec, log=lambda *_: None), nan=0.0)
        self.assertTrue(650 <= wide.height_at(spec, h, 37.9235, -122.5965) <= 790)
        self.assertEqual(wide.height_at(spec, h, 37.7, -123.2), 0.0)   # open ocean west of the Farallones' line


if __name__ == "__main__":
    unittest.main()
