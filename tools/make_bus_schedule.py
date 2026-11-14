"""Build site/v3/data/bus_schedule.json from a Muni GTFS schedule zip: the scheduled passing times of the two shuttle lines near each district.

The viewer (bustimeline.js) uses these times to place a bus where it is SCHEDULED to be when no GPS report exists: for the future, and for stretches
missing from the GPS log. Real GPS replaces them (see the header of bustimeline.js). Standard library only.

  python3 tools/make_bus_schedule.py --gtfs path/or/url/to/muni_gtfs.zip [--days 14] [--start YYYY-MM-DD] [--out site/v3/data/bus_schedule.json]

Which lines and where come from site/v3/data/bus_config.json (district "line", "route", "radius_m") and routes.json (the district's street polyline).
Output (times are absolute epoch seconds, so the viewer needs no calendar logic):
  { "generated": iso, "tz": "America/Los_Angeles", "from": epoch, "to": epoch,
    "lines": { "19": { "trips": [ { "id": "<trip_id>@<YYYY-MM-DD>", "d": 0|1, "p": [[epoch, lat, lon], ...] } ] } } }
Only stops inside the district box are kept (typically 2 to 10 per trip), so 14 days of both lines is a few hundred KB.
Not verified against the live SFMTA/511 feed from the build container (no network there); tests use a small synthetic GTFS."""
import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import sys
import urllib.request
import zipfile
from zoneinfo import ZoneInfo

TZ = "America/Los_Angeles"
A, F, K0 = 6378137.0, 1 / 298.257223563, 0.9996
E2 = F * (2 - F)
EP2 = E2 / (1 - E2)
LON0 = math.radians(-123)
X0, Y0 = 540100.0, 4171500.0


def ll_to_scene(lat, lon):
    """Same series as site/v3/busgeo.js (scene x = east metres from E 540100, z = minus north metres from N 4171500, UTM 10N)."""
    p, l = math.radians(lat), math.radians(lon)
    s, c, t = math.sin(p), math.cos(p), math.tan(p)
    N = A / math.sqrt(1 - E2 * s * s)
    T, C, a = t * t, EP2 * c * c, c * (l - LON0)
    M = A * ((1 - E2 / 4 - 3 * E2 * E2 / 64 - 5 * E2 ** 3 / 256) * p - (3 * E2 / 8 + 3 * E2 * E2 / 32 + 45 * E2 ** 3 / 1024) * math.sin(2 * p)
             + (15 * E2 * E2 / 256 + 45 * E2 ** 3 / 1024) * math.sin(4 * p) - (35 * E2 ** 3 / 3072) * math.sin(6 * p))
    E = 500000 + K0 * N * (a + (1 - T + C) * a ** 3 / 6 + (5 - 18 * T + T * T + 72 * C - 58 * EP2) * a ** 5 / 120)
    Nn = K0 * (M + N * t * (a * a / 2 + (5 - T + 9 * C + 4 * C * C) * a ** 4 / 24 + (61 - 58 * T + T * T + 600 * C - 330 * EP2) * a ** 6 / 720))
    return E - X0, -(Nn - Y0)


def scene_to_ll(x, z):
    lat, lon = 37.76 + (-z + Y0 - 4179000) / 111200, -122.44 + (x + X0 - 548500) / 88000
    for _ in range(4):
        a, b, c = ll_to_scene(lat, lon), ll_to_scene(lat + 1e-5, lon), ll_to_scene(lat, lon + 1e-5)
        dxla, dzla, dxlo, dzlo = (b[0] - a[0]) / 1e-5, (b[1] - a[1]) / 1e-5, (c[0] - a[0]) / 1e-5, (c[1] - a[1]) / 1e-5
        ex, ez, det = x - a[0], z - a[1], dxla * dzlo - dxlo * dzla
        lat += (ex * dzlo - ez * dxlo) / det
        lon += (dxla * ez - dzla * ex) / det
    return lat, lon


def district_boxes(cfg, routes):
    """{line: (lat0, lon0, lat1, lon1)}: the district's street polyline widened by its radius."""
    out = {}
    by_id = {r["id"]: r for r in routes["routes"]}
    for d in cfg["districts"]:
        poly = by_id[d["route"]]["poly"]
        m = float(d.get("radius_m", 1000))
        xs, zs = [p[0] for p in poly], [p[2] for p in poly]
        la0, lo0 = scene_to_ll(min(xs) - m, max(zs) + m)
        la1, lo1 = scene_to_ll(max(xs) + m, min(zs) - m)
        out[str(d["line"])] = (min(la0, la1), min(lo0, lo1), max(la0, la1), max(lo0, lo1))
    return out


