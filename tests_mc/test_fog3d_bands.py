import json
import tempfile
import unittest
from pathlib import Path

from microclimate import fog3d
from microclimate.config import load


class TestBands3d(unittest.TestCase):
    def test_k_ranges(self):
        b = load()["library"]["fog_bands3d_m"]
        rng = [fog3d.band_k_range(x) for x in b]
        self.assertEqual(rng[0], (0, 1))
        self.assertEqual(rng[-1], (32, 39))
        ks = [k for lo, hi in rng for k in range(lo, hi + 1)]
        self.assertEqual(ks, list(range(40)))  # disjoint, ordered, cover 0..39

    def test_nesting_of_old_bands(self):
        lib = load()["library"]
        new, old = lib["fog_bands3d_m"], lib["fog_bands_m"]
        for B, parts in fog3d.OLD_TO_NEW.items():
            ks = [k for i in parts for k in range(fog3d.band_k_range(new[i])[0], fog3d.band_k_range(new[i])[1] + 1)]
            lo, hi = fog3d.band_k_range(old[B])
            self.assertEqual(ks, list(range(lo, hi + 1)))

    def test_lock(self):
        with tempfile.TemporaryDirectory() as d:
            with fog3d.GpuLock(d, "t"):
                with self.assertRaises(fog3d.GpuLockHeld):
                    fog3d.GpuLock(d, "t2").__enter__()
            self.assertFalse((Path(d) / "gpu.lock").exists())
            (Path(d) / "gpu.lock").write_text(json.dumps({"pid": 4194301, "job": "dead"}))  # stale
            with fog3d.GpuLock(d, "t3"):
                pass


if __name__ == "__main__":
    unittest.main()
