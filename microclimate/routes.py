"""Multi-route support: routes.toml -> resolved street routes -> combined street-point table -> the rest of the pipeline.

`python -m microclimate routes` runs: geometry (resolve routes, zone points) -> street -> sun -> replay -> viewer data -> validate,
skipping steps whose outputs are newer than their inputs (`--force` reruns everything, `--from STEP` reruns from a step on).

Outputs (deathfog-data/grids/zone/): points.csv (all zones; the Potrero rows are byte-identical to potrero_points.csv plus two
extra columns zone, route_mask), zones.json (row ranges + sun-window per zone), routes_resolved.json, routes/<id>.png overlays.
site/v3/data/: routes.json (route geometry for the viewer), points.bin/points.json (make_points.py).
"""
import csv
import hashlib
import heapq
import io
import json
import math
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RIDE = {"residential", "tertiary", "secondary", "primary", "unclassified", "living_street", "tertiary_link", "secondary_link",
        "primary_link", "residential_link", "trunk", "trunk_link"}
SUFFIXES = {"avenue", "ave", "street", "st", "drive", "dr", "boulevard", "blvd", "way", "court", "ct", "road", "rd", "terrace", "ter",
            "lane", "ln", "place", "pl", "highway", "hwy", "alley", "circle", "cir", "walk", "path"}
UA = {"User-Agent": "deathfog-v3-mvp/0.1 (github.com/George-The-G/SF-DEATH-FOG-DETECTION)", "Accept": "application/json"}
MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.private.coffee/api/interpreter"]


# ------------------------------------------------------------------ config
def load_toml(path=None):
    path = Path(path or ROOT / "routes.toml")
    with open(path, "rb") as f:
        rt = tomllib.load(f)
    zones = rt.get("zone", {})
    routes = rt.get("route", [])
    ids = [r["id"] for r in routes]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate route ids in routes.toml")
    for r in routes:
        for k in ("id", "name", "zone", "color"):
            if k not in r:
                raise ValueError(f"route {r.get('id')}: missing '{k}'")
        if r["zone"] not in zones:
            raise ValueError(f"route {r['id']}: unknown zone '{r['zone']}'")
        if not (r.get("auto") or r.get("waypoints") or (r.get("start") and r.get("end"))):
            raise ValueError(f"route {r['id']}: give waypoints, or start and end, or auto = \"downhill\"")
    if len(routes) > 16:
        raise ValueError("at most 16 routes (route_mask is a 16-bit field)")
    return {"zones": zones, "routes": routes}


def waypoint_list(route):
    if route.get("waypoints"):
        return [str(w) for w in route["waypoints"]]
    return [str(route["start"])] + [str(v) for v in route.get("via", [])] + [str(route["end"])]


# ------------------------------------------------------------------ names and intersections
def name_key(s):
    """'9th Ave' / '9th Avenue' -> '9th'; 'Kansas Street' -> 'kansas'; 'Laguna Honda Blvd' -> 'laguna honda'."""
    t = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()
    if len(t) > 1 and t[-1] in SUFFIXES:
        t = t[:-1]
    return " ".join(t)


def split_intersection(text):
    parts = re.split(r"\s*(?:&|\band\b|/|@)\s*", text.strip(), flags=re.I)
    return [p for p in parts if p] if len(parts) == 2 else None


_LL = re.compile(r"^\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*$")


def parse_latlon(text):
    m = _LL.match(text)
    return (float(m.group(1)), float(m.group(2))) if m else None


