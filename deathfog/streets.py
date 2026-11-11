"""Street layer: OpenStreetMap streets around each zone -> per-street risk from the community's rules.

Two separate steps so tuning never needs a re-download:
  * fetch_zone / run_streets  download streets + street trees once from the free Overpass API and cache the raw facts
                              (geometry, trees near each stretch, parking tags, surface) in streets.json;
  * score_ways / top_streets  pure functions that turn those facts into a 0-1 risk using the weights in [streets].

The rules encode what skaters report: east-west blocks are hit harder than north-south ones, trees and parked cars
make it worse, porous or sun-baked surfaces are spared. Nothing here sees building shade or actual road wetness, and
the weights have not been checked against field reports yet.
"""
import json
import math
import os
import time

from .http import HttpError
from .util import iso

OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
HIGHWAYS = "residential|tertiary|secondary|primary|unclassified|living_street|trunk"
TREE_RADIUS_M = 15.0

DEFAULTS = {
    "radius_m": 700,               # map covers a square about 2 x radius wide around each zone centre
    "w_orient": 0.50,              # east-west streets
    "w_trees": 0.30,               # tree cover
    "w_parking": 0.20,             # parked cars
    "tree_full_per_100m": 6.0,     # this many trees per 100 m counts as full cover
    "parking_unknown": 0.5,        # OSM often lacks parking tags: treat unknown as half
    "porous_factor": 0.6,          # multiplier for porous / unpaved surfaces
    "porous_surfaces": ["concrete", "paving_stones", "sett", "cobblestone", "unhewn_cobblestone", "gravel",
                        "compacted", "dirt", "unpaved", "ground"],
    "hi": 0.80, "mid": 0.65, "low": 0.50,     # colour bands on the map
}


def settings(cfg):
    return {**DEFAULTS, **cfg.get("streets", {})}


def streets_path(cfg):
    g = cfg["general"]
    return g.get("streets_path") or os.path.join(g.get("data_dir", "data"), "streets.json")


# ---------------------------------------------------------------- geometry helpers (metres around a centre)
def _xy(lat, lon, lat0, lon0):
    return ((lon - lon0) * math.cos(math.radians(lat0)) * 111320.0, (lat - lat0) * 110540.0)


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _parking(tags):
    vals = [v for k, v in tags.items() if k.startswith("parking:lane") or k.startswith("parking:both")
            or k.startswith("parking:left") or k.startswith("parking:right")]
    if not vals:
        return "unknown"
    return "yes" if any(v not in ("no", "no_parking", "no_stopping", "no_standing", "none") for v in vals) else "no"


