"""City-wide continuous San Francisco road ribbon & intersection mesh builder (ROAD1).

Builds a unified, watertight topological road network and pre-baked GPU mesh directly from
OpenStreetMap + high-resolution USGS 3DEP DEM GeoTIFFs, preserving San Francisco's sharp
flat-to-steep terrain transitions (level intersection pads + piecewise-linear sustained grades
+ parabolic car-lip vertical curves) while retaining smooth microclimate data-point coloring
(`live/day0..2.u8`, `sun`, `ts`, and route overlays) and embedding a connected road graph for games.

Run:
    python -m microclimate.live.roadnet [--out DIR] [--fetch] [--osm PATH]
"""
from __future__ import annotations

import json
import math
import struct
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

import numpy as np

from ..paths import data_dir

# @sec: constants & binary schema
MAGIC = 0x32444152  # 'ROAD2' little-endian
VERSION = 2
HEADER_FMT = "<13I"
HEADER_BYTES = struct.calcsize(HEADER_FMT)

X0, Y0 = 540100.0, 4171500.0  # scene origin in EPSG:32610 (matches site/v3/data/meta.json)
CITY_BBOX = (37.690, -122.545, 37.835, -122.350)

RIDE_HIGHWAYS = {
    "residential", "tertiary", "secondary", "primary",
    "unclassified", "living_street", "trunk",
    "tertiary_link", "secondary_link", "primary_link", "trunk_link",
}

HIGHWAY_WIDTH_M = {
    "trunk": 12.0,
    "primary": 11.5,
    "secondary": 10.0,
    "tertiary": 8.5,
    "residential": 6.8,
    "unclassified": 6.5,
    "living_street": 4.8,
    "service": 4.2,
    "trunk_link": 7.5,
    "primary_link": 7.5,
    "secondary_link": 7.0,
    "tertiary_link": 6.5,
}

# San Francisco terrain & vertical profile constants
DEM_STEP_M = 2.5          # dense DEM sampling interval along road centerlines
JUNC_MAX_GRADE = 0.06     # max cross-grade on intersection pads (6% = realistic SF flat pad)
PL_EPS_M = 0.40           # Douglas-Peucker vertical tolerance (m) for sustained-grade fitting
PL_MIN_DG = 0.025         # min grade jump (2.5%) to insert a parabolic car-lip vertical curve
LIP_K = 36.0              # parabolic curve length factor (m per unit grade jump)
LIP_MIN_M = 4.0           # minimum vertical curve length (m)
LIP_MAX_M = 14.0          # maximum vertical curve length (m)
LIP_STEP_M = 3.0          # subdivision spacing inside parabolic car lips (m)
MAX_CROSS_SLOPE = 0.06    # max cross-slope camber/banking across ribbon width (6%)
TERRAIN_CLEAR_M = 0.30    # min vertical clearance above viewer's rendered 60 m terrain mesh (m)
MAX_SPAN_M = 12.0         # max along-road segment span (m) so 60 m terrain triangles never swallow mid-span
SKIRT_DROP_M = 1.6        # curb/embankment skirt depth below terrain (m) so roads never float
SKIRT_THRESH_M = 0.65     # per-segment height above 60 m terrain required to emit a skirt quad
MAX_MITER = 1.8           # max miter scale 1/cos(half_angle) at interior corners
HORIZ_EPS_M = 0.40        # 2D horizontal Douglas-Peucker tolerance (m) to drop collinear OSM nodes
FILLET_PTS = 2            # interior points on curb fillet arc at intersections
TILE_M = 2000.0           # spatial tile size (m) for viewer frustum culling
SENTINEL_SAMPLE = 0xFFFFFFFF


def _pad4(buf: bytes) -> bytes:
    """Pad a byte buffer to a 4-byte boundary."""
    rem = len(buf) % 4
    return buf if rem == 0 else buf + b"\x00" * (4 - rem)


# @sec: sf terrain & vertical profile
def fit_piecewise_linear(s: np.ndarray, z: np.ndarray, eps: float = PL_EPS_M) -> np.ndarray:
    """Fit a piecewise-linear elevation profile to (s, z) via Douglas-Peucker, preserving sustained grades.

    Returns sorted knot indices into s/z, always including 0 and len(s) - 1.
    """
    n = len(s)
    if n <= 2:
        return np.arange(n, dtype=np.int32)
    keep = np.zeros(n, dtype=bool)
    keep[0] = True
    keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a <= 1:
            continue
        ds = s[b] - s[a]
        if ds < 1e-6:
            continue
        t = (s[a + 1:b] - s[a]) / ds
        z_lin = z[a] + t * (z[b] - z[a])
        err = np.abs(z[a + 1:b] - z_lin)
        idx = int(np.argmax(err))
        if float(err[idx]) > eps:
            mid = a + 1 + idx
            keep[mid] = True
            stack.append((a, mid))
            stack.append((mid, b))
    return np.nonzero(keep)[0].astype(np.int32)


