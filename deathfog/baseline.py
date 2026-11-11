"""30-day baseline: backfill last month, replay the detector hour by hour, list when death fog could
have happened, and check the model against airport observations.

Sources (both free, no account):
  * Open-Meteo "historical forecast" API: what the weather models predicted at the time, hour by hour.
  * Iowa Environmental Mesonet ASOS archive: measured conditions at the airport (e.g. KSFO).
The replay uses the SAME state builder and detector as the live loop, so the numbers mean the same thing.
"""
import json
import pathlib
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import db, engine, states
from .detectors import build_detector
from .detectors.base import LEVELS
from .model import Reading
from .sources.openmeteo import VARS, location_params, readings_from_payload
from .util import (dew_point_c, hour_label, in_to_mm, iso, knots_to_mph, local_hour, local_str, mean,
                   parse_iso)

HIST_ENDPOINT = "https://historical-forecast-api.open-meteo.com/v1/forecast"
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
HIST_SOURCE, OBS_SOURCE = "openmeteo_hist", "iem_asos"


# ---------- fetching ----------

def fetch_model_history(http, cfg, start, end):
    names = list(cfg["zones"])
    params = {**location_params(cfg["zones"], names), "start_date": start.isoformat(), "end_date": end.isoformat(),
              "hourly": ",".join(VARS), "wind_speed_unit": "mph", "timezone": "UTC"}
    bcfg = cfg.get("baseline", {})
    if bcfg.get("models"):
        params["models"] = bcfg["models"]
    return readings_from_payload(HIST_SOURCE, names, http.get_json(bcfg.get("model_endpoint", HIST_ENDPOINT), params))