class StreetGraph:
    """OSM streets of one area: wraps zone.Zone (nodes, ways, adjacency, DEM heights) with name lookups and routing."""

    def __init__(self, osm, dem_path, include=RIDE):
        from .zone import Zone
        els = [e for e in osm["elements"] if e["type"] == "node" or
               (e["type"] == "way" and e.get("tags", {}).get("highway") in include)]
        nodes = [e for e in els if e["type"] == "node"]
        ways = sorted((e for e in els if e["type"] == "way"), key=lambda e: e["id"])
        self.Z = Zone({"elements": nodes + ways}, dem_path)
        Z = self.Z
        self.nodes = Z.nodes
        self.xyd = {n: Z.xy(n) for n in Z.adj}
        self.keys = {}
        self.edge_name = {}
        for w in Z.ways:
            nm = w.get("tags", {}).get("name", "")
            k = name_key(nm)
            for n in w["nodes"]:
                if k:
                    self.keys.setdefault(n, set()).add(k)
            for a, b in zip(w["nodes"], w["nodes"][1:]):
                self.edge_name.setdefault((a, b), nm)
                self.edge_name.setdefault((b, a), nm)

    def d(self, a, b):
        (x1, y1), (x2, y2) = self.xyd[a], self.xyd[b]
        return math.hypot(x1 - x2, y1 - y2)

    def candidates(self, text):
        """Nodes for a waypoint: an intersection 'A & B' (all matching nodes) or 'lat,lon' (nearest graph node within 60 m)."""
        ll = parse_latlon(text)
        if ll:
            x, y = self.Z.t.transform(ll[1], ll[0])
            best = min(self.xyd, key=lambda n: math.hypot(self.xyd[n][0] - x, self.xyd[n][1] - y))
            if math.hypot(self.xyd[best][0] - x, self.xyd[best][1] - y) > 60:
                raise ValueError(f"waypoint {text}: no street within 60 m")
            return [best]
        parts = split_intersection(text)
        if not parts:
            raise ValueError(f"waypoint '{text}': use 'Street A & Street B' or 'lat,lon'")
        ka, kb = name_key(parts[0]), name_key(parts[1])
        hit = sorted(n for n, s in self.keys.items() if ka in s and kb in s)
        if not hit:
            near = sorted({k for s in self.keys.values() for k in s if ka in k or kb in k or k in (ka, kb)})[:12]
            raise ValueError(f"intersection '{text}' not found in the OSM streets (keys {ka!r}, {kb!r}); similar names: {near}")
        return hit

    def pick(self, cand_lists):
        """One node per waypoint: the first by proximity to the next waypoint, each later one nearest the previous pick."""
        chosen = []
        for i, c in enumerate(cand_lists):
            if len(c) == 1:
                chosen.append(c[0])
            elif i == 0 and len(cand_lists) > 1:
                nxt = cand_lists[1]
                chosen.append(min(c, key=lambda n: min(self.d(n, m) for m in nxt)))
            elif i > 0:
                chosen.append(min(c, key=lambda n: self.d(n, chosen[-1])))
            else:
                chosen.append(c[0])
        return chosen

    def shortest(self, a, b):
        dist, prev, heap = {a: 0.0}, {}, [(0.0, a)]
        while heap:
            c, u = heapq.heappop(heap)
            if u == b:
                break
            if c > dist.get(u, 1e18):
                continue
            for v in self.Z.adj.get(u, ()):
                nc = c + self.d(u, v)
                if nc < dist.get(v, 1e18):
                    dist[v], prev[v] = nc, u
                    heapq.heappush(heap, (nc, v))
        if b not in dist:
            raise ValueError("no street path between consecutive waypoints")
        p = [b]
        while p[-1] != a:
            p.append(prev[p[-1]])
        return p[::-1]

    def route(self, wps):
        nodes = self.pick([self.candidates(w) for w in wps])
        path = [nodes[0]]
        for a, b in zip(nodes, nodes[1:]):
            if a != b:
                path += self.shortest(a, b)[1:]
        return path, nodes

    def describe(self, path):
        Z = self.Z
        z = [Z.z(n) for n in path]
        length = sum(self.d(a, b) for a, b in zip(path, path[1:]))
        climb = sum(max(0.0, q - p) for p, q in zip(z, z[1:]))
        names, st = [], []
        for a, b in zip(path, path[1:]):
            st.append(self.edge_name.get((a, b), "") or "(unnamed)")
        order = []
        for nm in st:
            if not order or order[-1][0] != nm:
                order.append([nm, 0.0])
        i = 0
        for a, b in zip(path, path[1:]):
            if order[i][0] != (self.edge_name.get((a, b), "") or "(unnamed)"):
                i += 1
            order[i][1] += self.d(a, b)
        return {"length_m": round(length, 1), "drop_m": round(z[0] - z[-1], 1), "climb_m": round(climb, 1), "z": z,
                "streets": [[n, round(l)] for n, l in order], "edge_streets": st}


