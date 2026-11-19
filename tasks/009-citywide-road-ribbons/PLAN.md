# Plan 009: City-Wide Continuous SF Road Ribbon Network & Terrain-Accurate Mesh

**Task:** `009-citywide-road-ribbons`
**Feature switch:** `city_roadnet` (`?beta=1&feat=city_roadnet`)
**Owned paths:** `microclimate/live/roadnet.py`, `site/v3/roadnet.js`, `site/v3/app.js`, `site/v3/skate.js`, `tests_mc/test_roadnet.py`, `tests_js/roadnet.test.mjs`

---

## 1. Problem & Root Causes

1. **Fragmented, two-tier road geometry:**
   - Legacy `make_ribbons.py` (district zones) and `microclimate/live/ribbons.py` (whole city) derived strips backwards from microclimate wetness sample tables (`points.csv` / `live_static.npz`) instead of building from a connected road graph, breaking at every intersection, OSM way split, and short block.
   - `site/v3/ribbons2.js` spent startup CPU smoothing, subdividing, spatial-hash bridging, and capping segments, and applied a `SIG_Y = 25 m` Gaussian low-pass that smeared out San Francisco's sharp flat-to-steep transitions (e.g. Potrero Hill).
2. **Terrain vs. road mismatch (learned in Iteration 1 & 2 previews):**
   - `site/v3/data/terrain.glb` is a **60 m** grid (`77,235` verts, `153,360` tris, `2.77 MB`). Upgrading terrain to 20 m would cost `~748k` tris (land-only, 4.9×) to `1.38M` tris (full grid, 9×) plus re-packing `buildings.bin`.
   - Meanwhile, naive whole-edge skirts with duplicated 56-byte road vertices in Iteration 2 added `862,012` verts / `811,756` tris (`53.5%` of total triangles, pushing `roadnet_city.bin` to `111.9 MB` raw).

---

## 2. Approved Architecture ("Best of Both Worlds" — Iteration 3)

Keep `terrain.glb` at its fast **60 m** triangle budget while achieving **100% real OSM connectivity, sharp SF terrain character, zero swallowed roads, zero floating roads, smooth data-point color blending, and lean GPU/download cost**:

### Layer 1 — Unified Real OSM Road Graph & SF Vertical Profile (`microclimate/live/roadnet.py`)
- **Input:** Real whole-city `data/raw/osm_city.json` (`51.4 MB`, `12,426` nodes, `19,051` continuous edges, `9,748` watertight junctions) + high-res USGS 3DEP DEM (`data/raw/dem_n38w123.tif`).
- **Degree-2 chaining & grade separation:** Merge consecutive OSM ways across degree-2 tag splits; preserve `layer` / `bridge` / `tunnel` grade separation; derive per-road widths from OSM `width` / `lanes` / `highway` tags.
- **SF Vertical Profile:**
  - **Intersection pads:** Clamped junction planes (`≤ 6%` cross-grade) so steep hills break into level/crowned SF crossings.
  - **Piecewise-linear sustained grades:** Douglas-Peucker fit (`PL_EPS_M = 0.40 m`) to dense `2.5 m` DEM samples preserves sustained 15–22% inclines (e.g. Potrero Hill) without Gaussian smearing.
  - **Parabolic "car-lip" vertical curves:** C¹ quadratic fillets ($L_v = \text{clamp}(36 |\Delta g|, 4, 14)\text{ m}$) at grade breaks (`|\Delta g| >= 2.5%`).
  - **Mitered & beveled corners:** 2-segment corner rounding at interior bends `> 22°` with clamped miter scaling (`MAX_MITER = 1.8`) so curved streets never pinch.
  - **Watertight junctions:** Setback-trimmed arms + quadratic Bezier curb fillets sharing exact edge vertices with incoming strips.
- **Nudge existing 60 m terrain vertices under roads (0 extra triangles):**
  - Offline helper computes a road-nudged 60 m elevation array (pulling existing 60 m vertices that fall on surface roads `layer == 0` toward `road_z - 0.15 m`, excluding bridges/tunnels) — optional beta `terrain_roadnet.glb` with **exact same `77,235` verts / `153,360` tris / `2.77 MB`** so mismatches shrink before skirts even kick in.
  - Road strips adaptively subdivide against `meshes.mesh_height(..., step=3)` (`TERRAIN_CLEAR_M = 0.30 m`) so no 60 m triangle ever pokes through mid-span.

