"""Open-Meteo: free, no key, non-commercial use (attribution required, CC BY 4.0).

By default the API's "best match" model is used. For North America that is currently NOAA's RRFS
(3 km over the contiguous US, updated every 6 hours), which is replacing HRRR; Open-Meteo retires its
HRRR feed on 2026-11-30. Leaving `models` unset means this keeps working when the model changes.
One request carries every zone; the free API counts each location as its own call, so a fetch weighs
(#zones x max(1, #variables / 10)). Free limits: 10,000 calls/day, 5,000/hour, 600/minute.
"""
from ..model import Reading
from ..registry import Source, register_source
from ..util import iso, parse_iso

DEFAULT_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
VARS = ["temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation",
        "cloud_cover_low", "wind_speed_10m", "wind_gusts_10m", "shortwave_radiation"]


def location_params(zones, names):
    return {"latitude": ",".join(str(zones[n]["lat"]) for n in names),
            "longitude": ",".join(str(zones[n]["lon"]) for n in names)}


def readings_from_payload(source, names, data):
    """Open-Meteo returns an object for one location and a list for several; names are in request order."""
    if isinstance(data, dict):
        data = [data]
    out = []
    for name, d in zip(names, data):
        h = d["hourly"]
        for i, t in enumerate(h["time"]):
            out.append(Reading(
                source=source, kind="model", zone=name, valid_utc=iso(parse_iso(t)),
                temp_c=h["temperature_2m"][i], rh_pct=h["relative_humidity_2m"][i],
                dewpoint_c=h["dew_point_2m"][i], precip_mm=h["precipitation"][i],
                cloud_low_pct=h["cloud_cover_low"][i], wind_mph=h["wind_speed_10m"][i],
                gust_mph=h["wind_gusts_10m"][i], solar_wm2=h["shortwave_radiation"][i]))
    return out


@register_source("openmeteo")
class OpenMeteo(Source):
    free_daily_limit = 10_000

    def cost(self):
        return len(self.zones) * max(1.0, len(VARS) / 10.0)

    def fetch(self, http, now):
        names = list(self.zones)
        params = {**location_params(self.zones, names), "hourly": ",".join(VARS),
                  "past_hours": self.cfg.get("past_hours", 24), "forecast_hours": self.cfg.get("forecast_hours", 24),
                  "wind_speed_unit": "mph", "timezone": "UTC"}
        if self.cfg.get("models"):
            params["models"] = self.cfg["models"]
        data = http.get_json(self.cfg.get("endpoint", DEFAULT_ENDPOINT), params)
        return readings_from_payload(self.name, names, data)
