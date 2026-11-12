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
    elif cmd == "street":
        from . import street
        print(json.dumps(street.build(cfg, DATA / "grids", DATA / "library"), indent=1))
    elif cmd == "sun":
        from . import sun
        if "--check" in argv:
            sun.check(cfg, DATA / "raw", DATA / "grids")
        else:
            print(json.dumps(sun.build(cfg, DATA / "raw", DATA / "grids"), indent=1))
    elif cmd == "replay":
        from . import replay
        replay.write(cfg, DATA, Path("site/v3/replay"))
    elif cmd == "routes":
        from . import routes
        sys.exit(routes.run(cfg, DATA, argv[1:]))
    elif cmd == "validate":
        from . import validate
        ok = validate.run(cfg, DATA, Path("site/v3/replay"))
        validate.overview(cfg, DATA, Path("site/v3/replay"))
        sys.exit(0 if ok else 1)
    else:
        print("commands: geometry, zone, meshes, wind, street, sun [--check], replay, validate, "
              "routes [--force] [--from STEP] [--only STEP] [--no-validate]  (routes: everything for all routes in routes.toml)")


if __name__ == "__main__":
    main(sys.argv[1:])





