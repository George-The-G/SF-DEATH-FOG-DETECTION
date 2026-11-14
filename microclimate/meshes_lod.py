"""Plan B: building LOD data built offline from site/v3/data/buildings.bin (BLD1), which stays byte-identical.

B1  report:  per 1 km tile, hidden box side walls and triangle counts, near vs block-merged far (docs/perf/bld_lod_report.json)
B2  mask:    buildings_mask.bin  'BLDM', uint32 nBox (== BLD1 nBox), uint8[nBox] (padded to 4). Bit set = that side wall is
             hidden and may be collapsed. Bits follow the viewer's unit box face order in buildBuildings():
             bit0 +z, bit1 -z, bit2 +x, bit3 -x, in the box's own frame (x axis at `ang` ccw from east, z = -north-ish:
             local +z points to map (sin a, -cos a)).
B3  far mesh: buildings_far.bin 'BLDF', uint32 nRecords, then per record (tile may be split in several records so that
             vertex counts fit uint16):  int32 tileKey, uint32 nVert, uint32 nIdx, float32 originE, float32 originN,
             float32 posQ, float32 hQ, then uint16 pos[3*nVert] (east, north, up; east/north = (m - origin)/posQ, up = m/hQ),
             uint8 tint[nVert] (0..255 = the viewer's height tint t), uint16 idx[nIdx]; each array padded to 4 bytes.
             Scene frame is the same as BLD1 (viewer: x = east, z = -north, y up). Vertices are shared between wall and roof, so
             normals are not stored: the viewer must use flatShading.  Tile key = floor(east/1000)*1000 + floor(north/1000).

Only decodes BLD1, so it needs neither raw/bldg.geojson nor the terrain grid. Deterministic: no randomness, fixed ordering.
CLI:  python -m microclimate meshes-lod [--tile KEY] [--check] [--out DIR] [--report PATH] [--band M --simplify M --min-part M2 --gap M]
"""
import json
import struct
import sys
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon

XY_Q, H_Q = 0.3, 0.1      # same quantisation as BLD1
TILE = 1000.0
BORDER = 30.0             # m of read-only neighbour ring around a tile
BUF_WALL = 0.4            # m, neighbour footprints are grown by this before the wall test
COVER = 0.9               # fraction of a wall that must be covered
TOP_SLACK = 0.5           # neighbour top >= own top - slack
BAND = 6.0                # m height bands for far merge
SIMPLIFY = 2.0            # m
MIN_PART = 30.0           # m2
GAP = 1.0                 # m, closing buffer
MAX_VERT = 65000          # per far record

FACE_BITS = ("+z", "-z", "+x", "-x")


@dataclass
class Bld:
    """Decoded BLD1. Boxes: centre, size, angle (rad), base and top (m). Polys: list of rings, base, top."""
    cx: np.ndarray; cy: np.ndarray; w: np.ndarray; d: np.ndarray; ang: np.ndarray
    base: np.ndarray; top: np.ndarray
    rings: list; pbase: np.ndarray; ptop: np.ndarray

    @property
    def nbox(self):
        return len(self.cx)


def decode(buf):
    if buf[:4] != b"BLD1":
        raise ValueError("not a BLD1 file")
    nb, npl, nvt = struct.unpack_from("<III", buf, 4)
    xyq, hq, wdq = struct.unpack_from("<fff", buf, 16)
    o = 28

    def take(dt, n):
        nonlocal o
        a = np.frombuffer(buf, dtype=dt, count=n, offset=o)
        o += (a.nbytes + 3) // 4 * 4
        return a
    cx, cn, bb, bh = (take("<u2", nb).astype(np.float64) for _ in range(4))
    bw, bd, ba = (take("u1", nb).astype(np.float64) for _ in range(3))
    pnv = take("u1", npl).astype(int)
    pb, ph = take("<u2", npl).astype(np.float64), take("<u2", npl).astype(np.float64)
    pv = take("<u2", nvt * 2).astype(np.float64).reshape(-1, 2) * xyq
    rings, i = [], 0
    for n in pnv:
        rings.append(pv[i:i + n]); i += n
    base = bb * hq
    return Bld(cx * xyq, cn * xyq, bw * wdq, bd * wdq, ba * np.pi / 256, base, base + bh * hq,
               rings, pb * hq, pb * hq + ph * hq)


