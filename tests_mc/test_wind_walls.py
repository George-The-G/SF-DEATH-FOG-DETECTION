"""wind.run must put a real wall on obstacles: a single cube has to leave a reversed-flow wake and a stagnation front.
Regression for the stale Warp kernel cache that left wall nodes at free-stream speed (cube lee deficit 3 %, library near-ground 1.006).
Needs the GPU and the gpu.lock to be free; skipped otherwise."""
import os
from pathlib import Path

import numpy as np
import pytest

LOCK = Path.home() / "deathfog-data" / "library" / "runs" / "gpu.lock"


def _gpu_ok():
    try:
        import warp as wp
        return wp.is_cuda_available() and not LOCK.exists()
    except Exception:
        return False


@pytest.mark.skipif(not _gpu_ok(), reason="needs a free GPU")
def test_cube_has_wake_and_stagnation():
    from microclimate import wind
    H = 12
    nx, ny, nz = 10 * H, 5 * H, 3 * H
    s = np.zeros((nx, ny, nz), bool)
    x0, yc = 3 * H, ny // 2
    s[x0:x0 + H, yc - H // 2:yc + H // 2, 1:1 + H] = True
    LOCK.write_text(str(os.getpid()))
    try:
        u, _ = wind.run(s, 270, steps=6000, avg_steps=2000, log=lambda *a: None)
    finally:
        LOCK.unlink()
    ux = u[0].astype(float)
    k = 1 + H // 2
    assert ux[x0 - H // 4, yc, k] < 0.7, "no stagnation in front of the cube"
    assert ux[x0 + H + 1:x0 + 4 * H, yc, k].min() < -0.05, "no reversed flow in the lee"
