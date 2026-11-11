"""Detector hooks.

A detector turns one `WeatherState` into a 0-100 score plus the reasons behind it. It is pure: no
database, no network, no knowledge of where the numbers came from. Register a new one with
`@register_detector("name")` (built in, or as a file in plugins/) and select it in config.toml:

    [detector]
    type = "my_detector"
    some_threshold = 3.0        # everything else in this table is passed to the detector as params
"""

LEVELS = ["none", "watch", "likely", "strong"]
DETECTORS = {}


def register_detector(name):
    def deco(cls):
        cls.type_name = name
        DETECTORS[name] = cls
        return cls
    return deco


class Detector:
    type_name = "base"
    defaults = {}
    bands = {"watch": 40, "likely": 60, "strong": 80}

    def __init__(self, params=None):
        self.params = {**self.bands, **self.defaults, **(params or {})}

    def score(self, state):
        """Return (raw_score 0-100, reasons). Each reason is a dict:
        {"trigger": str, "hit": bool, "detail": str, "points": number}."""
        raise NotImplementedError

    def level_for(self, score):
        p = self.params
        if score >= p["strong"]:
            return "strong"
        if score >= p["likely"]:
            return "likely"
        if score >= p["watch"]:
            return "watch"
        return "none"


def build_detector(cfg):
    params = dict(cfg.get("detector", {}))
    typ = params.pop("type", "death_fog_v1")
    if typ not in DETECTORS:
        raise KeyError(f"unknown detector '{typ}' (known: {sorted(DETECTORS)})")
    return DETECTORS[typ](params)
