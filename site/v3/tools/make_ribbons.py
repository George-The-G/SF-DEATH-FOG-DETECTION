"""Continuous street-ribbon geometry for the 3-D viewer (data side).

DISTRICT MODE (current): draws every route (cls 0) plus EVERY street of each zone that has simulation points and a class in
routes.toml [ribbons].highways (cls 1, mask 0, fade 255, depth 1; sampled every 2nd point = ~20 m). Route streets also get a cls 1
copy so the not-selected (dashed) routes still sit on a gradient street. cls 1 strips come first in the file (drawn underneath).
The old "route + side blocks" selection (depth 1-3, fades) is only kept in the stats; the file layout is unchanged (version 2).
Older description: ONE street block ("edge") out of every intersection node the route passes through, as ribbon strips
over the existing street sample points (grids/zone/points.csv; one ribbon per lane, i.e. per (way, side), so the left and
right lanes can differ because of sun and shadow).

Outputs (site/v3/data): ribbons.bin (RIB1, version 1), ribbons_meta.json; grids/zone/routes/ribbons_<zone>.png overlays.

ribbons.bin, little endian, every section padded to a multiple of 4 bytes, in this order:
  header  u32 magic 0x31424952 ('RIB1'), u32 S, u32 nStrips, u32 nIdx, f32 widthM, u32 version(=2)      24 bytes
  f32 pos[S,3]      x east from meta.x0, y = DEM elevation (NOT lifted), z = -(north - meta.y0)   (scene metres)
  f32 nrm[S,2]      unit direction in the xz plane pointing OUT of the street (away from the street centre line, towards
                    this lane's side) with the mitre scale 1/cos(half angle) folded into its length, clamped to 2.0.
                    Vertex 2*i+0 = pos - nrm*widthM/2, vertex 2*i+1 = pos + nrm*widthM/2.
  u32 src[S]        row id in points.csv / points.bin (end vertices repeat the id of their neighbouring sample)
  u16 st[S]         index into ribbons_meta.json "streets"
  u16 mask[S]       route_mask bits (bit i = i-th route): routes that use the segment OR whose side block it is
  u8  side[S]       0 left, 1 right (as points.csv)
  u8  cls[S]        0 = route sample (some route runs over this segment), 1 = side block only
  u8  fade[S]       alpha multiplier 0-255 (255 normally; tapers to 0 over the last 30 m of a side block cut at 250 m)
  u32 stripStart[nStrips+1]   sample index of each strip start
  f32 bsphere[nStrips,4]      centre xyz (scene) and radius, includes the ribbon half width
  u32 idx[nIdx]     triangles over the 2S vertices, 6*(n-1) per strip: (v, v+1, v+2), (v+1, v+3, v+2), v = 2*(start+i)
  u8  depth[S]      (version 2) 1 for route samples and for the first side block out of a route intersection; 2 and 3 for the
                    next blocks along the same branch (no U-turns, same 250 m cap measured along the branch from the route node)
Strips are ordered by depth (all depth-1 strips first, in the version-1 order, then depth 2, then depth 3), so the first S1
samples / streets_r columns are identical to the version-1 file.
Sample order = vertex order = column order of streets_r.u8.
"""
import csv
import json
import math
import struct
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
DATA = Path.home() / "deathfog-data"
OUT = ROOT / "site" / "v3" / "data"

# One street block ("edge": the chain of OSM segments between two consecutive intersection nodes) is drawn out of every
# intersection node of a route. Deliberately a constant, not a config knob (user decision); a block is cut at CAP_M.
SIDE_EDGE_STEPS = 3   # blocks per branch emitted: depth 1 = the block at the route intersection (the original 1x data), 2 and 3 continue it
CAP_M = 250.0          # side blocks longer than this are cut (measured from the route node) and faded out
FADE_M = 30.0          # fade length at a cut end
SPLIT_M = 25.0         # a lane is split when consecutive samples are further apart than this (points are 10-20 m apart)
MAX_SCALE = 2.0        # mitre scale clamp; sharper corners split the strip (bevel)
DISTRICT_STRIDE = 2    # version 3 data: every drivable street of the zone is a ribbon (cls 1, mask 0, fade 255, depth 1), sampled every 2nd point
VERSION = 2          # v2 appends u8 depth[S] after idx (v1 readers ignore trailing data)
DEFAULT_HIGHWAYS = ["residential", "tertiary", "secondary", "primary", "unclassified", "living_street", "trunk"]


