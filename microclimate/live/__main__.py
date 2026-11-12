"""python -m microclimate.live <command>

  build   [--out DIR] [--static PATH] [--ribbons PATH] [--cache DIR]   one live run (default out: site/v3/live)
  ribbons [--stride N] [--static PATH] [--out DIR]                     offline: city ribbons + column maps (out default: the static file's folder)
  check   [--since 30d] [--out DIR]                                    compare the model with METAR / GOES / CO-OPS (section 7)
  cloudscore [--file cloud_score.json] [--since 14d] [--archive DIR]   print the satellite cloud score summary (guide 04)
"""
import sys
from pathlib import Path


def opt(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def main(argv):
    cmd = argv[0] if argv else "help"
    if cmd == "build":
        from . import build
        sys.exit(build.run(opt(argv, "--out", "site/v3/live"), opt(argv, "--static"), opt(argv, "--ribbons"), opt(argv, "--cache")))
    elif cmd == "ribbons":
        from . import config, ribbons
        static = Path(opt(argv, "--static", config.STATIC_DEFAULT))
        root = Path(__file__).resolve().parents[2]
        ribbons.build(static, opt(argv, "--out", static.parent), root / "site" / "v3" / "data" / "ribbons.bin", int(opt(argv, "--stride", 2)))
    elif cmd == "check":
        from . import check
        sys.exit(check.run(argv[1:]))
    elif cmd == "cloudscore":
        from . import cloud_score
        sys.exit(cloud_score.run_cli(argv[1:]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