def box_axes(b):
    c, s = np.cos(b.ang), np.sin(b.ang)
    ex = np.column_stack([c, s])
    ez = np.column_stack([s, -c])
    return ex, ez


def box_polys(b):
    ex, ez = box_axes(b)
    ctr = np.column_stack([b.cx, b.cy])
    hw, hd = (b.w / 2)[:, None], (b.d / 2)[:, None]
    corners = np.stack([ctr - hw * ex - hd * ez, ctr + hw * ex - hd * ez, ctr + hw * ex + hd * ez, ctr - hw * ex + hd * ez], axis=1)
    return shapely.polygons(np.concatenate([corners, corners[:, :1]], axis=1))


def all_footprints(b):
    pts = box_polys(b)
    pl = [Polygon(r) for r in b.rings]
    polys = np.array(list(pts) + pl, dtype=object)
    return polys


def tri_count_poly(n, holes=0):
    return 2 * n + (n - 2)  # walls + roof fan; true polygons are drawn this way by the viewer (ear clip gives n-2)


def near_triangles(b):
    return 10 * b.nbox, int(sum(2 * len(r) + len(r) - 2 for r in b.rings))


# ------------------------------------------------------------------ B2 hidden walls
def wall_lines(b):
    ex, ez = box_axes(b)
    ctr = np.column_stack([b.cx, b.cy])
    hw, hd = (b.w / 2)[:, None], (b.d / 2)[:, None]
    L = []
    for sgn, ax, along, half_a, half_n in ((1, ez, ex, hw, hd), (-1, ez, ex, hw, hd), (1, ex, ez, hd, hw), (-1, ex, ez, hd, hw)):
        mid = ctr + sgn * half_n * ax
        a, c = mid - half_a * along, mid + half_a * along
        L.append(shapely.linestrings(np.stack([a, c], axis=1)))
    return L  # in FACE_BITS order


def hidden_mask(b, buf=BUF_WALL, cover=COVER, slack=TOP_SLACK):
    """uint8 per box, bit i set when face FACE_BITS[i] is hidden by taller-or-equal neighbours."""
    n = b.nbox
    mask = np.zeros(n, dtype=np.uint8)
    if n == 0:
        return mask
    foot = all_footprints(b)
    tops = np.concatenate([b.top, b.ptop])
    grown = shapely.buffer(foot, buf, join_style="mitre")
    tree = shapely.STRtree(grown)
    for bit, lines in enumerate(wall_lines(b)):
        length = shapely.length(lines)
        fi, pi = tree.query(lines, predicate="intersects")
        keep = (pi != fi) & (tops[pi] >= b.top[fi] - slack)
        fi, pi = fi[keep], pi[keep]
        if len(fi) == 0:
            continue
        order = np.argsort(fi, kind="stable")
        fi, pi = fi[order], pi[order]
        starts = np.flatnonzero(np.r_[True, fi[1:] != fi[:-1]])
        ends = np.r_[starts[1:], len(fi)]
        covered = np.zeros(n)
        for s, e in zip(starts, ends):
            f = fi[s]
            g = grown[pi[s]] if e - s == 1 else shapely.union_all(grown[pi[s:e]])
            covered[f] = shapely.length(shapely.intersection(lines[f], g))
        hid = covered >= cover * length
        mask[hid] |= np.uint8(1 << bit)
    return mask


def mask_bytes(mask):
    return b"BLDM" + struct.pack("<I", len(mask)) + mask.tobytes() + b"\0" * (-len(mask) % 4)