def load_ribbon_cfg():
    with open(ROOT / "routes.toml", "rb") as f:
        rb = tomllib.load(f).get("ribbons", {})
    return float(rb.get("width_m", 6.5)), set(rb.get("highways", DEFAULT_HIGHWAYS))


def pair(a, b):
    return (a, b) if a < b else (b, a)


class ZoneGraph:
    """Street graph of one zone from its cached OSM json (plus the route edges, whatever their class)."""

    def __init__(self, osm_path, classes, route_paths):
        osm = json.load(open(osm_path, encoding="utf-8"))
        self.ll = {e["id"]: (e["lat"], e["lon"]) for e in osm["elements"] if e["type"] == "node"}
        self.ways = {e["id"]: e for e in osm["elements"]
                     if e["type"] == "way" and "highway" in e.get("tags", {}) and all(n in self.ll for n in e["nodes"])}
        self.adj = defaultdict(set)
        for w in self.ways.values():
            if w["tags"]["highway"] in classes:
                for a, b in zip(w["nodes"], w["nodes"][1:]):
                    if a != b:
                        self.adj[a].add(b)
                        self.adj[b].add(a)
        for p in route_paths:
            for a, b in zip(p, p[1:]):
                self.adj[a].add(b)
                self.adj[b].add(a)
        from pyproj import Transformer
        t = Transformer.from_crs(4326, 32610, always_xy=True)
        ids = list(self.adj)
        xs, ys = t.transform([self.ll[n][1] for n in ids], [self.ll[n][0] for n in ids])
        self.xy = {n: (float(x), float(y)) for n, x, y in zip(ids, xs, ys)}

    def deg(self, n):
        return len(self.adj.get(n, ()))

    def d(self, a, b):
        return math.hypot(self.xy[a][0] - self.xy[b][0], self.xy[a][1] - self.xy[b][1])

    def d_chain(self, nodes):
        return sum(self.d(a, b) for a, b in zip(nodes, nodes[1:]))

    def walk(self, n0, n1):
        """Chain of nodes from intersection n0 through n1 along degree-2 nodes up to the next intersection / dead end."""
        nodes = [n0, n1]
        while self.deg(nodes[-1]) == 2 and nodes[-1] != n0:
            nxt = [m for m in self.adj[nodes[-1]] if m != nodes[-2]]
            if not nxt:
                break
            nodes.append(nxt[0])
        return nodes


