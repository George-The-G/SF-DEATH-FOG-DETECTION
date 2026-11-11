"""The 10-minute loop body: for each enabled source, decide whether it is due and within budget, then fetch."""
from datetime import timedelta

from . import db, reports
from .registry import SOURCES
from .util import iso


def build_sources(cfg):
    out = []
    for name, scfg in cfg.get("sources", {}).items():
        if not scfg.get("enabled", False):
            continue
        typ = scfg.get("type", name)
        cls = SOURCES.get(typ)
        if cls is None:
            raise KeyError(f"source '{name}': unknown type '{typ}' (known: {sorted(SOURCES)})")
        out.append(cls(name, scfg, cfg["zones"]))
    return out


def is_due(conn, name, interval_min, now):
    last = db.last_ok(conn, name)
    # one minute of slack so cron jitter never skips a cycle
    return last is None or (now - last) >= timedelta(minutes=max(interval_min - 1, 0))


def collect(cfg, conn, http, now, only=None, force=False):
    results = []
    for src in build_sources(cfg):
        if only and src.name not in only:
            continue
        cost = src.cost()
        budget = src.cfg.get("daily_budget")
        if not force and not is_due(conn, src.name, src.cfg.get("interval_min", 10), now):
            results.append({"source": src.name, "status": "not_due"})
            continue
        if budget and db.weight_today(conn, src.name, now) + cost > budget:
            results.append({"source": src.name, "status": "over_budget", "budget": budget})
            continue
        try:
            readings = src.fetch(http, now)
            new = db.insert_readings(conn, readings, iso(now))
            db.log_call(conn, src.name, iso(now), cost, True, f"{len(readings)} readings, {new} new")
            results.append({"source": src.name, "status": "ok", "readings": len(readings), "new": new})
        except Exception as e:  # one broken source must never stop the others
            db.log_call(conn, src.name, iso(now), cost, False, str(e)[:200])
            results.append({"source": src.name, "status": "error", "error": str(e)[:200]})
    return results


def sync_reports(cfg, conn, http, now):
    """Pull new field reports from a Google Sheet published to the web as CSV (no credentials needed)."""
    url = cfg.get("reports", {}).get("form_csv_url")
    if not url or not is_due(conn, "form_csv", cfg["reports"].get("interval_min", 30), now):
        return None
    tz = cfg["general"].get("timezone", "America/Los_Angeles")
    try:
        res = reports.import_url(conn, http, url, tz, cfg["zones"])
        db.log_call(conn, "form_csv", iso(now), 0, True, str(res))
        return res
    except Exception as e:
        db.log_call(conn, "form_csv", iso(now), 0, False, str(e)[:200])
        return {"error": str(e)[:200]}
