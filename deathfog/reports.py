"""Field reports (ground truth) and the verification that scores the detector against them."""
import csv
import hashlib
import io
import json
import re
from datetime import timedelta

from .detectors.base import LEVELS
from .util import iso, parse_iso, parse_local, utcnow

# Form question titles can be anything that CONTAINS these words (checked in this order).
_HEADER_RULES = [("timestamp", "timestamp"), ("zone", "zone"), ("slick", "slick"), ("mist", "mist"),
                 ("sky", "sky"), ("grip", "grip"), ("surface", "surface_temp_c"), ("air temp", "air_temp_c"),
                 ("air_temp", "air_temp_c"), ("side", "side"), ("street", "street"), ("cover", "cover"),
                 ("spot", "spot"), ("photo", "photo"), ("note", "notes"), ("comment", "notes")]
_SLICK_WORDS = {"dry": 0, "damp": 1, "wet": 1, "slick": 2, "death": 3}


def _canon(header):
    h = header.strip().lower()
    for needle, canon in _HEADER_RULES:
        if needle in h:
            return canon
    return None


def _is_fahrenheit(header):
    h = header.lower()
    return "fahrenheit" in h or "°f" in h or "(f)" in h or " f)" in h


def _lead_int(v, words=None):
    m = re.search(r"-?\d+", v or "")
    if m:
        return int(m.group())
    for w, n in (words or {}).items():
        if w in (v or "").lower():
            return n
    return None


def _yn(v):
    return 1 if str(v).strip().lower() in ("1", "y", "yes", "true", "t") else 0


def _num(v):
    try:
        return float(re.search(r"-?\d+(\.\d+)?", v).group()) if v and v.strip() else None
    except AttributeError:
        return None


def add_report(conn, ts_utc, zone, row_key=None, **kw):
    """Insert one field report. Returns False if a report with the same row_key already exists."""
    cols = ("spot", "slick", "mist", "sky", "grip", "surface_temp_c", "air_temp_c",
            "street", "side", "cover", "photo", "notes")
    cur = conn.execute(
        f"INSERT OR IGNORE INTO reports(ts_utc, zone, created_utc, row_key, {','.join(cols)}) VALUES (?,?,?,?,{','.join('?' * len(cols))})",
        (ts_utc, zone, iso(utcnow()), row_key, *(kw.get(c) for c in cols)))
    conn.commit()
    return cur.rowcount == 1


def _match_zone(value, zones):
    v = (value or "").strip().lower()
    if not zones:
        return value.strip() or None
    for key, z in zones.items():
        if v in (key.lower(), str(z.get("label", "")).lower()):
            return key
    for key, z in zones.items():                      # "Potrero Hill (east side)" style answers
        if v.startswith(str(z.get("label", key)).lower()):
            return key
    return None


def import_text(conn, text, tzname, zones=None):
    """Import a CSV from a Google Form / Sheet. Returns counts: added, already_had, skipped (bad rows)."""
    res = {"added": 0, "already_had": 0, "skipped": 0}
    reader = csv.DictReader(io.StringIO(text))
    for raw in reader:
        row, f_cols = {}, set()
        for header, value in raw.items():
            c = _canon(header or "")
            if c and c not in row:
                row[c] = (value or "").strip()
                if c.endswith("_temp_c") and _is_fahrenheit(header):
                    f_cols.add(c)
        try:
            zone = _match_zone(row.get("zone", ""), zones)
            ts = iso(parse_local(row["timestamp"], tzname))
            slick = _lead_int(row.get("slick"), _SLICK_WORDS)
            if zone is None or slick is None:
                raise ValueError("unknown zone or slick rating")
        except (KeyError, ValueError):
            res["skipped"] += 1
            continue
        temps = {}
        for k in ("surface_temp_c", "air_temp_c"):
            v = _num(row.get(k, ""))
            temps[k] = None if v is None else ((v - 32) * 5 / 9 if k in f_cols else v)
        key = hashlib.sha1(json.dumps(raw, sort_keys=True).encode()).hexdigest()
        ok = add_report(conn, ts, zone, row_key=key, spot=row.get("spot"), slick=slick, mist=_yn(row.get("mist", "")),
                        sky=row.get("sky"), grip=_lead_int(row.get("grip")), street=row.get("street"),
                        side=row.get("side"), cover=row.get("cover"), photo=row.get("photo"),
                        notes=row.get("notes"), **temps)
        res["added" if ok else "already_had"] += 1
    return res


def import_csv(conn, path, tzname, zones=None):
    with open(path, encoding="utf-8-sig") as f:
        return import_text(conn, f.read(), tzname, zones)["added"]


