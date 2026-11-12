"""Direction library: which validated wind/fog fields exist and how a replayed night picks (blends) them.

library/wind_dirs.json lists the validated directions (written by `python -m microclimate winddirs --validate`).
A direction counts as available when it is listed there AND wind_<d>.npz plus fog_<d>_0..3.npz exist; 270 is available whenever
its files exist (the original, hand-checked field). Wind-FROM directions between two available ones are served by a LINEAR
BLEND of the two neighbouring fields' per-street-point tracer values (weights proportional to angular closeness); beyond the
ends the nearest field is used (and the night is flagged approximate when the gap exceeds MAX_GAP_DEG).
"""
import json
from pathlib import Path

import numpy as np

MANIFEST = "wind_dirs.json"
MAX_GAP_DEG = 22.5   # a night whose mean direction lies farther than this from the nearest available direction stays 'approx'


def _stem(d):
    return f"{int(round(float(d) * 10)):04d}"


def available(lib_dir):
    lib = Path(lib_dir)
    ok = set()
    mf = lib / MANIFEST
    if mf.exists():
        ok = {float(d) for d in json.load(open(mf)).get("validated", [])}
    ok.add(270.0)
    out = []
    for d in sorted(ok):
        if (lib / f"wind_{_stem(d)}.npz").exists() and all((lib / f"fog_{_stem(d)}_{b}.npz").exists() for b in range(4)):
            out.append(d)
    return out


def weights(wdir, dirs):
    """Linear blend weights [n, D] of the available directions `dirs` (ascending, degrees, 360 is 360 not 0) for wind-FROM
    directions `wdir` (array or scalar). Directions below the lowest or above the highest available one use that end
    field only; (offshore directions carry zero fog weight in wetness.onshore_weight anyway)."""
    d = np.atleast_1d(np.asarray(wdir, float)) % 360.0
    d = np.where((d == 0.0) & (dirs[-1] >= 360.0), 360.0, d)
    ds = np.asarray(dirs, float)
    W = np.zeros((len(d), len(ds)))
    for i, v in enumerate(d):
        if v <= ds[0]:
            W[i, 0] = 1.0
        elif v >= ds[-1]:
            W[i, -1] = 1.0
        else:
            j = int(np.searchsorted(ds, v, side="right")) - 1
            t = (v - ds[j]) / (ds[j + 1] - ds[j])
            W[i, j], W[i, j + 1] = 1.0 - t, t
    return W


def gap_deg(mean_dir, dirs):
    """Angular distance from mean_dir to the nearest available direction."""
    ds = np.asarray(dirs, float)
    return float(np.min(np.abs((ds - mean_dir + 180.0) % 360.0 - 180.0)))
