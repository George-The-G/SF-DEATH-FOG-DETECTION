"""M1 part 3: viewer meshes. Local metres from one origin (stored in site/v3/data/meta.json).

terrain.glb: terrain grid at 60 m. buildings.bin: EVERY footprint of >= MIN_AREA m2 packed compactly (see pack_buildings):
near-rectangular footprints as oriented boxes (drawn as instanced boxes by the viewer), complex or very large footprints
as simplified true polygons (extruded by the viewer). Building bases are sampled from the terrain MESH that the viewer draws,
so they sit on it.
"""
import json
import struct
from pathlib import Path

import numpy as np
import shapely
import trimesh
from shapely.geometry import Polygon

from .geometry import grid_spec, load_buildings

MIN_AREA = 15.0     # m2; drops only the smallest sheds
MIN_H = 3.0         # m; floor for display height
RECT_MIN = 0.7      # rectangularity (area / min rotated rectangle) below this -> true polygon
BOX_MAX_AREA = 1500.0
XY_Q = 0.3          # m per uint16 step for positions
H_Q = 0.1           # m per uint16 step for heights / bases
WD_Q = 0.5          # m per uint8 step for box width / depth
SKIRT = 0.3         # m the base is sunk below the lowest sampled ground


def terrain_mesh(spec, ter, step=3):
    c = spec["cell_m"] * step
    t = ter[::step, ::step]
    nx, ny = t.shape
    xs, ys = np.arange(nx) * c, np.arange(ny) * c
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    V = np.column_stack([X.ravel(), t.ravel(), -Y.ravel()])  # three.js: x east, y up, z south
    idx = np.arange(nx * ny).reshape(nx, ny)
    a, b, d, e = idx[:-1, :-1].ravel(), idx[1:, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel()
    F = np.vstack([np.column_stack([a, b, d]), np.column_stack([b, e, d])])
    return trimesh.Trimesh(V, F, process=False)


def mesh_height(spec, ter, x, y, step=3):
    """Height of the terrain MESH (same triangulation as terrain_mesh) at local metres x east, y north."""
    t = ter[::step, ::step].astype(np.float64)
    c = spec["cell_m"] * step
    fx, fy = np.asarray(x) / c, np.asarray(y) / c
    i = np.clip(np.floor(fx).astype(int), 0, t.shape[0] - 2)
    j = np.clip(np.floor(fy).astype(int), 0, t.shape[1] - 2)
    u, v = np.clip(fx - i, 0, 1), np.clip(fy - j, 0, 1)
    a, b, d, e = t[i, j], t[i + 1, j], t[i, j + 1], t[i + 1, j + 1]
    return np.where(u + v <= 1, a + u * (b - a) + v * (d - a), e + (1 - u) * (d - e) + (1 - v) * (b - e))


def _pad4(b):
    return b + b"\0" * (-len(b) % 4)


def pack_buildings(spec, ter, geoms, heights):
    """Pack all buildings into buildings.bin bytes plus a stats dict.

    Layout (little endian), every array padded to a multiple of 4 bytes:
      header: 'BLD1', uint32 nBox, uint32 nPoly, uint32 nPolyVerts, float32 xyQ, float32 hQ, float32 wdQ
      boxes:  uint16 cx[nBox], uint16 cn[nBox] (centre east / north, local metres / xyQ), uint16 base[nBox] (ground bottom, / hQ),
              uint16 hgt[nBox] (bottom to roof, / hQ), uint8 w[nBox], uint8 d[nBox] (/ wdQ, w along the local x axis),
              uint8 ang[nBox] (x axis angle from east, counter-clockwise, in units of pi/256)
      polys:  uint8 nv[nPoly], uint16 base[nPoly], uint16 hgt[nPoly], uint16 verts[2*nPolyVerts] (east, north per vertex)
    Scene frame: x = east, z = -north, y up.
    """
    x0, y0 = spec["x0"], spec["y0"]
    parts, src = shapely.get_parts(geoms, return_index=True)
    ok = shapely.get_type_id(parts) == 3
    parts, src = parts[ok], src[ok]
    hgt = np.maximum(np.asarray(heights, dtype=np.float64)[src], MIN_H)
    # to local metres
    parts = shapely.transform(parts, lambda xy: xy - np.array([x0, y0]))
    area = shapely.area(parts)
    keep = area >= MIN_AREA
    parts, hgt, area = parts[keep], hgt[keep], area[keep]
    mrr = shapely.minimum_rotated_rectangle(parts)
    marea = np.maximum(shapely.area(mrr), 1e-6)
    rect = area / marea
    good = shapely.get_type_id(mrr) == 3
    rc = np.zeros((len(parts), 5, 2))
    rc[good] = shapely.get_coordinates(mrr[good]).reshape(-1, 5, 2)
    e1, e2 = rc[:, 1] - rc[:, 0], rc[:, 2] - rc[:, 1]
    w, d = np.hypot(*e1.T), np.hypot(*e2.T)
    ang = np.mod(np.arctan2(e1[:, 1], e1[:, 0]), np.pi)
    ctr = rc[:, :4].mean(axis=1)
    inside = (ctr[:, 0] > 0) & (ctr[:, 1] > 0) & (ctr[:, 0] < 65000 * XY_Q) & (ctr[:, 1] < 65000 * XY_Q)
    is_box = good & (rect >= RECT_MIN) & (area <= BOX_MAX_AREA) & (w < 255 * WD_Q) & (d < 255 * WD_Q) & (w >= 0.5) & (d >= 0.5) & inside
    is_poly = ~is_box & inside

    # ---- boxes
    bi = np.where(is_box)[0]
    pts = np.concatenate([ctr[bi, None, :], rc[bi, :4]], axis=1)  # centre + 4 corners
    gh = mesh_height(spec, ter, pts[..., 0], pts[..., 1])
    ground_c = gh[:, 0]
    base = np.maximum(gh.min(axis=1) - SKIRT, 0.0)
    roof = ground_c + hgt[bi]
    bh = np.maximum(roof - base, 1.0)
    # sort spatially (500 m tiles, then rows) so neighbours are adjacent (helps gzip and lets the viewer chunk cheaply)
    key = np.lexsort((ctr[bi, 0] // 100, ctr[bi, 1] // 100, ctr[bi, 0] // 1000, ctr[bi, 1] // 1000))
    bi, ctr_b, base, bh, w_b, d_b, ang_b = bi[key], ctr[bi][key], base[key], bh[key], w[bi][key], d[bi][key], ang[bi][key]
    nb = len(bi)
    u16 = lambda a: np.clip(np.round(a), 0, 65535).astype("<u2")
    u8 = lambda a: np.clip(np.round(a), 0, 255).astype("u1")
    out = [b"BLD1", struct.pack("<III", nb, 0, 0), struct.pack("<fff", XY_Q, H_Q, WD_Q)]
    box_arrays = [u16(ctr_b[:, 0] / XY_Q), u16(ctr_b[:, 1] / XY_Q), u16(base / H_Q), u16(bh / H_Q),
                  u8(w_b / WD_Q), u8(d_b / WD_Q), u8(ang_b / np.pi * 256)]

    # ---- polygons
    nv, pbase, phgt, verts = [], [], [], []
    for gi in np.where(is_poly)[0]:
        g = Polygon(parts[gi].exterior)
        s = g.simplify(1.0)
        tol = 1.0
        while (s.is_empty or s.geom_type != "Polygon" or len(s.exterior.coords) - 1 > 250) and tol < 8:
            tol *= 2
            s = g.simplify(tol)
        if s.is_empty or s.geom_type != "Polygon" or s.area < MIN_AREA * 0.5:
            continue
        c = np.array(s.exterior.coords)[:-1]
        if len(c) < 3 or c.min() < 0 or c.max() > 65000 * XY_Q:
            continue
        gv = mesh_height(spec, ter, c[:, 0], c[:, 1])
        cc = s.centroid
        gc = float(mesh_height(spec, ter, cc.x, cc.y))
        b = max(float(gv.min()) - SKIRT, 0.0)
        nv.append(len(c)); pbase.append(b); phgt.append(max(gc + hgt[gi] - b, 1.0)); verts.append(c)
    npoly = len(nv)
    nvert = int(sum(nv))
    out[1] = struct.pack("<III", nb, npoly, nvert)
    poly_arrays = [u8(nv), u16(np.array(pbase) / H_Q), u16(np.array(phgt) / H_Q),
                   u16(np.concatenate(verts).ravel() / XY_Q) if npoly else np.zeros(0, "<u2")]
    blob = b"".join(_pad4(x) for x in out) + b"".join(_pad4(a.tobytes()) for a in box_arrays + poly_arrays)
    stats = {"source_footprints": int(len(geoms)), "parts": int(len(ok)), "dropped_small": int((~keep).sum()),
             "boxes": int(nb), "polygons": int(npoly), "polygon_vertices": nvert,
             "drawn_fraction": round((nb + npoly) / len(geoms), 4), "min_area_m2": MIN_AREA,
             "bytes": len(blob)}
    return blob, stats


def build_buildings(raw_dir, grids_dir, out_dir, spec=None, meta_path=None):
    out = Path(out_dir)
    ter = np.load(Path(grids_dir) / "terrain_20m.npy")
    meta = json.load(open(out / "meta.json"))
    spec = {"x0": meta["x0"], "y0": meta["y0"], "cell_m": meta["cell_m"]}
    geoms, h = load_buildings(Path(raw_dir) / "bldg.geojson")
    blob, stats = pack_buildings(spec, ter, geoms, h)
    (out / "buildings.bin").write_bytes(blob)
    meta["buildings"] = {"file": "buildings.bin", "format": "BLD1", **{k: stats[k] for k in ("boxes", "polygons", "polygon_vertices", "min_area_m2")}}
    json.dump(meta, open(out / "meta.json", "w"), indent=1)
    return stats


def build(cfg, raw_dir, grids_dir, out_dir):
    spec = grid_spec(cfg)
    ter = np.load(Path(grids_dir) / "terrain_20m.npy")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    terrain_mesh(spec, ter).export(out / "terrain.glb")
    meta = {"x0": spec["x0"], "y0": spec["y0"], "cell_m": spec["cell_m"], "nx": spec["nx"], "ny": spec["ny"],
            "axes": "three.js: x = east metres from x0, y = up (elevation), z = -(north metres from y0)"}
    json.dump(meta, open(out / "meta.json", "w"), indent=1)
    stats = build_buildings(raw_dir, grids_dir, out)
    return {"terrain_kb": round((out / "terrain.glb").stat().st_size / 1024), "buildings": stats}