def read_mask(buf):
    if buf[:4] != b"BLDM":
        raise ValueError("not a BLDM file")
    n = struct.unpack_from("<I", buf, 4)[0]
    return np.frombuffer(buf, dtype="u1", count=n, offset=8)


# ------------------------------------------------------------------ B3 far mesh
def _tint(h):
    return np.power(np.clip((h - 6) / 90, 0, 1), 0.7)


def merge_tile(foot, base, top, band=BAND, simplify=SIMPLIFY, min_part=MIN_PART, gap=GAP):
    """foot: array of Polygons of one tile's owned buildings (plus ring handled by caller). Returns list of
    (Polygon, base, top) merged block parts."""
    out = []
    if len(foot) == 0:
        return out
    height = top - base
    band_id = np.floor(height / band).astype(int)
    for bid in np.unique(band_id):
        sel = np.flatnonzero(band_id == bid)
        u = shapely.union_all(shapely.buffer(foot[sel], gap, join_style="mitre"))
        u = shapely.buffer(u, -gap, join_style="mitre")
        parts = [p for p in shapely.get_parts(u) if p.geom_type == "Polygon" and not p.is_empty]
        if not parts:
            continue
        tree = shapely.STRtree(shapely.centroid(foot[sel]))
        area = shapely.area(foot[sel])
        for p in parts:
            p = p.simplify(simplify, preserve_topology=True)
            if p.is_empty or p.geom_type != "Polygon" or p.area < min_part:
                continue
            idx = tree.query(p, predicate="contains")
            if len(idx) == 0:
                idx = tree.query(p.buffer(gap), predicate="intersects")
            if len(idx) == 0:
                continue
            m = sel[idx]
            wgt = area[idx]
            out.append((p, float(base[m].min()), float((top[m] * wgt).sum() / wgt.sum())))
    return out


def _triangulate(poly):
    """Roof triangles; falls back to the hole-less outline if GEOS cannot triangulate the polygon with its holes."""
    for cand in (poly, Polygon(poly.exterior), Polygon(poly.exterior).buffer(0)):
        try:
            return [t for t in shapely.get_parts(shapely.constrained_delaunay_triangles(cand)) if t.geom_type == "Polygon"]
        except shapely.errors.GEOSException:
            continue
    return []


def extrude(parts):
    """parts: [(Polygon, base, top)] -> (verts (n,3) e,n,up; tint (n,); idx (m,)).
    Vertices are shared: per ring n bottom + n top vertices; roof triangles reuse the top ones. The viewer must therefore
    use flat shading (flatShading: true) instead of stored normals."""
    V, T, I = [], [], []
    nv = 0
    for poly, b, t in parts:
        tn = _tint(t - b)
        top_ix = {}
        for r in [poly.exterior] + list(poly.interiors):
            c = np.array(r.coords)[:-1]
            n = len(c)
            if n < 3:
                continue
            for p in c:
                V.append((p[0], p[1], b)); T.append(tn)
            for p in c:
                V.append((p[0], p[1], t)); T.append(tn)
            for i in range(n):
                j = (i + 1) % n
                I += [nv + i, nv + j, nv + n + j, nv + i, nv + n + j, nv + n + i]
            for i, p in enumerate(c):
                top_ix[(round(p[0], 6), round(p[1], 6))] = nv + n + i
            nv += 2 * n
        for tri in _triangulate(poly):
            ids = []
            for p in np.array(tri.exterior.coords)[:3]:
                k = (round(p[0], 6), round(p[1], 6))
                if k not in top_ix:
                    V.append((p[0], p[1], t)); T.append(tn); top_ix[k] = nv; nv += 1
                ids.append(top_ix[k])
            I += ids
    return np.array(V).reshape(-1, 3), np.array(T), np.array(I, dtype=np.int64)


