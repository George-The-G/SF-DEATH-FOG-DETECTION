"""Guide 03: 3D fog files in the live build (band weights, volume, direction rows, atlas, switch off = nothing changes, asset missing). Synthetic data."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path

import numpy as np

from microclimate import replay
from microclimate.live import config as C
from microclimate.live import fog3d as F3
from microclimate.live import outputs

H, W = 4, 5


def asset(nd=9, fill=None):
    tr = np.zeros((nd, 11, 24, H, W), np.uint8)
    if fill:
        for (d, b), v in fill.items():
            tr[d, b] = v
    return dict(schema=np.int64(1), dirs=np.array([180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5, 360][:nd], float), band_edges_m=np.array(F3.BAND_EDGES, np.float32),
                z_m=((np.arange(24) + 0.5) * 25).astype(np.float32), tr3d=tr, fluid=np.full((24, H, W), 255, np.uint8), ground_m=np.zeros((H, W), np.float32),
                urban=np.zeros((H, W), np.uint8))


class TestBandWeights(unittest.TestCase):
    def test_cases(self):
        self.assertTrue(np.all(F3.band_weights(0) == 0))
        self.assertTrue(np.all(F3.band_weights(800) == 1))
        self.assertTrue(np.allclose(F3.band_weights(170), [1, 1, 1, 0.5, 0, 0, 0, 0, 0, 0, 0]))
        self.assertTrue(np.allclose(F3.band_weights(300, lo_m=100), [0, 0, 1, 1, 1, 1, 0, 0, 0, 0, 0]))
        self.assertTrue(np.all(F3.band_weights(float("nan")) == 0))

    def test_monotone_in_top(self):
        prev = np.zeros(11)
        for top in range(0, 900, 25):
            a = F3.band_weights(top)
            self.assertTrue(np.all(a >= prev - 1e-12))
            prev = a


class TestVolume(unittest.TestCase):
    def test_band0_top40_gives_amp_in_every_level(self):
        T = asset(fill={(4, 0): 255})
        amp = np.random.default_rng(1).random((H, W)).astype(np.float32)
        vol = F3.volume(T, [(4, 1.0)], F3.band_weights(40), amp)
        self.assertEqual(vol.shape, (24, H, W))
        for k in range(24):
            self.assertTrue(np.allclose(vol[k], amp, atol=1e-6))

    def test_two_directions_blend_linearly(self):
        T = asset(fill={(3, 0): 255, (4, 0): 51})
        amp = np.ones((H, W), np.float32)
        vol = F3.volume(T, [(3, 0.25), (4, 0.75)], F3.band_weights(40), amp)
        self.assertTrue(np.allclose(vol[5], 0.25 * 1.0 + 0.75 * 0.2, atol=1e-6))

    def test_weight3d_hook_and_encode(self):
        T = asset(fill={(4, 0): 255})
        w3 = np.full((24, H, W), 0.5, np.float32)
        vol = F3.volume(T, [(4, 1.0)], F3.band_weights(40), np.ones((H, W), np.float32), weight3d=w3)
        self.assertTrue(np.all(F3.encode(vol) == 128))
        self.assertEqual(F3.encode(np.array([-1.0, 2.0])).tolist(), [0, 255])


class TestDirRows(unittest.TestCase):
    def test_east_weights_give_only_west_rows_and_no_renormalising(self):
        dirs15 = [22.5, 45, 67.5, 90, 112.5, 135, 180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5, 360]
        Wt = np.zeros(15)
        Wt[5], Wt[6] = 0.4, 0.6                       # 135 (east, not in the asset) and 180
        rows = F3.dir_rows(dirs15, Wt, asset()["dirs"])
        self.assertEqual(rows, [(0, 0.6)])             # the 0.4 is dropped, not moved to the west row
        self.assertEqual(F3.dir_rows(dirs15, np.eye(15)[0], asset()["dirs"]), [])


class TestLid(unittest.TestCase):
    def test_lid_weight(self):
        z = (np.arange(24) + 0.5) * 25.0
        w = F3.lid_weight(z, 65.0, 50.0, 100.0)[:, 0, 0]
        self.assertTrue(np.all(w[z <= 115.0] == 1.0))                 # up to top + margin
        self.assertAlmostEqual(float(F3.lid_weight(np.array([165.0]), 65.0, 50.0, 100.0)[0, 0, 0]), 0.5, places=6)   # half way down the fade
        self.assertTrue(np.all(w[z >= 215.0] == 0.0))                 # none above top + margin + fade
        self.assertTrue(np.all(np.diff(w) <= 1e-9))                   # never increases with height
        self.assertTrue(np.all(F3.lid_weight(z, float('nan')) == 0) and np.all(F3.lid_weight(z, 0.0) == 0))


class TestAtlas(unittest.TestCase):
    def test_level_7_position_and_north_row(self):
        u8 = np.zeros((24, 203, 214), np.uint8)
        u8[7, 0, :] = 200                             # row 0 = north row of level 7
        img = np.asarray(F3.atlas(u8))
        self.assertEqual(img.shape, (812, 1284))
        self.assertTrue(np.all(img[203, 214:428] == 200))   # level 7: column 1, row 1
        self.assertTrue(np.all(img[204:406, 214:428] == 0))
        self.assertEqual(F3.top_layer(u8), 7)
        self.assertEqual(F3.top_layer(np.zeros_like(u8)), -1)


class TestLoad(unittest.TestCase):
    def save(self, d, T, name="a.npz"):
        p = Path(d) / name
        np.savez_compressed(p, **T)
        return p

    def test_good_and_bad_assets(self):
        with tempfile.TemporaryDirectory() as d:
            T, info = F3.load(self.save(d, asset()), np.array([180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5, 360]))
            self.assertTrue(info["applied"] and T["tr3d"].dtype == np.uint8)
            self.assertIsNone(F3.load(Path(d) / "missing.npz")[0])
            bad = asset()
            bad["schema"] = np.int64(2)
            self.assertIn("schema", F3.load(self.save(d, bad, "b.npz"))[1]["reason"])
            self.assertIn("asset directions", F3.load(self.save(d, asset(), "c.npz"), np.array([180, 270]))[1]["reason"])


def synth_run(fog3d=None, switch_asset=True):
    """fog_frames + write on a synthetic fixture."""
    nd = 9
    dirs = np.array([180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5, 360], float)
    n = 3 * 6 * 4 + 1
    rng = np.random.default_rng(3)
    tr80 = rng.random((nd, replay.FOG_H, replay.FOG_W)).astype(np.float32)
    ser = dict(wind_dir=np.full(n, 270.0), wind_speed_10m=np.full(n, 5.0))
    s = np.clip(np.sin(np.arange(n) / 6.0), 0, 1)
    Fo = dict(ser=ser, s_series=s, fog_top=np.full(n, 300.0))
    S = dict(tr80=tr80, dirs=dirs)
    t_hours = [dt.datetime(2026, 11, 12, tzinfo=dt.timezone.utc) + dt.timedelta(hours=k) for k in range(n // 6 + 1)]
    return S, Fo, t_hours


class TestFogFrames(unittest.TestCase):
    def test_return_amp_does_not_change_tiles(self):
        S, Fo, th = synth_run()
        spf = 6 // 1
        a = outputs.fog_frames(S, {}, Fo, None, th, 6)
        b, amps, Wts = outputs.fog_frames(S, {}, Fo, None, th, 6, return_amp=True)
        self.assertEqual(a.tobytes(), b.tobytes())               # byte-identical with the switch's helper on
        self.assertEqual(len(amps), len(a))
        self.assertEqual(amps[0].shape, (replay.FOG_H, replay.FOG_W))
        self.assertEqual(Wts[0].shape, (9,))
        self.assertTrue(spf)

    def test_write_fog3d(self):
        S, Fo, th = synth_run()
        T = asset()
        T["tr3d"] = np.zeros((9, 11, 24, replay.FOG_H, replay.FOG_W), np.uint8)
        T["tr3d"][4, :6] = 255                                   # direction 270, bands 0-5 (fog layer up to 300 m)
        tiles, amps, Wts = outputs.fog_frames(S, {}, Fo, None, th, 6, return_amp=True)
        with tempfile.TemporaryDirectory() as d:
            field, names = outputs.write_fog3d(Path(d), (T, {}), S, Fo, amps, Wts, 6, th, th[0], log=lambda *_: None)
            self.assertEqual(len(field["files"]), len(amps))
            self.assertEqual(sorted(x for x in field["files"] if x), sorted(names))
            first = next(i for i, x in enumerate(field["files"]) if x)
            self.assertTrue((Path(d) / field["files"][first]).exists())
            self.assertGreaterEqual(field["top_layer"][first], 0)
            for key in ("nz", "dz_m", "z0_m", "cols", "rows", "w", "h", "top_layer", "ref"):
                self.assertIn(key, field)
            # frames with no fog get null and no png
            for f, fn in enumerate(field["files"]):
                if fn is None:
                    self.assertEqual(field["top_layer"][f], -1)
                    self.assertFalse((Path(d) / f"fog3d_{f:03d}.png").exists())

    def test_switch_defaults(self):
        self.assertFalse(C.FOG3D)
        self.assertFalse(C.FOG_LID)
        self.assertTrue(C.BETA["fog3d"])
        self.assertNotIn("fog3d", C.PROMOTED)

    def test_enabled_helper_and_settings_keys_unchanged(self):
        from microclimate.live import build
        self.assertTrue(build.fog3d_enabled(True))
        self.assertFalse(build.fog3d_enabled(False))
        self.assertFalse(build.fog3d_enabled(False, promoted=True))
        self.assertEqual(sorted(build.settings(True)), ["carry_state", "nudge_anchors", "physics", "top_fusion", "wind_east"])


if __name__ == "__main__":
    unittest.main()
