"""Street layer: OpenStreetMap streets around each zone -> per-street risk from the community's rules.

Two separate steps so tuning never needs a re-download:
  * fetch_zone / run_streets  download streets + street trees once from the free Overpass API (streets) and San Francisco's street-tree list (trees), and cache the raw facts
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

SF_TREES_URL = "https://data.sf.gov/resource/tkzw-k3nq.json"     # DataSF "Street Tree List": ~200k trees, free, no key
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
HIGHWAYS = "residential|tertiary|secondary|primary|unclassified|living_street|trunk"
TREE_RADIUS_M = 15.0
COUNT_METHOD = 2           # bump when tree counting changes: cached zones are then recounted automatically
TREE_WINDOW_M = 60.0       # tree density is averaged over +/- this far along the street

DEFAULTS = {
    "radius_m": 700,               # map covers a square about 2 x radius wide around each zone centre
    "w_orient": 0.50,              # east-west streets
    "w_trees": 0.30,               # tree cover
    "w_parking": 0.20,             # parked cars
    "tree_full_per_100m": 10.0,    # this many trees per 100 m (both sides) counts as full cover; SF averages about 8
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


def osm_trees(payload):
    return [(e["lat"], e["lon"]) for e in payload.get("elements", []) if e.get("type") == "node" and "lat" in e]


def count_trees(pts_list, trees_xy, lat0, lon0):
    """For each way (a list of (lat, lon) points) -> trees per stretch. Every tree is assigned once, to the nearest
    stretch of street within TREE_RADIUS_M, so a tree is never double counted at corners or across a street."""
    cell = 25.0
    segs, grid = [], {}
    for wi, pts in enumerate(pts_list):
        xy = [_xy(a, b, lat0, lon0) for a, b in pts]
        for si, ((ax, ay), (bx, by)) in enumerate(zip(xy, xy[1:])):
            k = len(segs)
            segs.append((wi, si, ax, ay, bx, by))
            for cx in range(int((min(ax, bx) - TREE_RADIUS_M) // cell), int((max(ax, bx) + TREE_RADIUS_M) // cell) + 1):
                for cy in range(int((min(ay, by) - TREE_RADIUS_M) // cell), int((max(ay, by) + TREE_RADIUS_M) // cell) + 1):
                    grid.setdefault((cx, cy), []).append(k)
    counts = [[0] * (len(p) - 1) for p in pts_list]
    for tx, ty in trees_xy:
        best, best_d = None, TREE_RADIUS_M
        for k in grid.get((int(tx // cell), int(ty // cell)), ()):
            wi, si, ax, ay, bx, by = segs[k]
            d = _seg_dist(tx, ty, ax, ay, bx, by)
            if d <= best_d:
                best, best_d = (wi, si), d
        if best:
            counts[best[0]][best[1]] += 1
    return counts


def parse_overpass(payload, lat0, lon0, trees=None):
    """Overpass JSON -> list of ways with trees counted per stretch. `trees` is a list of (lat, lon); when omitted,
    tree nodes inside the payload are used. Trees are not kept, only the counts."""
    trees_xy = [_xy(a, b, lat0, lon0) for a, b in (osm_trees(payload) if trees is None else trees)]
    raw = [e for e in payload.get("elements", []) if e.get("type") == "way" and len(e.get("geometry") or []) >= 2]
    pts_list = [[(g["lat"], g["lon"]) for g in e["geometry"]] for e in raw]
    counts = count_trees(pts_list, trees_xy, lat0, lon0)
    ways = []
    for e, pts, c in zip(raw, pts_list, counts):
        tags = e.get("tags", {})
        ways.append({"name": tags.get("name", ""), "hw": tags.get("highway", ""),
                     "pts": [[round(a, 5), round(b, 5)] for a, b in pts], "trees": c,
                     "parking": _parking(tags), "surface": tags.get("surface", "")})
    return ways


def _box(lat, lon, radius_m, pad_m=0):
    """South, west, north, east of a square around a point (degrees)."""
    r = radius_m + pad_m
    dlat, dlon = r / 110540.0, r / (111320.0 * math.cos(math.radians(lat)))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def query(lat, lon, radius_m):
    s, w, n, e = _box(lat, lon, radius_m)
    return f'[out:json][timeout:40];way["highway"~"^({HIGHWAYS})$"]({s:.5f},{w:.5f},{n:.5f},{e:.5f});out geom;'


def tree_query(lat, lon, radius_m):
    s, w, n, e = _box(lat, lon, radius_m, TREE_RADIUS_M)
    return f'[out:json][timeout:40];node["natural"="tree"]({s:.5f},{w:.5f},{n:.5f},{e:.5f});out;'


def _overpass(http, q, urls=None):
    last = None
    for url in urls or OVERPASS_URLS:
        try:
            http.timeout = 45
            return http.get_json(url, {"data": q})
        except (HttpError, ValueError) as e:       # ValueError covers a non-JSON "server busy" page
            last = e
        except OSError as e:                       # read timeouts surface as socket errors
            last = HttpError(0, f"{url}: {e}")
    raise last or HttpError(0, "no Overpass server configured")


def sf_tree_where(lat, lon, radius_m):
    """DataSF stores latitude/longitude as text, so a coarse box is filtered with text comparison (it sorts lat/lon the way
    we need; longitude must be given most-negative first). Exact distances are checked locally afterwards."""
    s, w, n, e = _box(lat, lon, radius_m, TREE_RADIUS_M + 15)
    return (f"latitude between '{math.floor(s * 1e4) / 1e4:.4f}' and '{math.ceil(n * 1e4) / 1e4:.4f}' "
            f"and longitude between '{math.floor(w * 1e4) / 1e4:.4f}' and '{math.ceil(e * 1e4) / 1e4:.4f}'")


def fetch_trees(http, lat, lon, radius_m):
    """Street trees near a point: San Francisco's official tree list first, OpenStreetMap trees as a fallback.
    Returns (list of (lat, lon), source name, why); source is 'none' when both are unavailable."""
    why = []
    try:
        rows = http.get_json(SF_TREES_URL, {"$select": "latitude,longitude", "$limit": 50000,
                                            "$where": sf_tree_where(lat, lon, radius_m)})
        return [(float(r["latitude"]), float(r["longitude"])) for r in rows if r.get("latitude") and r.get("longitude")], \
            "sf_street_tree_list", ""
    except (HttpError, ValueError, KeyError, TypeError, OSError) as e:
        why.append(f"SF tree list: {e}")
    try:
        return osm_trees(_overpass(http, tree_query(lat, lon, radius_m))), "osm", "; ".join(why)
    except (HttpError, ValueError, OSError) as e:
        why.append(f"OSM trees: {e}")
    return [], "none", "; ".join(why)


def fetch_zone(http, lat, lon, radius_m, urls=None):
    """-> (ways with tree counts, tree source, why trees failed). Raises when no streets could be downloaded."""
    payload = _overpass(http, query(lat, lon, radius_m), urls)
    trees, source, why = fetch_trees(http, lat, lon, radius_m)
    ways = parse_overpass(payload, lat, lon, trees)
    if not ways:
        raise HttpError(0, "Overpass returned no streets")
    return ways, source, why


def _key(zcfg, radius_m):
    return f"{zcfg['lat']:.5f},{zcfg['lon']:.5f},{radius_m}"


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def is_current(data, name, zcfg, radius_m):
    zd = (data or {}).get("zones", {}).get(name, {})
    return zd.get("key") == _key(zcfg, radius_m) and zd.get("trees", "none") != "none" and zd.get("method") == COUNT_METHOD


def run_streets(cfg, http, now, path=None, force=False, sleep=2.0, max_seconds=150):
    """Fetch streets + trees for every zone that lacks a current copy. Failures are reported, never raised, and the
    next scheduled run simply tries again for whatever is still missing."""
    st = settings(cfg)
    path = path or streets_path(cfg)
    data = load(path) or {"zones": {}}
    notes, changed, started = [], False, time.time()
    for name, z in cfg["zones"].items():
        if not force and is_current(data, name, z, st["radius_m"]):
            continue
        prev = data["zones"].get(name)
        if (not force and prev and prev.get("key") == _key(z, st["radius_m"]) and (prev.get("trees", "none") == "none" or prev.get("method") != COUNT_METHOD)
                and time.time() - started <= max_seconds):
            # streets are already saved: only the trees are missing, so fetch just those and recount
            trees, source, why = fetch_trees(http, z["lat"], z["lon"], st["radius_m"])
            if source == "none":
                notes.append(f"{name}: still no tree data ({why})")
                continue
            lat0, lon0 = z["lat"], z["lon"]
            counts = count_trees([[tuple(p) for p in w["pts"]] for w in prev["ways"]], [_xy(a, b, lat0, lon0) for a, b in trees], lat0, lon0)
            for w, c in zip(prev["ways"], counts):
                w["trees"] = c
            prev["trees"], prev["method"], prev["fetched_utc"] = source, COUNT_METHOD, iso(now)
            changed = True
            continue
        if time.time() - started > max_seconds:
            notes.append(f"{name}: skipped (out of time), will retry next run")
            continue
        if changed and sleep:
            time.sleep(sleep)               # be polite to the free public servers
        try:
            ways, source, why = fetch_zone(http, z["lat"], z["lon"], st["radius_m"])
        except Exception as e:
            notes.append(f"{name}: {e}")
            continue
        data["zones"][name] = {"key": _key(z, st["radius_m"]), "center": [z["lat"], z["lon"]], "radius_m": st["radius_m"],
                               "trees": source, "method": COUNT_METHOD, "fetched_utc": iso(now), "ways": ways}
        if source == "none":
            notes.append(f"{name}: streets saved but no tree data ({why}); will retry trees next run")
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
        geo = []
        for (a, b), n in zip(zip(w["pts"], w["pts"][1:]), w["trees"]):
            lat0 = (a[0] + b[0]) / 2
            dx, dy = (b[1] - a[1]) * math.cos(math.radians(lat0)) * 111320.0, (b[0] - a[0]) * 110540.0
            geo.append((a, b, dx, dy, math.hypot(dx, dy), n))
        mids, acc = [], 0.0
        for g in geo:
            mids.append(acc + g[4] / 2)
            acc += g[4]
        for i, (a, b, dx, dy, length, n) in enumerate(geo):
            if length < 1:
                continue
            ew = abs(dx) / length                      # 1 = due east-west, 0 = due north-south
            # trees per 100 m, smoothed over about 120 m of the same street so short stretches are not noisy
            near = [j for j in range(len(geo)) if abs(mids[j] - mids[i]) <= TREE_WINDOW_M]
            span = sum(geo[j][4] for j in near)
            per100 = sum(geo[j][5] for j in near) / span * 100.0 if span else 0.0
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
