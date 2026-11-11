"""PurpleAir neighbourhood sensors: the only metered source here (points-based billing).

New API keys get 1,000,000 free points; more cost about $10 per million. Cost per request is
roughly 5 base points + 3 points per sensor for temperature (2) + humidity (1); those numbers come
from PurpleAir's published pricing examples, so confirm them in your PurpleAir dashboard and edit
base_points / point_cost_per_sensor in config if they differ.

Sensors run warm and dry because of their own electronics; measure the offset against an NWS
station or your own thermometer and set temp_offset_c / rh_offset_pct in config.

Set zones' `purpleair_sensors = [sensor_index, ...]` in config.toml; the key comes from the
environment variable named by `api_key_env`.
"""
import os
from datetime import datetime, timezone

from ..model import Reading
from ..registry import Source, register_source
from ..util import dew_point_c, f_to_c, iso

URL = "https://api.purpleair.com/v1/sensors"


@register_source("purpleair")
class PurpleAir(Source):
    free_daily_limit = None  # points are an account balance, not a daily cap

    def sensor_map(self):
        m = {}
        for zname, z in self.zones.items():
            for idx in z.get("purpleair_sensors", []):
                m[int(idx)] = zname
        return m

    def cost(self):
        n = len(self.sensor_map())
        if n == 0:
            return 0.0
        return float(self.cfg.get("base_points", 5) + self.cfg.get("point_cost_per_sensor", 3) * n)

    def fetch(self, http, now):
        smap = self.sensor_map()
        if not smap:
            return []
        key = os.environ.get(self.cfg.get("api_key_env", "PURPLEAIR_API_KEY"))
        if not key:
            raise RuntimeError("PurpleAir API key not set in environment")
        data = http.get_json(URL, {
            "fields": "temperature,humidity",
            "show_only": ",".join(str(i) for i in smap),
            "max_age": self.cfg.get("max_age_s", 900),
        }, headers={"X-API-Key": key})
        fields = data["fields"]
        ts = datetime.fromtimestamp(data["data_time_stamp"], tz=timezone.utc)
        t_off = self.cfg.get("temp_offset_c", 0.0)
        h_off = self.cfg.get("rh_offset_pct", 0.0)
        out = []
        for row in data["data"]:
            rec = dict(zip(fields, row))
            idx = int(rec["sensor_index"])
            if rec.get("temperature") is None or rec.get("humidity") is None:
                continue
            t = f_to_c(rec["temperature"]) + t_off          # API reports Fahrenheit
            rh = min(100.0, max(0.0, rec["humidity"] + h_off))
            out.append(Reading(source=self.name, kind="obs", zone=smap[idx], sub=str(idx),
                               valid_utc=iso(ts), temp_c=t, rh_pct=rh, dewpoint_c=dew_point_c(t, rh)))
        return out
