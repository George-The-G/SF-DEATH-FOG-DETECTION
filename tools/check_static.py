#!/usr/bin/env python3
"""Step 2 'Done when': wetness.simulate on the static live table reproduces the shipped district values for the district rows.

    USERPROFILE=<home with deathfog-data> python tools/check_static.py [live_static.npz] [night-id ...]
"""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microclimate import city, replay, wetness  # noqa: E402
from microclimate.config import load  # noqa: E402

root = Path(__file__).resolve().parents[1]
args = [a for a in sys.argv[1:] if not a.endswith(".npz")]
npz = next((a for a in sys.argv[1:] if a.endswith(".npz")), str(Path.home().parent / "deathfog-data" / "live" / "live_static.npz"))
D = Path.home() / "deathfog-data"
wetness.DATA_DIR = D
P = city.load_static(npz)
nd = int(P["n_district"])
nights = replay.get_nights(load(), D, until=datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc))
want = args or [n["id"] for n in nights[:2]]
worst = 0.0
for n in nights:
    if n["id"] not in want:
        continue
    t0 = time.time()
    W, diag = wetness.simulate({k: P[k] for k in ("x", "y", "z", "svf", "porous", "wind_ratio", "rel_wind", "horizon")} | {"tracer": np.zeros(len(P["x"]), np.float32)}, n["ser"], 10, use_dirs=False)
    sh = np.fromfile(root / "site" / "v3" / "replay" / n["id"] / "streets.u8", np.uint8).reshape(W.shape[0], nd)
    d = np.abs(np.rint(W[:, :nd]).astype(np.int16) - sh.astype(np.int16))
    d2 = np.abs(W[:, :nd] - sh)
    print(f"{n['id']}: N={len(P['x'])} run {time.time() - t0:.1f} s, district rows max |rint(W) - shipped| = {d.max()} (mean {d.mean():.4f}); max |W - shipped| = {d2.max():.2f}; "
          f"city rows W at 09:00 mean {W[90, nd:].mean():.1f}")
    worst = max(worst, float(d.max()))
print("PASS" if worst <= 1 else "FAIL", "(tolerance 1 on the 0-100 scale)")
sys.exit(0 if worst <= 1 else 1)
