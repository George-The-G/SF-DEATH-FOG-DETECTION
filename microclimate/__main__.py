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
    else:
        print("commands: geometry")


if __name__ == "__main__":
    main(sys.argv[1:])
