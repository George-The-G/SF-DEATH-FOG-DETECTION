import json
import sys
from pathlib import Path

from .config import load

DATA = Path.home() / "deathfog-data"


def main(argv):
    cmd = argv[0] if argv else "help"
    cfg = load()
    if cmd == "geometry":
        from . import geometry
        print(json.dumps(geometry.build(cfg, DATA / "raw", DATA / "grids"), indent=1))
    elif cmd == "zone":
        from . import zone
        print(json.dumps(zone.build(cfg, DATA / "raw", DATA / "grids"), indent=1, default=str))
    elif cmd == "meshes":
        from . import meshes
        print(json.dumps(meshes.build(cfg, DATA / "raw", DATA / "grids", Path("site/v3/data")), indent=1))
    elif cmd == "wind":
        from . import wind
        order = [270, 247.5, 292.5, 225, 315, 202.5, 337.5, 360]
        wind.build(cfg, DATA / "grids", DATA / "library", directions=order)
    elif cmd == "refine":
        import argparse
        ap = argparse.ArgumentParser(prog="microclimate refine")
        ap.add_argument("--direction", type=float, required=True)
        ap.add_argument("--zone", default="potrero", choices=["potrero", "all"])
        ap.add_argument("--dx", type=float, default=5.0)
        ap.add_argument("--steps", type=int, default=20000)
        ap.add_argument("--avg-steps", type=int, default=5000)
        ap.add_argument("--max", type=int, default=None)
        ap.add_argument("--omega", type=float, default=1.95)
        ap.add_argument("--force", action="store_true")
        a = ap.parse_args(argv[1:])
        from . import refine
        d = refine.refine(cfg, a.direction, a.zone, a.dx, a.steps, a.avg_steps, a.max, a.force, log=lambda s: print(s, flush=True), omega=a.omega)
        print(json.dumps({k: v for k, v in d.items() if k != "patches"}, indent=1))
    else:
        print("commands: geometry, zone, meshes, wind, refine")


if __name__ == "__main__":
    main(sys.argv[1:])