def select_zone(G, zroutes):
    """-> (segment table {pair: {route, side, edge}}, side edges {key: {...}}). zroutes: resolved route dicts of this zone."""
    seg, edges = {}, {}

    def add_edge(nodes, anchor, offset, depth, bit, rid):
        key = min(tuple(nodes), tuple(nodes[::-1]))
        e = edges.setdefault(key, {"nodes": list(key), "anchors": {}, "mask": 0, "routes": set(), "depth": depth})
        e["anchors"][anchor] = min(offset, e["anchors"].get(anchor, 1e18))   # node -> metres along the branch from the route node
        e["depth"] = min(e["depth"], depth)
        e["mask"] |= bit
        e["routes"].add(rid)

    def expand(nodes, depth, offset, bit, rid, rsegs):
        """Continue a side block through its far intersection (never back the way it came), up to SIDE_EDGE_STEPS blocks / CAP_M."""
        far = nodes[-1]
        if depth >= SIDE_EDGE_STEPS or offset >= CAP_M or G.deg(far) < 3:
            return
        for m in sorted(G.adj[far]):
            if m == nodes[-2]:
                continue
            nn = G.walk(far, m)
            if any(pair(a, b) in rsegs for a, b in zip(nn, nn[1:])):
                continue
            add_edge(nn, far, offset, depth + 1, bit, rid)
            expand(nn, depth + 1, offset + G.d_chain(nn), bit, rid, rsegs)

    for r in zroutes:
        bit = 1 << r["index"]
        for a, b in zip(r["path"], r["path"][1:]):
            seg.setdefault(pair(a, b), {"route": 0, "side": 0, "edge": None})["route"] |= bit
    for r in zroutes:
        bit = 1 << r["index"]
        rsegs = {pair(a, b) for a, b in zip(r["path"], r["path"][1:])}
        for n in dict.fromkeys(r["path"]):
            if G.deg(n) < 3:
                continue
            for m in sorted(G.adj[n]):
                if pair(n, m) in rsegs:
                    continue
                nodes = G.walk(n, m)
                if any(pair(a, b) in rsegs for a, b in zip(nodes, nodes[1:])):
                    continue
                add_edge(nodes, n, 0.0, 1, bit, r["id"])
                expand(nodes, 1, G.d_chain(nodes), bit, r["id"], rsegs)
    for key, e in edges.items():
        nodes = e["nodes"]
        e["cum"] = [0.0]
        for a, b in zip(nodes, nodes[1:]):
            e["cum"].append(e["cum"][-1] + G.d(a, b))
        e["length"] = e["cum"][-1]
        for i, (a, b) in enumerate(zip(nodes, nodes[1:])):
            s = seg.setdefault(pair(a, b), {"route": 0, "side": 0, "edge": None})
            s["side"] |= e["mask"]
            if s["route"] == 0:
                s["edge"] = (key, i)
                s["depth"] = min(s.get("depth", 9), e["depth"])
    return seg, edges


