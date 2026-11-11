"""Shared fixtures: recorded-format API payloads and a fake HTTP client (no network in tests)."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from deathfog.util import iso  # noqa: E402

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)   # 2:00 am PST


def temp(i):   # i = hours relative to NOW: warm afternoon (18C at i=-11), cooling to 9C by NOW, warming after sunrise
    if i <= -11:
        return 18 - 0.4 * (-11 - i)
    if i <= 0:
        return 18 + (9 - 18) * (i + 11) / 11
    return 9.0 + max(0, i - 6) * 0.5


def om_payload(zones):
    hours = list(range(-24, 25))
    times = [iso(NOW + timedelta(hours=i)).replace(":00Z", "").replace("Z", "") for i in hours]
    one = {"hourly": {
        "time": times,
        "temperature_2m": [temp(i) for i in hours],
        "relative_humidity_2m": [96 if i <= 6 else 70 for i in hours],
        "dew_point_2m": [temp(i) - (0.5 if i <= 6 else 5) for i in hours],
        "precipitation": [0.0 for _ in hours],
        "cloud_cover_low": [10 for _ in hours],
        "wind_speed_10m": [3.0 for _ in hours],
        "wind_gusts_10m": [6.0 for _ in hours],
        "shortwave_radiation": [0 if -8 <= i <= 6 else 300 for i in hours],
    }}
    return [one] * len(zones) if len(zones) > 1 else one


def hist_payload(zones, first_day, days):
    """Every night, 08-15 UTC (midnight-8am PST) is a textbook death-fog night; days are dry and breezy."""
    def t(h):
        if h <= 7:
            return 18 - 1.125 * (h + 1)
        if h <= 15:
            return 9.0
        return 9.0 + (h - 15) * 1.125
    times, temps, dews, rhs, winds, sun = [], [], [], [], [], []
    for d in range(days):
        for h in range(24):
            fog = 8 <= h <= 15
            times.append((first_day + timedelta(days=d, hours=h)).strftime("%Y-%m-%dT%H:%M"))
            temps.append(t(h))
            dews.append(t(h) - (0.5 if fog else 6))
            rhs.append(96 if fog else 65)
            winds.append(3.0 if fog else 10.0)
            sun.append(0 if fog else 400)
    n = len(times)
    one = {"hourly": {"time": times, "temperature_2m": temps, "relative_humidity_2m": rhs, "dew_point_2m": dews,
                      "precipitation": [0.0] * n, "cloud_cover_low": [10] * n, "wind_speed_10m": winds,
                      "wind_gusts_10m": [8.0] * n, "shortwave_radiation": sun}}
    return [one] * len(zones) if len(zones) > 1 else one


NWS_PAYLOAD = {"properties": {
    "timestamp": "2026-11-12T09:56:00+00:00", "textDescription": "Mostly Clear",
    "temperature": {"unitCode": "wmoUnit:degC", "value": 9.4},
    "dewpoint": {"unitCode": "wmoUnit:degC", "value": 8.9},
    "relativeHumidity": {"unitCode": "wmoUnit:percent", "value": 96.0},
    "windSpeed": {"unitCode": "wmoUnit:km_h-1", "value": 8.0},
    "windGust": {"unitCode": "wmoUnit:km_h-1", "value": None},
    "precipitationLastHour": {"unitCode": "wmoUnit:mm", "value": None},
}}

PA_PAYLOAD = {"fields": ["sensor_index", "temperature", "humidity"], "data_time_stamp": int(NOW.timestamp()),
              "data": [[111, 50.0, 80], [222, 52.0, 70]]}

IEM_TEXT = ("#DEBUG: Format Description: ...\n#DEBUG: more\nstation\tvalid\ttmpf\tdwpf\trelh\tsknt\tgust\tp01i\n"
            "SFO\t2026-11-10 09:56\t48.2\t47.3\t96\t10.00\tM\t0.10\n"
            "SFO\t2026-11-10 10:56\t48.2\t47.3\t96.0\t5.00\tM\tM\n")


def _way(i, name, pts, **tags):
    return {"type": "way", "id": i, "tags": {"highway": "residential", "name": name, **tags},
            "geometry": [{"lat": a, "lon": b} for a, b in pts]}


# Mariposa: an east-west block with trees and parked cars; Missouri: a north-south block with neither.
OVERPASS_PAYLOAD = {"elements": [
    _way(1, "Mariposa St", [(37.7595, -122.4020), (37.7595, -122.4010), (37.7595, -122.4000), (37.7595, -122.3990)],
         **{"parking:lane:both": "parallel"}),
    _way(2, "Missouri St", [(37.7570, -122.4000), (37.7585, -122.4000), (37.7600, -122.4000), (37.7615, -122.4000)],
         **{"parking:lane:both": "no", "surface": "concrete"}),
] + [{"type": "node", "id": 100 + k, "lat": 37.75956, "lon": -122.4020 + 0.0002 * k, "tags": {"natural": "tree"}}
     for k in range(15)]}


class FakeHttp:
    def __init__(self, zones=None, hist_days=31):
        self.calls, self.zones, self.hist_days = [], zones or {}, hist_days

    def get_json(self, url, params=None, headers=None):
        self.calls.append((url, params))
        if "historical-forecast" in url:
            first = datetime.fromisoformat(params["start_date"]).replace(tzinfo=timezone.utc)
            days = (datetime.fromisoformat(params["end_date"]) - datetime.fromisoformat(params["start_date"])).days + 1
            return hist_payload(self.zones, first, days)
        if "open-meteo" in url:
            return om_payload(self.zones)
        if "api.weather.gov/stations" in url:
            return NWS_PAYLOAD
        if "overpass" in url:
            return OVERPASS_PAYLOAD
        if "purpleair" in url:
            return PA_PAYLOAD
        raise AssertionError(url)

    def get_bytes(self, url, params=None, headers=None):
        self.calls.append((url, params))
        if "asos.py" in url:
            return IEM_TEXT.encode()
        return b"imagebytes"


def make_cfg(**over):
    cfg = {
        "general": {"timezone": "America/Los_Angeles", "report_url": "https://forms.example/x"},
        "zones": {"potrero": {"label": "Potrero", "lat": 37.76, "lon": -122.4, "susceptibility": 1.0, "obs_subs": {"nws": ["KSFO"]}},
                  "control": {"label": "Control", "lat": 37.74, "lon": -122.49, "susceptibility": 0.3, "obs_subs": {"nws": ["KSFO"]}}},
        "sources": {"nws": {"enabled": True, "interval_min": 10, "stations": ["KSFO"]},
                    "openmeteo": {"enabled": True, "interval_min": 30, "daily_budget": 6000}},
        "state": {}, "detector": {}, "notify": {"alert_min_level": "likely", "cooldown_hours": 4},
        "reports": {}, "baseline": {}, "site": {},
    }
    cfg.update(over)
    return cfg
