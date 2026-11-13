"""Promotion of the stage 1 beta settings to the public live build (2026-11-13): `build --promoted` runs the public build with config.PROMOTED, nothing else changes,
and the default public build (no flag) is exactly the old one."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from microclimate import city, wetness
from microclimate.live import build, config as C

NOW = dt.datetime(2026, 11, 12, 23, 30, tzinfo=dt.timezone.utc)


class Stop(Exception):
    pass


class TestPromotedSettings(unittest.TestCase):
    def setUp(self):
        self.saved = (dict(C.BETA), dict(C.PROMOTED), C.PHYSICS, C.NUDGE_ANCHORS, C.CARRY_STATE, C.WIND_EAST)

    def tearDown(self):
        C.BETA.clear()
        C.BETA.update(self.saved[0])
        C.PROMOTED.clear()
        C.PROMOTED.update(self.saved[1])
        C.PHYSICS, C.NUDGE_ANCHORS, C.CARRY_STATE, C.WIND_EAST = self.saved[2:]

    def test_exactly_the_approved_set(self):
        """Sunlight, film, pavement, station anchors, carried state; no east wind."""
        self.assertEqual(C.PROMOTED, dict(physics=("sun_clear", "film", "pavement"), nudge_anchors=True, carry_state=True, wind_east=False))
        self.assertEqual(wetness.check_physics(C.PROMOTED["physics"]), frozenset({"sun_clear", "film", "pavement"}))
        self.assertEqual(build.settings(False, promoted=True), dict(physics=("sun_clear", "film", "pavement"), nudge_anchors=True, carry_state=True, wind_east=False))
        self.assertEqual(build.beta_label(build.settings(False, promoted=True)), "clear-sky sun (GOES sunlight), water film, pavement heat, station anchors, carried state")

    def test_east_wind_never_reaches_the_promoted_build(self):
        C.BETA.update(wind_east=True)
        C.WIND_EAST = True                                                                 # even the public switch: the promoted set states its own value
        self.assertFalse(build.settings(False, promoted=True)["wind_east"])
        self.assertEqual(C.PROMOTED.get("wind_east"), False)
        self.assertTrue(build.settings(True)["wind_east"])
        C.WIND_EAST = False

    def test_default_public_build_is_the_old_one(self):
        self.assertEqual(build.settings(False), dict(physics=(), nudge_anchors=False, carry_state=False, wind_east=False))
        self.assertEqual((C.PHYSICS, C.NUDGE_ANCHORS, C.CARRY_STATE), ((), False, False))

    def test_the_three_settings_never_mix(self):
        C.BETA.update(physics=("film",), nudge_anchors=False, carry_state=True, wind_east=True)  # the next beta stage: must not reach the promoted build
        self.assertEqual(build.settings(False, promoted=True), dict(physics=("sun_clear", "film", "pavement"), nudge_anchors=True, carry_state=True, wind_east=False))
        C.PROMOTED.update(physics=("film",), carry_state=True)                            # and the other way round
        self.assertEqual(build.settings(True), dict(physics=("film",), nudge_anchors=False, carry_state=True, wind_east=True))
        C.PHYSICS, C.NUDGE_ANCHORS, C.CARRY_STATE = ("pavement",), True, True             # the legacy values do not reach either
        self.assertEqual(build.settings(False, promoted=True)["physics"], ("film",))
        self.assertEqual(build.settings(True)["physics"], ("film",))
        self.assertEqual(build.settings(True, promoted=True)["physics"], ("film",))        # beta wins over promoted

    def run_(self, promoted, tmp, beta=False):
        rec = {}

        def fake_forcing(rr, t0, n, now, x, y, obs, **kw):
            rec.update(physics=kw["physics"], nudge=C.NUDGE_ANCHORS, dsr=kw.get("dsr_hours"))
            raise Stop("stop after the settings are known")
        S = dict(x=np.zeros(3), y=np.zeros(3), z=np.zeros(3))
        calls = []
        with mock.patch.object(city, "load_static", lambda p: S), mock.patch.object(build.forcing, "build", fake_forcing), \
                mock.patch.object(build, "gather", lambda st, t0, now, cache: ({"model": "x"}, {}, None, {})), \
                mock.patch.object(build, "fetch_dsr", lambda *a, **k: (calls.append(1), ({}, {"daylight_hours": 0}))[1]):
            rec["rc"] = build.run(Path(tmp) / "pub", static=Path(tmp) / "s.npz", now=NOW, beta=beta, promoted=promoted, log=lambda *a: None)
        rec["dsr_calls"] = len(calls)
        return rec

    def test_promoted_run_uses_sunlight_and_anchors_and_restores_the_module(self):
        with tempfile.TemporaryDirectory() as d:
            rec = self.run_(True, d)
        self.assertEqual(rec["physics"], frozenset({"sun_clear", "film", "pavement"}))
        self.assertTrue(rec["nudge"])
        self.assertEqual(rec["dsr_calls"], 1)
        self.assertFalse(C.NUDGE_ANCHORS)
        self.assertEqual(C.PHYSICS, ())

    def test_unflagged_run_is_still_the_old_public_run(self):
        with tempfile.TemporaryDirectory() as d:
            rec = self.run_(False, d)
        self.assertEqual(rec["physics"], frozenset())
        self.assertFalse(rec["nudge"])
        self.assertEqual(rec["dsr_calls"], 0)


if __name__ == "__main__":
    unittest.main()
