"""Default detector: score the conditions that make roads slick with condensation.

Physics in one line: a film forms when the road surface drops to the dew point. Roads cool below the
air on calm, clear nights, so we look for air already near saturation (small temperature - dew point
gap), still, recently much warmer, and with little sun to dry things out. Every threshold is a
starting guess in config.toml [detector]; tune them against field reports with `verify`.
"""
from ..util import c_to_f, dc_to_f
from .base import Detector, register_detector


@register_detector("death_fog_v1")
class DeathFogV1(Detector):
    defaults = {
        "spread_hit_c": 2.0,       # temp - dew point at or below this (and RH high) = saturated
        "spread_watch_c": 4.0,     # close to saturated
        "rh_min": 90.0,
        "wind_max_mph": 8.0,       # wind above this mixes/dries the film away
        "rain_mm": 0.2,            # measurable rain = a different problem; caps the score
        "swing_min_c": 8.0,        # 24h high minus current temperature
        "cool_rate_c_3h": 3.0,     # fast cooling over 3 hours
        "solar_max_wm2": 20.0,     # no meaningful sun
    }
    points = {"saturation": 40, "saturation_near": 20, "calm": 20, "swing": 25, "cooling": 10, "no_sun": 15}
    rain_cap = 20

    def score(self, s):
        th, pts, reasons = self.params, 0.0, []

        def add(name, hit, detail, points=0):
            reasons.append({"trigger": name, "hit": hit, "detail": detail, "points": points if hit else 0})
            return points if hit else 0

        t, rh, td = s.temp_c, s.rh_pct, s.dew_point()
        if t is None or td is None:
            reasons.append({"trigger": "saturation", "hit": False, "detail": "unknown (no temp/dew point)", "points": 0})
        else:
            spread = t - td
            if spread <= th["spread_hit_c"] and (rh is None or rh >= th["rh_min"]):
                pts += add("saturation", True, f"temp-dewpoint gap {dc_to_f(spread):.1f}°F, RH {rh if rh is not None else '?'}%",
                           self.points["saturation"])
            elif spread <= th["spread_watch_c"]:
                pts += add("saturation_near", True, f"gap {dc_to_f(spread):.1f}°F (close)", self.points["saturation_near"])
            else:
                add("saturation", False, f"gap {dc_to_f(spread):.1f}°F")

        if s.wind_mph is None:
            reasons.append({"trigger": "calm", "hit": False, "detail": "unknown wind", "points": 0})
        else:
            pts += add("calm", s.wind_mph <= th["wind_max_mph"], f"wind {s.wind_mph:.0f} mph", self.points["calm"])

        if s.high_24h_c is None or t is None:
            reasons.append({"trigger": "swing", "hit": False, "detail": "unknown 24h swing", "points": 0})
        else:
            swing = s.high_24h_c - t
            pts += add("swing", swing >= th["swing_min_c"],
                       f"24h high {c_to_f(s.high_24h_c):.0f}°F -> now {c_to_f(t):.0f}°F ({dc_to_f(swing):.0f}°F swing)", self.points["swing"])

        if s.temp_3h_ago_c is not None and t is not None:
            drop = s.temp_3h_ago_c - t
            pts += add("cooling", drop >= th["cool_rate_c_3h"], f"{dc_to_f(drop):.0f}°F drop over 3h", self.points["cooling"])

        if s.solar_wm2 is not None:
            pts += add("no_sun", s.solar_wm2 < th["solar_max_wm2"], f"solar {s.solar_wm2:.0f} W/m2", self.points["no_sun"])

        if s.precip_3h_mm is not None and s.precip_3h_mm > th["rain_mm"]:
            reasons.append({"trigger": "rain_veto", "hit": True,
                            "detail": f"{s.precip_3h_mm / 25.4:.2f} in of rain in last 3h (different problem)", "points": 0})
            pts = min(pts, self.rain_cap)

        return min(pts, 100.0), reasons
