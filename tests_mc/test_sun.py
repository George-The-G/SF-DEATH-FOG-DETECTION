import unittest

import numpy as np

from microclimate import sun


class TestSun(unittest.TestCase):
    def test_sunlit_interpolates(self):
        h = np.zeros((2, 72), np.float32)
        h[1, :] = 30.0
        h[0, 18] = 10.0  # azimuth 90 deg (east) blocked up to 10 deg
        self.assertTrue(sun.sunlit(h, 180, 5)[0])
        self.assertFalse(sun.sunlit(h, 90, 5)[0])
        self.assertTrue(sun.sunlit(h, 90, 15)[0])
        self.assertFalse(sun.sunlit(h, 180, 20)[1])
        self.assertTrue(sun.sunlit(h, 180, 31)[1])
        self.assertFalse(sun.sunlit(h, 180, -1)[0])
        # halfway between azimuth 90 (10 deg) and 95 (0 deg) the horizon is 5 deg
        self.assertFalse(sun.sunlit(h, 92.5, 4.9)[0])
        self.assertTrue(sun.sunlit(h, 92.5, 5.1)[0])

    def test_wraparound(self):
        h = np.zeros((1, 72), np.float32)
        h[0, 71] = 20.0
        self.assertFalse(sun.sunlit(h, 357.5, 9.0)[0])  # between 355 (20) and 360 (0)

    def test_ray_distances(self):
        d = sun.ray_distances()
        self.assertAlmostEqual(d[0], 2.0)
        self.assertGreater(d[-1], 2900)
        self.assertLessEqual(float(np.diff(d).max()), 25.0 + 1e-6)

    def test_noon_elevation(self):
        _, _, el = sun.sun_track("2026-12-21", 1)
        self.assertAlmostEqual(float(el.max()), 28.8, delta=0.5)


if __name__ == "__main__":
    unittest.main()
