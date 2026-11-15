import unittest

import numpy as np

from microclimate.live import tracer3d as T


class TestTracer3d(unittest.TestCase):
    def test_block_sums_and_orientation(self):
        a = np.zeros((8, 4, 1), np.float32)      # [nx, ny, nz]
        a[0, 3, 0] = 5.0                          # west column, north row (y high)
        out = T.block80(a)                        # [1, 2, 1]
        self.assertEqual(out.shape, (1, 2, 1))
        self.assertEqual(out[0, 0, 0], 5.0)       # tile [0, 0]
        self.assertEqual(out.sum(), 5.0)
        self.assertEqual(T.block80(np.ones((8, 4)))[0, 0], 16.0)

    def test_edge_padding(self):
        a = np.ones((5, 5), np.float32)           # 5 -> padded to 8 by repeating the last row/column
        self.assertEqual(T.block80(a).shape, (2, 2))
        self.assertTrue(np.all(T.block80(a) == 16.0))

    def test_vertical_weights_reproduce_z(self):
        zs = (np.arange(40) + 0.5) * 20.0
        num = (16 * zs)[:, None, None] * np.ones((40, 1, 1), np.float32)
        air = 16 * np.ones((40, 1, 1), np.float32)
        c, a = T.to_levels(num, air)
        self.assertTrue(np.allclose(c[:, 0, 0], T.Z_M, atol=0.5))
        self.assertTrue(np.allclose(a, 16))

    def test_air_rule(self):
        num = np.full((40, 1, 1), 0.5, np.float32)
        air = np.full((40, 1, 1), 0.4, np.float32)   # below 0.8: value 0
        self.assertTrue(np.all(T.to_levels(num, air)[0] == 0))
        air[:] = 1.0
        self.assertTrue(np.allclose(T.to_levels(num, air)[0], 0.5))  # num/air, solid cells do not drag it down

    def test_urban_range(self):
        spec = {"x0": 0.0, "cell_m": 20}
        bm = np.zeros((16, 8), np.float32)
        bm[:8] = 10
        x = np.array([10.0, 50.0, 70.0, 100.0])
        y = np.array([10.0, 30.0, 70.0, 100.0])
        u = T.urban_map(bm, x, y, spec | {"y0": 0.0}, (2, 4))
        self.assertEqual(u.dtype, np.uint8)
        self.assertTrue(u.min() >= 0 and u.max() <= 255)


if __name__ == "__main__":
    unittest.main()