def pack_record(key, V, T, I, origin):
    pos = np.empty((len(V), 3), dtype="<u2")
    pos[:, 0] = np.clip(np.round((V[:, 0] - origin[0]) / XY_Q), 0, 65535)
    pos[:, 1] = np.clip(np.round((V[:, 1] - origin[1]) / XY_Q), 0, 65535)
    pos[:, 2] = np.clip(np.round(V[:, 2] / H_Q), 0, 65535)
    tint = np.clip(np.round(T * 255), 0, 255).astype("u1")
    pad = lambda x: x + b"\0" * (-len(x) % 4)
    hdr = struct.pack("<iIIffff", key, len(V), len(I), origin[0], origin[1], XY_Q, H_Q)
    return hdr + pad(pos.tobytes()) + pad(tint.tobytes()) + pad(I.astype("<u2").tobytes())


def split_records(V, T, I, limit=MAX_VERT):
    """Split indexed mesh into chunks with <= limit vertices (triangles stay whole)."""
    if len(V) <= limit:
        return [(V, T, I)]
    out, tri = [], I.reshape(-1, 3)
    start = 0
    # triangles are emitted part by part so vertex ranges are contiguous: cut on triangle boundaries
    while start < len(tri):
        lo = tri[start:].min()
        end = start
        hi = lo
        while end < len(tri) and max(hi, tri[end].max()) - lo < limit:
            hi = max(hi, tri[end].max()); end += 1
        sel = tri[start:end] - lo
        out.append((V[lo:hi + 1], T[lo:hi + 1], sel.ravel()))
        start = end
    return out


_G = {}


def _init(path):
    b = decode(Path(path).read_bytes())
    foot = all_footprints(b)
    _G.update(b=b, foot=foot, base=np.concatenate([b.base, b.pbase]), top=np.concatenate([b.top, b.ptop]))
    ctr = shapely.centroid(foot)
    xy = shapely.get_coordinates(ctr)
    _G["xy"] = xy
    _G["bounds"] = shapely.bounds(foot)


def tile_key(x, y):
    return int(np.floor(x / TILE) * 1000 + np.floor(y / TILE))