def import_url(conn, http, url, tzname, zones=None):
    """Pull a Google Sheet that is 'published to the web' as CSV. No credentials needed."""
    return import_text(conn, http.get_bytes(url).decode("utf-8-sig"), tzname, zones)


def verify(conn, days=30, positive_slick=2, predicted_level="watch", before_min=90, after_min=30, now=None):
    """Compare what the detector said around each report with what the person saw.

    A report is 'positive' when slick >= positive_slick. The detector 'predicted' it when any evaluation for
    that zone within [-before_min, +after_min] of the report (live evaluations by their run time, forward
    evaluations by the hour they were predicting) reached predicted_level. Reports with no evaluation nearby
    are counted as uncovered and left out of the matrix."""
    now = now or utcnow()
    since = iso(now - timedelta(days=days))
    pred_rank = LEVELS.index(predicted_level)
    lo_d, hi_d = timedelta(minutes=before_min), timedelta(minutes=after_min)
    zones, trig_pos, trig_neg, uncovered = {}, {}, {}, 0
    for rep in conn.execute("SELECT * FROM reports WHERE ts_utc>=? ORDER BY ts_utc", (since,)):
        t = parse_iso(rep["ts_utc"])
        evs = conn.execute("SELECT * FROM evaluations WHERE zone=? AND ts_utc>=? AND ts_utc<=?",
                           (rep["zone"], iso(t - timedelta(hours=14)), iso(t + hi_d))).fetchall()
        near = []
        for e in evs:
            ref = parse_iso(e["valid_utc"]) if e["mode"] == "ahead" and e["valid_utc"] else parse_iso(e["ts_utc"])
            if t - lo_d <= ref <= t + hi_d:
                near.append(e)
        if not near:
            uncovered += 1
            continue
        top = max(near, key=lambda e: e["score"])
        predicted = LEVELS.index(top["level"]) >= pred_rank
        actual = (rep["slick"] or 0) >= positive_slick
        z = zones.setdefault(rep["zone"], {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
        z[("tp" if predicted else "fn") if actual else ("fp" if predicted else "tn")] += 1
        bucket = trig_pos if actual else trig_neg
        bucket["_n"] = bucket.get("_n", 0) + 1
        for r in json.loads(top["reasons"] or "[]"):
            if r.get("hit") and r.get("points"):
                bucket[r["trigger"]] = bucket.get(r["trigger"], 0) + 1

    def ratios(z):
        tp, fp, fn = z["tp"], z["fp"], z["fn"]
        return {"precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None}

    total = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for z in zones.values():
        for k in total:
            total[k] += z[k]
    unverified = 0
    for a in conn.execute("SELECT * FROM alerts WHERE ts_utc>=? AND ts_utc<=?", (since, iso(now - timedelta(hours=3)))):
        t = parse_iso(a["ts_utc"])
        has = conn.execute("SELECT 1 FROM reports WHERE zone=? AND ts_utc BETWEEN ? AND ?",
                           (a["zone"], iso(t - timedelta(minutes=30)), iso(t + timedelta(hours=3)))).fetchone()
        unverified += 0 if has else 1
    return {"zones": {k: {**v, **ratios(v)} for k, v in zones.items()}, "total": {**total, **ratios(total)},
            "uncovered_reports": uncovered, "alerts_without_report": unverified,
            "trigger_hits_in_real_events": dict(trig_pos), "trigger_hits_in_non_events": dict(trig_neg)}


def format_verify(r):
    def pct(x):
        return "n/a" if x is None else f"{100 * x:.0f}%"
    t = r["total"]
    lines = [f"Overall: {t['tp']} hit, {t['fn']} missed, {t['fp']} false alarm, {t['tn']} correct quiet   "
             f"precision {pct(t['precision'])}  recall {pct(t['recall'])}"]
    for z, v in sorted(r["zones"].items()):
        lines.append(f"  {z:<18} TP {v['tp']:>3}  FN {v['fn']:>3}  FP {v['fp']:>3}  TN {v['tn']:>3}   "
                     f"precision {pct(v['precision'])}  recall {pct(v['recall'])}")
    lines.append(f"Reports with no detector data nearby: {r['uncovered_reports']}   Alerts nobody reported on: {r['alerts_without_report']}")
    lines.append(f"Triggers present when death fog was real:  {r['trigger_hits_in_real_events']}")
    lines.append(f"Triggers present when it was NOT:          {r['trigger_hits_in_non_events']}")
    lines.append("(A trigger common in both columns isn't helping; one only in the first column is the signal.)")
    return "\n".join(lines)
