"""Banding test: the same 11 bands for one direction with a larger eddy diffusivity kd (default 2 m2/s in microclimate.toml). Writes into deathfog-data/scratch/kd_test/kd<value>/.
usage: python spikes/kd_test.py <kd> <dir[,dir...]> [kdv]   (GPU, shared gpu.lock)"""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from microclimate import fog  # noqa: E402
from microclimate.config import load  # noqa: E402

kd, d = float(sys.argv[1]), [float(x) for x in sys.argv[2].split(",")]
kdv = float(sys.argv[3]) if len(sys.argv) > 3 else None
D = Path.home() / "deathfog-data"
cfg = copy.deepcopy(load(ROOT / "microclimate.toml"))
cfg["library"]["eddy_diffusivity_m2s"] = kd
if kdv is not None:
    cfg["library"]["eddy_diffusivity_v_m2s"] = kdv
out = D / "scratch" / "kd_test" / (f"kd{kd:g}" + (f"v{kdv:g}" if kdv is not None else ""))
fog.build_bands3d(cfg, D / "grids", D / "library", d, None, out_dir=out, lock_dir=D / "library")