### Layer 2 — Compacted Pre-Baked `ROAD2` Top Roadbed & Smooth Color Blending
- **Preserve existing live data contract:** Every roadbed and junction vertex binds offline to `(ida, idb, t)` in the existing `live/day0..2.u8` (`S1 + S2`) column space — zero physics rerun, full compatibility with `wetness`, `sun`, and `road temp` (`uMode` 0/1/2) smooth shader blending.
- **Compact vertex layout (cuts ~65% off raw bytes):**
  - `pos[nVerts, 3]`: `Float32` (`12 B/vert`) — unexaggerated `(x, y, z)`; shader multiplies `y * uEx`.
  - `nrm[nVerts, 4]`: normalized `Int8` (`4 B/vert`, was `12 B`) — subtle 3D hillside shading.
  - `aSamples[nVerts, 2]`: `Uint32` (`8 B/vert`: `ida, idb`) — direct sample IDs.
  - `aAttr[nVerts, 2]`: `Int16` normalized (`4 B/vert`: `across`, `t`) — drops unused `aRt` (`12 B/vert`).
  - **Total top-road vertex stride:** `28 B/vert` (down from `56 B/vert` — **2× smaller** VRAM & file size!).
- **Spatially-tiled `2 km` index buffers:** 1 draw call when zoomed out (`dist > 6,000 m`), frustum-culled per-tile draw ranges when zoomed in.

### Layer 3 — Close-Up-Only, Per-Segment, Position-Only Skirts
- **Per-segment gating:** Emit a skirt quad *only* on the specific individual edge segment `(q, q+1)` where `max(y_q - t60_q, y_{q+1} - t60_{q+1}) > 0.65 m` (actual hillside/crest drop-offs, cutting skirt triangles by ~60–70% down to `~150k–250k` tris).
- **Separate position-only stream (`12 B/vert`):** Stored in its own `ROAD2` section (`skirtPos[nSkirtVerts, 3]` `Float32`, `skirtTileRanges[nTiles, 2]` `Uint32`, `skirtIdx[nSkirtIdx]` `Uint32`) — no normals, no sample IDs, no wetness texture fetches!
- **LOD distance cutoff (`SKIRT_MAX_D = 2500 m`):** Rendered with a trivial flat dark-slate shader (`vec3(0.11, 0.14, 0.20)`) in a dedicated close-up `skirtG` group that is **hidden completely when camera distance `> 2,500 m`** (`0` skirt triangles drawn in city-wide view; only the 1–4 visible tiles draw when zoomed in or skating).

### Layer 4 — Connected Game Road Graph (`site/v3/roadnet.js` → `site/v3/skate.js`)
- Compact `graph` section embedded in `roadnet_city.bin` (`nodePos`, `nodeEdgeSpan`, `nodeAdj`, `edgeMeta`, `edgeProps`, `polyPts`, `polySample`).
- `createRoadGraph` exposes `roadAt(x, z, hx, hz, maxR)`, `intersectionAt(nodeId)`, and `nearestRoad(x, z, maxR)` backed by a 32 m CSR spatial grid; wired into `skate.js` behind `featOn('city_roadnet')`.

---

## 3. Target Metrics & Verification

- **File size target:** `roadnet_city.bin` down from `111.9 MB` raw to **~20–28 MB raw (~6–8 MB gzipped)** for the entire city (top roadbed + 9,748 junctions + lean close-up skirts + 19k-edge game graph).
- **Runtime performance:**
  - City-wide view (`dist > 2,500 m`): **0 skirt triangles drawn**, 1 draw call for all SF streets.
  - Close-up / skater view (`dist < 2,500 m`): only 1–4 frustum-culled tiles draw (~5–15k skirt tris visible), zero floating roads, zero swallowed roads.
- **Verification gate:**
  - `pytest` (including `tests_mc/test_roadnet.py` + `tests/test_repo_hygiene.py`)
  - `node --test tests_js/*.mjs` (including `tests_js/roadnet.test.mjs`)
  - `python tools/task.py verify 009` (`ok: true`, desktop + phone Playwright checks on `?beta=1&feat=city_roadnet&night=2026-10-01`)
  - Local beta preview visual sign-off on Potrero Hill & city-wide view before merge.
