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
EAST_MANIFEST = "wind_dirs_east.json"   # validated east directions (22.5 .. 135), written by `winddir --validate`; read only with available(east=True)
WRAP_MAX_GAP_DEG = 45.0   # weights() blends across north (the 360 = 0 wrap) only when the lowest and highest available directions are at most this far apart there
MAX_GAP_DEG = 22.5   # a night whose mean direction lies farther than this from the nearest available direction stays 'approx'


def _stem(d):
    return f"{int(round(float(d) * 10)):04d}"


def available(lib_dir, east=False):
    """Available directions, ascending. east=True also reads wind_dirs_east.json (the east set 22.5 .. 135; 157.5 is an accepted gap, see
    docs/model_guides/14_wind_east.md); the default (False) is the west set only, so every caller that does not ask stays as it was."""
    lib = Path(lib_dir)
    ok = set()
    mf = lib / MANIFEST
    if mf.exists():
        ok = {float(d) for d in json.load(open(mf)).get("validated", [])}
    if east and (lib / EAST_MANIFEST).exists():
        ok |= {float(d) for d in json.load(open(lib / EAST_MANIFEST)).get("validated", [])}
    ok.add(270.0)
    out = []
    for d in sorted(ok):
        if (lib / f"wind_{_stem(d)}.npz").exists() and all((lib / f"fog_{_stem(d)}_{b}.npz").exists() for b in range(4)):
            out.append(d)
    return out


def weights(wdir, dirs):
    """Linear blend weights [n, D] of the available directions `dirs` (ascending, degrees, 360 is 360 not 0) for wind-FROM
    directions `wdir` (array or scalar). Between two neighbouring available directions the blend is linear in angle, also across a missing
    direction (the east set has a gap at 157.5: wind from 150 blends 135 and 180). Across north (360 = 0) the blend wraps when the
    lowest direction is at most WRAP_MAX_GAP_DEG above 0 (west set + east set: 337.5, 360, 22.5); for the west set alone (lowest 180) there is no wrap
    and directions below the lowest or above the highest use that end field only; (offshore directions carry zero fog weight in
    wetness.onshore_weight anyway)."""
    d = np.atleast_1d(np.asarray(wdir, float)) % 360.0
    d = np.where((d == 0.0) & (dirs[-1] >= 360.0), 360.0, d)
    ds = np.asarray(dirs, float)
    wrap = bool(ds[-1] >= 360.0 and ds[0] <= WRAP_MAX_GAP_DEG)
    W = np.zeros((len(d), len(ds)))
    for i, v in enumerate(d):
        if v <= ds[0] and wrap and v > 0.0:
            lo, hi = ds[-1] - 360.0, ds[0]
            t = (v - lo) / (hi - lo)
            W[i, -1], W[i, 0] = 1.0 - t, t
        elif v <= ds[0]:
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