def proj_t(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    return ((px - ax) * dx + (py - ay) * dy) / max(dx * dx + dy * dy, 1e-9)


def build(write=True, verbose=True):
    width_m, classes = load_ribbon_cfg()
    meta = json.load(open(OUT / "meta.json"))
    zdir = DATA / "grids" / "zone"
    zones = json.load(open(zdir / "zones.json"))["zones"]
    resolved = json.load(open(zdir / "routes_resolved.json"))
    rows = list(csv.DictReader(open(zdir / "points.csv", newline="", encoding="utf-8")))
    R = {k: [] for k in ("x", "y", "z")}
    streets, sidx = [], {}
    samples = []          # dict per sample
    strips = []
    stats = {"routes": {}, "capped": [], "missing": []}
    overlay = {}

    for z in zones:
        zid = z["id"]
        zr = [r for r in resolved if r["zone"] == zid]
        G = ZoneGraph(DATA / "raw" / z["osm"][0], classes, [r["path"] for r in zr])
        seg, edges = select_zone(G, zr)
        for r in zr:
            mine = [e for e in edges.values() if r["id"] in e["routes"]]
            stats["routes"][r["id"]] = {"route_m": r["length_m"], "side_edges": len(mine),
                                        "side_m": round(sum(min(e["length"], CAP_M) for e in mine), 1)}
        for e in edges.values():
            if e["length"] > CAP_M:
                stats["capped"].append({"zone": zid, "length_m": round(e["length"]), "routes": sorted(e["routes"]),
                                        "street": next((G.ways[w]["tags"].get("name", "") for w in G.ways
                                                        if e["nodes"][0] in G.ways[w]["nodes"] and e["nodes"][1] in G.ways[w]["nodes"]), "")})
        # candidate rows of this zone
        have = defaultdict(set)
        sel = []
        for k in range(z["row0"], z["row1"]):
            r = rows[k]
            w = G.ways.get(int(r["way"]))
            if w is None:
                continue
            si = int(r["seg"])
            a, b = w["nodes"][si], w["nodes"][si + 1]
            info = seg.get(pair(a, b))
            if info is None or info["route"] == 0:
                # district street (version 3): every street of the zone that has simulation points and a ribbon class
                if w["tags"].get("highway") not in classes:
                    continue
                info = {"route": 0, "side": 0, "edge": None, "depth": 1}
            fade = 255
            have[pair(a, b)].add(r["side"])
            sel.append((k, w, si, a, b, info, fade))
            if info["route"] != 0 and w["tags"].get("highway") in classes:
                # a route street also gets its district gradient (cls 1, drawn first, i.e. underneath): the not-selected routes are only a
                # thin dashed line, the street itself must still read as part of the district
                sel.append((k, w, si, a, b, {"route": 0, "side": 0, "edge": None, "depth": 1, "dup": 1}, fade))
        for pr, info in seg.items():
            if info["route"] == 0 and info["edge"] is not None and info["edge"][0] and \
                    edges[info["edge"][0]]["length"] > CAP_M:
                continue
            if G.d(*pr) >= 1.0 and len(have.get(pr, ())) < 2:
                stats["missing"].append({"zone": zid, "pair": list(pr), "have": sorted(have.get(pr, ())), "depth": info.get("depth", 0), "m": round(G.d(*pr))})

        # ---- lanes
        groups = defaultdict(list)
        for it in sel:
            groups[(it[1]["id"], rows[it[0]]["side"], it[5].get("dup", 0))].append(it)
        lanes = []
        for (wid, sd, _dup), its in groups.items():
            its.sort(key=lambda t: t[0])
            cur = []
            for it in its:
                if cur:
                    p = cur[-1]
                    brk = (it[2] - p[2] > 1 or it[2] < p[2] or
                           math.hypot(float(rows[it[0]]["x"]) - float(rows[p[0]]["x"]),
                                      float(rows[it[0]]["y"]) - float(rows[p[0]]["y"])) > SPLIT_M)
                    if not brk and it[2] != p[2] and G.deg(it[1]["nodes"][it[2]]) >= 3:
                        brk = True
                    ck = (it[5]["route"] != 0, it[5]["route"] | it[5]["side"], it[5].get("depth", 1))
                    pk = (p[5]["route"] != 0, p[5]["route"] | p[5]["side"], p[5].get("depth", 1))
                    if brk or ck != pk:
                        lanes.append(cur)
                        cur = []
                cur.append(it)
            if cur:
                lanes.append(cur)
        lanes = [{"its": ln, "way": ln[0][1], "side": 0 if rows[ln[0][0]]["side"] == "left" else 1,
                  "cls": 0 if ln[0][5]["route"] else 1, "mask": ln[0][5]["route"] | ln[0][5]["side"],
                  "depth": 1 if ln[0][5]["route"] else ln[0][5].get("depth", 1),
                  "name": ln[0][1]["tags"].get("name", "") or "(unnamed)"} for ln in lanes]
        for ln in lanes:
            w, its = ln["way"], ln["its"]
            ln["start"] = (w["nodes"][its[0][2]], w["nodes"][its[0][2] + 1])        # (node, neighbour)
            ln["end"] = (w["nodes"][its[-1][2] + 1], w["nodes"][its[-1][2]])
        # ---- chain lanes through degree-2 nodes
        ends = defaultdict(list)
        for li, ln in enumerate(lanes):
            for which in ("start", "end"):
                n, m = ln[which]
                if G.deg(n) != 2 or m not in G.adj[n]:
                    continue
                lo, hi = sorted(G.adj[n])
                agree = (m == hi) if which == "start" else (m == lo)
                ts = ln["side"] if agree else 1 - ln["side"]
                ends[(n, ts, ln["name"], ln["cls"], ln["mask"])].append((li, which, m))
        links = {}
        for key, lst in ends.items():
            if len(lst) == 2 and lst[0][0] != lst[1][0] and lst[0][2] != lst[1][2]:
                links[(lst[0][0], lst[0][1])] = (lst[1][0], lst[1][1])
                links[(lst[1][0], lst[1][1])] = (lst[0][0], lst[0][1])
        seen, chains = set(), []

        def follow(li, rev):
            chain = []
            while li is not None and li not in seen:
                seen.add(li)
                chain.append((li, rev))
                nxt = links.get((li, "start" if rev else "end"))
                if nxt is None:
                    break
                li, rev = nxt[0], nxt[1] == "end"
            return chain
        for li in range(len(lanes)):
            if li in seen:
                continue
            if (li, "start") not in links:
                chains.append(follow(li, False))
            elif (li, "end") not in links:
                chains.append(follow(li, True))
        for li in range(len(lanes)):
            if li not in seen:
                chains.append(follow(li, False))

        # ---- strips
        pxy = {}
        for chain in chains:
            first, frev = chain[0]
            L0 = lanes[first]
            sgn = (1 if L0["side"] == 0 else -1) * (-1 if frev else 1)   # outward = left of travel when +1
            verts = []     # (x, y, z, row_id, fade, is_end)
            for li, rev in chain:
                its = lanes[li]["its"][::-1] if rev else lanes[li]["its"]
                for it in its:
                    r = rows[it[0]]
                    verts.append([float(r["x"]), float(r["y"]), float(r["z"]), it[0], it[6], False])
            # end vertices at way nodes
            def end_vertex(li, which):
                ln = lanes[li]
                w = ln["way"]
                if which == "start":
                    it = ln["its"][0]
                    a, b = w["nodes"][it[2]], w["nodes"][it[2] + 1]
                    node = a
                else:
                    it = ln["its"][-1]
                    a, b = w["nodes"][it[2]], w["nodes"][it[2] + 1]
                    node = b
                if a not in G.xy or b not in G.xy:
                    return None
                (ax, ay), (bx, by) = G.xy[a], G.xy[b]
                Ls = math.hypot(bx - ax, by - ay)
                if Ls < 1e-6:
                    return None
                r = rows[it[0]]
                t = proj_t(float(r["x"]), float(r["y"]), ax, ay, bx, by)
                nn = max(1, int(Ls // 10))
                dist = t * Ls if which == "start" else (1 - t) * Ls
                if dist > Ls / (2 * nn) + 1.0:
                    return None
                dx, dy = (bx - ax) / Ls, (by - ay) / Ls
                sg = 1 if ln["side"] == 0 else -1
                nx_, ny_ = G.xy[node]
                return [nx_ - dy * 5.0 * sg, ny_ + dx * 5.0 * sg, float(r["z"]), it[0], it[6], True]
            lf, lrev = chain[0]
            ev = end_vertex(lf, "end" if lrev else "start") if (lf, "end" if lrev else "start") not in links else None
            ll_, lrev2 = chain[-1]
            ev2 = end_vertex(ll_, "start" if lrev2 else "end") if (ll_, "start" if lrev2 else "end") not in links else None
            if ev:
                verts.insert(0, ev)
            if ev2:
                verts.append(ev2)
            # drop zero-length duplicates
            clean = [verts[0]]
            for v in verts[1:]:
                if math.hypot(v[0] - clean[-1][0], v[1] - clean[-1][1]) > 0.05:
                    clean.append(v)
            verts = clean
            if L0["cls"] == 1 and len(verts) > 3:
                # district streets: ~20 m sample spacing (every second 10 m point; both strip ends kept) to keep the page light
                keepi = list(range(0, len(verts), DISTRICT_STRIDE))
                if keepi[-1] != len(verts) - 1:
                    keepi.append(len(verts) - 1)
                verts = [verts[i] for i in keepi]
                clean = [verts[0]]
                for v in verts[1:]:
                    if math.hypot(v[0] - clean[-1][0], v[1] - clean[-1][1]) > 0.05:
                        clean.append(v)
                verts = clean
            if len(verts) < 2:
                continue
            # split at sharp corners (beyond the mitre clamp)
            pieces, cur = [], [verts[0]]
            for i in range(1, len(verts) - 1):
                cur.append(verts[i])
                d1 = (verts[i][0] - verts[i - 1][0], verts[i][1] - verts[i - 1][1])
                d2 = (verts[i + 1][0] - verts[i][0], verts[i + 1][1] - verts[i][1])
                l1, l2 = math.hypot(*d1), math.hypot(*d2)
                c = (d1[0] * d2[0] + d1[1] * d2[1]) / (l1 * l2)       # cos(turn angle) = cos(2*half) ... half-angle h: turn = 2h
                h = math.acos(max(-1.0, min(1.0, c))) / 2
                if math.cos(h) < 1 / MAX_SCALE:
                    pieces.append(cur)
                    cur = [verts[i]]
            cur.append(verts[-1])
            pieces.append(cur)
            for pv in pieces:
                if len(pv) < 2:
                    continue
                n = len(pv)
                segn = []
                for i in range(n - 1):
                    dx, dy = pv[i + 1][0] - pv[i][0], pv[i + 1][1] - pv[i][1]
                    l = math.hypot(dx, dy)
                    segn.append((-dy / l * sgn, dx / l * sgn))
                start = len(samples)
                for i, v in enumerate(pv):
                    if i == 0:
                        nx_, ny_ = segn[0]
                        sc = 1.0
                    elif i == n - 1:
                        nx_, ny_ = segn[-1]
                        sc = 1.0
                    else:
                        sx, sy = segn[i - 1][0] + segn[i][0], segn[i - 1][1] + segn[i][1]
                        ln_ = math.hypot(sx, sy)
                        nx_, ny_ = sx / ln_, sy / ln_
                        sc = min(MAX_SCALE, 2.0 / ln_)
                    r = rows[v[3]]
                    nm = r["street"] or "(unnamed)"
                    if nm not in sidx:
                        sidx[nm] = len(streets)
                        streets.append(nm)
                    samples.append({"x": v[0] - meta["x0"], "y": v[2], "z": -(v[1] - meta["y0"]), "nx": nx_ * sc, "nz": -ny_ * sc,
                                    "src": v[3], "st": sidx[nm], "mask": L0["mask"], "side": 0 if r["side"] == "left" else 1,
                                    "cls": L0["cls"], "fade": v[4], "depth": L0["depth"]})
                    pxy.setdefault(len(strips), []).append((v[0], v[1]))
                strips.append({"way": L0["way"]["id"], "side": L0["side"], "street": sidx[L0["name"]] if L0["name"] in sidx else None,
                               "name": L0["name"], "cls": L0["cls"], "route_mask": L0["mask"], "start": start, "count": n, "depth": L0["depth"],
                               "zone": zid})
        overlay[zid] = (G, zr, edges, [(strips[i]["cls"], np.array(p)) for i, p in pxy.items() if strips[i]["zone"] == zid])

    # fix street index for strips whose name was never seen as a sample street
    for s in strips:
        nm = s.pop("name")
        if nm not in sidx:
            sidx[nm] = len(streets)
            streets.append(nm)
        s["street"] = sidx[nm]
    # order strips by depth (stable): the depth-1 prefix is identical to the version-1 file
    # district strips (cls 1) first so they are drawn underneath the route strips (cls 0): blending follows index order (no depth writes)
    order = sorted(range(len(strips)), key=lambda i: (0 if strips[i]["cls"] == 1 else 1, strips[i]["depth"], i))
    new_samples, new_strips = [], []
    for i in order:
        s = strips[i]
        a = len(new_samples)
        new_samples.extend(samples[s["start"]:s["start"] + s["count"]])
        new_strips.append(dict(s, start=a))
    samples, strips = new_samples, new_strips
    S, nS = len(samples), len(strips)
    pos = np.array([[s["x"], s["y"], s["z"]] for s in samples], np.float32)
    nrm = np.array([[s["nx"], s["nz"]] for s in samples], np.float32)
    src = np.array([s["src"] for s in samples], np.uint32)
    st = np.array([s["st"] for s in samples], np.uint16)
    mask = np.array([s["mask"] for s in samples], np.uint16)
    side = np.array([s["side"] for s in samples], np.uint8)
    cls = np.array([s["cls"] for s in samples], np.uint8)
    fade = np.array([s["fade"] for s in samples], np.uint8)
    depth = np.array([s["depth"] for s in samples], np.uint8)
    stripStart = np.array([s["start"] for s in strips] + [S], np.uint32)
    bs = np.zeros((nS, 4), np.float32)
    idx = []
    for i, s in enumerate(strips):
        a, b = s["start"], s["start"] + s["count"]
        P = np.concatenate([pos[a:b] + np.c_[nrm[a:b, 0], np.zeros(b - a), nrm[a:b, 1]] * width_m / 2,
                            pos[a:b] - np.c_[nrm[a:b, 0], np.zeros(b - a), nrm[a:b, 1]] * width_m / 2])
        c = (P.min(0) + P.max(0)) / 2
        bs[i] = (*c, np.max(np.linalg.norm(P - c, axis=1)))
        for k in range(a, b - 1):
            v = 2 * k
            idx += [v, v + 1, v + 2, v + 1, v + 3, v + 2]
    idx = np.array(idx, np.uint32)

    def pad(buf):
        return buf + b"\0" * (-len(buf) % 4)
    blob = struct.pack("<IIIIfI", 0x31424952, S, nS, len(idx), width_m, VERSION)
    for a in (pos, nrm, src, st, mask, side, cls, fade, stripStart, bs, idx, depth):
        blob += pad(a.tobytes())
    route_len = {r["id"]: r["length_m"] for r in resolved}
    metaj = {"S": S, "nStrips": nS, "widthM": width_m, "version": VERSION, "district": True, "streets": streets, "strips":
             [[s["start"], s["count"], s["cls"], s["street"]] for s in strips],   # [start, count, cls, street] (compact)
             "route_lengths_m": route_len, "side_block_counts": {k: v["side_edges"] for k, v in stats["routes"].items()},
             "side_block_m": {k: v["side_m"] for k, v in stats["routes"].items()}}
    if write:
        (OUT / "ribbons.bin").write_bytes(blob)
        (OUT / "ribbons_meta.json").write_text(json.dumps(metaj, separators=(",", ":")))
        make_overlays(overlay, zdir / "routes")
    stats["S"], stats["nStrips"], stats["bytes"] = S, nS, len(blob)
    stats["route_km"] = round(sum(route_len.values()) / 1000, 2)
    stats["side_km_per_route_sum"] = round(sum(v["side_m"] for v in stats["routes"].values()) / 1000, 2)
    return stats


def make_overlays(overlay, outdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    outdir.mkdir(parents=True, exist_ok=True)
    for zid, (G, zr, edges, lines) in overlay.items():
        fig, ax = plt.subplots(figsize=(10, 10), dpi=100)
        for w in G.ways.values():
            xy = np.array([G.xy[n] for n in w["nodes"] if n in G.xy])
            if len(xy) > 1:
                ax.plot(xy[:, 0], xy[:, 1], color="#d5d8dc", lw=0.6)
        for cl, p in lines:
            ax.plot(p[:, 0], p[:, 1], color="#e8434a" if cl == 0 else "#1f77b4", lw=1.6 if cl == 0 else 1.2)
        for r in zr:
            P = np.array([G.xy[n] for n in r["path"]])
            ax.plot(P[:, 0], P[:, 1], color="k", lw=0.5, alpha=0.6)
            ax.plot(*P[0], "g^", ms=8, mec="k")
            ax.plot(*P[-1], "ks", ms=6)
        ax.set_aspect("equal")
        allp = np.vstack([p for _, p in lines])
        ax.set_xlim(allp[:, 0].min() - 100, allp[:, 0].max() + 100)
        ax.set_ylim(allp[:, 1].min() - 100, allp[:, 1].max() + 100)
        ax.set_title(f"{zid}: route lanes (red) + side blocks (blue), {len(edges)} side edges")
        fig.tight_layout()
        fig.savefig(outdir / f"ribbons_{zid}.png")
        plt.close(fig)


if __name__ == "__main__":
    st = build()
    print(json.dumps(st, indent=1))
