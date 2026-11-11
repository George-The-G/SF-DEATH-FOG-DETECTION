"""Archive an image on a schedule: for example the GOES-West fog / low-cloud view of the Bay Area.

It produces no numbers, just timestamped files you can line up with alerts and field reports.
Find an image link at https://www.goes.noaa.gov/wfo.php?wfo=mtr (the "Download images" links),
paste it as `url` in config.toml, and enable the source. Check that site's terms before heavy use.
"""
import pathlib

from ..registry import Source, register_source


@register_source("snapshot")
class Snapshot(Source):
    def fetch(self, http, now):
        url = self.cfg.get("url", "").strip()
        if not url:
            raise ValueError(f"source '{self.name}': set `url` in config.toml first")
        folder = pathlib.Path(self.cfg.get("dir", f"data/snapshots/{self.name}"))
        folder.mkdir(parents=True, exist_ok=True)
        ext = self.cfg.get("ext", "jpg")
        (folder / f"{now:%Y%m%dT%H%M%SZ}.{ext}").write_bytes(http.get_bytes(url))
        return []
