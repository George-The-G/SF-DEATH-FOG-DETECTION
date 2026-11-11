import os
import pathlib
import tomllib

# environment variable -> (config section, key). Lets GitHub secrets/variables override the committed config.
ENV = [("DEATHFOG_USER_AGENT", "general", "user_agent"),
       ("DEATHFOG_REPORT_URL", "general", "report_url"),
       ("DEATHFOG_DATA_DIR", "general", "data_dir"),
       ("DEATHFOG_SITE_DIR", "site", "dir"),
       ("DEATHFOG_FORM_CSV_URL", "reports", "form_csv_url"),
       ("DEATHFOG_NTFY_TOPIC", "notify", "ntfy_topic")]


def apply_env(cfg, environ=None):
    environ = os.environ if environ is None else environ
    for var, section, key in ENV:
        v = environ.get(var, "").strip()
        if v:
            cfg.setdefault(section, {})[key] = v
    if cfg.get("notify", {}).get("ntfy_topic") and cfg["notify"].get("type", "stdout") == "stdout":
        cfg["notify"]["type"] = "ntfy"
    return cfg


def load_config(path="config.toml", environ=None):
    p = pathlib.Path(path)
    with p.open("rb") as f:
        cfg = tomllib.load(f)
    for section in ("general", "zones", "sources", "state", "detector", "notify", "reports", "baseline", "site"):
        cfg.setdefault(section, {})
    if not cfg["zones"]:
        raise ValueError("config needs at least one [zones.<name>] table")
    for name, z in cfg["zones"].items():
        z.setdefault("label", name)
        if "lat" not in z or "lon" not in z:
            raise ValueError(f"zone '{name}' needs lat and lon")
    apply_env(cfg, environ)
    cfg["_base_dir"] = str(p.resolve().parent)
    return cfg


def db_path(cfg):
    g = cfg["general"]
    return g.get("db_path") or os.path.join(g.get("data_dir", "data"), "deathfog.db")


def baseline_path(cfg):
    g = cfg["general"]
    return g.get("baseline_path") or os.path.join(g.get("data_dir", "data"), "baseline.json")
