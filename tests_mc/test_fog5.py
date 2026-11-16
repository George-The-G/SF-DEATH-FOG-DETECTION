"""Guide 08 Part A output: fog5/ folder (index, hashed names, kinds, determinism, switch defaults). Synthetic data."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from microclimate import replay
from microclimate.live import build
from microclimate.live import config as C
from microclimate.live import fog3d as F3
from microclimate.live import fog5

H, W = replay.FOG_H, replay.FOG_W


def fixture(hours=14):
    n = hours * 6 + 1
    t0 = dt.datetime(2026, 11, 15, 0, tzinfo=dt.timezone.utc)
    now = t0 + dt.timedelta(hours=9)
    F = dict(ser=dict(wind_dir=np.full(n, 270.0), wind_speed_10m=np.full(n, 5.0)), s_series=np.full(n, 0.8), fog_top=np.full(n, 300.0))
    dirs = np.array([180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5, 360], float)
    tr80 = np.zeros((9, H, W), np.float32)
    tr80[4] = np.linspace(1.0, 0.2, W)[None, :]
    S = dict(dirs=dirs, tr80=tr80)
    tr = np.zeros((9, 11, 24, H, W), np.uint8)
    tr[4, :6] = 255
    T3 = dict(dirs=dirs, tr3d=tr, band_edges_m=np.array(F3.BAND_EDGES, np.float32), z_m=((np.arange(24) + .5) * 25).astype(np.float32),
              fluid=np.full((24, H, W), 255, np.uint8), ground_m=np.full((H, W), 5.0, np.float32), urban=np.zeros((H, W), np.uint8))
    xs = 540100.0 + 80 * (np.arange(W) + .5)
    ys = 4171500.0 + 20 * 811 - 80 * (np.arange(H) + .5)
    return F, S, T3, t0, now, xs, ys


class TestFog5(unittest.TestCase):
    def run_(self, scan_at, d):
        F, S, T3, t0, now, xs, ys = fixture()
        info = fog5.write(d, F, S, T3, None, t0, now, scan_at, xs, ys, log=lambda *_: None)
        return info, json.loads((Path(d) / "fog5" / "index.json").read_text()), now

    def test_no_scans_all_forecast_and_hashed_names(self):
        with tempfile.TemporaryDirectory() as d:
            info, idx, now = self.run_(lambda t: None, d)
            self.assertEqual(idx["schema"], 1)
            self.assertEqual(idx["step_min"], 5)
            self.assertEqual(len(idx["frames"]), int(8 * 12) + 1)
            self.assertTrue(all(f["kind"] == "forecast" for f in idx["frames"]))
            names = {f["tile"] for f in idx["frames"]}
            self.assertTrue(all(n.startswith("tile_") and n.endswith(".png") and len(n.split("_")[-1]) == len("12345678.png") for n in names))
            for n in names:
                self.assertTrue((Path(d) / "fog5" / n).exists())
            vols = [f["vol"] for f in idx["frames"] if f["vol"]]
            self.assertEqual(len(vols), info["volumes"])
            self.assertTrue(0 < len(vols) <= int((3 + 1) * 12) + 1)          # only the last 3 h and the next 1 h
            self.assertFalse(info["over_budget"])

    def test_scans_make_observed_frames(self):
        p, c = np.full((H, W), 0.9, np.float32), np.ones((H, W), np.float32)

        def scan_at(t):
            return (p, c) if t % 600 == 0 and t < 1_700_000_000_000 else None
        with tempfile.TemporaryDirectory() as d:
            info, idx, now = self.run_(scan_at, d)
            kinds = [f["kind"] for f in idx["frames"]]
            self.assertIn("observed", kinds)
            self.assertEqual(info["kinds"]["observed"], kinds.count("observed"))

    def test_deterministic_content(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            _, i1, _ = self.run_(lambda t: None, d1)
            _, i2, _ = self.run_(lambda t: None, d2)
            self.assertEqual([(f["tile"], f["vol"]) for f in i1["frames"]], [(f["tile"], f["vol"]) for f in i2["frames"]])

    def test_switch_defaults(self):
        self.assertFalse(C.FOG_TRAVEL)
        self.assertTrue(C.BETA["fog_travel"])                        # beta only (George 2026-11-16); the gate runs on beta data afterwards
        self.assertTrue(build.fog_travel_enabled(True))
        self.assertFalse(build.fog_travel_enabled(False))
        saved = dict(C.BETA)
        try:
            C.BETA["fog_travel"] = True
            self.assertTrue(build.fog_travel_enabled(True))
            self.assertFalse(build.fog_travel_enabled(False, promoted=True))
        finally:
            C.BETA.clear()
            C.BETA.update(saved)


if __name__ == "__main__":
    unittest.main()