def tile_job(args):
    key, kw = args
    xy = _G["xy"]
    tx = np.floor(xy[:, 0] / TILE).astype(int)
    ty = np.floor(xy[:, 1] / TILE).astype(int)
    own = np.flatnonzero(tx * 1000 + ty == key)
    foot, base, top = _G["foot"], _G["base"], _G["top"]
    parts = merge_tile(foot[own], base[own], top[own], **kw)
    V, T, I = extrude(parts)
    b = _G["b"]
    nbox_own = int((own < b.nbox).sum())
    near = 10 * nbox_own
    for gi in own[own >= b.nbox]:
        n = len(b.rings[gi - b.nbox])
        near += 3 * n - 2
    recs = []
    if len(V):
        ox, oy = np.floor(V[:, 0].min() / TILE) * TILE, np.floor(V[:, 1].min() / TILE) * TILE
        for v, t, i in split_records(V, T, I):
            recs.append(pack_record(key, v, t, i, (ox, oy)))
    return {"tile": key, "buildings": int(len(own)), "boxes": nbox_own, "near_tris": int(near),
            "far_parts": len(parts), "far_tris": int(len(I) // 3), "far_verts": int(len(V)), "records": recs}


def far_bytes(recs):
    return b"BLDF" + struct.pack("<I", len(recs)) + b"".join(recs)


def build(src, out_dir, only=None, procs=None, kw=None):
    kw = kw or {}
    src = Path(src)
    out_dir = Path(out_dir)
    buf = src.read_bytes()
    b = decode(buf)
    mask = hidden_mask(b)
    xy = np.column_stack([np.r_[b.cx, [r.mean(0)[0] for r in b.rings]], np.r_[b.cy, [r.mean(0)[1] for r in b.rings]]])
    keys = sorted({tile_key(x, y) for x, y in xy})
    if only is not None:
        keys = [k for k in keys if k == only]
    with Pool(procs, initializer=_init, initargs=(str(src),)) as pool:
        res = pool.map(tile_job, [(k, kw) for k in keys], chunksize=1)
    recs = [r for t in res for r in t.pop("records")]
    # hidden walls per tile
    bt = np.array([tile_key(x, y) for x, y in xy[:b.nbox]])
    tiles = {t["tile"]: t for t in res}
    for t in res:
        sel = bt == t["tile"]
        t["hidden_faces"] = int(sum(bin(int(m)).count("1") for m in mask[sel]))
        t["near_tris_after_mask"] = t["near_tris"] - 2 * t["hidden_faces"]
    tot = lambda k: int(sum(t[k] for t in res))
    hid = tot("hidden_faces")
    box_faces = 4 * b.nbox
    report = {"source": str(src), "source_bytes": len(buf), "boxes": b.nbox, "polygons": len(b.rings), "tiles": len(res),
              "params": {"wall_buffer_m": BUF_WALL, "cover": COVER, "top_slack_m": TOP_SLACK, "band_m": BAND,
                         "simplify_m": SIMPLIFY, "min_part_m2": MIN_PART, "gap_m": GAP, **kw},
              "near_tris": tot("near_tris"), "hidden_side_faces": hid,
              "hidden_fraction_of_box_side_faces": round(hid / max(box_faces, 1), 4),
              "near_tris_after_mask": tot("near_tris_after_mask"),
              "far_tris": tot("far_tris"),
              "far_vs_near_tris": round(tot("far_tris") / max(tot("near_tris"), 1), 4),
              "far_file_bytes": len(far_bytes(recs)), "mask_file_bytes": len(mask_bytes(mask)),
              "per_tile": res}
    return b, mask, recs, report


def check(b, mask, recs, far_path=None):
    """Light sanity checks on the outputs; returns a list of problems."""
    bad = []
    if len(mask) != b.nbox:
        bad.append("mask length != nBox")
    for r in recs:
        key, nv, ni = struct.unpack_from("<iII", r, 0)
        if nv > 65535 or ni % 3:
            bad.append(f"tile {key}: bad counts {nv} {ni}")
        o = 28 + (nv * 6 + 3) // 4 * 4 + (nv + 3) // 4 * 4
        idx = np.frombuffer(r, "<u2", ni, o)
        if ni and idx.max() >= nv:
            bad.append(f"tile {key}: index out of range")
    return bad


def run(argv, root=Path(".")):
    only = None
    out = root / "site/v3/data"
    rep = root / "docs/perf/bld_lod_report.json"
    a = list(argv)
    do_check = "--check" in a
    if "--tile" in a:
        only = int(a[a.index("--tile") + 1])
    if "--out" in a:
        out = Path(a[a.index("--out") + 1])
    if "--report" in a:
        rep = Path(a[a.index("--report") + 1])
    src = root / "site/v3/data/buildings.bin"
    kw = {}
    for flag, name in (("--band", "band"), ("--simplify", "simplify"), ("--min-part", "min_part"), ("--gap", "gap")):
        if flag in a:
            kw[name] = float(a[a.index(flag) + 1])
    b, mask, recs, report = build(src, out, only, kw=kw)
    out.mkdir(parents=True, exist_ok=True)
    rep.parent.mkdir(parents=True, exist_ok=True)
    if only is None:
        (out / "buildings_mask.bin").write_bytes(mask_bytes(mask))
        (out / "buildings_far.bin").write_bytes(far_bytes(recs))
    rep.write_text(json.dumps(report, indent=1))
    problems = check(b, mask, recs) if do_check else []
    print(json.dumps({k: v for k, v in report.items() if k != "per_tile"}, indent=1))
    if problems:
        print("PROBLEMS:", *problems, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
