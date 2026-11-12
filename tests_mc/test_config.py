import unittest

from microclimate.config import load


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        c = load()
        self.assertEqual(c["domain"]["cell_m"], 20)
        self.assertEqual(len(c["library"]["directions_deg"]), 8)
        self.assertEqual(len(c["library"]["fog_bands_m"]), 4)


if __name__ == "__main__":
    unittest.main()
