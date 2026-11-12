"""M1 part 2: OSM streets for the Potrero zone, skate route (20th & Kansas downhill to a bus stop), and street sample points."""
import csv
import json
import math
import time
from pathlib import Path

import numpy as np
import rasterio
import requests
from pyproj import Transformer

MIRRORS = ["https://overpass.kumi.systems/api/interpreter", "https://overpass-api.de/api/interpreter",
           "https://overpass.private.coffee/api/interpreter"]
ROAD = "residential|tertiary|secondary|primary|unclassified|living_street|service|tertiary_link|secondary_link|primary_link"


def fetch_osm(lat, lon, radius, cache):
    cache = Path(cache)
    if cache.exists():
        return json.load(open(cache))
    q = (f'[out:json][timeout:60];(way["highway"~"^({ROAD})$"](around:{radius},{lat},{lon});'
         f'node["highway"="bus_stop"](around:{radius + 300},{lat},{lon}););out body;>;out skel qt;')
    err = None
    for attempt in range(2):
        for url in MIRRORS:
            try:
                r = requests.post(url, data={"data": q}, timeout=75, headers={"User-Agent": "deathfog-v3-mvp/0.1 (github.com/George-The-G/SF-DEATH-FOG-DETECTION)", "Accept": "application/json"})
                if r.status_code == 200 and r.text.startswith("{"):
                    data = r.json()
                    json.dump(data, open(cache, "w"))
                    return data
                err = f"{url} -> {r.status_code}"
            except Exception as e:  # noqa
                err = f"{url} -> {e!r}"
        time.sleep(5)
    raise RuntimeError(f"Overpass failed: {err}")


