#!/usr/bin/env python3
"""deathfog command line.  Local cron: */10 * * * * cd /path/to/deathfog && python3 run.py collect >> data/run.log 2>&1
On GitHub the same commands run from .github/workflows/collect.yml."""
import argparse
import os
import sys
import time
from datetime import date, timedelta

from deathfog import alerts, baseline, budget, collect, db, engine, registry, reports, site
from deathfog import detectors as _detectors  # noqa: F401  (registers built-in detectors)
from deathfog import sources as _sources  # noqa: F401  (registers built-in sources)
from deathfog.config import baseline_path, db_path, load_config
from deathfog.http import Http
from deathfog.notify import make_notifier
from deathfog.sources import nws
from deathfog.util import iso, local_str, parse_iso, parse_local, utcnow


def setup(args):
    cfg = load_config(args.config)
    os.chdir(cfg["_base_dir"])
    registry.load_plugins(cfg["general"].get("plugin_dir", "plugins"))
    conn = db.connect(db_path(cfg))
    http = Http(cfg["general"].get("user_agent", "deathfog"))
    return cfg, conn, http


def cmd_collect(args):
    cfg, conn, http = setup(args)
    notifier = make_notifier(cfg, dry_run=args.dry_run)
    while True:
        started = time.time()
        now = utcnow()
        res = collect.collect(cfg, conn, http, now, only=args.only, force=args.force)
        sync = collect.sync_reports(cfg, conn, http, now)
        db.prune_old(conn, now, model_days=cfg["general"].get("retention_days_model", 1))
        evals = engine.evaluate_all(conn, cfg, now)
        engine.store_evaluations(conn, evals, now)
        alerts.process_alerts(conn, cfg, evals, now, notifier)
        print(f"[{iso(now)}] " + "; ".join(f"{r['source']}={r['status']}" + (f"({r['error']})" if 'error' in r else "") for r in res)
              + (f" | reports {sync}" if sync else "")
              + " | " + ", ".join(f"{e['zone']}/{e['mode']}={e['level']}({e['score']:.0f})" for e in evals))
        sys.stdout.flush()
        if not args.loop:
            return
        time.sleep(max(1, 600 - (time.time() - started)))


def cmd_evaluate(args):
    cfg, conn, _ = setup(args)
    tz = cfg["general"].get("timezone", "America/Los_Angeles")
    for e in engine.evaluate_all(conn, cfg, utcnow()):
        when = "now" if e["mode"] == "now" else "worst hour " + local_str(parse_iso(e["valid_utc"]), tz)
        print(f"{e['zone']:<18}{when:<24}{e['level']:<8}{e['score']:>4.0f}/100  (susceptibility x{e['susceptibility']})")
        for r in e["reasons"]:
            print(f"    {'+' if r['hit'] and r['points'] else ' '} {r['trigger']:<16}{r['detail']}")


def cmd_status(args):
    cfg, conn, _ = setup(args)
    now = utcnow()
    for src in collect.build_sources(cfg):
        last = db.last_ok(conn, src.name)
        used = db.weight_today(conn, src.name, now)
        b = src.cfg.get("daily_budget")
        print(f"{src.name:<12} last ok: {iso(last) if last else 'never':<22} used today: {used:g}" + (f" / {b}" if b else ""))
    print(f"baseline: {'built' if os.path.exists(baseline_path(cfg)) else 'not built yet'}")
    n = conn.execute("SELECT COUNT(*) c FROM readings").fetchone()["c"]
    print(f"{n} readings, {conn.execute('SELECT COUNT(*) c FROM alerts').fetchone()['c']} alerts, "
          f"{conn.execute('SELECT COUNT(*) c FROM reports').fetchone()['c']} field reports")
    for r in conn.execute("SELECT ts_utc, source, note FROM api_calls WHERE ok=0 ORDER BY id DESC LIMIT 3"):
        print(f"  recent error: {r['ts_utc']} {r['source']}: {r['note']}")


def cmd_estimate(args):
    cfg, _, _ = setup(args)
    if args.interval:
        for s in cfg["sources"].values():
            s["interval_min"] = args.interval
    print(budget.format_estimate(budget.estimate(cfg)))


def cmd_baseline(args):
    cfg, conn, http = setup(args)
    now, path = utcnow(), baseline_path(cfg)
    if args.show:
        import json
        if not os.path.exists(path):
            sys.exit("baseline not built yet")
        print(baseline.format_baseline(json.load(open(path))))
        return
    if args.if_missing:
        if os.path.exists(path):
            print("baseline already built; skipping (run `baseline` to rebuild)")
            return
        last = db.last_attempt(conn, "baseline")
        if last and now - last < timedelta(hours=6):
            print("baseline was attempted recently and failed; waiting before retrying")
            return
    try:
        end = date.fromisoformat(args.end) if args.end else None
        summary = baseline.run_baseline(cfg, conn, http, now, days=args.days, end=end, path=path)
        print(baseline.format_baseline(summary))
    except Exception as e:      # never break the 10-minute job over the baseline
        db.log_call(conn, "baseline", iso(now), 0, False, str(e)[:200])
        print(f"baseline failed: {e}")
        if not args.if_missing:
            sys.exit(1)


def cmd_site(args):
    cfg, conn, _ = setup(args)
    out = args.out or cfg["site"].get("dir", "site")
    print("wrote", site.write_site(cfg, conn, utcnow(), out, baseline_path(cfg)))