# ------------------------------------------------------------------ OSM data
def fetch_bbox(bbox, cache):
    import requests
    cache = Path(cache)
    if cache.exists():
        return json.load(open(cache, encoding="utf-8"))
    s, w, n, e = bbox
    q = f'[out:json][timeout:90];(way["highway"]({s},{w},{n},{e});node["highway"="bus_stop"]({s},{w},{n},{e}););out body;>;out skel qt;'
    err = None
    for attempt in range(2):
        for url in MIRRORS:
            try:
                r = requests.post(url, data={"data": q}, timeout=150, headers=UA)
                if r.status_code == 200 and r.text.startswith("{"):
                    cache.write_text(r.text, encoding="utf-8")
                    return r.json()
                err = f"{url} -> {r.status_code}"
            except Exception as ex:  # noqa
                err = f"{url} -> {ex!r}"
        time.sleep(5)
    raise RuntimeError(f"Overpass failed: {err}")


def zone_osm_file(zid):
    return f"osm_{zid}.json"


def load_zone_osm(zid, zcfg, raw_dir):
    if zcfg["builder"] == "disc":
        return json.load(open(Path(raw_dir) / "osm_potrero.json"))
    return fetch_bbox(zcfg["osm_bbox"], Path(raw_dir) / zone_osm_file(zid))


# ------------------------------------------------------------------ route resolution
def potrero_route(Z, grids_dir):
    """The legacy auto-downhill Potrero route as written by zone.build (route.geojson), mapped onto OSM nodes."""
    gj = json.load(open(Path(grids_dir) / "zone" / "route.geojson"))
    lookup = {(round(Z.nodes[n][0], 6), round(Z.nodes[n][1], 6)): n for n in Z.nodes}
    coords, stop = None, None
    for f in gj["features"]:
        if f["properties"]["kind"] == "route":
            coords = f["geometry"]["coordinates"]
        elif f["properties"]["kind"] == "bus_stop":
            stop = f["properties"].get("name", "")
    path = [lookup[(round(c[1], 6), round(c[0], 6))] for c in coords]
    return path, stop


def resolve_routes(cfg, rt, raw_dir, grids_dir):
    """-> (graphs by zone id, list of resolved route dicts in routes.toml order)."""
    dem = Path(raw_dir) / "dem_n38w123.tif"
    graphs, out = {}, []
    for zid, zc in rt["zones"].items():
        if any(r["zone"] == zid for r in rt["routes"]):
            osm = load_zone_osm(zid, zc, raw_dir)
            # Potrero keeps its original road set (incl. service) for points; routes there come from route.geojson
            graphs[zid] = StreetGraph(osm, dem, include=(RIDE | {"service"}) if zc["builder"] == "disc" else RIDE)
            graphs[zid].osm_name = zone_osm_file(zid) if zc["builder"] != "disc" else "osm_potrero.json"
    for i, r in enumerate(rt["routes"]):
        G = graphs[r["zone"]]
        if r.get("auto") == "downhill":
            path, stop = potrero_route(G, grids_dir)
            wps = [f"{G.nodes[path[0]][0]:.6f},{G.nodes[path[0]][1]:.6f}", stop or ""]
            nodes = [path[0], path[-1]]
        else:
            wps = waypoint_list(r)
            path, nodes = G.route(wps)
        d = G.describe(path)
        out.append({**{k: r[k] for k in ("id", "name", "zone", "color")}, "index": i, "waypoints": wps, "waypoint_nodes": nodes,
                    "path": path, "start_label": r.get("start_label") or wps[0], "end_label": r.get("end_label") or wps[-1],
                    "drawn": r.get("drawn"), **d})
    return graphs, out