def parse_iem(text, station_id, source=OBS_SOURCE):
    """IEM returns tab- or comma-separated text with optional '#' comment lines; 'M' means missing."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines:
        return []
    delim = "\t" if "\t" in lines[0] else ","
    header = [h.strip() for h in lines[0].split(delim)]
    if "valid" not in header:
        raise ValueError("unexpected response from IEM (no 'valid' column)")
    out = []
    for ln in lines[1:]:
        row = dict(zip(header, (c.strip() for c in ln.split(delim))))

        def num(k, fn=lambda x: x):
            v = row.get(k, "")
            return None if v in ("", "M", "NA") else fn(float(v))
        t = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        temp, dew = num("tmpf", lambda f: (f - 32) * 5 / 9), num("dwpf", lambda f: (f - 32) * 5 / 9)
        rh = num("relh")
        out.append(Reading(source=source, kind="obs", zone="*", sub=station_id, valid_utc=iso(t),
                           temp_c=temp, dewpoint_c=dew if dew is not None else dew_point_c(temp, rh), rh_pct=rh,
                           wind_mph=num("sknt", knots_to_mph), gust_mph=num("gust", knots_to_mph),
                           precip_mm=num("p01i", in_to_mm)))
    return out


def fetch_iem(http, station_id, start, end, iem_id=None):
    iem_id = iem_id or (station_id[1:] if len(station_id) == 4 and station_id.startswith("K") else station_id)
    nxt = end + timedelta(days=1)       # the archive's end date is exclusive of that day
    text = http.get_bytes(IEM_URL, {
        "station": iem_id, "data": "tmpf,dwpf,relh,sknt,gust,p01i", "year1": start.year, "month1": start.month,
        "day1": start.day, "year2": nxt.year, "month2": nxt.month, "day2": nxt.day, "tz": "UTC",
        "format": "tdf", "latlon": "no", "elev": "no", "report_type": "3,4"}).decode("utf-8", "replace")
    return parse_iem(text, station_id)


# ---------- analysis ----------

def _episodes(rows):
    eps, cur = [], None
    rank = LEVELS.index("likely")
    for r in rows:
        hot = LEVELS.index(r["level"]) >= rank
        if hot and cur is None:
            cur = {"start": r["t"], "hours": 1, "peak": r["score"], "hits": Counter(r["hits"])}
        elif hot:
            cur["hours"] += 1
            cur["peak"] = max(cur["peak"], r["score"])
            cur["hits"].update(r["hits"])
        elif cur:
            eps.append(cur)
            cur = None
    if cur:
        eps.append(cur)
    return eps


def summarize_zone(rows, days, tz):
    n = len(rows)
    levels = Counter(r["level"] for r in rows)
    eps = _episodes(rows)
    starts = Counter(local_hour(e["start"], tz) for e in eps)
    hits = Counter(h for r in rows for h in r["hits"])
    return {
        "hours": n,
        "pct_of_hours": {lv: round(100 * levels[lv] / n, 1) if n else 0 for lv in LEVELS},
        "episodes": len(eps),
        "episodes_per_week": round(len(eps) / (days / 7), 1) if days else 0,
        "typical_start_hour_local": starts.most_common(1)[0][0] if starts else None,
        "trigger_hit_pct_of_hours": {k: round(100 * v / n, 1) for k, v in hits.most_common()} if n else {},
        "episode_list": [{"start_utc": iso(e["start"]), "start_local": local_str(e["start"], tz), "hours": e["hours"],
                          "peak_score": round(e["peak"]), "why": [k for k, _ in e["hits"].most_common(4)]}
                         for e in eps[:90]],
    }


def compare_obs(conn, zname, station, start):
    series = {r["valid_utc"]: r for r in states.model_series(conn, HIST_SOURCE, zname)}
    d_t, d_d = [], []
    for o in conn.execute("SELECT * FROM readings WHERE kind='obs' AND source=? AND sub=? AND valid_utc>=?",
                          (OBS_SOURCE, station, iso(start))):
        hour = iso((parse_iso(o["valid_utc"]) + timedelta(minutes=30)).replace(minute=0, second=0))
        m = series.get(hour)
        if not m:
            continue
        if o["temp_c"] is not None and m["temp_c"] is not None:
            d_t.append(m["temp_c"] - o["temp_c"])
        if o["dewpoint_c"] is not None and m["dewpoint_c"] is not None:
            d_d.append(m["dewpoint_c"] - o["dewpoint_c"])
    r1 = lambda v: None if v is None else round(v, 2)
    return {"station": station, "n": len(d_t), "temp_bias_c": r1(mean(d_t)), "temp_mae_c": r1(mean(abs(x) for x in d_t) if d_t else None),
            "dewpoint_bias_c": r1(mean(d_d)), "dewpoint_mae_c": r1(mean(abs(x) for x in d_d) if d_d else None)}


def run_baseline(cfg, conn, http, now, days=30, end=None, path=None):
    tz = cfg["general"].get("timezone", "America/Los_Angeles")
    end = end or (now - timedelta(days=1)).date()
    start = end - timedelta(days=days - 1)
    warm = start - timedelta(days=1)    # one extra day so the first hours still have a 24 h history
    notes = []

    readings = fetch_model_history(http, cfg, warm, end)
    db.insert_readings(conn, readings, iso(now))
    db.log_call(conn, "baseline", iso(now), len(cfg["zones"]) * 3.0, True, f"{len(readings)} model rows")

    stations = sorted({(z.get("obs_subs") or {}).get("nws", [None])[0] for z in cfg["zones"].values()} - {None})
    for st in stations:
        try:
            obs = fetch_iem(http, st, start, end, (cfg.get("baseline", {}).get("iem_stations") or {}).get(st))
            db.insert_readings(conn, obs, iso(now))
            if not obs:
                notes.append(f"no airport observations returned for {st}")
        except Exception as e:  # observations are a cross-check; never fail the baseline over them
            notes.append(f"could not fetch airport observations for {st}: {str(e)[:120]}")

    detector = build_detector(cfg)
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    zones, vs_obs = {}, {}
    for zname, zcfg in cfg["zones"].items():
        series = states.model_series(conn, HIST_SOURCE, zname)
        zones[zname] = {"label": zcfg.get("label", zname), "susceptibility": zcfg.get("susceptibility", 1.0),
                        **summarize_zone(engine.replay(series, zcfg, detector, HIST_SOURCE, start_dt), days, tz)}
        st = (zcfg.get("obs_subs") or {}).get("nws", [None])[0]
        if st:
            vs_obs[zname] = compare_obs(conn, zname, st, start_dt)
    summary = {"generated_utc": iso(now), "start": start.isoformat(), "end": end.isoformat(), "days": days,
               "detector": detector.type_name, "thresholds": detector.params,
               "model": "Open-Meteo historical forecast (past model runs, best match)",
               "zones": zones, "model_vs_airport": vs_obs, "notes": notes}
    if path:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, indent=1))
    return summary


def format_baseline(s):
    lines = [f"Baseline {s['start']} to {s['end']} ({s['days']} days), detector {s['detector']}", ""]
    for z in s["zones"].values():
        p = z["pct_of_hours"]
        lines.append(f"{z['label']} (susceptibility x{z['susceptibility']})")
        lines.append(f"  {z['episodes']} episodes at 'likely' or stronger ({z['episodes_per_week']}/week); "
                     f"hours: quiet {p['none']}%, watch {p['watch']}%, likely {p['likely']}%, strong {p['strong']}%")
        if z["trigger_hit_pct_of_hours"]:
            lines.append("  triggers firing (% of all hours): " + ", ".join(f"{k} {v}%" for k, v in z["trigger_hit_pct_of_hours"].items()))
        for e in z["episode_list"]:
            lines.append(f"    {e['start_local']:<14}{e['hours']:>3} h   peak {e['peak_score']:>3}   {', '.join(e['why'])}")
        lines.append("")
    for name, c in s["model_vs_airport"].items():
        lines.append(f"Model vs {c['station']} airport at {name}: {c['n']} hours compared, temperature bias {c['temp_bias_c']}C "
                     f"(typical error {c['temp_mae_c']}C), dew point bias {c['dewpoint_bias_c']}C (typical error {c['dewpoint_mae_c']}C)")
    lines += s["notes"]
    return "\n".join(lines)
