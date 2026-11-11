"""Alert policy: when does an evaluation become a notification?"""
from datetime import timedelta

from .detectors.base import LEVELS
from .util import iso, local_str, parse_iso


def process_alerts(conn, cfg, evals, now, notifier):
    """Alert on a zone when it reaches notify.alert_min_level, once per cooldown unless the level rises."""
    ncfg = cfg.get("notify", {})
    min_rank = LEVELS.index(ncfg.get("alert_min_level", "likely"))
    cooldown = timedelta(hours=ncfg.get("cooldown_hours", 4))
    tz = cfg.get("general", {}).get("timezone", "America/Los_Angeles")
    report_url = cfg.get("general", {}).get("report_url", "")
    sent = []
    for e in sorted(evals, key=lambda e: (e["mode"] != "now")):  # live conditions first
        rank = LEVELS.index(e["level"])
        if rank < min_rank:
            continue
        last = conn.execute("SELECT * FROM alerts WHERE zone=? ORDER BY ts_utc DESC LIMIT 1", (e["zone"],)).fetchone()
        if last and now - parse_iso(last["ts_utc"]) < cooldown and LEVELS.index(last["level"]) >= rank:
            continue
        label = cfg["zones"][e["zone"]].get("label", e["zone"])
        when = "right now" if e["mode"] == "now" else f"expected around {local_str(parse_iso(e['valid_utc']), tz)}"
        hits = [r["detail"] for r in e["reasons"] if r["hit"] and r["points"]]
        body = f"{label}: {e['level'].upper()} death-fog risk ({e['score']:.0f}/100), {when}.\n" + "\n".join(f"- {h}" for h in hits)
        if report_url:
            body += f"\nLog what you see: {report_url}"
        conn.execute("INSERT INTO alerts(ts_utc, zone, level, score, mode, valid_utc, reasons) VALUES (?,?,?,?,?,?,?)",
                     (iso(now), e["zone"], e["level"], e["score"], e["mode"], e["valid_utc"], str(len(hits))))
        conn.commit()
        notifier(f"Death fog {e['level']}: {label}", body, "high" if e["level"] == "strong" else "default", report_url or None)
        sent.append(e)
    return sent
