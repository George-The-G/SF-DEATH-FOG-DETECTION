"""Source hooks.

A data source is a class with a `fetch()` that returns normalised `Reading`s. Register it with
`@register_source("type_name")`, then enable it in config.toml with `type = "type_name"`.
Drop a .py file in the plugins/ folder and it is loaded automatically: no core code changes.
"""
import importlib.util
import pathlib

SOURCES = {}


def register_source(name):
    def deco(cls):
        cls.type_name = name
        SOURCES[name] = cls
        return cls
    return deco


class Source:
    type_name = "base"
    # Free-tier ceiling in this source's own cost units (used only by the `estimate` command).
    free_daily_limit = None

    def __init__(self, name, cfg, zones):
        self.name = name      # config key, e.g. "openmeteo"
        self.cfg = cfg        # that source's config table
        self.zones = zones    # {zone_name: zone_cfg} for every zone

    def cost(self):
        """Cost of ONE fetch, in whatever unit the provider meters (calls, points...). 0 = free/local."""
        return 1.0

    def fetch(self, http, now):
        """Return a list[Reading]. Raise on failure; the collector logs it and moves on."""
        raise NotImplementedError


def load_plugins(directory):
    d = pathlib.Path(directory)
    loaded = []
    if not d.is_dir():
        return loaded
    for p in sorted(d.glob("*.py")):
        if p.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"deathfog_plugin_{p.stem}", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        loaded.append(p.name)
    return loaded