def parse_overpass(payload, lat0, lon0):
    """Overpass JSON -> list of ways with trees counted per stretch. Trees are not kept, only the counts."""
    trees = [_xy(e["lat"], e["lon"], lat0, lon0) for e in payload.get("elements", [])
             if e.get("type") == "node" and "lat" in e]
    cell = 25.0
    grid = {}
    for x, y in trees:
        grid.setdefault((int(x // cell), int(y // cell)), []).append((x, y))
    ways = []
    for e in payload.get("elements", []):
        if e.get("type") != "way" or len(e.get("geometry") or []) < 2:
            continue
        tags = e.get("tags", {})
        pts = [(g["lat"], g["lon"]) for g in e["geometry"]]
        xy = [_xy(a, b, lat0, lon0) for a, b in pts]
        counts = []
        for (ax, ay), (bx, by) in zip(xy, xy[1:]):
            n = 0
            for cx in range(int((min(ax, bx) - TREE_RADIUS_M) // cell), int((max(ax, bx) + TREE_RADIUS_M) // cell) + 1):
                for cy in range(int((min(ay, by) - TREE_RADIUS_M) // cell), int((max(ay, by) + TREE_RADIUS_M) // cell) + 1):
                    n += sum(1 for tx, ty in grid.get((cx, cy), ()) if _seg_dist(tx, ty, ax, ay, bx, by) <= TREE_RADIUS_M)
            counts.append(n)
        ways.append({"name": tags.get("name", ""), "hw": tags.get("highway", ""),
                     "pts": [[round(a, 5), round(b, 5)] for a, b in pts], "trees": counts,
                     "parking": _parking(tags), "surface": tags.get("surface", "")})
    return ways


def query(lat, lon, radius_m):
    return (f'[out:json][timeout:60];way["highway"~"^({HIGHWAYS})$"](around:{radius_m},{lat},{lon});out geom;'
            f'node["natural"="tree"](around:{radius_m + int(TREE_RADIUS_M)},{lat},{lon});out;')


def fetch_zone(http, lat, lon, radius_m, urls=None):
    last = None
    for url in urls or OVERPASS_URLS:
        try:
            http.timeout = max(getattr(http, "timeout", 20), 90)
            payload = http.get_json(url, {"data": query(lat, lon, radius_m)})
            ways = parse_overpass(payload, lat, lon)
            if ways:
                return ways
            last = HttpError(0, f"{url} returned no streets")
        except (HttpError, ValueError) as e:       # ValueError covers a non-JSON "server busy" page
            last = e
    raise last or HttpError(0, "no Overpass server configured")


def _key(zcfg, radius_m):
    return f"{zcfg['lat']:.5f},{zcfg['lon']:.5f},{radius_m}"


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def run_streets(cfg, http, now, path=None, force=False, sleep=2.0):
    """Fetch streets for every zone that lacks a current copy. Failures are reported, never raised."""
    st = settings(cfg)
    path = path or streets_path(cfg)
    data = load(path) or {"zones": {}}
    notes, changed = [], False
    for i, (name, z) in enumerate(cfg["zones"].items()):
        key = _key(z, st["radius_m"])
        if not force and data["zones"].get(name, {}).get("key") == key:
            continue
        if changed and sleep:
            time.sleep(sleep)               # be polite to the free public servers
        try:
            ways = fetch_zone(http, z["lat"], z["lon"], st["radius_m"])
        except Exception as e:
            notes.append(f"{name}: {e}")
            continue
        data["zones"][name] = {"key": key, "center": [z["lat"], z["lon"]], "radius_m": st["radius_m"],
                               "fetched_utc": iso(now), "ways": ways}
        changed = True
    if changed:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
    return {"zones": len(data["zones"]), "fetched": changed, "notes": notes}


# ---------------------------------------------------------------- scoring (pure)
def score_ways(ways, st, susceptibility=1.0):
    """One record per stretch of street: endpoints, length, east-west-ness, trees per 100 m and a 0-1 risk
    (scaled by the zone's susceptibility, the same factor the detector uses)."""
    total_w = st["w_orient"] + st["w_trees"] + st["w_parking"]
    out = []
    for w in ways:
        porous = st["porous_factor"] if w.get("surface") in st["porous_surfaces"] else 1.0
        park = {"yes": 1.0, "no": 0.0}.get(w.get("parking"), st["parking_unknown"])
        for (a, b), n in zip(zip(w["pts"], w["pts"][1:]), w["trees"]):
            lat0 = (a[0] + b[0]) / 2
            dx, dy = (b[1] - a[1]) * math.cos(math.radians(lat0)) * 111320.0, (b[0] - a[0]) * 110540.0
            length = math.hypot(dx, dy)
            if length < 1:
                continue
            ew = abs(dx) / length                      # 1 = due east-west, 0 = due north-south
            per100 = n / length * 100.0
            tree = min(1.0, per100 / st["tree_full_per_100m"])
            risk = (st["w_orient"] * ew + st["w_trees"] * tree + st["w_parking"] * park) / total_w * porous * susceptibility
            out.append({"name": w["name"], "a": a, "b": b, "len": length, "ew": ew, "trees100": per100,
                        "parking": w.get("parking"), "porous": porous < 1.0, "risk": risk})
    return out


def band(risk, st):
    return "hi" if risk >= st["hi"] else "mid" if risk >= st["mid"] else "low" if risk >= st["low"] else "none"


def top_streets(segs, n=5, min_len=80.0):
    """Named streets ranked by length-weighted risk, each with a plain-English reason."""
    by = {}
    for s in segs:
        if s["name"]:
            by.setdefault(s["name"], []).append(s)
    ranked = []
    for name, ss in by.items():
        length = sum(s["len"] for s in ss)
        if length < min_len:
            continue
        mean = lambda key: sum(s[key] * s["len"] for s in ss) / length
        ew, trees = mean("ew"), mean("trees100")
        why = ["runs east-west" if ew >= 0.75 else "runs mostly north-south" if ew <= 0.35 else "runs diagonally"]
        if trees >= 2:
            why.append(f"about {trees:.0f} street trees per 100 m")
        if any(s["parking"] == "yes" for s in ss):
            why.append("parked cars along the curb")
        ranked.append({"name": name, "risk": mean("risk"), "len": length, "ew": ew, "why": why})
    ranked.sort(key=lambda r: -r["risk"])
    return ranked[:n]
