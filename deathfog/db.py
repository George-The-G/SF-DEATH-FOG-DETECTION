import json
import pathlib
import sqlite3
from datetime import timedelta

from .model import FIELDS
from .util import iso, parse_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings(
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL, kind TEXT NOT NULL, zone TEXT NOT NULL, sub TEXT NOT NULL DEFAULT '',
  valid_utc TEXT NOT NULL, fetched_utc TEXT NOT NULL,
  temp_c REAL, dewpoint_c REAL, rh_pct REAL, wind_mph REAL, gust_mph REAL,
  precip_mm REAL, cloud_low_pct REAL, solar_wm2 REAL, extra TEXT
);
-- observations: one row per instrument per measurement time
CREATE UNIQUE INDEX IF NOT EXISTS ux_obs ON readings(source, zone, sub, valid_utc) WHERE kind='obs';
-- model output: keep every forecast issue so past forecasts can be checked later
CREATE UNIQUE INDEX IF NOT EXISTS ux_model ON readings(source, zone, valid_utc, fetched_utc) WHERE kind='model';
CREATE INDEX IF NOT EXISTS ix_readings_zone_time ON readings(zone, valid_utc);

CREATE TABLE IF NOT EXISTS api_calls(
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, ts_utc TEXT NOT NULL,
  weight REAL NOT NULL, ok INTEGER NOT NULL, note TEXT
);
CREATE INDEX IF NOT EXISTS ix_calls ON api_calls(source, ts_utc);

CREATE TABLE IF NOT EXISTS evaluations(
  id INTEGER PRIMARY KEY, ts_utc TEXT NOT NULL, zone TEXT NOT NULL,
  mode TEXT NOT NULL,            -- 'now' (live conditions) or 'ahead' (worst hour in lookahead window)
  valid_utc TEXT, score REAL, level TEXT, reasons TEXT, inputs TEXT
);
CREATE INDEX IF NOT EXISTS ix_eval ON evaluations(zone, ts_utc);

CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY, ts_utc TEXT NOT NULL, zone TEXT NOT NULL, level TEXT NOT NULL,
  score REAL, mode TEXT, valid_utc TEXT, reasons TEXT
);

CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY, ts_utc TEXT NOT NULL, zone TEXT NOT NULL, spot TEXT,
  slick INTEGER,                 -- 0 dry, 1 damp, 2 slick, 3 death fog
  mist INTEGER, sky TEXT, grip INTEGER,
  surface_temp_c REAL, air_temp_c REAL,
  street TEXT, side TEXT, cover TEXT, photo TEXT, notes TEXT, created_utc TEXT,
  row_key TEXT                  -- dedupe key for rows imported from a form export
);
CREATE INDEX IF NOT EXISTS ix_reports ON reports(zone, ts_utc);
"""


def connect(path):
    if str(path) != ":memory:":
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(reports)")}
    if "row_key" not in cols:                     # databases made by an older version
        conn.execute("ALTER TABLE reports ADD COLUMN row_key TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_reports_key ON reports(row_key) WHERE row_key IS NOT NULL")
    conn.commit()


def insert_readings(conn, readings, fetched_utc):
    cols = ("source", "kind", "zone", "sub", "valid_utc", "fetched_utc") + FIELDS + ("extra",)
    rows = [(r.source, r.kind, r.zone, r.sub, r.valid_utc, fetched_utc,
             *(getattr(r, f) for f in FIELDS), json.dumps(r.extra)) for r in readings]
    cur = conn.executemany(
        f"INSERT OR IGNORE INTO readings({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)
    conn.commit()
    return cur.rowcount if rows else 0


def log_call(conn, source, ts_utc, weight, ok, note=""):
    conn.execute("INSERT INTO api_calls(source, ts_utc, weight, ok, note) VALUES (?,?,?,?,?)",
                 (source, ts_utc, weight, 1 if ok else 0, note))
    conn.commit()


def last_ok(conn, source):
    r = conn.execute("SELECT MAX(ts_utc) m FROM api_calls WHERE source=? AND ok=1", (source,)).fetchone()
    return parse_iso(r["m"]) if r["m"] else None


def weight_today(conn, source, now):
    """Usage so far in the current UTC day (the unit Open-Meteo counts its daily limit in)."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    r = conn.execute("SELECT COALESCE(SUM(weight),0) w FROM api_calls WHERE source=? AND ts_utc>=?",
                     (source, iso(start))).fetchone()
    return r["w"]


def last_attempt(conn, source):
    r = conn.execute("SELECT MAX(ts_utc) m FROM api_calls WHERE source=?", (source,)).fetchone()
    return parse_iso(r["m"]) if r["m"] else None


def prune_old(conn, now, model_days=1, eval_days=180, call_days=30):
    """Keep the database small: only the newest model issue is ever scored, so old ones go quickly.
    Backfilled history (source ending in _hist), observations and field reports are kept."""
    n = conn.execute("DELETE FROM readings WHERE kind='model' AND source NOT LIKE '%\\_hist' ESCAPE '\\' AND fetched_utc<?",
                     (iso(now - timedelta(days=model_days)),)).rowcount
    conn.execute("DELETE FROM evaluations WHERE ts_utc<?", (iso(now - timedelta(days=eval_days)),))
    conn.execute("DELETE FROM api_calls WHERE ts_utc<?", (iso(now - timedelta(days=call_days)),))
    conn.commit()
    return n