class Zone:
    def __init__(self, osm, dem_path):
        self.t = Transformer.from_crs(4326, 32610, always_xy=True)
        self.nodes = {e["id"]: (e["lat"], e["lon"]) for e in osm["elements"] if e["type"] == "node"}
        self.bus = [(e["lat"], e["lon"], e.get("tags", {}).get("name", "")) for e in osm["elements"]
                    if e["type"] == "node" and e.get("tags", {}).get("highway") == "bus_stop"]
        self.ways = [e for e in osm["elements"] if e["type"] == "way" and all(n in self.nodes for n in e["nodes"])]
        self.dem = rasterio.open(dem_path)
        self.adj = {}
        for w in self.ways:
            for a, b in zip(w["nodes"], w["nodes"][1:]):
                self.adj.setdefault(a, set()).add(b)
                self.adj.setdefault(b, set()).add(a)
        self._z = {}

    def xy(self, nid):
        lat, lon = self.nodes[nid]
        return self.t.transform(lon, lat)

    def z_ll(self, lat, lon):
        return float(next(self.dem.sample([(lon, lat)]))[0])

    def z(self, nid):
        if nid not in self._z:
            self._z[nid] = self.z_ll(*self.nodes[nid])
        return self._z[nid]

    def dist(self, a, b):
        (x1, y1), (x2, y2) = self.xy(a), self.xy(b)
        return math.hypot(x1 - x2, y1 - y2)

    def start_node(self, lat, lon):
        """Intersection of 20th Street and Kansas Street if both are in the data, else the nearest node."""
        names = {}
        for w in self.ways:
            nm = (w.get("tags", {}).get("name") or "").lower()
            for n in w["nodes"]:
                names.setdefault(n, set()).add(nm)
        hits = [n for n, s in names.items() if any(x.startswith("20th") for x in s) and any(x.startswith("kansas") for x in s)]
        if hits:
            return hits[0], "20th & Kansas intersection found in OSM"
        x, y = self.t.transform(lon, lat)
        best = min(self.nodes, key=lambda n: math.hypot(self.xy(n)[0] - x, self.xy(n)[1] - y))
        return best, "20th & Kansas not found by name; used nearest node"

    def route(self, start, max_m=2500):
        """Steepest descent along streets until no lower neighbour, then end at the nearest bus stop if one is within 150 m."""
        path, seen, total = [start], {start}, 0.0
        while total < max_m:
            cur = path[-1]
            opts = [n for n in self.adj.get(cur, ()) if n not in seen]
            if not opts:
                break
            nxt = min(opts, key=lambda n: self.z(n))
            if self.z(nxt) >= self.z(cur) - 0.05 and len(path) > 1:
                # allow flat or slightly rising runs of one node to cross intersections
                lower = [n for n in opts if self.z(n) < self.z(cur)]
                if not lower:
                    break
                nxt = min(lower, key=lambda n: self.z(n))
            total += self.dist(cur, nxt)
            path.append(nxt)
            seen.add(nxt)
        x, y = self.xy(path[-1])
        stops = sorted(self.bus, key=lambda b: math.hypot(self.t.transform(b[1], b[0])[0] - x, self.t.transform(b[1], b[0])[1] - y))
        stop = None
        if stops:
            sx, sy = self.t.transform(stops[0][1], stops[0][0])
            if math.hypot(sx - x, sy - y) < 150:
                stop = stops[0]
        return path, stop, total

    def points(self, route_set, step=10.0, side_m=5.0):
        rows, pid = [], 0
        for w in self.ways:
            tags = w.get("tags", {})
            nodes = w["nodes"]
            for si, (a, b) in enumerate(zip(nodes, nodes[1:])):
                (x1, y1), (x2, y2) = self.xy(a), self.xy(b)
                L = math.hypot(x2 - x1, y2 - y1)
                if L < 1:
                    continue
                dx, dy = (x2 - x1) / L, (y2 - y1) / L
                bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 180  # undirected 0-180, 90 = east-west
                slope = (self.z(b) - self.z(a)) / L * 100
                n = max(1, int(L // step))
                for k in range(n):
                    f = (k + 0.5) / n
                    px, py = x1 + dx * L * f, y1 + dy * L * f
                    lon, lat = self.t.transform(px, py, direction="INVERSE")
                    z = self.z_ll(lat, lon)
                    for side, sgn in (("left", 1), ("right", -1)):
                        sx, sy = px - dy * side_m * sgn, py + dx * side_m * sgn
                        slon, slat = self.t.transform(sx, sy, direction="INVERSE")
                        rows.append({"id": pid, "way": w["id"], "seg": si, "side": side, "x": round(sx, 1), "y": round(sy, 1),
                                     "lat": round(slat, 6), "lon": round(slon, 6), "z": round(z, 1), "street": tags.get("name", ""),
                                     "bearing": round(bearing, 1), "slope_pct": round(slope, 1), "surface": tags.get("surface", ""),
                                     "on_route": int((a, b) in route_set or (b, a) in route_set)})
                        pid += 1
        return rows


def build(cfg, raw_dir, out_dir):
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    zdir = out_dir / "zone"
    zdir.mkdir(parents=True, exist_ok=True)
    z = cfg["zone"]["potrero"]
    lat, lon = z["start"]
    osm = fetch_osm(lat, lon, z["radius_m"] + 300, raw_dir / "osm_potrero.json")
    zone = Zone(osm, raw_dir / "dem_n38w123.tif")
    start, note = zone.start_node(lat, lon)
    from .route import downhill_routes
    cands = downhill_routes(zone, start, top=8, min_drop_m=15)
    if cands:
        md = max(c['drop_m'] for c in cands)
        pick = min((c for c in cands if c['drop_m'] >= md - 3 and c['climb_m'] <= 8), key=lambda c: c['length_m'], default=cands[0])
        path, stop, total = pick['path'], pick['stop'], pick['length_m']
    else:
        path, stop, total = zone.route(start)
    route_set = set(zip(path, path[1:]))
    rows = zone.points(route_set)
    with open(zdir / "potrero_points.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    coords = [[zone.nodes[n][1], zone.nodes[n][0], zone.z(n)] for n in path]
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "route", "length_m": round(total), "note": note},
         "geometry": {"type": "LineString", "coordinates": coords}}]}
    if stop:
        gj["features"].append({"type": "Feature", "properties": {"kind": "bus_stop", "name": stop[2]},
                               "geometry": {"type": "Point", "coordinates": [stop[1], stop[0]]}})
    json.dump(gj, open(zdir / "route.geojson", "w"))
    names = []
    for n in path:
        for w_ in zone.ways:
            if n in w_["nodes"]:
                nm = w_.get("tags", {}).get("name", "")
                if nm and (not names or names[-1] != nm):
                    names.append(nm)
                break
    summary = {"start_note": note, "start_lat_lon": zone.nodes[start], "route_length_m": round(total),
               "start_z": round(zone.z(start), 1), "end_z": round(zone.z(path[-1]), 1), "route_streets": names,
               "ends_at_bus_stop": bool(stop), "bus_stop": stop, "ways": len(zone.ways), "points": len(rows),
               "bus_stops_in_area": len(zone.bus), "alternatives": [{k: v for k, v in c.items() if k != "path"} for c in cands]}
    json.dump(summary, open(zdir / "zone_summary.json", "w"), indent=1)
    plot(zone, path, stop, start, zdir / "route.png")
    return summary


def plot(zone, path, stop, start, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 8))
    for w in zone.ways:
        xy = np.array([zone.xy(n) for n in w["nodes"]])
        ax.plot(xy[:, 0], xy[:, 1], color="#aaa", lw=0.8)
    r = np.array([zone.xy(n) for n in path])
    ax.plot(r[:, 0], r[:, 1], color="red", lw=3, label="route")
    sx, sy = zone.xy(start)
    ax.plot(sx, sy, "go", ms=10, label="20th & Kansas")
    for b in zone.bus:
        bx, by = zone.t.transform(b[1], b[0])
        ax.plot(bx, by, "b^", ms=6)
    if stop:
        bx, by = zone.t.transform(stop[1], stop[0])
        ax.plot(bx, by, "b^", ms=14, label="bus stop at bottom")
    ax.set_aspect("equal")
    ax.legend()
    ax.set_title("Potrero Hill skate route (north up)")
    fig.savefig(out, dpi=100)





