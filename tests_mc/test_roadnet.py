"""Unit tests for the city-wide SF road ribbon & intersection mesh builder (microclimate.live.roadnet)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from microclimate.live import roadnet


def test_potrero_hill_sharp_incline_with_parabolic_car_lips():
    """Flat pad -> sudden 20% sustained incline -> flat pad preserves steep slope while rounding lips."""
    s = np.linspace(0.0, 140.0, 281)  # 0.5 m spacing
    # 0..20 m flat at z=10, 20..120 m steep +20% incline to z=30, 120..140 m flat at z=30
    z = np.where(s < 20.0, 10.0, np.where(s <= 120.0, 10.0 + 0.20 * (s - 20.0), 30.0))

    knots = roadnet.fit_piecewise_linear(s, z, eps=0.25)
    assert len(knots) == 4, f"expected 4 knots (start, foot, crest, end), got {len(knots)}"

    sp, zp, gp = roadnet.apply_parabolic_lips(s[knots], z[knots])
    # Sustained middle of the hill (50..90 m) must hold the true 20% grade
    mid_z_50 = float(np.interp(50.0, sp, zp))
    mid_z_90 = float(np.interp(90.0, sp, zp))
    sustained_grade = (mid_z_90 - mid_z_50) / 40.0
    assert abs(sustained_grade - 0.20) < 0.002

    # At the foot (s=20) and crest (s=120), the parabolic car-lip rounds the corner smoothly
    z_at_foot = float(np.interp(20.0, sp, zp))
    z_at_crest = float(np.interp(120.0, sp, zp))
    assert z_at_foot > 10.02, "sag lip at foot of hill should lift slightly for smooth car transition"
    assert z_at_crest < 29.98, "crest lip at top of hill should round slightly for car clearance"

    # Grades must transition monotonically from ~0 -> ~0.20 -> ~0 without wild overshoot
    assert float(np.min(gp)) >= -0.01
    assert float(np.max(gp)) <= 0.205


def test_junction_plane_clamps_steep_hillside_grade():
    """Intersection pad plane caps cross-grade at JUNC_MAX_GRADE even on a 25% hillside."""
    def steep_dem(xs, ys):
        return 50.0 + 0.25 * xs + 0.10 * ys

    z0, gx, gy = roadnet.fit_junction_plane(0.0, 0.0, steep_dem, radius_m=8.0)
    assert abs(z0 - 50.0) < 1e-6
    assert math.hypot(gx, gy) <= roadnet.JUNC_MAX_GRADE + 1e-6


def test_banked_edge_heights_clamped_and_above_terrain():
    """Cross-slope camber is clamped and both curbs stay at least TERRAIN_CLEAR_M above terrain20."""
    yl, yr = roadnet.banked_edge_heights(
        zc=20.0,
        z_left_dem=24.0,   # huge 40% cross-slope on hillside
        z_right_dem=16.0,
        z_left_t20=20.5,
        z_right_t20=18.0,
        half_w=5.0,
    )
    assert abs((yl - yr) / 10.0) <= roadnet.MAX_CROSS_SLOPE + 1e-6
    assert yl >= 20.5 + roadnet.TERRAIN_CLEAR_M - 1e-6
    assert yr >= 18.0 + roadnet.TERRAIN_CLEAR_M - 1e-6


def test_osm_degree2_chaining_and_bridge_layer_separation(tmp_path: Path):
    """Degree-2 way splits merge into one continuous edge; bridge overpasses stay grade-separated."""
    osm = {
        "elements": [
            {"type": "node", "id": 1, "lat": 37.7600, "lon": -122.4000},
            {"type": "node", "id": 2, "lat": 37.7600, "lon": -122.3990},
            {"type": "node", "id": 3, "lat": 37.7600, "lon": -122.3980},
            {"type": "node", "id": 4, "lat": 37.7590, "lon": -122.3990},
            {"type": "node", "id": 5, "lat": 37.7610, "lon": -122.3990},
            # Two separate OSM ways meeting at degree-2 node 2 (same layer=0, same residential width)
            {"type": "way", "id": 100, "nodes": [1, 2], "tags": {"highway": "residential", "name": "20th Street"}},
            {"type": "way", "id": 101, "nodes": [2, 3], "tags": {"highway": "residential", "name": "20th Street"}},
            # Bridge overpass crossing through node 2 on layer=1 -> must NOT intersect layer=0
            {"type": "way", "id": 102, "nodes": [4, 2, 5], "tags": {"highway": "secondary", "bridge": "yes", "layer": "1", "name": "Overpass"}},
        ]
    }
    osm_file = tmp_path / "test_osm.json"
    osm_file.write_text(json.dumps(osm), encoding="utf-8")

    nodes, edges, streets = roadnet.build_graph_from_osm([osm_file])
    assert len(edges) == 2, f"expected 2 continuous edges (chained 20th St + Overpass), got {len(edges)}"
    assert "20th Street" in streets
    assert "Overpass" in streets
    layers = sorted(e["layer"] for e in edges)
    assert layers == [0, 1]


def test_watertight_4way_intersection_and_road2_roundtrip():
    """4-way intersection produces watertight shared vertices, upward normals, and bit-exact ROAD2 round-trip."""
    x0, y0 = roadnet.X0, roadnet.Y0
    # 4-way cross centered at (x0+100, y0+100) with 80 m arms
    cx, cy = x0 + 100.0, y0 + 100.0
    nodes = [
        {"key": (0, 0), "x": cx, "y": cy, "layer": 0},
        {"key": (1, 0), "x": cx + 80.0, "y": cy, "layer": 0},
        {"key": (2, 0), "x": cx, "y": cy + 80.0, "layer": 0},
        {"key": (3, 0), "x": cx - 80.0, "y": cy, "layer": 0},
        {"key": (4, 0), "x": cx, "y": cy - 80.0, "layer": 0},
    ]
    edges = [
        {"nodeA": 0, "nodeB": i, "xy": np.array([[cx, cy], [nodes[i]["x"], nodes[i]["y"]]], dtype=np.float64),
         "width": 8.0, "layer": 0, "street": 1 if i in (1, 3) else 2, "ways": [10 + i]}
        for i in (1, 2, 3, 4)
    ]

    # Potrero-style hill rising steeply to the east (+18% beyond 12 m from intersection)
    def dem_fn(xs, ys):
        dx = xs - cx
        return 20.0 + np.where(dx > 12.0, 0.18 * (dx - 12.0), 0.0)

    sample_xy = np.array([
        [cx, cy], [cx + 40.0, cy], [cx - 40.0, cy], [cx, cy + 40.0], [cx, cy - 40.0],
    ], dtype=np.float64)
    binder = roadnet.SampleBinder(sample_xy, S1=5)

    # Simulate 60 m terrain cutting 1 m below only on the east arm (x > cx + 25) so per-segment skirts trigger selectively
    def t60_fn(xs, ys):
        return dem_fn(xs, ys) - np.where(xs > cx + 25.0, 1.0, 0.0)

    mesh, graph = roadnet.build_road_mesh(nodes, edges, dem_fn, t60_fn, binder)
    assert mesh["nJunctions"] == 1
    assert len(mesh["pos"]) > 16
    assert len(mesh["streetIdx"]) > 0 and len(mesh["streetIdx"]) % 3 == 0
    assert len(mesh["skirtPos"]) > 0 and len(mesh["skirtIdx"]) > 0 and len(mesh["skirtIdx"]) % 3 == 0
    # Per-segment gating: east arm has ~55 m of >0.65 m drop-off, other 3 arms have 0 -> far fewer skirt verts than top verts
    assert len(mesh["skirtPos"]) < len(mesh["pos"]) * 2
    assert mesh["nrm"].dtype == np.int8 and mesh["nrm"].shape == (len(mesh["pos"]), 4)
    assert np.all(mesh["nrm"][:, 1] > 64), "all top road surface normals must point upward (int8 > 0.5*127)"
    assert mesh["aSamples"].dtype == np.float32 and mesh["aSamples"].shape == (len(mesh["pos"]), 2)
    assert mesh["aAttr"].dtype == np.int16 and mesh["aAttr"].shape == (len(mesh["pos"]), 2)
    assert np.all((mesh["aAttr"][:, 1] >= 0) & (mesh["aAttr"][:, 1] <= 32767))

    # Game graph connectivity: center node 0 has 4 incident edges sorted CCW
    assert int(graph["nodeEdgeSpan"][0, 1]) == 4
    assert len(graph["edgeMeta"]) == 4

    # ROAD2 pack/unpack round-trip
    blob = roadnet.pack_road2(mesh, graph)
    mesh2, graph2 = roadnet.unpack_road2(blob)
    assert mesh2["S1"] == 5
    np.testing.assert_array_equal(mesh["pos"], mesh2["pos"])
    np.testing.assert_array_equal(mesh["nrm"], mesh2["nrm"])
    np.testing.assert_array_equal(mesh["aSamples"], mesh2["aSamples"])
    np.testing.assert_array_equal(mesh["aAttr"], mesh2["aAttr"])
    np.testing.assert_array_equal(mesh["streetIdx"], mesh2["streetIdx"])
    np.testing.assert_array_equal(mesh["skirtPos"], mesh2["skirtPos"])
    np.testing.assert_array_equal(mesh["skirtTileRanges"], mesh2["skirtTileRanges"])
    np.testing.assert_array_equal(mesh["skirtIdx"], mesh2["skirtIdx"])
    np.testing.assert_array_equal(graph["nodePos"], graph2["nodePos"])
    np.testing.assert_array_equal(graph["edgeMeta"], graph2["edgeMeta"])
    np.testing.assert_array_equal(graph["polyPts"], graph2["polyPts"])


def test_midspan_terrain_clearance_and_sharp_corner_rounding():
    """Mid-span subdivision prevents 60 m terrain bulges from swallowing roads; sharp bends are beveled."""
    x0, y0 = roadnet.X0, roadnet.Y0
    nodes = [
        {"key": (0, 0), "x": x0, "y": y0, "layer": 0},
        {"key": (1, 0), "x": x0 + 100.0, "y": y0 + 100.0, "layer": 0},
    ]
    # 90-degree L-bend in a single edge
    edges = [{
        "nodeA": 0, "nodeB": 1,
        "xy": np.array([[x0, y0], [x0 + 100.0, y0], [x0 + 100.0, y0 + 100.0]], dtype=np.float64),
        "width": 7.0, "layer": 0, "street": 1, "ways": [1],
    }]

    def dem_flat(xs, ys):
        return np.full_like(xs, 10.0, dtype=np.float64)

    # Simulate a 60 m terrain mesh bulging +4 m above DEM at x = x0 + 50
    def t60_bulge(xs, ys):
        return 10.0 + 4.0 * np.exp(-((xs - (x0 + 50.0)) / 18.0) ** 2)

    mesh, graph = roadnet.build_road_mesh(nodes, edges, dem_flat, t60_bulge, None)
    # Every top-surface vertex must stay above t60_bulge (no swallowing!)
    wx = mesh["pos"][:, 0] + x0
    wy = y0 - mesh["pos"][:, 2]
    wz = mesh["pos"][:, 1]
    assert np.all(wz >= t60_bulge(wx, wy) + roadnet.TERRAIN_CLEAR_M - 0.05)
    # Adaptive subdivision over the bulge + rounded 90-deg corner must emit > 8 centerline points
    assert len(graph["polyPts"]) > 8


def test_nudge_terrain60_for_roads_pulls_surface_vertices_only():
    """nudge_terrain60_for_roads pulls 60 m land vertices under layer==0 roads toward road_z - 0.15 m."""
    spec = {"x0": 0.0, "y0": 0.0, "cell_m": 20.0}
    ter = np.full((10, 10), 14.0, dtype=np.float32)
    ter[0, :] = 0.0  # water cells must remain untouched
    edges = [
        {"xy": np.array([[60.0, 0.0], [60.0, 180.0]]), "width": 8.0, "layer": 0},
        {"xy": np.array([[120.0, 0.0], [120.0, 180.0]]), "width": 8.0, "layer": 1},  # bridge: ignored
    ]
    def dem_fn(xs, ys):
        return np.full_like(xs, 10.0, dtype=np.float64)

    nudged = roadnet.nudge_terrain60_for_roads(ter, spec, edges, dem_fn, step=3)
    assert nudged.shape == ter.shape
    assert np.all(nudged[0, :] == 0.0), "water cells must not be nudged"
    # Vertex at (3, 3) -> x=60, y=60 is directly on the surface road (target 9.85 m)
    assert nudged[3, 3] < 12.0, f"expected 60 m vertex under surface road to be pulled down, got {nudged[3, 3]}"
    # Vertex at (6, 3) -> x=120, y=60 is under a bridge (layer=1) -> untouched
    assert nudged[6, 3] == pytest.approx(14.0)
