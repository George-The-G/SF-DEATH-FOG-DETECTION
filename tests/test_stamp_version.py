"""live_ci/stamp_version.py: the viewer's scripts and styles get a content-hash address (cache-busting)."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "live_ci"))
import stamp_version  # noqa: E402


class TestStamp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.v3 = os.path.join(self.d, "v3")
        shutil.copytree(os.path.join(ROOT, "site", "v3"), self.v3, ignore=shutil.ignore_patterns("data", "live", "live_beta", "replay", "replay_mock", "tools"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def read(self, name):
        with open(os.path.join(self.v3, name), encoding="utf-8") as f:
            return f.read()

    def test_every_script_and_style_is_stamped(self):
        v = stamp_version.stamp(self.v3)
        idx, app, bx = self.read("index.html"), self.read("app.js"), self.read("beta_ui.js")
        for ref in ('href="style.css?v=%s"', 'src="app.js?v=%s"', 'src="about.js?v=%s"'):
            self.assertIn(ref % v, idx)
        for m in ("fog", "ribbons", "skater", "icons", "beta_ui"):
            self.assertIn("'./%s.js?v=%s'" % (m, v), app)
        self.assertIn("new URL('beta_ui.css?v=%s'" % v, bx)
        self.assertNotIn("'./fog.js'", app)

    def test_idempotent_and_follows_content(self):
        v1 = stamp_version.stamp(self.v3)
        before = {n: self.read(n) for n in ("index.html", "app.js", "beta_ui.js")}
        self.assertEqual(stamp_version.stamp(self.v3), v1)
        self.assertEqual(before, {n: self.read(n) for n in before})
        with open(os.path.join(self.v3, "fog.js"), "a", encoding="utf-8") as f:
            f.write("\n// changed\n")
        v2 = stamp_version.stamp(self.v3)
        self.assertNotEqual(v1, v2)
        self.assertIn("'./fog.js?v=%s'" % v2, self.read("app.js"))
        self.assertNotIn(v1, self.read("app.js"))

    def test_repository_files_are_unstamped(self):
        for n in ("index.html", "app.js", "beta_ui.js"):
            with open(os.path.join(ROOT, "site", "v3", n), encoding="utf-8") as f:
                self.assertNotIn("?v=", f.read().replace("'?v=' +", ""), n)


if __name__ == "__main__":
    unittest.main()
