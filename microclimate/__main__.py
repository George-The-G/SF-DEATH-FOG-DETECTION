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
    else:
        print("commands: geometry, zone, meshes")


if __name__ == "__main__":
    main(sys.argv[1:])