def _rows(zf, name):
    if name not in zf.namelist():
        return
    with zf.open(name) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))


def _gtfs_secs(s):
    h, m, sec = (int(x) for x in s.strip().split(":"))
    return h * 3600 + m * 60 + sec


def build(zf, boxes, start, days, tz=TZ):
    """zf: open GTFS zipfile; boxes: {line: (lat0, lon0, lat1, lon1)}; start: first local date; days: how many dates."""
    zone = ZoneInfo(tz)
    dates = [start + dt.timedelta(days=i) for i in range(days)]
    route_line = {r["route_id"]: r["route_short_name"] for r in _rows(zf, "routes.txt") if r["route_short_name"] in boxes}
    trips = {t["trip_id"]: t for t in _rows(zf, "trips.txt") if t["route_id"] in route_line}
    cal = {c["service_id"]: c for c in _rows(zf, "calendar.txt")}
    exc = {}
    for e in _rows(zf, "calendar_dates.txt"):
        exc.setdefault(e["service_id"], {})[e["date"]] = e["exception_type"]
    wk = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    def active(svc, day):
        ds = day.strftime("%Y%m%d")
        e = exc.get(svc, {}).get(ds)
        if e == "1":
            return True
        if e == "2":
            return False
        c = cal.get(svc)
        return bool(c and c["start_date"] <= ds <= c["end_date"] and c[wk[day.weekday()]] == "1")

    stops = {s["stop_id"]: (float(s["stop_lat"]), float(s["stop_lon"])) for s in _rows(zf, "stops.txt")}
    inbox = {}
    for sid, (la, lo) in stops.items():
        inbox[sid] = [ln for ln, (a0, o0, a1, o1) in boxes.items() if a0 <= la <= a1 and o0 <= lo <= o1]
    times = {}  # trip_id -> [(seq, secs, stop_id)] for stops in the line's box
    for st in _rows(zf, "stop_times.txt"):
        t = trips.get(st["trip_id"])
        if t is None or not st.get("arrival_time", "").strip():
            continue
        if route_line[t["route_id"]] in inbox.get(st["stop_id"], ()):
            times.setdefault(st["trip_id"], []).append((int(st["stop_sequence"]), _gtfs_secs(st["arrival_time"]), st["stop_id"]))
    out = {ln: {"trips": []} for ln in boxes}
    for tid, rows in times.items():
        if len(rows) < 2:
            continue
        rows.sort()
        t = trips[tid]
        line = route_line[t["route_id"]]
        for day in dates:
            if not active(t["service_id"], day):
                continue
            midnight = dt.datetime(day.year, day.month, day.day, tzinfo=zone)
            # GTFS times count from "noon minus 12 h" so a trip after midnight is >= 24:00:00; use the local midnight of the service day
            base = midnight.timestamp()
            p = [[int(round(base + sec)), round(stops[sid][0], 5), round(stops[sid][1], 5)] for _, sec, sid in rows]
            out[line]["trips"].append({"id": f"{tid}@{day.isoformat()}", "d": int(t.get("direction_id") or 0), "p": p})
    for ln in out:
        out[ln]["trips"].sort(key=lambda x: x["p"][0][0])
    lo = int(dt.datetime(dates[0].year, dates[0].month, dates[0].day, tzinfo=zone).timestamp())
    return {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "tz": tz, "from": lo, "to": lo + days * 86400, "lines": out}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gtfs", required=True, help="GTFS zip file or http(s) URL")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--start", help="first local date YYYY-MM-DD (default: 3 days ago)")
    ap.add_argument("--config", default="site/v3/data/bus_config.json")
    ap.add_argument("--routes", default="site/v3/data/routes.json")
    ap.add_argument("--out", default="site/v3/data/bus_schedule.json")
    a = ap.parse_args(argv)
    with open(a.config, encoding="utf-8") as f:
        cfg = json.load(f)
    with open(a.routes, encoding="utf-8") as f:
        routes = json.load(f)
    src = a.gtfs
    if src.startswith(("http://", "https://")):
        with urllib.request.urlopen(src, timeout=120) as r:
            src = io.BytesIO(r.read())
    start = dt.date.fromisoformat(a.start) if a.start else dt.datetime.now(ZoneInfo(TZ)).date() - dt.timedelta(days=3)
    with zipfile.ZipFile(src) as zf:
        res = build(zf, district_boxes(cfg, routes), start, a.days)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, separators=(",", ":"))
    n = {ln: len(v["trips"]) for ln, v in res["lines"].items()}
    print(f"wrote {a.out}: trips per line {n}, {os.path.getsize(a.out) / 1024:.0f} KB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
