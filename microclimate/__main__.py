import json
import sys
from pathlib import Path

from .config import load
from .paths import data_dir

DATA = data_dir()


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
    elif cmd == "meshes-wide":  # multi-scale guide 09: 400 m terrain out to 60 km -> site/v3/data/terrain_wide.bin
        from . import wide
        print(json.dumps(wide.run(DATA, Path("site/v3/data/terrain_wide.bin")), indent=1))
    elif cmd == "winddirs":  # winddirs [dir ...] [--fog] [--validate]: rotated-frame wind runs, fog bands, validation + manifest
        from . import windrot, fog, validate_dirs
        dirs = [float(a) for a in argv[1:] if not a.startswith("--")] or [315, 225, 180, 360, 247.5, 292.5, 337.5, 202.5]
        if "--validate" not in argv:
            windrot.build(cfg, DATA / "grids", DATA / "library", dirs)
        if "--fog" in argv:
            fog.build_dirs(cfg, DATA / "grids", DATA / "library", dirs)
        if "--validate" in argv:
            validate_dirs.run(DATA, dirs)
    elif cmd == "fog3d":  # fog3d [--dirs 270,...] [--bands 0,1,...] [--tol 0.01] [--dry-run] [--check]: 11 inflow bands (guide 01)
        from . import fog3d

        def opt(name, conv, default):
            return conv(argv[argv.index(name) + 1]) if name in argv else default
        dirs = opt("--dirs", lambda v: [float(x) for x in v.split(",")], None)
        bands = opt("--bands", lambda v: [int(x) for x in v.split(",")], None)
        tol = opt("--tol", float, 0.01)
        kd = opt("--kd", float, None)   # eddy diffusivity (m2/s) for this run; default = microclimate.toml (2.0). Sideways spreading: 2 leaves sharp wake lanes, 20 looks right (2026-11-16)
        if kd is not None:
            cfg["library"]["eddy_diffusivity_m2s"] = kd
        kdv = opt("--kdv", float, None)   # up-down mixing (m2/s); default = the same as kd
        if kdv is not None:
            cfg["library"]["eddy_diffusivity_v_m2s"] = kdv
        if "--check" in argv:
            res = fog3d.check(cfg, Path(opt("--out", str, str(DATA / "library"))), DATA / "grids", Path(opt("--report", str, "reports/fog3d")), tol, dirs, old_dir=Path(opt("--old", str, str(DATA / "library"))))   # --old none: skip the nesting test (a new wind library has no matching old 4-band files)
            print("PASS" if res["pass"] else "FAIL")
            sys.exit(0 if res["pass"] else 1)
        from . import fog
        try:
            fog.build_bands3d(cfg, DATA / "grids", Path(opt("--lib", str, str(DATA / "library"))), dirs or fog3d.WEST_DIRS, bands, tol, dry_run="--dry-run" in argv,
                              out_dir=opt("--out", str, None), lock_dir=DATA / "library")
        except fog3d.GpuLockHeld as e:
            print(e)
            sys.exit(3)
    elif cmd == "winddir":  # winddir --dir <deg> [--force|--validate|--status]: ONE direction, atomic, resumable, status json (see winddir.py)
        from . import winddir
        sys.exit(winddir.main(cfg, DATA, argv[1:]))
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
    elif cmd == "city":  # whole-city street points + static live table: city [points|features|sun|static] [--out DIR] [--max-tiles N]
        from . import city
        sys.exit(city.run(DATA, argv[1:]))
    elif cmd == "validate":
        from . import validate
        ok = validate.run(cfg, DATA, Path("site/v3/replay"))
        validate.overview(cfg, DATA, Path("site/v3/replay"))
        sys.exit(0 if ok else 1)
    else:
        print("commands: geometry, zone, meshes, meshes-wide, wind, winddirs [dirs] [--fog|--validate], street, sun [--check], replay, validate, "
              "routes [--force] [--from STEP] [--only STEP] [--no-validate]  (routes: everything for all routes in routes.toml)")


if __name__ == "__main__":
    main(sys.argv[1:])





