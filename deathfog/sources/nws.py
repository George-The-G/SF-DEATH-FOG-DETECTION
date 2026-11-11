"""NWS observations (api.weather.gov): free, no key, needs a User-Agent with contact info.

Only the latest observation is kept by NWS for 7 days, so we poll and store our own history.
Stations usually report hourly (plus specials), so polling every 10 minutes mostly returns the
same observation; the unique index drops duplicates and the cost is tiny.
"""
from ..model import Reading
from ..registry import Source, register_source
from ..util import iso, kmh_to_mph, parse_iso

BASE = "https://api.weather.gov"
HEADERS = {"Accept": "application/geo+json"}


def _val(props, key):
    v = (props.get(key) or {}).get("value")
    return None if v is None else float(v)


def _unit(props, key):
    return (props.get(key) or {}).get("unitCode", "")


def parse_observation(station, payload, source_name):
    p = payload["properties"]
    wind = _val(p, "windSpeed")
    gust = _val(p, "windGust")
    # NWS reports wind in km/h (wmoUnit:km_h-1)
    if wind is not None and "km_h" in _unit(p, "windSpeed"):
        wind = kmh_to_mph(wind)
    if gust is not None and "km_h" in _unit(p, "windGust"):
        gust = kmh_to_mph(gust)
    precip = _val(p, "precipitationLastHour")
    if precip is not None and _unit(p, "precipitationLastHour").endswith(":m"):
        precip *= 1000.0
    return Reading(
        source=source_name, kind="obs", zone="*", sub=station,
        valid_utc=iso(parse_iso(p["timestamp"])),
        temp_c=_val(p, "temperature"), dewpoint_c=_val(p, "dewpoint"),
        rh_pct=_val(p, "relativeHumidity"), wind_mph=wind, gust_mph=gust, precip_mm=precip,
        extra={"station": station, "text": p.get("textDescription")},
    )


def nearest_stations(http, lat, lon):
    """Helper for the `nws-stations` command: which stations are closest to a point?"""
    pt = http.get_json(f"{BASE}/points/{lat},{lon}", headers=HEADERS)
    stations = http.get_json(pt["properties"]["observationStations"], headers=HEADERS)
    return [(f["properties"]["stationIdentifier"], f["properties"]["name"]) for f in stations["features"]]


@register_source("nws")
class NWS(Source):
    free_daily_limit = None  # NWS publishes no number; asks for sane polling and a real User-Agent

    def stations(self):
        return list(self.cfg.get("stations", ["KSFO"]))

    def cost(self):
        return float(len(self.stations()))

    def fetch(self, http, now):
        out = []
        for st in self.stations():
            payload = http.get_json(f"{BASE}/stations/{st}/observations/latest", headers=HEADERS)
            out.append(parse_observation(st, payload, self.name))
        return out
