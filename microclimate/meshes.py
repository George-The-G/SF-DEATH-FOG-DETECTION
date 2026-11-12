"""M1 part 3: viewer meshes. Local metres from one origin (stored in viewer/meta.json). Terrain grid at 40 m, buildings merged into one mesh."""
import json
from pathlib import Path

import numpy as np
import trimesh

from .geometry import grid_spec, load_buildings


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


def buildings_mesh(spec, ter, geoms, heights, min_h=15.0, simplify=3.0):
    """Extruded footprints from ground to roof. Skips tiny buildings to keep the file small."""
    c, x0, y0 = spec["cell_m"], spec["x0"], spec["y0"]
    meshes = []
    for g, h in zip(geoms, heights):
        if h < min_h or g.is_empty:
            continue
        g = g.simplify(simplify)
        polys = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
        cx, cy = g.centroid.x, g.centroid.y
        i, j = int((cx - x0) // c), int((cy - y0) // c)
        if not (0 <= i < ter.shape[0] and 0 <= j < ter.shape[1]):
            continue
        base = float(ter[i, j])
        for p in polys:
            if p.is_empty or p.area < 150:
                continue
            try:
                m = trimesh.creation.extrude_polygon(p, h + 3.0)
            except Exception:  # noqa
                continue
            v = m.vertices.copy()
            v = np.column_stack([v[:, 0] - x0, v[:, 2] + base - 3.0, -(v[:, 1] - y0)])
            meshes.append(trimesh.Trimesh(v, m.faces, process=False))
    return trimesh.util.concatenate(meshes)


def build(cfg, raw_dir, grids_dir, out_dir):
    spec = grid_spec(cfg)
    ter = np.load(Path(grids_dir) / "terrain_20m.npy")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tm = terrain_mesh(spec, ter)
    tm.export(out / "terrain.glb")
    geoms, h = load_buildings(Path(raw_dir) / "bldg.geojson")
    bm = buildings_mesh(spec, ter, geoms, h)
    bm.export(out / "buildings.glb")
    meta = {"x0": spec["x0"], "y0": spec["y0"], "cell_m": spec["cell_m"], "nx": spec["nx"], "ny": spec["ny"],
            "axes": "three.js: x = east metres from x0, y = up (elevation), z = -(north metres from y0)"}
    json.dump(meta, open(out / "meta.json", "w"), indent=1)
    return {"terrain_kb": round((out / "terrain.glb").stat().st_size / 1024), "buildings_kb": round((out / "buildings.glb").stat().st_size / 1024),
            "building_triangles": int(len(bm.faces))}


