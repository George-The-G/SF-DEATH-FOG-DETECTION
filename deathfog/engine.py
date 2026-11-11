"""Engine: run a detector over WeatherStates, apply zone geometry, store results.

The engine is the only layer that knows about zones. It asks `states` for a WeatherState, hands it to
whichever detector config.toml selects, and scales the result by the zone's `susceptibility`
(street geometry: east-west blocks, south side, trees = more; sunny/porous = less).
"""
import json
from datetime import timedelta

from . import states
from .detectors import build_detector
from .util import iso, parse_iso


def _score(detector, state, susc):
    raw, reasons = detector.score(state)
    score = min(100.0, raw * susc)
    return score, detector.level_for(score), reasons


def _eval(zname, mode, state, score, level, reasons, susc, **extra):
    return {"zone": zname, "mode": mode, "valid_utc": state.valid_utc, "score": score, "level": level,
            "reasons": reasons, "inputs": state.compact(), "provenance": state.provenance,
            "susceptibility": susc, **extra}


def evaluate_zone(conn, cfg, zname, now, detector=None):
    detector = detector or build_detector(cfg)
    susc = float(cfg["zones"][zname].get("susceptibility", 1.0))
    lookahead = cfg.get("state", {}).get("lookahead_hours", 12)
    model_src = cfg.get("state", {}).get("model_source", "openmeteo")
    state, series = states.build_now(conn, cfg, zname, now)
    out = []
    if state is not None and (state.temp_c is not None or state.rh_pct is not None):
        score, level, reasons = _score(detector, state, susc)
        out.append(_eval(zname, "now", state, score, level, reasons, susc))

    best, hourly = None, []
    for i, r in enumerate(series):
        if now < r["_t"] <= now + timedelta(hours=lookahead):
            st = states.state_from_model(series, i, model_src)
            score, level, reasons = _score(detector, st, susc)
            hourly.append({"valid_utc": r["valid_utc"], "score": score, "level": level})
            if best is None or score > best["score"]:
                best = _eval(zname, "ahead", st, score, level, reasons, susc)
    if best:
        best["hourly"] = hourly
        out.append(best)
    return out


def timeline(conn, cfg, zname, now, back_hours=12, ahead_hours=24, detector=None):
    """Hour-by-hour scores around `now` for the chart. Each entry splits its score into per-trigger points
    (`parts`, already scaled by susceptibility and any rain cap) so a chart can show *why* it is high."""
    detector = detector or build_detector(cfg)
    susc = float(cfg["zones"][zname].get("susceptibility", 1.0))
    model_src = cfg.get("state", {}).get("model_source", "openmeteo")
    series = states.model_series(conn, model_src, zname)
    out = []
    for i, r in enumerate(series):
        if not (now - timedelta(hours=back_hours) <= r["_t"] <= now + timedelta(hours=ahead_hours)):
            continue
        raw, reasons = detector.score(states.state_from_model(series, i, model_src))
        score = min(100.0, raw * susc)
        hits = [x for x in reasons if x["hit"] and x["points"]]
        total = sum(x["points"] for x in hits)
        k = score / total if total else 0.0
        out.append({"valid_utc": r["valid_utc"], "t": r["_t"], "score": score, "level": detector.level_for(score),
                    "parts": {x["trigger"]: x["points"] * k for x in hits}, "why": [(x["trigger"], x["detail"]) for x in hits],
                    "rain": any(x["trigger"] == "rain_veto" and x["hit"] for x in reasons)})
    return out


def evaluate_all(conn, cfg, now):
    detector = build_detector(cfg)
    evals = []
    for zname in cfg["zones"]:
        evals.extend(evaluate_zone(conn, cfg, zname, now, detector))
    return evals


def replay(series, zcfg, detector, source, start_dt):
    """Score every hour of a model series (used for the 30-day baseline). Same states, same detector as live."""
    susc = float(zcfg.get("susceptibility", 1.0))
    rows = []
    for i, r in enumerate(series):
        if r["_t"] < start_dt:
            continue
        st = states.state_from_model(series, i, source)
        score, level, reasons = _score(detector, st, susc)
        rows.append({"t": r["_t"], "score": score, "level": level,
                     "hits": [x["trigger"] for x in reasons if x["hit"] and x["points"]]})
    return rows


def store_evaluations(conn, evals, now, min_gap_min=30):
    """Keep the history small: one row per zone/mode every 30 minutes, or immediately when the level changes."""
    rows = []
    for e in evals:
        last = conn.execute("SELECT ts_utc, level FROM evaluations WHERE zone=? AND mode=? ORDER BY ts_utc DESC LIMIT 1",
                            (e["zone"], e["mode"])).fetchone()
        if last and last["level"] == e["level"] and now - parse_iso(last["ts_utc"]) < timedelta(minutes=min_gap_min):
            continue
        hits = [{"trigger": r["trigger"], "hit": True, "points": r["points"], "detail": r["detail"]}
                for r in e["reasons"] if r["hit"] and r["points"]]
        rows.append((iso(now), e["zone"], e["mode"], e["valid_utc"], e["score"], e["level"],
                     json.dumps(hits, separators=(",", ":")), json.dumps(e["inputs"], separators=(",", ":"))))
    conn.executemany("INSERT INTO evaluations(ts_utc, zone, mode, valid_utc, score, level, reasons, inputs) VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)
