"""The two data contracts that keep the layers apart.

    sources  --Reading-->  store  --states.py-->  WeatherState  --detector-->  Result

* `Reading` is what a source adapter emits (one instrument, one time, normalised units).
* `WeatherState` is the ONLY thing a detector ever sees: one place, one moment, with everything a
  detector might want already merged from whichever sources were available and derived
  (24 h high, 3 h ago, recent rain). Detectors never know which provider a number came from;
  `provenance` records it for display and debugging.
"""
from dataclasses import dataclass, field

from .util import dew_point_c

# Normalised units everywhere: Celsius, mph, mm, percent, W/m2.
FIELDS = ("temp_c", "dewpoint_c", "rh_pct", "wind_mph", "gust_mph",
          "precip_mm", "cloud_low_pct", "solar_wm2")


@dataclass
class Reading:
    """One normalised data point. Every source adapter returns a list of these.

    kind:  "obs"   = a real measurement (station, sensor)
           "model" = model output for a valid time (past, present or forecast)
    zone:  a zone name from config, or "*" for a regional reading that applies to all zones
    sub:   station / sensor id, so several instruments can feed one zone
    """
    source: str
    kind: str
    zone: str
    valid_utc: str
    sub: str = ""
    temp_c: float | None = None
    dewpoint_c: float | None = None
    rh_pct: float | None = None
    wind_mph: float | None = None
    gust_mph: float | None = None
    precip_mm: float | None = None
    cloud_low_pct: float | None = None
    solar_wm2: float | None = None
    extra: dict = field(default_factory=dict)


# Fields of WeatherState that carry numbers (everything except valid_utc / basis / provenance).
STATE_FIELDS = ("temp_c", "dewpoint_c", "rh_pct", "wind_mph", "gust_mph", "solar_wm2", "cloud_low_pct",
                "precip_3h_mm", "high_24h_c", "temp_3h_ago_c")


@dataclass
class WeatherState:
    """Detector input: source-independent description of conditions at one place and time."""
    valid_utc: str = ""
    temp_c: float | None = None
    dewpoint_c: float | None = None
    rh_pct: float | None = None
    wind_mph: float | None = None
    gust_mph: float | None = None
    solar_wm2: float | None = None
    cloud_low_pct: float | None = None
    precip_3h_mm: float | None = None     # rain in the last 3 h (None = unknown)
    high_24h_c: float | None = None       # warmest temperature in the previous 24 h
    temp_3h_ago_c: float | None = None
    basis: str = "none"                   # "model", "obs" or "obs+model"
    provenance: dict = field(default_factory=dict)   # field name -> source that supplied it

    def dew_point(self):
        """Reported dew point, or derived from temperature and humidity when a source lacks it."""
        return self.dewpoint_c if self.dewpoint_c is not None else dew_point_c(self.temp_c, self.rh_pct)

    def compact(self):
        out = {k: round(getattr(self, k), 1) for k in STATE_FIELDS if isinstance(getattr(self, k), float)}
        out["basis"] = self.basis
        out["valid_utc"] = self.valid_utc
        return out