def apply_parabolic_lips(
    s_knots: np.ndarray,
    z_knots: np.ndarray,
    k_factor: float = LIP_K,
    l_min: float = LIP_MIN_M,
    l_max: float = LIP_MAX_M,
    min_dg: float = PL_MIN_DG,
    step_m: float = LIP_STEP_M,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Insert C1-continuous parabolic vertical curves ('car lips') at grade-break knots.

    Between lips, the profile remains strictly linear so steep sustained SF inclines
    (e.g. Potrero Hill 15-22% blocks) hold their crisp grade instead of sagging.
    Returns (s_out, z_out, grade_out).
    """
    m = len(s_knots)
    if m < 2:
        return s_knots.astype(np.float64), z_knots.astype(np.float64), np.zeros(m, dtype=np.float64)
    if m == 2:
        ds = max(float(s_knots[1] - s_knots[0]), 1e-6)
        g = (float(z_knots[1]) - float(z_knots[0])) / ds
        return s_knots.astype(np.float64), z_knots.astype(np.float64), np.array([g, g], dtype=np.float64)

    seg_len = np.diff(s_knots)
    seg_g = np.diff(z_knots) / np.maximum(seg_len, 1e-6)

    out_s = [float(s_knots[0])]
    out_z = [float(z_knots[0])]
    out_g = [float(seg_g[0])]

    for k in range(1, m - 1):
        g1 = float(seg_g[k - 1])
        g2 = float(seg_g[k])
        dg = g2 - g1
        sk = float(s_knots[k])
        zk = float(z_knots[k])
        if abs(dg) < min_dg:
            out_s.append(sk)
            out_z.append(zk)
            out_g.append(0.5 * (g1 + g2))
            continue
        max_avail = 0.42 * min(float(seg_len[k - 1]), float(seg_len[k]))
        lv = min(max(l_min, min(l_max, k_factor * abs(dg))), max_avail)
        if lv < 1.0:
            out_s.append(sk)
            out_z.append(zk)
            out_g.append(0.5 * (g1 + g2))
            continue
        half = 0.5 * lv
        sa = sk - half
        za = zk - g1 * half
        n_sub = max(2, int(math.ceil(lv / step_m)))
        for j in range(n_sub + 1):
            u = lv * (j / n_sub)
            sv = sa + u
            if sv <= out_s[-1] + 1e-4:
                continue
            zv = za + g1 * u + 0.5 * (dg / lv) * u * u
            gv = g1 + (dg / lv) * u
            out_s.append(sv)
            out_z.append(zv)
            out_g.append(gv)

    if float(s_knots[-1]) > out_s[-1] + 1e-4:
        out_s.append(float(s_knots[-1]))
        out_z.append(float(z_knots[-1]))
        out_g.append(float(seg_g[-1]))
    return np.array(out_s, dtype=np.float64), np.array(out_z, dtype=np.float64), np.array(out_g, dtype=np.float64)


def fit_junction_plane(
    cx: float,
    cy: float,
    dem_fn: Callable[[np.ndarray, np.ndarray], np.ndarray],
    radius_m: float = 8.0,
    max_grade: float = JUNC_MAX_GRADE,
) -> tuple[float, float, float]:
    """Fit a clamped intersection pad plane (z0, gx, gy) around (cx, cy).

    Limits pad tilt to max_grade so intersections act as realistic SF flat/crowned pads
    where steep hills break into level crossings.
    """
    offs = np.array([
        [0.0, 0.0],
        [radius_m, 0.0], [-radius_m, 0.0],
        [0.0, radius_m], [0.0, -radius_m],
    ], dtype=np.float64)
    xs = cx + offs[:, 0]
    ys = cy + offs[:, 1]
    zs = dem_fn(xs, ys)
    z0 = float(zs[0])
    gx = float((zs[1] - zs[2]) / (2.0 * radius_m))
    gy = float((zs[3] - zs[4]) / (2.0 * radius_m))
    gmag = math.hypot(gx, gy)
    if gmag > max_grade and gmag > 1e-9:
        sc = max_grade / gmag
        gx *= sc
        gy *= sc
    return z0, gx, gy


def banked_edge_heights(
    zc: float,
    z_left_dem: float,
    z_right_dem: float,
    z_left_t20: float,
    z_right_t20: float,
    half_w: float,
    max_cross: float = MAX_CROSS_SLOPE,
    clear_m: float = TERRAIN_CLEAR_M,
) -> tuple[float, float]:
    """Compute left/right curb heights with clamped cross-slope and positive terrain clearance."""
    raw_slope = (z_left_dem - z_right_dem) / max(2.0 * half_w, 1.0)
    slope = max(-max_cross, min(max_cross, raw_slope))
    yl = zc + slope * half_w
    yr = zc - slope * half_w
    lift = max(0.0, (z_left_t20 + clear_m) - yl, (z_right_t20 + clear_m) - yr)
    return yl + lift, yr + lift


def make_dem_sampler(dem_path: Path):
    """Build a fast bilinear EPSG:32610 -> elevation (m) sampler backed by a USGS 3DEP GeoTIFF."""
    import rasterio
    from pyproj import Transformer
    from rasterio.windows import from_bounds

    tr_inv = Transformer.from_crs(32610, 4326, always_xy=True)
    with rasterio.open(dem_path) as src:
        win = from_bounds(
            CITY_BBOX[1] - 0.01, CITY_BBOX[0] - 0.01,
            CITY_BBOX[3] + 0.01, CITY_BBOX[2] + 0.01,
            src.transform,
        ).round_offsets().round_lengths()
        arr = src.read(1, window=win).astype(np.float32)
        inv = ~src.window_transform(win)
    H, W = arr.shape

    def sample(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        lon, lat = tr_inv.transform(xs, ys)
        colf, rowf = inv * (lon, lat)
        c0 = np.clip(np.floor(colf).astype(np.int32), 0, W - 2)
        r0 = np.clip(np.floor(rowf).astype(np.int32), 0, H - 2)
        fc = np.clip(colf - c0, 0.0, 1.0)
        fr = np.clip(rowf - r0, 0.0, 1.0)
        z00 = arr[r0, c0]
        z10 = arr[r0, c0 + 1]
        z01 = arr[r0 + 1, c0]
        z11 = arr[r0 + 1, c0 + 1]
        return ((1.0 - fc) * (1.0 - fr) * z00 + fc * (1.0 - fr) * z10 +
                (1.0 - fc) * fr * z01 + fc * fr * z11).astype(np.float64)

    return sample


def make_terrain60_sampler(grids_dir: Path, step: int = 3):
    """Build an exact triangle-plane sampler matching site/v3/data/terrain.glb (meshes.mesh_height, 60 m)."""
    from .. import meshes

    spec = json.loads((grids_dir / "grid_spec.json").read_text(encoding="utf-8"))
    ter = np.load(grids_dir / "terrain_20m.npy").astype(np.float32)
    x0, y0 = float(spec["x0"]), float(spec["y0"])

    def sample(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        return meshes.mesh_height(spec, ter, np.asarray(xs) - x0, np.asarray(ys) - y0, step=step).astype(np.float64)

    return sample, ter, spec


def nudge_terrain60_for_roads(
    ter: np.ndarray,
    spec: dict,
    edges: list[dict],
    dem_fn: Callable[[np.ndarray, np.ndarray], np.ndarray],
    step: int = 3,
    clear_m: float = 0.15,
    max_dist_m: float = 12.0,
    blend: float = 0.65,
) -> np.ndarray:
    """Gently pull existing 60 m terrain vertices under surface roads (layer == 0) toward road_z - clear_m."""
    out = np.array(ter, dtype=np.float32, copy=True)
    nx, ny = out.shape
    x0, y0 = float(spec["x0"]), float(spec["y0"])
    cell_m = float(spec["cell_m"])
    grid_m = cell_m * step

    w_sum = np.zeros_like(out, dtype=np.float64)
    z_sum = np.zeros_like(out, dtype=np.float64)

    for e in edges:
        if int(e.get("layer", 0)) != 0:
            continue
        xy = np.asarray(e["xy"], dtype=np.float64)
        if len(xy) < 2:
            continue
        r_max = max(max_dist_m, 0.5 * float(e.get("width", 7.0)) + 4.0)
        pad_cells = int(math.ceil(r_max / grid_m)) + 1

        for k in range(len(xy) - 1):
            ax, ay = float(xy[k, 0]), float(xy[k, 1])
            bx, by = float(xy[k + 1, 0]), float(xy[k + 1, 1])
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy
            if l2 < 1e-6:
                continue

            gx0 = max(0, int(math.floor((min(ax, bx) - x0) / grid_m)) - pad_cells)
            gx1 = min((nx - 1) // step, int(math.floor((max(ax, bx) - x0) / grid_m)) + pad_cells)
            gy0 = max(0, int(math.floor((min(ay, by) - y0) / grid_m)) - pad_cells)
            gy1 = min((ny - 1) // step, int(math.floor((max(ay, by) - y0) / grid_m)) + pad_cells)
            if gx1 < gx0 or gy1 < gy0:
                continue

            ix = np.arange(gx0, gx1 + 1) * step
            iy = np.arange(gy0, gy1 + 1) * step
            gxx, gyy = np.meshgrid(ix, iy, indexing="ij")
            vx = x0 + gxx * cell_m
            vy = y0 + gyy * cell_m
            t = np.clip(((vx - ax) * dx + (vy - ay) * dy) / l2, 0.0, 1.0)
            px = ax + t * dx
            py = ay + t * dy
            dist = np.hypot(vx - px, vy - py)
            land = out[gxx, gyy] > 0.5
            mask = land & (dist <= r_max)
            if not np.any(mask):
                continue

            road_z = dem_fn(px[mask], py[mask])
            target_z = road_z - clear_m
            w = blend * (1.0 - dist[mask] / r_max)
            np.add.at(w_sum, (gxx[mask], gyy[mask]), w)
            np.add.at(z_sum, (gxx[mask], gyy[mask]), w * target_z)

    hit = w_sum > 1e-6
    if np.any(hit):
        avg_target = z_sum[hit] / w_sum[hit]
        alpha = np.clip(w_sum[hit], 0.0, blend)
        out[hit] = (out[hit] + alpha * (avg_target - out[hit])).astype(np.float32)
    return out


# @sec: osm road graph & degree-2 chaining
def way_width_m(tags: dict) -> float:
    """Derive a realistic street width (m) from OSM tags (width, lanes, highway class)."""
    raw_w = tags.get("width", "")
    if raw_w:
        tok = str(raw_w).strip().split()[0].replace("m", "")
        try:
            val = float(tok)
            if 2.5 <= val <= 30.0:
                return val
        except ValueError:
            pass
    raw_l = tags.get("lanes", "")
    if raw_l:
        try:
            lanes = float(str(raw_l).strip().split(";")[0])
            if 1.0 <= lanes <= 8.0:
                return max(4.5, min(18.0, lanes * 3.1 + 1.4))
        except ValueError:
            pass
    return HIGHWAY_WIDTH_M.get(tags.get("highway", "residential"), 7.5)


def way_layer(tags: dict) -> int:
    """Return integer grade-separation layer for an OSM way (bridges > 0, tunnels < 0)."""
    if "layer" in tags:
        try:
            return int(str(tags["layer"]).strip().split(";")[0])
        except ValueError:
            pass
    if tags.get("bridge") in ("yes", "viaduct", "elevated"):
        return 1
    if tags.get("tunnel") in ("yes", "building_passage"):
        return -1
    return 0


def build_graph_from_osm(
    osm_paths: list[Path],
    land_mask_fn: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None,
) -> tuple[list[dict], list[dict], list[str]]:
    """Build a connected topological road graph from one or more OSM JSON files.

    Merges degree-2 OSM way splits (same layer & similar width) into continuous edges,
    preserves grade separation via (osm_node, layer), and retains short blocks.
    Returns (nodes, edges, street_names).
    """
    from pyproj import Transformer

    ll: dict[int, tuple[float, float]] = {}
    ways: dict[int, dict] = {}
    for p in osm_paths:
        osm = json.loads(p.read_text(encoding="utf-8"))
        for e in osm.get("elements", ()):
            if e["type"] == "node":
                ll[e["id"]] = (float(e["lat"]), float(e["lon"]))
            elif e["type"] == "way":
                hw = e.get("tags", {}).get("highway", "")
                if hw in RIDE_HIGHWAYS and len(e.get("nodes", ())) >= 2:
                    ways[e["id"]] = e

    # Project all referenced OSM nodes to EPSG:32610
    used_nids = sorted({n for w in ways.values() for n in w["nodes"] if n in ll})
    if not used_nids:
        return [], [], ["(unnamed)"]
    tr = Transformer.from_crs(4326, 32610, always_xy=True)
    lons = [ll[n][1] for n in used_nids]
    lats = [ll[n][0] for n in used_nids]
    xs, ys = tr.transform(lons, lats)
    nxy = {n: (float(x), float(y)) for n, x, y in zip(used_nids, xs, ys)}

    if land_mask_fn is not None:
        ok = land_mask_fn(np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64))
        land_ok = {n: bool(v) for n, v in zip(used_nids, ok)}
    else:
        land_ok = {n: True for n in used_nids}

    # Build elementary undirected segments keyed by (osm_node, layer)
    seg_adj: dict[tuple[int, int], list[int]] = defaultdict(list)
    raw_segs: list[dict] = []
    seen_pairs: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    streets: list[str] = ["(unnamed)"]
    st_idx: dict[str, int] = {"(unnamed)": 0}

    for wid in sorted(ways):
        w = ways[wid]
        tags = w.get("tags", {})
        lay = way_layer(tags)
        wm = way_width_m(tags)
        nm = tags.get("name", "") or "(unnamed)"
        if nm not in st_idx:
            st_idx[nm] = len(streets)
            streets.append(nm)
        sid = st_idx[nm]
        wnodes = [n for n in w["nodes"] if n in nxy]
        for a, b in zip(wnodes, wnodes[1:]):
            if a == b or (not land_ok[a] and not land_ok[b]):
                continue
            ka, kb = (a, lay), (b, lay)
            pkey = (ka, kb) if ka < kb else (kb, ka)
            if pkey in seen_pairs:
                continue
            ax, ay = nxy[a]
            bx, by = nxy[b]
            if math.hypot(bx - ax, by - ay) < 0.25:
                continue
            seen_pairs.add(pkey)
            idx = len(raw_segs)
            raw_segs.append({"a": ka, "b": kb, "width": wm, "layer": lay, "street": sid, "way": wid})
            seg_adj[ka].append(idx)
            seg_adj[kb].append(idx)

    # Identify topological anchor nodes (degree != 2, or degree-2 with width/layer jump)
    is_anchor: set[tuple[int, int]] = set()
    for nk, sids in seg_adj.items():
        if len(sids) != 2:
            is_anchor.add(nk)
        else:
            s0, s1 = raw_segs[sids[0]], raw_segs[sids[1]]
            if abs(s0["width"] - s1["width"]) > 1.5 or s0["layer"] != s1["layer"]:
                is_anchor.add(nk)

    # Chain degree-2 segments into continuous edges
    visited_seg: set[int] = set()
    node_id_map: dict[tuple[int, int], int] = {}
    nodes: list[dict] = []
    edges: list[dict] = []

    def get_node(nk: tuple[int, int]) -> int:
        if nk not in node_id_map:
            node_id_map[nk] = len(nodes)
            x, y = nxy[nk[0]]
            nodes.append({"key": nk, "x": x, "y": y, "layer": nk[1]})
        return node_id_map[nk]

    def trace_chain(start_nk: tuple[int, int], first_sid: int):
        chain_pts = [nxy[start_nk[0]]]
        cur_nk = start_nk
        cur_sid = first_sid
        seg0 = raw_segs[cur_sid]
        width_sum = 0.0
        len_sum = 0.0
        street_id = seg0["street"]
        layer = seg0["layer"]
        ways_set = [seg0["way"]]
        while True:
            visited_seg.add(cur_sid)
            s = raw_segs[cur_sid]
            nxt_nk = s["b"] if s["a"] == cur_nk else s["a"]
            p0 = nxy[cur_nk[0]]
            p1 = nxy[nxt_nk[0]]
            sl = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            width_sum += s["width"] * sl
            len_sum += sl
            chain_pts.append(p1)
            cur_nk = nxt_nk
            if cur_nk in is_anchor or cur_nk == start_nk:
                break
            nxt_cands = [i for i in seg_adj[cur_nk] if i not in visited_seg]
            if not nxt_cands:
                break
            cur_sid = nxt_cands[0]
            if raw_segs[cur_sid]["way"] != ways_set[-1]:
                ways_set.append(raw_segs[cur_sid]["way"])
        na = get_node(start_nk)
        nb = get_node(cur_nk)
        if len(chain_pts) >= 2 and len_sum >= 0.5:
            edges.append({
                "nodeA": na,
                "nodeB": nb,
                "xy": np.asarray(chain_pts, dtype=np.float64),
                "width": width_sum / max(len_sum, 1e-6),
                "layer": layer,
                "street": street_id,
                "ways": ways_set,
            })

    for nk in sorted(is_anchor):
        for sid in seg_adj[nk]:
            if sid not in visited_seg:
                trace_chain(nk, sid)
    # Any closed loops with all degree-2 nodes
    for sid, s in enumerate(raw_segs):
        if sid not in visited_seg:
            is_anchor.add(s["a"])
            trace_chain(s["a"], sid)

    return nodes, edges, streets


def build_graph_from_static_fallback(static_path: Path, exclude_xy: np.ndarray | None = None, exclude_r: float = 15.0):
    """Reconstruct connected road edges from live_static.npz when osm_city.json is unavailable offline."""
    from scipy.spatial import cKDTree
    from .. import city

    P = city.load_static(static_path)
    z_col = P["zone"]
    ids = np.nonzero(z_col == 2)[0]
    way, seg, side = P["way"][ids], P["seg"][ids], P["side"][ids]
    cx_list, cy_list, way_list, seg_list, st_list = [], [], [], [], []
    i, n = 0, len(ids)
    while i < n:
        j = i + 1
        if side[i] == 0 and j < n and side[j] == 1 and way[j] == way[i] and seg[j] == seg[i]:
            a, b = ids[i], ids[j]
            cx_list.append(0.5 * (float(P["x"][a]) + float(P["x"][b])))
            cy_list.append(0.5 * (float(P["y"][a]) + float(P["y"][b])))
            way_list.append(int(way[i]))
            seg_list.append(int(seg[i]))
            st_list.append(int(P["street"][a]))
            i += 2
        else:
            a = ids[i]
            cx_list.append(float(P["x"][a]))
            cy_list.append(float(P["y"][a]))
            way_list.append(int(way[i]))
            seg_list.append(int(seg[i]))
            st_list.append(int(P["street"][a]))
            i += 1

    cx = np.asarray(cx_list, dtype=np.float64)
    cy = np.asarray(cy_list, dtype=np.float64)
    way_a = np.asarray(way_list, dtype=np.int64)
    seg_a = np.asarray(seg_list, dtype=np.int32)
    st_a = np.asarray(st_list, dtype=np.int32)

    ex_tree = cKDTree(exclude_xy) if exclude_xy is not None and len(exclude_xy) else None

    brk = np.ones(len(cx), dtype=bool)
    if len(cx) > 1:
        d = np.hypot(np.diff(cx), np.diff(cy))
        same = (way_a[1:] == way_a[:-1]) & (seg_a[1:] - seg_a[:-1] >= 0) & (seg_a[1:] - seg_a[:-1] <= 1) & (d <= 45.0)
        brk[1:] = ~same
    starts = np.nonzero(brk)[0]
    ends = np.append(starts[1:], len(cx))

    # Snap endpoints within 14 m into shared intersection nodes
    SNAP_M = 14.0
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    nodes: list[dict] = []

    def snap_node(x: float, y: float) -> int:
        gx, gy = int(math.floor(x / SNAP_M)), int(math.floor(y / SNAP_M))
        best, bd = -1, SNAP_M * SNAP_M
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for nid in grid.get((gx + dx, gy + dy), ()):
                    nd = nodes[nid]
                    d2 = (nd["x"] - x) ** 2 + (nd["y"] - y) ** 2
                    if d2 < bd:
                        bd = d2
                        best = nid
        if best >= 0:
            return best
        nid = len(nodes)
        nodes.append({"key": (nid, 0), "x": x, "y": y, "layer": 0})
        grid[(gx, gy)].append(nid)
        return nid

    streets = [str(s) or "(unnamed)" for s in P["street_names"]]
    edges: list[dict] = []
    for s0, s1 in zip(starts, ends):
        if s1 - s0 < 2:
            continue
        pts = np.column_stack([cx[s0:s1], cy[s0:s1]])
        if ex_tree is not None:
            mid = pts[len(pts) // 2]
            if float(ex_tree.query(mid)[0]) < exclude_r:
                continue
        na = snap_node(float(pts[0, 0]), float(pts[0, 1]))
        nb = snap_node(float(pts[-1, 0]), float(pts[-1, 1]))
        pts[0] = [nodes[na]["x"], nodes[na]["y"]]
        pts[-1] = [nodes[nb]["x"], nodes[nb]["y"]]
        edges.append({
            "nodeA": na,
            "nodeB": nb,
            "xy": pts,
            "width": 7.5,
            "layer": 0,
            "street": int(st_a[s0]),
            "ways": [int(way_a[s0])],
        })
    return nodes, edges, streets


# @sec: watertight junction & ribbon meshing
def _polyline_arclen(xy: np.ndarray) -> np.ndarray:
    """Cumulative arc length along an (N, 2) polyline."""
    if len(xy) < 2:
        return np.zeros(len(xy), dtype=np.float64)
    d = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    return np.concatenate([[0.0], np.cumsum(d)])


def _interp_polyline(xy: np.ndarray, arc: np.ndarray, s_query: np.ndarray) -> np.ndarray:
    """Linearly interpolate (x, y) coordinates along a polyline at arc lengths s_query."""
    qx = np.interp(s_query, arc, xy[:, 0])
    qy = np.interp(s_query, arc, xy[:, 1])
    return np.column_stack([qx, qy])


def _simplify_xy_2d(xy: np.ndarray, eps: float = HORIZ_EPS_M) -> np.ndarray:
    """Simplify a 2D polyline with Douglas-Peucker, dropping collinear OSM nodes while preserving curves."""
    n = len(xy)
    if n <= 2:
        return xy
    keep = np.zeros(n, dtype=bool)
    keep[0] = True
    keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a <= 1:
            continue
        ab = xy[b] - xy[a]
        lab = math.hypot(float(ab[0]), float(ab[1]))
        if lab < 1e-6:
            continue
        ap = xy[a + 1:b] - xy[a]
        perp = np.abs(ap[:, 0] * ab[1] - ap[:, 1] * ab[0]) / lab
        idx = int(np.argmax(perp))
        if float(perp[idx]) > eps:
            mid = a + 1 + idx
            keep[mid] = True
            stack.append((a, mid))
            stack.append((mid, b))
    return xy[keep]


def _round_sharp_corners(xy: np.ndarray, min_deg: float = 22.0, corner_r: float = 4.5) -> np.ndarray:
    """Bevel sharp interior polyline bends (> min_deg) with a 2-segment arc so ribbons never pinch."""
    n = len(xy)
    if n < 3:
        return xy
    cos_thr = math.cos(math.radians(min_deg))
    out = [xy[0]]
    for i in range(1, n - 1):
        p0, p1, p2 = xy[i - 1], xy[i], xy[i + 1]
        v1 = p1 - p0
        v2 = p2 - p1
        l1 = math.hypot(float(v1[0]), float(v1[1]))
        l2 = math.hypot(float(v2[0]), float(v2[1]))
        if l1 < 0.5 or l2 < 0.5:
            out.append(p1)
            continue
        u1 = v1 / l1
        u2 = v2 / l2
        dot = max(-1.0, min(1.0, float(u1[0] * u2[0] + u1[1] * u2[1])))
        if dot < cos_thr:
            d = min(corner_r, 0.35 * l1, 0.35 * l2)
            a = p1 - u1 * d
            b = p1 + u2 * d
            mid = 0.25 * a + 0.5 * p1 + 0.25 * b
            out.append(a)
            out.append(mid)
            out.append(b)
        else:
            out.append(p1)
    out.append(xy[-1])
    return np.asarray(out, dtype=np.float64)


def _subdivide_for_terrain(
    xy_trim: np.ndarray,
    t_arc: np.ndarray,
    s_vals: np.ndarray,
    z_prof_s: np.ndarray,
    z_prof_z: np.ndarray,
    t60_fn: Callable[[np.ndarray, np.ndarray], np.ndarray],
    clear_m: float = TERRAIN_CLEAR_M,
    max_span: float = 36.0,
    min_span: float = 7.0,
) -> np.ndarray:
    """Adaptively subdivide only where the 60 m terrain mesh bulges near/above the road segment."""
    if len(s_vals) < 2:
        return s_vals
    pts = list(map(float, s_vals))
    for _ in range(4):
        mids_s = []
        insert_after = []
        for i in range(len(pts) - 1):
            ds = pts[i + 1] - pts[i]
            if ds > min_span:
                mids_s.append(0.5 * (pts[i] + pts[i + 1]))
                insert_after.append(i)
        if not mids_s:
            break
        ms = np.asarray(mids_s, dtype=np.float64)
        mxy = _interp_polyline(xy_trim, t_arc, ms)
        mt60 = t60_fn(mxy[:, 0], mxy[:, 1])
        mz = np.interp(ms, z_prof_s, z_prof_z)
        new_pts = [pts[0]]
        added = False
        mi = 0
        for i in range(len(pts) - 1):
            ds = pts[i + 1] - pts[i]
            if mi < len(insert_after) and insert_after[mi] == i:
                if ds > max_span or float(mt60[mi]) + clear_m > float(mz[mi]) + 0.08:
                    new_pts.append(float(ms[mi]))
                    added = True
                mi += 1
            new_pts.append(pts[i + 1])
        pts = new_pts
        if not added:
            break
    return np.asarray(pts, dtype=np.float64)


def _trim_polyline(xy: np.ndarray, s0: float, s1: float) -> np.ndarray:
    """Trim an (N, 2) polyline to arc-length range [s0, s1]."""
    arc = _polyline_arclen(xy)
    total = float(arc[-1])
    if total < 1e-6 or s1 <= s0 + 0.1:
        mid = 0.5 * (xy[0] + xy[-1])
        return np.vstack([mid, mid])
    s0 = max(0.0, min(s0, total))
    s1 = max(s0, min(s1, total))
    p0 = _interp_polyline(xy, arc, np.array([s0]))[0]
    p1 = _interp_polyline(xy, arc, np.array([s1]))[0]
    mid_mask = (arc > s0 + 1e-3) & (arc < s1 - 1e-3)
    pts = [p0]
    if np.any(mid_mask):
        pts.extend(xy[mid_mask])
    pts.append(p1)
    return np.asarray(pts, dtype=np.float64)


def _line_intersect_2d(p1: np.ndarray, d1: np.ndarray, p2: np.ndarray, d2: np.ndarray) -> tuple[float, float] | None:
    """Solve p1 + t1*d1 = p2 + t2*d2 in 2D. Returns (t1, t2) or None if nearly parallel."""
    det = float(d1[0] * d2[1] - d1[1] * d2[0])
    if abs(det) < 1e-4:
        return None
    dx, dy = float(p2[0] - p1[0]), float(p2[1] - p1[1])
    t1 = (dx * d2[1] - dy * d2[0]) / det
    t2 = (dx * d1[1] - dy * d1[0]) / det
    return t1, t2


def _earclip_2d(pts: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Simple 2D ear-clipping triangulation for a CCW polygon (fallback for non-convex junctions)."""
    n = len(pts)
    if n < 3:
        return []
    # Ensure CCW winding
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    idxs = list(range(n)) if area >= 0 else list(reversed(range(n)))
    tris: list[tuple[int, int, int]] = []

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def in_tri(p, a, b, c):
        return cross(a, b, p) >= -1e-9 and cross(b, c, p) >= -1e-9 and cross(c, a, p) >= -1e-9

    guard = 0
    while len(idxs) > 3 and guard < n * n:
        guard += 1
        clipped = False
        m = len(idxs)
        for i in range(m):
            ia, ib, ic = idxs[(i - 1) % m], idxs[i], idxs[(i + 1) % m]
            a, b, c = pts[ia], pts[ib], pts[ic]
            if cross(a, b, c) <= 1e-9:
                continue
            if any(in_tri(pts[k], a, b, c) for k in idxs if k not in (ia, ib, ic)):
                continue
            tris.append((ia, ib, ic))
            idxs.pop(i)
            clipped = True
            break
        if not clipped:
            break
    if len(idxs) == 3:
        tris.append((idxs[0], idxs[1], idxs[2]))
    return tris


def compute_junction_setbacks(
    node_xy: tuple[float, float],
    arms: list[dict],
) -> tuple[dict[tuple[int, int], float], list[dict]]:
    """Compute per-arm setback distances and CCW-ordered curb corners for a degree >= 3 junction.

    Each arm dict has: edge_idx, end_side (0 for nodeA, 1 for nodeB), dir (unit 2D outward),
    width (m), edge_len (m).
    Returns (setbacks[(edge_idx, end_side)] -> m, sorted_arms_with_corners).
    """
    arms_sorted = sorted(arms, key=lambda a: math.atan2(a["dir"][1], a["dir"][0]))
    k = len(arms_sorted)
    setbacks: dict[tuple[int, int], float] = {}
    for a in arms_sorted:
        setbacks[(a["edge_idx"], a["end_side"])] = min(0.5 * a["width"], 0.35 * a["edge_len"])

    nx, ny = node_xy
    origin = np.array([nx, ny], dtype=np.float64)
    corners = []
    for i in range(k):
        a1 = arms_sorted[i]
        a2 = arms_sorted[(i + 1) % k]
        d1, d2 = a1["dir"], a2["dir"]
        # Left normal of arm i (points CCW toward arm i+1)
        n1_left = np.array([-d1[1], d1[0]], dtype=np.float64)
        # Right normal of arm i+1 (points CW toward arm i)
        n2_right = np.array([d2[1], -d2[0]], dtype=np.float64)
        hw1 = 0.5 * a1["width"]
        hw2 = 0.5 * a2["width"]
        p1 = origin + n1_left * hw1
        p2 = origin + n2_right * hw2
        hit = _line_intersect_2d(p1, d1, p2, d2)
        if hit is not None:
            t1, t2 = hit
            max_t1 = min(16.0, 0.38 * a1["edge_len"])
            max_t2 = min(16.0, 0.38 * a2["edge_len"])
            t1 = max(hw1 * 0.5, min(max_t1, t1))
            t2 = max(hw2 * 0.5, min(max_t2, t2))
            corner_pt = 0.5 * ((p1 + d1 * t1) + (p2 + d2 * t2))
        else:
            t1 = min(hw1, 0.35 * a1["edge_len"])
            t2 = min(hw2, 0.35 * a2["edge_len"])
            corner_pt = 0.5 * (p1 + p2)
        k1 = (a1["edge_idx"], a1["end_side"])
        k2 = (a2["edge_idx"], a2["end_side"])
        setbacks[k1] = max(setbacks[k1], min(t1 + 1.5, 0.40 * a1["edge_len"]))
        setbacks[k2] = max(setbacks[k2], min(t2 + 1.5, 0.40 * a2["edge_len"]))
        corners.append({"corner": corner_pt, "a1": a1, "a2": a2})

    return setbacks, arms_sorted


# @sec: simulation sample binding
class SampleBinder:
    """Binds arbitrary SF (x, y) road vertices to bracketing live simulation columns (ida, idb, t).

    Column space matches live/day0..2.u8: [0 .. S1) = district ribbons.bin samples,
    [S1 .. S1 + S2) = city ribbons_city.bin samples.
    """

    def __init__(self, sample_xy: np.ndarray, S1: int):
        from scipy.spatial import cKDTree

        self.xy = np.asarray(sample_xy, dtype=np.float64)
        self.S1 = int(S1)
        self.ST = len(self.xy)
        self.tree = cKDTree(self.xy) if self.ST > 0 else None

    @classmethod
    def from_ribbons(cls, ribbons_a: Path, ribbons_city: Path | None = None) -> "SampleBinder":
        """Build a SampleBinder from the shipped ribbons.bin and optional ribbons_city.bin."""
        from . import ribbons as live_rib

        A = live_rib.read_rib(ribbons_a)
        S1 = int(A["S"])
        pos_a = A["pos"].reshape(-1, 3)
        xy_a = np.column_stack([pos_a[:, 0] + X0, Y0 - pos_a[:, 2]])
        if ribbons_city is not None and ribbons_city.exists():
            B = live_rib.read_rib(ribbons_city)
            pos_b = B["pos"].reshape(-1, 3)
            xy_b = np.column_stack([pos_b[:, 0] + X0, Y0 - pos_b[:, 2]])
            xy = np.vstack([xy_a, xy_b])
        else:
            xy = xy_a
        return cls(xy, S1)

    def bind_points(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Bind an (N, 2) array of world EPSG:32610 points to (ida, idb, t) for smooth blending."""
        n = len(xy)
        if self.tree is None or n == 0:
            return np.zeros(n, np.int32), np.zeros(n, np.int32), np.zeros(n, np.float32)
        k = min(2, self.ST)
        dists, idxs = self.tree.query(xy, k=k)
        if k == 1:
            return idxs.astype(np.int32), idxs.astype(np.int32), np.zeros(n, np.float32)
        d0 = np.maximum(dists[:, 0], 1e-3)
        d1 = np.maximum(dists[:, 1], 1e-3)
        # Inverse-distance blend weight toward second nearest sample when both are close
        t = np.where(d1 < 60.0, d0 / (d0 + d1), 0.0).astype(np.float32)
        return idxs[:, 0].astype(np.int32), idxs[:, 1].astype(np.int32), t


# @sec: mesh assembly & ROAD2 serialization
def build_road_mesh(
    nodes: list[dict],
    edges: list[dict],
    dem_fn: Callable[[np.ndarray, np.ndarray], np.ndarray],
    t20_fn: Callable[[np.ndarray, np.ndarray], np.ndarray] | None,
    binder: SampleBinder | None,
    x0: float = X0,
    y0: float = Y0,
) -> tuple[dict, dict]:
    """Assemble watertight continuous road ribbons, intersection polygons, and game road graph.

    Returns (mesh_dict, graph_dict) ready for pack_road2().
    """
    if t20_fn is None:
        t20_fn = dem_fn

    # Map incident arms per node
    node_arms: dict[int, list[dict]] = defaultdict(list)
    edge_lens = np.zeros(len(edges), dtype=np.float64)
    for ei, e in enumerate(edges):
        xy = e["xy"]
        arc = _polyline_arclen(xy)
        el = float(arc[-1])
        edge_lens[ei] = el
        if el < 0.25:
            continue
        d_start = xy[1] - xy[0]
        l0 = math.hypot(float(d_start[0]), float(d_start[1])) or 1.0
        d_end = xy[-2] - xy[-1]
        l1 = math.hypot(float(d_end[0]), float(d_end[1])) or 1.0
        node_arms[e["nodeA"]].append({
            "edge_idx": ei, "end_side": 0, "dir": d_start / l0,
            "width": e["width"], "edge_len": el,
        })
        node_arms[e["nodeB"]].append({
            "edge_idx": ei, "end_side": 1, "dir": d_end / l1,
            "width": e["width"], "edge_len": el,
        })

    # Fit clamped intersection pad planes & setbacks for all degree >= 3 nodes
    junc_planes: dict[int, tuple[float, float, float]] = {}
    setbacks: dict[tuple[int, int], float] = {}
    sorted_node_arms: dict[int, list[dict]] = {}
    node_z = np.zeros(len(nodes), dtype=np.float64)

    if nodes:
        nxy_arr = np.array([[nd["x"], nd["y"]] for nd in nodes], dtype=np.float64)
        node_z = dem_fn(nxy_arr[:, 0], nxy_arr[:, 1])
        node_t20 = t20_fn(nxy_arr[:, 0], nxy_arr[:, 1])
        node_z = np.maximum(node_z, node_t20 + TERRAIN_CLEAR_M)

    for ni, nd in enumerate(nodes):
        arms = node_arms.get(ni, [])
        if len(arms) >= 3:
            z0, gx, gy = fit_junction_plane(nd["x"], nd["y"], dem_fn)
            z0 = max(z0, float(node_z[ni]))
            junc_planes[ni] = (z0, gx, gy)
            node_z[ni] = z0
            sb, s_arms = compute_junction_setbacks((nd["x"], nd["y"]), arms)
            setbacks.update(sb)
            sorted_node_arms[ni] = s_arms
        else:
            sorted_node_arms[ni] = sorted(arms, key=lambda a: math.atan2(a["dir"][1], a["dir"][0]))

    # Growable compact top-roadbed vertex lists (28 B/vert)
    v_pos: list[tuple[float, float, float]] = []
    v_nrm: list[tuple[int, int, int, int]] = []
    v_samples: list[tuple[float, float]] = []
    v_attr: list[tuple[int, int]] = []
    tile_street_idx: dict[tuple[int, int], list[int]] = defaultdict(list)

    # Position-only close-up skirt vertex/index lists (12 B/vert)
    sk_pos: list[tuple[float, float, float]] = []
    tile_skirt_idx: dict[tuple[int, int], list[int]] = defaultdict(list)

    # Track exact left/right end vertex indices at each arm end so junctions share vertices watertightly
    arm_end_verts: dict[tuple[int, int], tuple[int, int, np.ndarray, np.ndarray]] = {}

    # Game road-graph polyline buffers
    g_edge_meta: list[tuple[int, int, int, int]] = []
    g_edge_props: list[tuple[float, float, float, float]] = []
    g_poly_pts: list[tuple[float, float, float, float]] = []
    g_poly_sample: list[int] = []

    def add_vert(
        wx: float, wy: float, wz: float,
        nx_: float, ny_: float, nz_: float,
        ida: int, across: float, cls_: float, t_: float,
        idb: int, arc_m: float = 0.0, mask_: float = 0.0, rt_idx: float = -1.0,
    ) -> int:
        vi = len(v_pos)
        v_pos.append((wx - x0, wz, -(wy - y0)))
        v_nrm.append((
            int(round(max(-1.0, min(1.0, nx_)) * 127.0)),
            int(round(max(-1.0, min(1.0, ny_)) * 127.0)),
            int(round(max(-1.0, min(1.0, nz_)) * 127.0)),
            0,
        ))
        v_samples.append((float(ida), float(idb)))
        v_attr.append((
            int(round(max(-1.0, min(1.0, across)) * 32767.0)),
            int(round(max(0.0, min(1.0, t_)) * 32767.0)),
        ))
        return vi

    def add_skirt_vert(wx: float, wy: float, wz: float) -> int:
        vi = len(sk_pos)
        sk_pos.append((wx - x0, wz, -(wy - y0)))
        return vi

    def tile_key(wx: float, wy: float) -> tuple[int, int]:
        return (int(math.floor((wx - x0) / TILE_M)), int(math.floor(-(wy - y0) / TILE_M)))

    # 1. Build continuous ribbon strips for every edge
    for ei, e in enumerate(edges):
        el = float(edge_lens[ei])
        if el < 0.25:
            continue
        sb0 = setbacks.get((ei, 0), 0.0)
        sb1 = setbacks.get((ei, 1), 0.0)
        if sb0 + sb1 >= el - 0.5:
            # Very short link between two big junctions: shrink setbacks proportionally
            scale = max(0.0, (el - 0.5) / max(sb0 + sb1, 1e-6))
            sb0 *= scale
            sb1 *= scale
        trimmed = _round_sharp_corners(_simplify_xy_2d(_trim_polyline(e["xy"], sb0, el - sb1), eps=HORIZ_EPS_M))
        t_arc = _polyline_arclen(trimmed)
        t_len = float(t_arc[-1])
        if t_len < 0.2:
            continue

        # Dense DEM sampling along trimmed centerline
        n_dem = max(2, int(math.ceil(t_len / DEM_STEP_M)) + 1)
        s_dem = np.linspace(0.0, t_len, n_dem)
        xy_dem = _interp_polyline(trimmed, t_arc, s_dem)
        z_dem = dem_fn(xy_dem[:, 0], xy_dem[:, 1])

        # Pin start/end elevations to adjacent intersection pad planes if present
        na, nb = e["nodeA"], e["nodeB"]
        if na in junc_planes:
            z0, gx, gy = junc_planes[na]
            z_dem[0] = z0 + gx * (xy_dem[0, 0] - nodes[na]["x"]) + gy * (xy_dem[0, 1] - nodes[na]["y"])
        if nb in junc_planes:
            z0, gx, gy = junc_planes[nb]
            z_dem[-1] = z0 + gx * (xy_dem[-1, 0] - nodes[nb]["x"]) + gy * (xy_dem[-1, 1] - nodes[nb]["y"])

        # SF vertical profile: piecewise-linear sustained grades + parabolic car lips
        knot_idx = fit_piecewise_linear(s_dem, z_dem, eps=PL_EPS_M)
        s_prof, z_prof, g_prof = apply_parabolic_lips(s_dem[knot_idx], z_dem[knot_idx])

        # Merge horizontal curve vertices and adaptively subdivide only where 60 m terrain bulges near road
        s_merged = _subdivide_for_terrain(
            trimmed, t_arc, np.unique(np.concatenate([s_prof, t_arc])), s_prof, z_prof, t20_fn,
        )
        xy_final = _interp_polyline(trimmed, t_arc, s_merged)
        z_final = np.interp(s_merged, s_prof, z_prof)
        g_final = np.interp(s_merged, s_prof, g_prof)
        m = len(s_merged)
        if m < 2:
            continue

        # Compute per-segment tangents & mitered left normals (no pinching on sharp turns)
        sdx = np.diff(xy_final[:, 0])
        sdy = np.diff(xy_final[:, 1])
        sdl = np.maximum(np.hypot(sdx, sdy), 1e-6)
        stx, sty = sdx / sdl, sdy / sdl
        snx, sny = -sty, stx

        tx = np.zeros(m, dtype=np.float64)
        ty = np.zeros(m, dtype=np.float64)
        mnx = np.zeros(m, dtype=np.float64)
        mny = np.zeros(m, dtype=np.float64)
        tx[0], ty[0], mnx[0], mny[0] = stx[0], sty[0], snx[0], sny[0]
        tx[-1], ty[-1], mnx[-1], mny[-1] = stx[-1], sty[-1], snx[-1], sny[-1]
        for q in range(1, m - 1):
            ux = snx[q - 1] + snx[q]
            uy = sny[q - 1] + sny[q]
            ul = math.hypot(float(ux), float(uy))
            if ul < 1e-3:
                mnx[q], mny[q] = snx[q], sny[q]
            else:
                sc = min(MAX_MITER, 2.0 / ul)
                mnx[q], mny[q] = (ux / ul) * sc, (uy / ul) * sc
            txq, tyq = stx[q - 1] + stx[q], sty[q - 1] + sty[q]
            tlq = math.hypot(float(txq), float(tyq)) or 1.0
            tx[q], ty[q] = txq / tlq, tyq / tlq

        half_w = 0.5 * e["width"]
        left_xy = xy_final + np.column_stack([mnx * half_w, mny * half_w])
        right_xy = xy_final - np.column_stack([mnx * half_w, mny * half_w])

        zl_dem = dem_fn(left_xy[:, 0], left_xy[:, 1])
        zr_dem = dem_fn(right_xy[:, 0], right_xy[:, 1])
        zc_t60 = t20_fn(xy_final[:, 0], xy_final[:, 1])
        zl_t60 = t20_fn(left_xy[:, 0], left_xy[:, 1])
        zr_t60 = t20_fn(right_xy[:, 0], right_xy[:, 1])

        # Bind simulation samples along the strip for smooth color blending
        if binder is not None:
            ida_arr, idb_arr, t_arr = binder.bind_points(xy_final)
        else:
            ida_arr = np.zeros(m, np.int32)
            idb_arr = np.zeros(m, np.int32)
            t_arr = np.zeros(m, np.float32)

        # Record game road-graph centerline polyline
        poly_start = len(g_poly_pts)
        max_grade = float(np.max(np.abs(g_final))) if len(g_final) else 0.0

        base_v = len(v_pos)
        yl_arr = np.zeros(m, dtype=np.float64)
        yr_arr = np.zeros(m, dtype=np.float64)
        for q in range(m):
            yl, yr = banked_edge_heights(
                float(z_final[q]),
                float(zl_dem[q]), float(zr_dem[q]),
                max(float(zl_t60[q]), float(zc_t60[q])),
                max(float(zr_t60[q]), float(zc_t60[q])),
                half_w,
            )
            yc = 0.5 * (yl + yr)
            # Pin junction boundary curb heights to junction pad plane so seam is watertight
            if q == 0 and na in junc_planes:
                z0, gx, gy = junc_planes[na]
                yl = max(z0 + gx * (left_xy[q, 0] - nodes[na]["x"]) + gy * (left_xy[q, 1] - nodes[na]["y"]), float(zl_t60[q]) + TERRAIN_CLEAR_M)
                yr = max(z0 + gx * (right_xy[q, 0] - nodes[na]["x"]) + gy * (right_xy[q, 1] - nodes[na]["y"]), float(zr_t60[q]) + TERRAIN_CLEAR_M)
                yc = 0.5 * (yl + yr)
            elif q == m - 1 and nb in junc_planes:
                z0, gx, gy = junc_planes[nb]
                yl = max(z0 + gx * (left_xy[q, 0] - nodes[nb]["x"]) + gy * (left_xy[q, 1] - nodes[nb]["y"]), float(zl_t60[q]) + TERRAIN_CLEAR_M)
                yr = max(z0 + gx * (right_xy[q, 0] - nodes[nb]["x"]) + gy * (right_xy[q, 1] - nodes[nb]["y"]), float(zr_t60[q]) + TERRAIN_CLEAR_M)
                yc = 0.5 * (yl + yr)
            yl_arr[q], yr_arr[q] = yl, yr

            # 3D surface normal from forward tangent + cross-slope
            slope_fwd = float(g_final[q])
            slope_lat = (yl - yr) / max(2.0 * half_w, 1.0)
            # Scene coords: (x, y_up, z = -north)
            t_vec = (float(tx[q]), slope_fwd, -float(ty[q]))
            ln_len = math.hypot(float(mnx[q]), float(mny[q])) or 1.0
            r_vec = (-float(mnx[q]) / ln_len, -slope_lat, float(mny[q]) / ln_len)
            # Normal = r_vec x t_vec (upward)
            nx3 = r_vec[1] * t_vec[2] - r_vec[2] * t_vec[1]
            ny3 = r_vec[2] * t_vec[0] - r_vec[0] * t_vec[2]
            nz3 = r_vec[0] * t_vec[1] - r_vec[1] * t_vec[0]
            if ny3 < 0:
                nx3, ny3, nz3 = -nx3, -ny3, -nz3
            nl3 = math.hypot(nx3, ny3, nz3) or 1.0
            nx3, ny3, nz3 = nx3 / nl3, ny3 / nl3, nz3 / nl3

            ida_q, idb_q, t_q = int(ida_arr[q]), int(idb_arr[q]), float(t_arr[q])
            arc_q = float(s_merged[q])
            add_vert(float(left_xy[q, 0]), float(left_xy[q, 1]), yl, nx3, ny3, nz3, ida_q, -1.0, 1.0, t_q, idb_q, arc_q)
            add_vert(float(right_xy[q, 0]), float(right_xy[q, 1]), yr, nx3, ny3, nz3, ida_q, 1.0, 1.0, t_q, idb_q, arc_q)

            g_poly_pts.append((float(xy_final[q, 0]) - x0, yc, -(float(xy_final[q, 1]) - y0), arc_q))
            g_poly_sample.append(ida_q)

        # Store exact boundary vertices for watertight junction stitching
        arm_end_verts[(ei, 0)] = (base_v, base_v + 1, left_xy[0], right_xy[0])
        end_v = base_v + 2 * (m - 1)
        arm_end_verts[(ei, 1)] = (end_v + 1, end_v, right_xy[-1], left_xy[-1])

        mid_xy = xy_final[m // 2]
        tk = tile_key(float(mid_xy[0]), float(mid_xy[1]))
        tlist = tile_street_idx[tk]
        for q in range(m - 1):
            v0 = base_v + 2 * q
            tlist.extend((v0, v0 + 1, v0 + 2, v0 + 1, v0 + 3, v0 + 2))

        # Emit per-segment, position-only curb/embankment skirts only where segment rises > SKIRT_THRESH_M above 60 m terrain
        sk_tlist = tile_skirt_idx[tk]
        prev_l_pair: int | None = None
        prev_r_pair: int | None = None
        for q in range(m - 1):
            if max(float(yl_arr[q] - zl_t60[q]), float(yl_arr[q + 1] - zl_t60[q + 1])) > SKIRT_THRESH_M:
                if prev_l_pair is None:
                    yb0 = min(float(yl_arr[q]) - 0.6, float(zl_t60[q]) - SKIRT_DROP_M)
                    v_q0 = add_skirt_vert(float(left_xy[q, 0]), float(left_xy[q, 1]), float(yl_arr[q]))
                    add_skirt_vert(float(left_xy[q, 0]), float(left_xy[q, 1]), yb0)
                else:
                    v_q0 = prev_l_pair
                yb1 = min(float(yl_arr[q + 1]) - 0.6, float(zl_t60[q + 1]) - SKIRT_DROP_M)
                v_q1 = add_skirt_vert(float(left_xy[q + 1, 0]), float(left_xy[q + 1, 1]), float(yl_arr[q + 1]))
                add_skirt_vert(float(left_xy[q + 1, 0]), float(left_xy[q + 1, 1]), yb1)
                sk_tlist.extend((v_q0, v_q1, v_q0 + 1, v_q0 + 1, v_q1, v_q1 + 1))
                prev_l_pair = v_q1
            else:
                prev_l_pair = None

            if max(float(yr_arr[q] - zr_t60[q]), float(yr_arr[q + 1] - zr_t60[q + 1])) > SKIRT_THRESH_M:
                if prev_r_pair is None:
                    yb0 = min(float(yr_arr[q]) - 0.6, float(zr_t60[q]) - SKIRT_DROP_M)
                    v_q0 = add_skirt_vert(float(right_xy[q, 0]), float(right_xy[q, 1]), float(yr_arr[q]))
                    add_skirt_vert(float(right_xy[q, 0]), float(right_xy[q, 1]), yb0)
                else:
                    v_q0 = prev_r_pair
                yb1 = min(float(yr_arr[q + 1]) - 0.6, float(zr_t60[q + 1]) - SKIRT_DROP_M)
                v_q1 = add_skirt_vert(float(right_xy[q + 1, 0]), float(right_xy[q + 1, 1]), float(yr_arr[q + 1]))
                add_skirt_vert(float(right_xy[q + 1, 0]), float(right_xy[q + 1, 1]), yb1)
                sk_tlist.extend((v_q0, v_q0 + 1, v_q1, v_q0 + 1, v_q1 + 1, v_q1))
                prev_r_pair = v_q1
            else:
                prev_r_pair = None

        g_edge_meta.append((na, nb, poly_start, len(g_poly_pts) - poly_start))
        g_edge_props.append((float(e["width"]), t_len, max_grade, float(e["street"])))

    # 2. Build watertight intersection polygons for every degree >= 3 node
    n_junctions = 0
    for ni, (z0, gx, gy) in junc_planes.items():
        arms = [a for a in sorted_node_arms[ni] if (a["edge_idx"], a["end_side"]) in arm_end_verts]
        if len(arms) < 3:
            continue
        nd = nodes[ni]
        cx, cy = nd["x"], nd["y"]
        if binder is not None:
            c_ida, c_idb, c_t = binder.bind_points(np.array([[cx, cy]], dtype=np.float64))
            ida_c, idb_c, t_c = int(c_ida[0]), int(c_idb[0]), float(c_t[0])
        else:
            ida_c, idb_c, t_c = 0, 0, 0.0

        # Normal of the junction pad plane
        jn_len = math.hypot(gx, 1.0, gy) or 1.0
        jnx, jny, jnz = -gx / jn_len, 1.0 / jn_len, gy / jn_len
        c_v = add_vert(cx, cy, z0, jnx, jny, jnz, ida_c, 0.0, 1.0, t_c, idb_c, 0.0)

        # Build CCW perimeter ring sharing exact left/right vertices of each incident road strip
        ring_verts: list[int] = []
        ring_xy: list[tuple[float, float]] = []
        k = len(arms)
        for i in range(k):
            a1 = arms[i]
            a2 = arms[(i + 1) % k]
            v1_l, v1_r, xy1_l, xy1_r = arm_end_verts[(a1["edge_idx"], a1["end_side"])]
            v2_l, v2_r, xy2_l, xy2_r = arm_end_verts[(a2["edge_idx"], a2["end_side"])]
            # Walking CCW around the junction: arm i's right curb -> arm i's left curb -> fillet -> arm (i+1)'s right curb
            ring_verts.append(v1_r)
            ring_xy.append((float(xy1_r[0]), float(xy1_r[1])))
            ring_verts.append(v1_l)
            ring_xy.append((float(xy1_l[0]), float(xy1_l[1])))

            # Quadratic Bezier curb fillet between xy1_l and xy2_r
            if math.hypot(float(xy2_r[0] - xy1_l[0]), float(xy2_r[1] - xy1_l[1])) > 1.0:
                hit = _line_intersect_2d(xy1_l, -a1["dir"], xy2_r, -a2["dir"])
                if hit is not None and 0.0 < hit[0] < 14.0 and 0.0 < hit[1] < 14.0:
                    ctrl = xy1_l - a1["dir"] * hit[0]
                else:
                    ctrl = 0.5 * (xy1_l + xy2_r)
                for fp in range(1, FILLET_PTS + 1):
                    u = fp / (FILLET_PTS + 1)
                    om = 1.0 - u
                    fx = float(om * om * xy1_l[0] + 2.0 * om * u * ctrl[0] + u * u * xy2_r[0])
                    fy = float(om * om * xy1_l[1] + 2.0 * om * u * ctrl[1] + u * u * xy2_r[1])
                    fz = z0 + gx * (fx - cx) + gy * (fy - cy)
                    ft20 = float(t20_fn(np.array([fx]), np.array([fy]))[0])
                    fz = max(fz, ft20 + TERRAIN_CLEAR_M)
                    fv = add_vert(fx, fy, fz, jnx, jny, jnz, ida_c, 1.0, 1.0, t_c, idb_c, 0.0)
                    ring_verts.append(fv)
                    ring_xy.append((fx, fy))

        tk = tile_key(cx, cy)
        tlist = tile_street_idx[tk]
        # Fan triangulation around centroid (always clean for star-shaped intersections)
        nr = len(ring_verts)
        if nr >= 3:
            for r in range(nr):
                tlist.extend((c_v, ring_verts[r], ring_verts[(r + 1) % nr]))
            n_junctions += 1

    # 3. Pack spatially-tiled index buffers & tile bounding spheres
    pos_arr = np.asarray(v_pos, dtype=np.float32) if v_pos else np.zeros((0, 3), dtype=np.float32)
    nrm_arr = np.asarray(v_nrm, dtype=np.int8) if v_nrm else np.zeros((0, 4), dtype=np.int8)
    samples_arr = np.asarray(v_samples, dtype=np.float32) if v_samples else np.zeros((0, 2), dtype=np.float32)
    attr_arr = np.asarray(v_attr, dtype=np.int16) if v_attr else np.zeros((0, 2), dtype=np.int16)
    sk_pos_arr = np.asarray(sk_pos, dtype=np.float32) if sk_pos else np.zeros((0, 3), dtype=np.float32)

    street_idx_parts: list[np.ndarray] = []
    skirt_idx_parts: list[np.ndarray] = []
    tile_bounds: list[tuple[float, float, float, float]] = []
    tile_ranges: list[tuple[int, int, int, int]] = []
    skirt_tile_ranges: list[tuple[int, int]] = []
    cur_off = 0
    cur_sk_off = 0
    all_tiles = sorted(set(tile_street_idx) | set(tile_skirt_idx))
    for tk in all_tiles:
        idxs = np.asarray(tile_street_idx.get(tk, ()), dtype=np.uint32)
        sk_idxs = np.asarray(tile_skirt_idx.get(tk, ()), dtype=np.uint32)
        if len(idxs) == 0 and len(sk_idxs) == 0:
            continue
        pts = pos_arr[idxs] if len(idxs) > 0 else sk_pos_arr[sk_idxs]
        pmin = pts.min(axis=0)
        pmax = pts.max(axis=0)
        ctr = 0.5 * (pmin + pmax)
        rad = float(np.max(np.linalg.norm(pts - ctr, axis=1))) + 15.0
        tile_bounds.append((float(ctr[0]), float(ctr[1]), float(ctr[2]), rad))
        tile_ranges.append((cur_off, int(len(idxs)), 0, 0))
        skirt_tile_ranges.append((cur_sk_off, int(len(sk_idxs))))
        if len(idxs) > 0:
            street_idx_parts.append(idxs)
            cur_off += int(len(idxs))
        if len(sk_idxs) > 0:
            skirt_idx_parts.append(sk_idxs)
            cur_sk_off += int(len(sk_idxs))

    street_idx = np.concatenate(street_idx_parts) if street_idx_parts else np.zeros((0,), dtype=np.uint32)
    skirt_idx = np.concatenate(skirt_idx_parts) if skirt_idx_parts else np.zeros((0,), dtype=np.uint32)
    route_idx = np.zeros((0,), dtype=np.uint32)

    # 4. Pack compact game road graph
    node_pos = np.array(
        [[nd["x"] - x0, float(node_z[i]), -(nd["y"] - y0)] for i, nd in enumerate(nodes)],
        dtype=np.float32,
    ) if nodes else np.zeros((0, 3), dtype=np.float32)

    # Map valid edge index (0..len(g_edge_meta)-1) for node adjacency
    valid_edge_map: dict[int, int] = {}
    ge_i = 0
    for ei, e in enumerate(edges):
        if edge_lens[ei] >= 0.25 and ge_i < len(g_edge_meta):
            if g_edge_meta[ge_i][0] == e["nodeA"] and g_edge_meta[ge_i][1] == e["nodeB"]:
                valid_edge_map[ei] = ge_i
                ge_i += 1

    node_edge_span = np.zeros((len(nodes), 2), dtype=np.uint32)
    node_adj_list: list[int] = []
    for ni in range(len(nodes)):
        st_off = len(node_adj_list)
        for a in sorted_node_arms.get(ni, ()):
            if a["edge_idx"] in valid_edge_map:
                node_adj_list.append(valid_edge_map[a["edge_idx"]])
        node_edge_span[ni] = (st_off, len(node_adj_list) - st_off)

    mesh = {
        "pos": pos_arr,
        "nrm": nrm_arr,
        "aSamples": samples_arr,
        "aAttr": attr_arr,
        "tileBounds": np.asarray(tile_bounds, dtype=np.float32) if tile_bounds else np.zeros((0, 4), dtype=np.float32),
        "tileRanges": np.asarray(tile_ranges, dtype=np.uint32) if tile_ranges else np.zeros((0, 4), dtype=np.uint32),
        "streetIdx": street_idx,
        "routeIdx": route_idx,
        "skirtPos": sk_pos_arr,
        "skirtTileRanges": np.asarray(skirt_tile_ranges, dtype=np.uint32) if skirt_tile_ranges else np.zeros((0, 2), dtype=np.uint32),
        "skirtIdx": skirt_idx,
        "S1": binder.S1 if binder is not None else 0,
        "nJunctions": n_junctions,
    }
    graph = {
        "nodePos": node_pos,
        "nodeEdgeSpan": node_edge_span,
        "nodeAdj": np.asarray(node_adj_list, dtype=np.uint32),
        "edgeMeta": np.asarray(g_edge_meta, dtype=np.uint32) if g_edge_meta else np.zeros((0, 4), dtype=np.uint32),
        "edgeProps": np.asarray(g_edge_props, dtype=np.float32) if g_edge_props else np.zeros((0, 4), dtype=np.float32),
        "polyPts": np.asarray(g_poly_pts, dtype=np.float32) if g_poly_pts else np.zeros((0, 4), dtype=np.float32),
        "polySample": np.asarray(g_poly_sample, dtype=np.uint32) if g_poly_sample else np.zeros((0,), dtype=np.uint32),
    }
    return mesh, graph


def pack_road2(mesh: dict, graph: dict) -> bytes:
    """Serialize mesh and game road-graph dicts into a pre-baked ROAD2 binary buffer."""
    n_verts = len(mesh["pos"])
    n_sidx = len(mesh["streetIdx"])
    n_ridx = len(mesh["routeIdx"])
    n_tiles = len(mesh["tileBounds"])
    n_sk_verts = len(mesh["skirtPos"])
    n_sk_idx = len(mesh["skirtIdx"])
    n_nodes = len(graph["nodePos"])
    n_edges = len(graph["edgeMeta"])
    s1 = int(mesh.get("S1", 0))
    n_adj = len(graph["nodeAdj"])
    n_poly = len(graph["polyPts"])

    header = struct.pack(
        HEADER_FMT,
        MAGIC, VERSION, n_verts, n_sidx, n_ridx, n_tiles,
        n_sk_verts, n_sk_idx, n_nodes, n_edges, s1, n_adj, n_poly,
    )

    parts = [
        header,
        _pad4(mesh["pos"].astype("<f4", copy=False).tobytes()),
        _pad4(mesh["nrm"].astype("<i1", copy=False).tobytes()),
        _pad4(mesh["aSamples"].astype("<f4", copy=False).tobytes()),
        _pad4(mesh["aAttr"].astype("<i2", copy=False).tobytes()),
        _pad4(mesh["tileBounds"].astype("<f4", copy=False).tobytes()),
        _pad4(mesh["tileRanges"].astype("<u4", copy=False).tobytes()),
        _pad4(mesh["streetIdx"].astype("<u4", copy=False).tobytes()),
        _pad4(mesh["routeIdx"].astype("<u4", copy=False).tobytes()),
        _pad4(mesh["skirtPos"].astype("<f4", copy=False).tobytes()),
        _pad4(mesh["skirtTileRanges"].astype("<u4", copy=False).tobytes()),
        _pad4(mesh["skirtIdx"].astype("<u4", copy=False).tobytes()),
        _pad4(graph["nodePos"].astype("<f4", copy=False).tobytes()),
        _pad4(graph["nodeEdgeSpan"].astype("<u4", copy=False).tobytes()),
        _pad4(graph["nodeAdj"].astype("<u4", copy=False).tobytes()),
        _pad4(graph["edgeMeta"].astype("<u4", copy=False).tobytes()),
        _pad4(graph["edgeProps"].astype("<f4", copy=False).tobytes()),
        _pad4(graph["polyPts"].astype("<f4", copy=False).tobytes()),
        _pad4(graph["polySample"].astype("<u4", copy=False).tobytes()),
    ]
    return b"".join(parts)


def unpack_road2(blob: bytes) -> tuple[dict, dict]:
    """Parse a ROAD2 binary buffer back into (mesh_dict, graph_dict) for verification and tests."""
    (
        magic, ver, n_verts, n_sidx, n_ridx, n_tiles,
        n_sk_verts, n_sk_idx, n_nodes, n_edges, s1, n_adj, n_poly,
    ) = struct.unpack_from(HEADER_FMT, blob, 0)
    if magic != MAGIC or ver != VERSION:
        raise ValueError(f"Invalid ROAD2 header: magic=0x{magic:08x}, version={ver}")
    off = HEADER_BYTES

    def take(dt: str, shape: tuple[int, ...]) -> np.ndarray:
        nonlocal off
        count = int(math.prod(shape))
        arr = np.frombuffer(blob, dtype=dt, count=count, offset=off).reshape(shape)
        nbytes = count * np.dtype(dt).itemsize
        off += nbytes + (-nbytes % 4)
        return arr

    mesh = {
        "S1": s1,
        "pos": take("<f4", (n_verts, 3)),
        "nrm": take("<i1", (n_verts, 4)),
        "aSamples": take("<f4", (n_verts, 2)),
        "aAttr": take("<i2", (n_verts, 2)),
        "tileBounds": take("<f4", (n_tiles, 4)),
        "tileRanges": take("<u4", (n_tiles, 4)),
        "streetIdx": take("<u4", (n_sidx,)),
        "routeIdx": take("<u4", (n_ridx,)),
        "skirtPos": take("<f4", (n_sk_verts, 3)),
        "skirtTileRanges": take("<u4", (n_tiles, 2)),
        "skirtIdx": take("<u4", (n_sk_idx,)),
    }
    graph = {
        "nodePos": take("<f4", (n_nodes, 3)),
        "nodeEdgeSpan": take("<u4", (n_nodes, 2)),
        "nodeAdj": take("<u4", (n_adj,)),
        "edgeMeta": take("<u4", (n_edges, 4)),
        "edgeProps": take("<f4", (n_edges, 4)),
        "polyPts": take("<f4", (n_poly, 4)),
        "polySample": take("<u4", (n_poly,)),
    }
    return mesh, graph


# @sec: cli driver
def build_city_roadnet(
    out_dir: Path | None = None,
    osm_path: Path | None = None,
    fetch: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    """Build roadnet_city.bin and roadnet_meta.json for San Francisco."""
    t0 = time.time()
    dd = data_dir()
    raw = dd / "raw"
    grids = dd / "grids"
    live = dd / "live"
    out = Path(out_dir) if out_dir is not None else live
    out.mkdir(parents=True, exist_ok=True)

    # Terrain samplers (exact 60 m terrain.glb triangle plane + high-res 3DEP DEM)
    dem_path = raw / "dem_n38w123.tif"
    dem_fn = make_dem_sampler(dem_path)
    t20_fn, ter, spec = make_terrain60_sampler(grids, step=3)

    def land_mask(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        ci = np.floor((xs - float(spec["x0"])) / float(spec["cell_m"])).astype(np.int32)
        cj = np.floor((ys - float(spec["y0"])) / float(spec["cell_m"])).astype(np.int32)
        inside = (ci >= 0) & (cj >= 0) & (ci < ter.shape[0]) & (cj < ter.shape[1])
        ok = np.zeros(len(xs), dtype=bool)
        ok[inside] = ter[ci[inside], cj[inside]] > 0.5
        return ok

    # Load road graph (prefer osm_city.json; otherwise merge cached OSM zones + static city fallback)
    city_osm = osm_path if osm_path is not None else (raw / "osm_city.json")
    if fetch and not city_osm.exists():
        from .. import routes
        routes.fetch_bbox(CITY_BBOX, city_osm)

    if city_osm.exists():
        nodes, edges, streets = build_graph_from_osm([city_osm], land_mask_fn=land_mask)
        source = str(city_osm.name)
    else:
        cached = sorted(raw.glob("osm_*.json"))
        nodes, edges, streets = build_graph_from_osm(cached, land_mask_fn=land_mask) if cached else ([], [], ["(unnamed)"])
        static_npz = live / "live_static.npz"
        if static_npz.exists():
            ex_xy = np.array([[nd["x"], nd["y"]] for nd in nodes], dtype=np.float64) if nodes else None
            c_nodes, c_edges, c_streets = build_graph_from_static_fallback(static_npz, exclude_xy=ex_xy)
            n_off = len(nodes)
            st_off = len(streets)
            nodes.extend(c_nodes)
            for ce in c_edges:
                ce["nodeA"] += n_off
                ce["nodeB"] += n_off
                ce["street"] += st_off
                edges.append(ce)
            streets.extend(c_streets)
        source = "hybrid_osm_and_static"

    # Bind simulation sample columns
    repo_root = Path(__file__).resolve().parents[2]
    ribbons_a = repo_root / "site" / "v3" / "data" / "ribbons.bin"
    ribbons_city = live / "ribbons_city.bin"
    binder = SampleBinder.from_ribbons(ribbons_a, ribbons_city) if ribbons_a.exists() else None

    mesh, graph = build_road_mesh(nodes, edges, dem_fn, t20_fn, binder)
    blob = pack_road2(mesh, graph)
    (out / "roadnet_city.bin").write_bytes(blob)

    meta = {
        "version": VERSION,
        "source": source,
        "nVerts": int(len(mesh["pos"])),
        "nStreetIdx": int(len(mesh["streetIdx"])),
        "nTopTris": int(len(mesh["streetIdx"]) // 3),
        "nSkirtVerts": int(len(mesh["skirtPos"])),
        "nSkirtIdx": int(len(mesh["skirtIdx"])),
        "nSkirtTris": int(len(mesh["skirtIdx"]) // 3),
        "nRouteIdx": int(len(mesh["routeIdx"])),
        "nTiles": int(len(mesh["tileBounds"])),
        "nNodes": int(len(graph["nodePos"])),
        "nEdges": int(len(graph["edgeMeta"])),
        "nJunctions": int(mesh["nJunctions"]),
        "S1": int(mesh["S1"]),
        "bytes": len(blob),
        "streets": streets,
        "seconds": round(time.time() - t0, 2),
    }
    (out / "roadnet_meta.json").write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    log(json.dumps({k: v for k, v in meta.items() if k != "streets"}))
    return meta


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Build pre-baked ROAD1 city-wide SF road ribbon mesh.")
    ap.add_argument("--out", type=Path, default=None, help="Output directory (default: data/live)")
    ap.add_argument("--osm", type=Path, default=None, help="Explicit OSM JSON path (default: data/raw/osm_city.json)")
    ap.add_argument("--fetch", action="store_true", help="Fetch osm_city.json from Overpass if missing")
    args = ap.parse_args()
    build_city_roadnet(out_dir=args.out, osm_path=args.osm, fetch=args.fetch)
