"""Unit tests for microclimate.paths data root resolution."""
import os
import unittest
from pathlib import Path
from unittest import mock

from microclimate import paths


class TestPaths(unittest.TestCase):
    def test_repo_root_contains_microclimate(self):
        self.assertTrue((paths.REPO_ROOT / "microclimate").is_dir())

    def test_env_override(self):
        with mock.patch.dict(os.environ, {"DEATHFOG_DATA": "/tmp/custom-deathfog-data"}):
            self.assertEqual(paths.data_dir(), Path("/tmp/custom-deathfog-data"))
            self.assertEqual(paths.library_dir(), Path("/tmp/custom-deathfog-data") / "library")
            self.assertEqual(paths.live_dir(), Path("/tmp/custom-deathfog-data") / "live")
            self.assertEqual(paths.grids_dir(), Path("/tmp/custom-deathfog-data") / "grids")
            self.assertEqual(paths.raw_dir(), Path("/tmp/custom-deathfog-data") / "raw")

    def test_default_resolves_to_repo_data_or_legacy(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DEATHFOG_DATA", None)
            d = paths.data_dir()
            self.assertIsInstance(d, Path)
            self.assertIn(d.name, ("data", "deathfog-data"))


if __name__ == "__main__":
    unittest.main()
