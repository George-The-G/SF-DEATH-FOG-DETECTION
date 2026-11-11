"""Example plugin: read your own sensor's CSV log (a $30 temp/humidity logger at Back Side 9, say).

This whole file is all it takes to add a data source. Enable it in config.toml:

    [sources.my_logger]
    type = "local_csv"
    enabled = true
    interval_min = 10
    path = "data/backside9_logger.csv"   # columns: timestamp,temp_c,rh_pct  (ISO timestamps, UTC)
    zone = "back_side_9"
"""
import csv
import pathlib

from deathfog.model import Reading
from deathfog.registry import Source, register_source
from deathfog.util import dew_point_c, iso, parse_iso


@register_source("local_csv")
class LocalCsv(Source):
    def cost(self):
        return 0.0  # local file: nothing to meter

    def fetch(self, http, now):
        p = pathlib.Path(self.cfg["path"])
        if not p.exists():
            return []
        out = []
        with p.open(newline="") as f:
            for row in csv.DictReader(f):
                t, rh = float(row["temp_c"]), float(row["rh_pct"])
                out.append(Reading(source=self.name, kind="obs", zone=self.cfg["zone"],
                                   sub=self.cfg.get("sub", "logger"), valid_utc=iso(parse_iso(row["timestamp"])),
                                   temp_c=t, rh_pct=rh, dewpoint_c=dew_point_c(t, rh)))
        return out
