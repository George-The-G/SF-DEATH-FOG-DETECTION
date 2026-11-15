import unittest

from microclimate.config import load


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        c = load()
        self.assertEqual(c["domain"]["cell_m"], 20)
        self.assertEqual(len(c["library"]["directions_deg"]), 8)
        self.assertEqual(len(c["library"]["fog_bands_m"]), 4)

    def test_bands3d(self):
        b = load()["library"]["fog_bands3d_m"]
        self.assertEqual(len(b), 11)
        self.assertEqual(b[0][0], 0)
        self.assertEqual(b[-1][1], 800)
        for (lo, hi), (lo2, _) in zip(b, b[1:]):
            self.assertEqual(hi, lo2)  # contiguous
        edges = {e for band in b for e in band}
        self.assertTrue(all(e % 20 == 0 for e in edges))
        self.assertTrue({140, 300, 500} <= edges)


if __name__ == "__main__":
    unittest.main()