def route_edges(path):
    return set(zip(path, path[1:]))


# ------------------------------------------------------------------ points
def densify(G, path, step=5.0):
    pts = []
    for a, b in zip(path, path[1:]):
        (x1, y1), (x2, y2) = G.xyd[a], G.xyd[b]
        n = max(1, int(math.hypot(x2 - x1, y2 - y1) // step))
        for k in range(n):
            pts.append((x1 + (x2 - x1) * k / n, y1 + (y2 - y1) * k / n))
    pts.append(G.xyd[path[-1]])
    return np.array(pts)


def build_points(rt, graphs, resolved, grids_dir):
    """Write grids/zone/points.csv, zones.json. Potrero rows are copied unchanged from potrero_points.csv."""
    from scipy.spatial import cKDTree
    from . import sun
    zdir = Path(grids_dir) / "zone"
    nR = len(rt["routes"])
    rows_all, zones, pid = [], [], 0
    base_fields = None
    for zid, zc in rt["zones"].items():
        zr = [r for r in resolved if r["zone"] == zid]
        if not zr:
            continue
        t0 = len(rows_all)
        if zc["builder"] == "disc":
            rd = csv.DictReader(open(zdir / "potrero_points.csv", newline=""))
            base_fields = list(rd.fieldnames)
            bit = zr[0]["index"]
            for r in rd:
                r["zone"] = zid
                r["route_mask"] = str((1 << bit) if r["on_route"] == "1" else 0)
                rows_all.append(r)
            center, half = sun.ZONE_CENTER, sun.WINDOW_HALF_M
        else:
            G = graphs[zid]
            dpts = np.vstack([densify(G, r["path"]) for r in zr])
            tree = cKDTree(dpts)
            cm = float(zc.get("corridor_m", 250))
            keep = lambda x, y: tree.query([x, y])[0] <= cm  # noqa: E731
            union = set().union(*[route_edges(r["path"]) for r in zr])
            sets = [set() for _ in range(nR)]
            for r in zr:
                sets[r["index"]] = route_edges(r["path"])
            rows = G.Z.points(union, keep=keep, route_sets=sets, pid0=pid)
            for r in rows:
                r["zone"] = zid
                r["route_mask"] = str(r["route_mask"])
                rows_all.append({k: str(v) for k, v in r.items()})
            xs = np.array([float(r["x"]) for r in rows_all[t0:]])
            ys = np.array([float(r["y"]) for r in rows_all[t0:]])
            cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
            half = math.ceil((max(xs.max() - xs.min(), ys.max() - ys.min()) / 2 + 1000.0) / 100.0) * 100.0
            lon, lat = G.Z.t.transform(cx, cy, direction="INVERSE")
            center = (round(lat, 5), round(lon, 5))
        pid = len(rows_all)
        for k, r in enumerate(rows_all[t0:], start=t0):
            assert int(r["id"]) == k, "row ids must equal row index"
        zones.append({"id": zid, "name": zc["name"], "row0": t0, "row1": len(rows_all), "center": list(center), "half_m": half,
                      "osm": sorted({graphs[zid].osm_name}), "routes": [r["id"] for r in zr]})
    fields = base_fields + ["zone", "route_mask"]
    with open(zdir / "points.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows_all)
    (zdir / "zones.json").write_text(json.dumps({"zones": zones, "routes": [r["id"] for r in rt["routes"]]}, indent=1))
    return zones


# ------------------------------------------------------------------ regression against the single-zone pipeline
def _sha(b):
    return hashlib.sha256(b).hexdigest()


def baseline():
    p = ROOT / "tests_mc" / "baseline_potrero.json"
    return json.load(open(p)) if p.exists() else None


def _csv_text(rows):
    s = io.StringIO()
    csv.writer(s, lineterminator="\n").writerows(rows)
    return s.getvalue().encode()


def check_regression(grids_dir, what):
    """Assert the Potrero rows (the first n of every combined table) equal the pre-routes.toml baseline."""
    bl = baseline()
    if bl is None:
        return "no baseline file"
    n, zdir = bl["n_points"], Path(grids_dir) / "zone"
    msg = []
    if what == "points":
        rows = list(csv.reader(open(zdir / "points.csv", newline="")))[:n + 1]
        assert _sha(_csv_text([r[:-2] for r in rows])) == bl["points_sha256"], "Potrero point rows changed"
        msg.append("points ok")
    if what == "street":
        rows = list(csv.reader(open(zdir / "street_features.csv", newline="")))[:n + 1]
        assert _sha("\n".join(",".join(r[:-1]) for r in rows).encode()) == bl["features_no_refine_sha256"], "Potrero street features changed"
        ref = _sha("\n".join(r[-1] for r in rows[1:]).encode()) == bl["refine_corr_sha256"]
        msg.append("street_features ok" + ("" if ref else " (refine_corr differs: refined wind file was rewritten since the baseline)"))
    if what == "sun":
        hor = np.load(zdir / "street_horizon.npy")[:n]
        svf = np.load(zdir / "street_svf.npy")[:n]
        assert _sha(np.ascontiguousarray(hor).tobytes()) == bl["horizon_sha256"], "Potrero horizon changed"
        assert _sha(np.ascontiguousarray(svf).tobytes()) == bl["svf_sha256"], "Potrero SVF changed"
        rows = list(csv.reader(open(zdir / "street_surface.csv", newline="")))[:n + 1]
        assert _sha(_csv_text(rows)) == bl["surface_sha256"], "Potrero surface table changed"
        msg.append("horizon, svf, surface ok")
    return "; ".join(msg)


# ------------------------------------------------------------------ viewer data and overlays
def write_viewer_routes(rt, graphs, resolved, zones, data_dir, out_json):
    meta = json.load(open(Path(out_json).parent / "meta.json"))
    G0 = next(iter(graphs.values()))
    t = G0.Z.t

    def sc(lat, lon, z):
        x, y = t.transform(lon, lat)
        return [round(x - meta["x0"], 1), round(z, 1), round(-(y - meta["y0"]), 1)]

    zname = {z["id"]: z["name"] for z in zones}
    routes = []
    for r in resolved:
        G = graphs[r["zone"]]
        path = r["path"]
        poly = [sc(*G.nodes[n], r["z"][i]) for i, n in enumerate(path)]
        names = []
        sidx = []
        for nm in r["edge_streets"]:
            if nm not in names:
                names.append(nm)
            sidx.append(names.index(nm))
        sidx = sidx + sidx[-1:]
        s = [0.0]
        for a, b in zip(path, path[1:]):
            s.append(round(s[-1] + G.d(a, b), 1))
        routes.append({"id": r["id"], "name": r["name"], "zone": r["zone"], "zone_name": zname[r["zone"]], "color": r["color"],
                       "index": r["index"], "length_m": r["length_m"], "drop_m": r["drop_m"], "climb_m": r["climb_m"],
                       "start": {"label": r["start_label"], "p": poly[0]}, "end": {"label": r["end_label"], "p": poly[-1]},
                       "poly": poly, "s": s, "st": sidx, "streets": names})
    Path(out_json).write_text(json.dumps({"zones": [{"id": z["id"], "name": z["name"]} for z in zones], "routes": routes},
                                         separators=(",", ":")))
    return routes


def overlays(rt, graphs, resolved, grids_dir, drawn_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    out = Path(grids_dir) / "zone" / "routes"
    out.mkdir(parents=True, exist_ok=True)
    drawn = json.load(open(drawn_path))["lines"] if Path(drawn_path).exists() else {}
    for r in resolved:
        G = graphs[r["zone"]]
        fig, ax = plt.subplots(figsize=(8, 10.5), dpi=90)
        for w in G.Z.ways:
            xy = np.array([G.xyd[n] for n in w["nodes"] if n in G.xyd])
            if len(xy) > 1:
                ax.plot(xy[:, 0], xy[:, 1], color="#c4c8cf", lw=0.7)
        if r.get("drawn") in drawn:
            pts = np.array([G.Z.t.transform(lo, la) for la, lo in drawn[r["drawn"]]])
            ax.plot(pts[:, 0], pts[:, 1], ".", color=r["color"], ms=2.2, alpha=0.55, label="hand-drawn line (georeferenced)")
        P = np.array([G.xyd[n] for n in r["path"]])
        ax.plot(P[:, 0], P[:, 1], color="k", lw=4.2)
        ax.plot(P[:, 0], P[:, 1], color=r["color"], lw=2.4, label="matched OSM route")
        for i, n in enumerate(r["waypoint_nodes"]):
            ax.plot(*G.xyd[n], "wo", mec="k", ms=6)
        ax.plot(*P[0], "g^", ms=11, mec="k", label="start")
        ax.plot(*P[-1], "rs", ms=9, mec="k", label="end")
        m = 120
        ax.set_xlim(P[:, 0].min() - m, P[:, 0].max() + m)
        ax.set_ylim(P[:, 1].min() - m, P[:, 1].max() + m)
        ax.set_aspect("equal")
        ax.legend(loc="lower left", fontsize=7)
        ax.set_title(f"{r['name']}\n{r['length_m']:.0f} m, drop {r['drop_m']:.0f} m, climb {r['climb_m']:.0f} m", fontsize=9)
        fig.tight_layout()
        fig.savefig(out / f"{r['id']}.png")
        plt.close(fig)


# ------------------------------------------------------------------ geometry step
def step_geometry(cfg, rt, data, force=False):
    raw, grids = data / "raw", data / "grids"
    zdir = grids / "zone"
    if not (zdir / "potrero_points.csv").exists():
        from . import zone
        print("potrero_points.csv missing: running the original Potrero zone builder", flush=True)
        zone.build(cfg, raw, grids)
    graphs, resolved = resolve_routes(cfg, rt, raw, grids)
    for r in resolved:
        print(f"  {r['name']}: {r['length_m']:.0f} m, drop {r['drop_m']:.1f} m, climb {r['climb_m']:.1f} m, "
              f"{len(r['path'])} nodes", flush=True)
    zones = build_points(rt, graphs, resolved, grids)
    print("  regression:", check_regression(grids, "points"), flush=True)
    for z in zones:
        print(f"  zone {z['id']}: rows {z['row0']}..{z['row1']} ({z['row1'] - z['row0']} points), sun window half {z['half_m']:.0f} m", flush=True)
    res = [{k: v for k, v in r.items() if k not in ("z",)} for r in resolved]
    (zdir / "routes_resolved.json").write_text(json.dumps(res, default=str))
    overlays(rt, graphs, resolved, grids, ROOT / "routes_drawn.json")
    return graphs, resolved, zones


def step_viewer(cfg, rt, data, graphs=None, resolved=None, zones=None):
    grids = data / "grids"
    if graphs is None:
        graphs, resolved = resolve_routes(cfg, rt, data / "raw", grids)
        zones = json.load(open(grids / "zone" / "zones.json"))["zones"]
    out = ROOT / "site" / "v3" / "data"
    routes = write_viewer_routes(rt, graphs, resolved, zones, data, out / "routes.json")
    subprocess.run([sys.executable, str(ROOT / "site" / "v3" / "tools" / "make_points.py"), str(grids / "zone" / "points.csv"), str(out)],
                   check=True)
    return routes


# ------------------------------------------------------------------ orchestrator
def _mt(paths):
    return [Path(p).stat().st_mtime for p in paths if Path(p).exists()]


def fresh(outputs, inputs):
    if not all(Path(o).exists() for o in outputs):
        return False
    return min(_mt(outputs)) >= max(_mt(inputs) or [0])


STEPS = ["geometry", "street", "sun", "replay", "viewer", "validate"]


def run(cfg, data, argv):
    data = Path(data)
    force = "--force" in argv
    start = argv[argv.index("--from") + 1] if "--from" in argv else None
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    rt = load_toml()
    grids, lib, raw = data / "grids", data / "library", data / "raw"
    z = grids / "zone"
    src = ROOT / "microclimate"
    rep = ROOT / "site" / "v3" / "replay"
    refined = data / "refined"
    osm_files = [raw / zone_osm_file(zid) for zid, zc in rt["zones"].items() if zc["builder"] != "disc"] + [raw / "osm_potrero.json"]
    plan = {
        "geometry": ([z / "points.csv", z / "zones.json", z / "routes_resolved.json"],
                     [ROOT / "routes.toml", ROOT / "microclimate.toml", src / "routes.py", src / "zone.py", z / "potrero_points.csv"] + osm_files),
        "street": ([z / "street_features.csv"],
                   [z / "points.csv", src / "street.py", lib / "wind_2700.npz", lib / "fog_2700_0.npz", lib / "fog_2700_3.npz",
                    refined / "patch_corr_2700.npz", refined / "patch_corr_2700_dx2p5.npz"]),
        "sun": ([z / "street_horizon.npy", z / "street_svf.npy", z / "street_surface.csv", z / "street_sun_meta.npz"],
                [z / "points.csv", z / "zones.json", src / "sun.py", z / "street_features.csv"] + osm_files),
        "replay": ([rep / "index.json"], [z / "street_features.csv", z / "street_horizon.npy", z / "street_surface.csv",
                                          z / "street_svf.npy", src / "replay.py", src / "wetness.py", ROOT / "microclimate.toml"]),
        "viewer": ([ROOT / "site" / "v3" / "data" / "routes.json", ROOT / "site" / "v3" / "data" / "points.bin"],
                   [z / "points.csv", z / "routes_resolved.json", ROOT / "site" / "v3" / "tools" / "make_points.py", src / "routes.py"]),
        "validate": ([rep / "validation.json"], [rep / "index.json", src / "validate.py"]),
    }
    rerun = force
    graphs = resolved = zones = None
    for st in STEPS:
        if only and st != only:
            continue
        if start and st == start:
            rerun = True
        if "--no-validate" in argv and st == "validate":
            continue
        outs, ins = plan[st]
        if not (rerun or only) and fresh(outs, ins):
            print(f"[{st}] up to date, skipped", flush=True)
            continue
        t0 = time.time()
        print(f"[{st}] running", flush=True)
        if st == "geometry":
            graphs, resolved, zones = step_geometry(cfg, rt, data)
        elif st == "street":
            from . import street
            info = street.build(cfg, grids, lib)
            print("  ", {k: info[k] for k in ("points", "finite_wind", "refine_corr_present")}, flush=True)
            print("  regression:", check_regression(grids, "street"), flush=True)
        elif st == "sun":
            from . import sun
            info = sun.build(cfg, raw, grids)
            print("  ", info, flush=True)
            print("  regression:", check_regression(grids, "sun"), flush=True)
        elif st == "replay":
            from . import replay
            replay.write(cfg, data, rep)
        elif st == "viewer":
            step_viewer(cfg, rt, data, graphs, resolved, zones)
        elif st == "validate":
            from . import validate
            ok = validate.run(cfg, data, rep)
            validate.overview(cfg, data, rep)
            print("  validation", "PASS" if ok else "FAIL", flush=True)
        print(f"[{st}] done in {time.time() - t0:.0f} s", flush=True)
    return 0
