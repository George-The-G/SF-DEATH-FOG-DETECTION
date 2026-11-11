"""State builder: turn stored Readings (any mix of sources) into the WeatherState a detector scores.

This is the only place that knows about source names and merge policy:
  * model data supplies the full picture (and the future);
  * the first source in [state].obs_sources with fresh measurements overrides the model's numbers;
  * derived values (24 h high, 3 h ago, recent rain) are computed here so detectors stay pure.
"""
from datetime import timedelta

from .model import STATE_FIELDS, WeatherState
from .util import dew_point_c, iso, mean, parse_iso

_MODEL_FIELDS = {"temp_c": "temp_c", "dewpoint_c": "dewpoint_c", "rh_pct": "rh_pct", "wind_mph": "wind_mph",
                 "gust_mph": "gust_mph", "solar_wm2": "solar_wm2", "cloud_low_pct": "cloud_low_pct"}


def _stamp(state, source):
    state.provenance = {k: source for k in STATE_FIELDS if getattr(state, k) is not None}
    return state


def model_series(conn, source, zone):
    """Rows from the most recent model fetch for this zone, oldest first, each with a parsed `_t`."""
    cur = conn.execute(
        """SELECT * FROM readings WHERE kind='model' AND source=? AND zone=?
           AND fetched_utc=(SELECT MAX(fetched_utc) FROM readings WHERE kind='model' AND source=? AND zone=?)
           ORDER BY valid_utc""", (source, zone, source, zone))
    rows = []
    for r in cur:
        d = dict(r)
        d["_t"] = parse_iso(d["valid_utc"])
        rows.append(d)
    return rows


def state_from_model(series, idx, source):
    row = series[idx]
    t = row["_t"]
    lo, t3 = t - timedelta(hours=24), t - timedelta(hours=3)
    temps = [r["temp_c"] for r in series if r["temp_c"] is not None and lo <= r["_t"] <= t]
    rain = [r["precip_mm"] for r in series if t3 < r["_t"] <= t and r["precip_mm"] is not None]
    s = WeatherState(
        valid_utc=row["valid_utc"], basis="model",
        precip_3h_mm=sum(rain) if rain else None,
        high_24h_c=max(temps) if temps else None,
        temp_3h_ago_c=next((r["temp_c"] for r in series if r["_t"] == t3), None),
        **{dst: row[src] for dst, src in _MODEL_FIELDS.items()})
    return _stamp(s, source)


def state_from_obs(conn, source, zone, zcfg, now, max_age_min):
    """Fresh measurements for a zone from one source, or None. Averages several instruments (e.g. sensors)."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM readings WHERE kind='obs' AND source=? AND (zone=? OR zone='*') AND valid_utc>=? ORDER BY valid_utc",
        (source, zone, iso(now - timedelta(hours=24))))]
    subs = (zcfg.get("obs_subs") or {}).get(source)       # optional per-zone instrument filter
    if subs:
        rows = [r for r in rows if r["sub"] in subs]
    if not rows:
        return None
    latest = {}
    for r in rows:                       # oldest -> newest, so the last row per instrument wins
        latest[r["sub"]] = r
    fresh = [r for r in latest.values() if now - parse_iso(r["valid_utc"]) <= timedelta(minutes=max_age_min)]
    if not fresh:
        return None
    mine = [r for r in rows if r["sub"] in {r2["sub"] for r2 in fresh}]
    s = WeatherState(valid_utc=max(r["valid_utc"] for r in fresh), basis="obs",
                     **{k: mean(r[k] for r in fresh) for k in ("temp_c", "dewpoint_c", "rh_pct", "wind_mph", "gust_mph")})
    if s.dewpoint_c is None:
        s.dewpoint_c = dew_point_c(s.temp_c, s.rh_pct)
    recent = [r["precip_mm"] for r in mine if r["precip_mm"] is not None
              and now - parse_iso(r["valid_utc"]) <= timedelta(hours=3)]
    s.precip_3h_mm = max(recent) if recent else None     # max hourly amount: "any measurable rain lately"
    temps = [r["temp_c"] for r in mine if r["temp_c"] is not None]
    s.high_24h_c = max(temps) if len(temps) >= 4 else None  # too few points -> the model's value is kept
    target = now - timedelta(hours=3)
    near = [r for r in mine if r["temp_c"] is not None and abs(parse_iso(r["valid_utc"]) - target) <= timedelta(minutes=60)]
    if near:
        s.temp_3h_ago_c = min(near, key=lambda r: abs(parse_iso(r["valid_utc"]) - target))["temp_c"]
    return _stamp(s, source)


def merge(model_state, obs_state):
    """Measurements win; the model fills whatever the instruments don't report (wind, sun, 24 h history)."""
    if obs_state is None:
        return model_state
    if model_state is None:
        return obs_state
    out = WeatherState(**{k: getattr(model_state, k) for k in STATE_FIELDS}, valid_utc=obs_state.valid_utc or model_state.valid_utc,
                       basis="obs+model", provenance=dict(model_state.provenance))
    for k in STATE_FIELDS:
        v = getattr(obs_state, k)
        if v is not None:
            setattr(out, k, v)
            out.provenance[k] = obs_state.provenance.get(k, "obs")
    return out


def build_now(conn, cfg, zname, now):
    """Best current WeatherState for a zone, plus the model series used (for look-ahead)."""
    scfg = cfg.get("state", {})
    model_src = scfg.get("model_source", "openmeteo")
    series = model_series(conn, model_src, zname)
    idx = None
    for i, r in enumerate(series):
        if r["_t"] <= now:
            idx = i
    model_now = state_from_model(series, idx, model_src) if idx is not None else None
    obs = None
    for src in scfg.get("obs_sources", ["purpleair", "nws"]):
        obs = state_from_obs(conn, src, zname, cfg["zones"][zname], now, scfg.get("obs_max_age_min", 90))
        if obs:
            break
    return merge(model_now, obs), series
