#!/usr/bin/env python3
"""Death Fog live contract inspector: prints PROMOTED vs BETA feature switches and .npz/.json schemas."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def print_contracts() -> None:
    """Print live feature switches (`PROMOTED` vs `BETA`) and `.npz` / index schemas."""
    from microclimate.live import config as C
    from microclimate.paths import data_dir

    print("=== FEATURE SWITCHES (microclimate.live.config) ===")
    all_keys = sorted(set(C.PROMOTED) | set(C.BETA))
    for k in all_keys:
        p_val = C.PROMOTED.get(k, False)
        b_val = C.BETA.get(k, False)
        print(f"  {k:<18} promoted={str(p_val):<5}  beta={str(b_val):<5}")

    dd = data_dir()
    print(f"\n=== DATA ROOT ({dd}) ===")
    import numpy as np

    for rel_npz in ("live/live_static.npz", "live/live_tracer3d.npz", "live/live_extra.npz", "live/live_east.npz", "grids/solid_20m.npz"):
        np_path = dd / rel_npz
        if np_path.exists():
            try:
                z = np.load(np_path, allow_pickle=False, mmap_mode="r")
                cols = ", ".join(f"{k}:{tuple(z[k].shape)}:{z[k].dtype}" for k in z.files[:12])
                more = f" (+{len(z.files) - 12} more)" if len(z.files) > 12 else ""
                print(f"  {rel_npz}: {cols}{more}")
            except Exception as exc:
                print(f"  {rel_npz}: <{exc}>")

    for st_rel in ("site/v3/live/index.json", "site/v3/replay/index.json"):
        st_p = ROOT / st_rel
        if st_p.exists():
            try:
                obj = json.loads(st_p.read_text(encoding="utf-8"))
                print(f"  {st_rel} keys: {sorted(obj.keys())}")
            except Exception:
                pass


def main() -> int:
    """CLI entry point for `tools/contracts.py`."""
    print_contracts()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
