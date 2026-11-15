"""Guide 05: cloud floor and urban lift of the 3D fog files. Synthetic data."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path

import numpy as np

from microclimate.live import build
from microclimate.live import config as C
from microclimate.live import fog3d as F3
from microclimate.live import fogbase as FB
from microclimate.live import outputs

H, W = 3, 4


class Lat:
    """Every tile takes lattice point 0 (weight 1)."""
    def weights(self, lat, lon):
        n = len(lat)
        return np.zeros((n, 4), int), np.tile([1.0, 0, 0, 0], (n, 1))


def lb(dt_k=2.0, zm=0.0, off=0.0, steps=3):
    return dict(T=np.full((4, steps), 10.0 + dt_k), Td=np.full((4, steps), 10.0), zm=np.full(4, zm), off=np.full(steps, off))


def tbl():
    return Lat().weights(np.zeros(H * W), np.zeros(H * W))


def asset_parts():
    return dict(band_edges_m=np.array(F3.BAND_EDGES, np.float32), z_m=((np.arange(24) + .5) * 25).astype(np.float32), fluid=np.full((24, H, W), 255, np.uint8),
                ground_m=np.full((H, W), 5.0, np.float32), urban=np.full((H, W), 255, np.uint8))


class TestBase(unittest.TestCase):
    def test_uniform_dt2(self):
        b = FB.base_field(lb(2.0, 0.0, 17.0), 1, tbl(), (H, W))
        self.assertEqual(b.shape, (H, W))
        self.assertTrue(np.allclose(b, 250.0 + 17.0))
        self.assertTrue(np.allclose(FB.base_field(lb(2.0, 30.0, 0.0), 0, tbl(), (H, W)), 280.0))   # zm (model terrain) is added

    def test_negative_dt_clamped_and_monotone(self):
        lo, hi = FB.base_field(lb(-1.0), 0, tbl(), (H, W)), FB.base_field(lb(3.0), 0, tbl(), (H, W))
        self.assertTrue(np.all(lo == 0) and np.all(hi > lo))

    def test_ramp_weights(self):
        z = np.array([112.5, 137.5, 162.5, 187.5, 212.5, 237.5], np.float32)
        w = FB.weight3d(np.full((H, W), 200.0, np.float32), z, np.full((6, H, W), 255, np.uint8))
        self.assertEqual(w[0, 0, 0], 0.0)                      # more than 60 m below the base
        self.assertTrue(0.3 < w[3, 0, 0] < 0.9)                # inside the ramp
        self.assertEqual(w[4, 0, 0], 1.0)
        self.assertEqual(w[5, 0, 0], 1.0)
        self.assertTrue(np.all(np.diff(w[:, 0, 0]) >= 0))
        fl = np.full((6, H, W), 255, np.uint8)
        fl[5] = 0
        self.assertEqual(FB.weight3d(np.zeros((H, W), np.float32), z, fl)[5, 0, 0], 0.0)   # solid cell: no fog

    def test_guard_only_where_amp_strong(self):
        base = np.full((2, 2), 400.0, np.float32)
        amp = np.array([[0.9, 0.3], [0.51, 0.5]], np.float32)
        g = FB.guard(base, 420.0, amp)
        self.assertEqual(g[0, 0], 390.0)
        self.assertEqual(g[0, 1], 400.0)
        self.assertEqual(g[1, 0], 390.0)
        self.assertEqual(g[1, 1], 400.0)                       # amp == 0.5 is not strong
        self.assertTrue(np.all(FB.guard(base, 1000.0, amp) == 400.0))


class TestLift(unittest.TestCase):
    def test_zero_cases_and_cap(self):
        urb = np.array([[0, 255]], np.uint8)
        z = np.zeros((1, 2))
        self.assertEqual(FB.lift_dT(urb, 1.0, 1.0, z)[0, 0], 0.0)           # urban == 0
        self.assertTrue(np.all(FB.lift_dT(urb, 0.0, 1.0, z) == 0))          # night, cooled road
        self.assertAlmostEqual(float(FB.lift_dT(urb, 1.0, 1.0, z)[0, 1]), FB.DT_URBAN_K)
        self.assertTrue(np.all(FB.lift_dT(np.full((1, 2), 255, np.uint8), 5.0, 1.0, z) <= FB.DT_CAP_K))

    def test_monotone(self):
        urb, a = np.full((1, 1), 200, np.uint8), np.zeros((1, 1))
        base = float(FB.lift_dT(urb, 0.5, FB.vent(2.0), a)[0, 0])
        self.assertGreaterEqual(float(FB.lift_dT(np.full((1, 1), 255, np.uint8), 0.5, FB.vent(2.0), a)[0, 0]), base)   # more urban
        self.assertGreaterEqual(float(FB.lift_dT(urb, 0.9, FB.vent(2.0), a)[0, 0]), base)                              # more heat
        self.assertGreaterEqual(float(FB.lift_dT(urb, 0.5, FB.vent(0.5), a)[0, 0]), base)                              # less wind
        self.assertGreaterEqual(base, float(FB.lift_dT(urb, 0.5, FB.vent(2.0), np.full((1, 1), 0.8))[0, 0]))           # more fog supply lowers the lift
        self.assertGreater(FB.vent(0.0), FB.vent(5.0))


class TestFrame(unittest.TestCase):
    def setUp(self):
        self.T = asset_parts()

    def mk(self, urban_on, heat=0.8):
        return FB.FogBase(lb(2.0), tbl(), (H, W), self.T, urban_on, np.full(5, heat, np.float32), np.full(10, 1.0), 1, {})

    def test_lift_raises_base_and_reports(self):
        amp = np.zeros((H, W), np.float32)
        w0, s0 = self.mk(False).frame(0, amp, 800.0)
        w1, s1 = self.mk(True).frame(0, amp, 800.0)
        self.assertAlmostEqual(s0["base_m"], 250.0)
        self.assertGreater(s1["base_m"], s0["base_m"])
        self.assertGreater(s1["lift_m"], 0)
        self.assertEqual(s0["lift_m"], 0)
        self.assertTrue(np.all(w1 <= w0 + 1e-6))               # the lift only removes fog

    def test_prepare_without_cloud_base_is_not_applied(self):
        fb, info = FB.prepare({"ser": {"wind_speed_10m": [1.0]}}, {}, self.T, (H, W), (np.zeros(W), np.zeros(H)), 1, False, None, 2)
        self.assertIsNone(fb)
        self.assertFalse(info["applied"])
        self.assertIn("cloud_base", info["reason"])


class TestSwitchesAndOutput(unittest.TestCase):
    def test_defaults_and_pairing(self):
        self.assertFalse(C.FOG_BASE or C.URBAN_LIFT)
        self.assertEqual(build.fog_base_switches(False), (False, False))
        self.assertEqual(build.fog_base_switches(False, promoted=True), (False, False))
        self.assertEqual(build.fog_base_switches(True), (True, False))
        saved = dict(C.BETA)
        try:
            C.BETA.update(fog_base=False, urban_lift=True)
            with self.assertRaises(ValueError):
                build.fog_base_switches(True)
        finally:
            C.BETA.clear()
            C.BETA.update(saved)

    def test_off_is_guide3_output_and_floor_only_removes_fog(self):
        parts = asset_parts()
        T = dict(schema=1, dirs=np.array([270.0]), tr3d=np.zeros((1, 11, 24, H, W), np.uint8), **parts)
        T["tr3d"][0, :6] = 255
        S, n = dict(dirs=np.array([270.0])), 3
        amps, Wts, Fo = [np.ones((H, W), np.float32)] * n, [np.array([1.0])] * n, dict(fog_top=np.full(n, 300.0))
        now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        th = [now + dt.timedelta(hours=k) for k in range(n)]
        Tb = dict(urban=np.zeros((H, W), np.uint8), ground_m=parts["ground_m"], z_m=parts["z_m"], fluid=parts["fluid"])
        fb = FB.FogBase(lb(2.0, steps=n), tbl(), (H, W), Tb, False, np.zeros(n, np.float32), np.ones(n), 1, {})   # base 250 m: levels below ~190 m lose fog
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            f1, n1 = outputs.write_fog3d(Path(d1), (T, {}), S, Fo, amps, Wts, 1, th, now, log=lambda *_: None)
            f2, n2 = outputs.write_fog3d(Path(d2), (T, {}), S, Fo, amps, Wts, 1, th, now, log=lambda *_: None, base=fb, dbg_frame=1)
            self.assertNotIn("base_m", f1)
            self.assertIn("base_m", f2)
            self.assertIn("fog_base_dbg.png", n2)
            from PIL import Image
            a = np.asarray(Image.open(Path(d1) / n1[0])).astype(int)
            b = np.asarray(Image.open(Path(d2) / n2[0])).astype(int)
            self.assertTrue(np.all(b <= a))
            self.assertTrue((b < a).any())


if __name__ == "__main__":
    unittest.main()
