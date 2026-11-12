"""Direction library helpers: blend weights, rotated-frame geometry, direction-aware lag (no GPU, no data files)."""
import numpy as np

from microclimate import dirlib, windrot, wetness

DIRS = np.array([225.0, 247.5, 270.0, 315.0, 360.0])


def test_weights_sum_to_one_and_interpolate():
    W = dirlib.weights([225, 236.25, 270, 292.5, 315, 359, 90, 360], DIRS)
    assert np.allclose(W.sum(1), 1.0)
    assert W[0].argmax() == 0 and W[0, 0] == 1.0
    assert np.isclose(W[1, 0], 0.5) and np.isclose(W[1, 1], 0.5)
    assert W[2, 2] == 1.0
    assert np.isclose(W[3, 2] + W[3, 3], 1.0) and W[3, 2] > 0 and W[3, 3] > 0
    assert W[6, 0] == 1.0          # offshore: nearest end field (zero fog weight anyway)
    assert W[7, -1] == 1.0


def test_gap():
    assert dirlib.gap_deg(313.8, [270.0, 315.0]) < 2.0
    assert dirlib.gap_deg(150.0, [270.0, 315.0]) == 120.0


def test_frame_axis_aligned_is_exact():
    nx, ny = 855, 811
    assert windrot.frame(270, nx, ny)["nxr"] == nx and windrot.frame(270, nx, ny)["nyr"] == ny
    f180 = windrot.frame(180, nx, ny)
    assert (f180["nxr"], f180["nyr"]) == (ny, nx) and f180["f"] == (0.0, 1.0)
    f315 = windrot.frame(315, nx, ny)
    assert f315["nxr"] == f315["nyr"] == 1179


def test_rotate_roundtrip_identity_270_and_permutation_180():
    rng = np.random.default_rng(0)
    solid = rng.random((40, 30, 6)) < 0.1
    sr, fr = windrot.rotate_solid(solid, 270)
    # axis-aligned frames carry real-terrain padding (AXIS_PAD) up/down-wind and laterally: the core must be the original box
    core = sr[fr["pad"]:fr["pad"] + fr["nxr"], fr["padlat"]:fr["padlat"] + fr["nyr"]]
    assert core.shape == solid.shape and np.array_equal(core, solid)
    sr180, fr180 = windrot.rotate_solid(solid, 180)
    core = sr180[fr180["pad"]:fr180["pad"] + fr180["nxr"], fr180["padlat"]:fr180["padlat"] + fr180["nyr"]]
    assert core.shape == (30, 40, 6)


def test_lag_dir_matches_west_lag_for_270():
    x = np.array([540100.0, 545000.0, 556000.0])
    y = np.array([4171500.0, 4175000.0, 4180000.0])
    a = wetness.lag_seconds(x, 3.0)
    b = wetness.lag_seconds_dir(x, y, 270.0, 3.0)
    assert np.allclose(a, b)
    nw = wetness.lag_seconds_dir(x, y, 315.0, 3.0)
    assert nw.min() >= 0 and np.isfinite(nw).all()
