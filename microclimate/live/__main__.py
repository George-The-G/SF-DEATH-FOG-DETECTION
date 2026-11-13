"""python -m microclimate.live <command>

  build   [--out DIR] [--static PATH] [--ribbons PATH] [--cache DIR] [--physics a,b|none] [--carry-state] [--archive DIR]   one live run (default out: site/v3/live;
          physics default: config.PHYSICS)
          [--save-inputs FILE]   write the fetched inputs for a beta build of the same job
          [--beta [--inputs FILE]]   the beta preview channel: settings from config.BETA only (default out: site/v3/live_beta), fetched inputs from FILE (the
                                 public build's --save-inputs file) or its own fetch; never writes the run archive
  compare --physics a,b|none [--base x,y] [--anchors] [--static PATH] [--out DIR]   same inputs, config.PHYSICS (or --base) versus the given switches; writes
                                                                       --anchors (guide 03): the given set (default config.PHYSICS) without versus with the station anchors on
                                                                       compare_<time>.md/.json (default out: reports/physics_compare)
  ribbons [--stride N] [--static PATH] [--out DIR]                     offline: city ribbons + column maps (out default: the static file's folder)
  stationwind [--data DIR] [--out FILE]                                offline (PC with the wind library): simulated wind vectors at the in-grid stations -> station_wind.npz (guide 03)
  check   [--since 30d] [--out DIR]                                    compare the model with METAR / GOES / CO-OPS (section 7); guide 03: leave-one-station-out table
  cloudscore [--file cloud_score.json] [--since 14d] [--archive DIR]   print the satellite cloud score summary (guide 04)
"""
import sys
from pathlib import Path


def opt(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def main(argv):
    cmd = argv[0] if argv else "help"
    if cmd == "build":
        from . import build, config
        ph = opt(argv, "--physics")
        if ph is not None:
            from . import compare
            ph = compare.parse_physics(ph)
        beta = "--beta" in argv
        try:
            from .. import wetness
            wetness.check_physics(ph if ph is not None else build.settings(beta)["physics"])
        except ValueError as e:
            sys.exit(f"build: {e}")
        sys.exit(build.run(opt(argv, "--out", "site/v3/" + config.BETA_DIR if beta else "site/v3/live"), opt(argv, "--static"), opt(argv, "--ribbons"), opt(argv, "--cache"), physics=ph,
                           carry_state=True if "--carry-state" in argv else None, archive_dir=opt(argv, "--archive"), beta=beta, inputs=opt(argv, "--inputs"),
                           inputs_out=opt(argv, "--save-inputs")))
    elif cmd == "compare":
        from . import compare
        if "--physics" not in argv and "--anchors" not in argv and "--east" not in argv:
            sys.exit("compare needs --physics a,b (or --physics none), or --anchors, or --east")
        base = opt(argv, "--base")
        try:
            sys.exit(compare.run(compare.parse_physics(opt(argv, "--physics")), opt(argv, "--static"), opt(argv, "--ribbons"), opt(argv, "--cache"),
                                 opt(argv, "--out", "reports/physics_compare"), anchors="--anchors" in argv, east="--east" in argv, base=None if base is None else compare.parse_physics(base)))
        except ValueError as e:
            sys.exit(f"compare: {e}")
    elif cmd == "ribbons":
        from . import config, ribbons
        static = Path(opt(argv, "--static", config.STATIC_DEFAULT))
        root = Path(__file__).resolve().parents[2]
        ribbons.build(static, opt(argv, "--out", static.parent), root / "site" / "v3" / "data" / "ribbons.bin", int(opt(argv, "--stride", 2)))
    elif cmd == "check":
        from . import check
        sys.exit(check.run(argv[1:]))
    elif cmd == "stationwind":
        from . import stationwind
        sys.exit(stationwind.run(argv[1:]))
    elif cmd == "cloudscore":
        from . import cloud_score
        sys.exit(cloud_score.run_cli(argv[1:]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