def cmd_report(args):
    cfg, conn, _ = setup(args)
    tz = cfg["general"].get("timezone", "America/Los_Angeles")
    ts = parse_local(args.time, tz) if args.time else utcnow()
    if args.zone not in cfg["zones"]:
        sys.exit(f"unknown zone {args.zone!r}; choose from {', '.join(cfg['zones'])}")
    reports.add_report(conn, iso(ts), args.zone, spot=args.spot, slick=args.slick, mist=int(args.mist == "y"),
                       sky=args.sky, grip=args.grip, surface_temp_c=args.surface_temp, air_temp_c=args.air_temp,
                       street=args.street, side=args.side, cover=args.cover, photo=args.photo, notes=args.notes)
    print(f"saved report for {args.zone} at {local_str(ts, tz)}")


def cmd_import(args):
    cfg, conn, http = setup(args)
    tz = cfg["general"].get("timezone", "America/Los_Angeles")
    if args.url:
        print(reports.import_url(conn, http, args.url, tz, cfg["zones"]))
    else:
        with open(args.csv, encoding="utf-8-sig") as f:
            print(reports.import_text(conn, f.read(), tz, cfg["zones"]))


def cmd_verify(args):
    cfg, conn, _ = setup(args)
    print(reports.format_verify(reports.verify(conn, days=args.days, positive_slick=args.positive_slick,
                                               predicted_level=args.level)))


def cmd_stations(args):
    cfg, _, http = setup(args)
    for name, z in cfg["zones"].items():
        print(f"{name}:")
        for sid, label in nws.nearest_stations(http, z["lat"], z["lon"])[:6]:
            print(f"    {sid:<8}{label}")


def cmd_plugins(args):
    setup(args)
    print("sources:  ", ", ".join(sorted(registry.SOURCES)))
    from deathfog.detectors import DETECTORS
    print("detectors:", ", ".join(sorted(DETECTORS)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect", help="fetch due sources, score zones, send alerts (run every 10 min)")
    p.add_argument("--loop", action="store_true", help="stay running and repeat every 10 minutes")
    p.add_argument("--only", nargs="*", help="limit to these source names")
    p.add_argument("--force", action="store_true", help="ignore each source's interval")
    p.add_argument("--dry-run", action="store_true", help="print alerts instead of sending them")
    p.set_defaults(fn=cmd_collect)

    sub.add_parser("evaluate", help="show current scores per zone with reasons").set_defaults(fn=cmd_evaluate)
    sub.add_parser("status", help="last fetches and budget used today").set_defaults(fn=cmd_status)
    p = sub.add_parser("estimate", help="daily API usage of the current config vs free limits")
    p.add_argument("--interval", type=int, help="pretend every source polls at this many minutes")
    p.set_defaults(fn=cmd_estimate)

    p = sub.add_parser("baseline", help="backfill the last N days, replay the detector, list when it would have fired")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--end", help="last day to include, YYYY-MM-DD (default: yesterday)")
    p.add_argument("--if-missing", action="store_true", help="only build if no baseline exists yet")
    p.add_argument("--show", action="store_true", help="print the saved baseline instead of rebuilding it")
    p.set_defaults(fn=cmd_baseline)

    p = sub.add_parser("site", help="write the static status page (index.html + status.json)")
    p.add_argument("--out", help="output folder (default: [site] dir)")
    p.set_defaults(fn=cmd_site)

    p = sub.add_parser("report", help="log a field observation")
    p.add_argument("--zone", required=True)
    p.add_argument("--slick", type=int, required=True, choices=[0, 1, 2, 3], help="0 dry, 1 damp, 2 slick, 3 death fog")
    p.add_argument("--time", help="local time, e.g. '2026-11-12 06:30' (default: now)")
    p.add_argument("--spot")
    p.add_argument("--sky", choices=["clear", "overcast", "fog"])
    p.add_argument("--mist", choices=["y", "n"], default="n")
    p.add_argument("--grip", type=int, choices=[1, 2, 3, 4, 5], help="skate grip, 1 terrible .. 5 great")
    p.add_argument("--surface-temp", type=float, help="IR thermometer road temp, C")
    p.add_argument("--air-temp", type=float, help="C")
    p.add_argument("--street", choices=["ew", "ns"], help="street orientation")
    p.add_argument("--side", choices=["north", "south"], help="side of the street")
    p.add_argument("--cover", help="trees / parked cars / open")
    p.add_argument("--photo")
    p.add_argument("--notes")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("import-reports", help="import field reports from a CSV file or a published-sheet URL")
    p.add_argument("csv", nargs="?")
    p.add_argument("--url", help="published-to-web CSV link of the form's response sheet")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("verify", help="score the detector against field reports")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--positive-slick", type=int, default=2, help="slick rating counted as a real event")
    p.add_argument("--level", default="watch", choices=["watch", "likely", "strong"], help="alert level counted as a prediction")
    p.set_defaults(fn=cmd_verify)

    sub.add_parser("nws-stations", help="list nearest NWS stations for each zone").set_defaults(fn=cmd_stations)
    sub.add_parser("plugins", help="list available source and detector types (built-in + plugins)").set_defaults(fn=cmd_plugins)

    args = ap.parse_args()
    if args.cmd == "import-reports" and not (args.csv or args.url):
        ap.error("give a CSV file or --url")
    args.fn(args)


if __name__ == "__main__":
    main()
