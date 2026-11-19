# Render performance plan (viewer v3, branch v3-dev)

Status: plan only, no code changed. "Measured" = counted by running the page headless; everything else is code reading or an **estimate**.

## 1. Where the frame goes

Measured with renderer.info (headless Chromium, replay night, no beta):

| View | Draw calls | Triangles |
|---|---|---|
| Whole-city default camera | 159 | 2.16 M |
| Same, buildings hidden | 8 | 0.32 M |
| Mid zoom (2.5 km up) | 118 | 1.86 M |
| Street level | 54 | 1.0 M |

So buildings are ~85% of triangles and ~95% of draw calls in the default view.
(Headless runs used software GL, so ms/frame numbers are meaningless; only counts are trusted. Real GPU timing is Stage 0 below.)

Breakdown of the 2.16 M (computed from data files):
- Buildings, instanced boxes: 163,375 x 10 tris = 1.63 M, spread over 150 one-km tile meshes (draw calls).
- Buildings, true polygons: 5,375 footprints, ~0.2 M tris, ONE mesh (cannot be culled in parts).
- Terrain: 153k tris, one draw, double sided, no LOD (cheap, not a bottleneck).
- Ribbons (streets): ~34k tris for district set, one draw; live city set adds more. Vertex shader does 2 texture fetches and a 16-iteration mask loop per vertex.
- Fog: sheets = 6 transparent full-city layers (overdraw heavy); beta volumetric = scene to render target + raymarch passes + composite.
- Sky: one fullscreen triangle + 5k star points. Cheap.
- UI/DOM per frame: labels (2), throttled chip/list updates. Cheap.

Key finding: median building is 12x12 m and 7.9 m tall. At the default camera one pixel is ~19 m, so most of 1.6 M triangles are under one pixel. GPUs shade sub-pixel triangles at 2-4x cost, and they shimmer. This is wasted work AND an aliasing source.

Other structural findings:
1. The loop renders every frame forever (requestAnimationFrame, no dirty check), even when nothing moves. Sheets fog also animates continuously. On phones this drives heat and then throttling, which dominates sustained FPS.
2. Beta fog renders the scene into an offscreen target, but the canvas is still created with antialias:true (wasted MSAA buffer). Phone fog tier has no MSAA (ms 0), so edges are unsmoothed there.
3. Pixel ratio is capped at 2 (phone) / 1.75 (desktop) but there is no dynamic scaling; only the fog governor adapts.
4. Each building instance carries a 64-byte matrix + 12-byte colour (~12 MB GPU) where ~16 bytes would do.
5. Terrain is DoubleSide though the camera is clamped above ground.

## 2. Can geometry be simplified without visible loss?

Yes, mainly buildings.
- Distance LOD per tile (roof-only 2-tri quad beyond ~3-4 km, full box nearby). Roof tint pre-averaged with wall shade so brightness does not pop. Pairs of InstancedMesh can share one instance buffer. Cuts the overview from ~1.6 M to ~0.3-0.4 M building tris (estimate). Visually nearly identical at that scale, less shimmer.
- Drop hidden party-wall faces offline (adjacent buildings), ~15-30% of nearby faces (estimate; needs a tool run on the real footprints).
- Block merging offline for far LOD (163k boxes -> roughly 10-20k block shapes), the approach map renderers use at low zoom. Bigger win, more work, needs the Python mesh builder (microclimate/meshes.py).
- Polygon mesh: split per tile so it culls, and add the same distance LOD.
- Terrain: FrontSide; optional 2-level LOD. Not needed for speed.
- Draw calls: 150 is fine on desktop; merging tiles is only worth it on low-end phones.

## 3. What others do (applies / does not)

- Map renderers (Mapbox GL fill-extrusion, Google 3D tiles, deck.gl): tiled data with per-zoom LOD, merged buildings at low zoom, only visible tiles. Applies: our 1 km tiles + LOD levels.
- three.js guidance: instancing, BatchedMesh for mixed meshes with per-instance culling, render on demand, dynamic resolution scaling, cap pixel ratio, avoid overdraw of transparent layers. Applies: render on demand, dynamic resolution, shader-side instance compaction.
- Aggressive options not recommended yet: mesh simplification libraries (meshopt) on boxes are pointless since boxes are already minimal; GPU-driven culling needs WebGPU.

## 4. Prioritised plan (estimates, not measured)

| # | Item | Gain | Risk | Effort |
|---|---|---|---|---|
| 0 | Measuring stage (below) | enables everything | none | S |
| 1 | Render only when something changed (camera, time, fly, toggles); drop sheets drift to ~30 fps | big battery / sustained FPS on phones, 0 visual change | medium: missed dirty cases = frozen screen | M |
| 2 | Building distance LOD (roof-only far) | large GPU gain at overview: -1.2 M tris (~55% of frame triangles) | low; tune distance and tint | M |
| 3 | Dynamic resolution scale while moving, full res when idle | steady FPS on weak phones | low; slight softness during motion | S |
| 4 | antialias:false when volumetric fog active; cheap AA (FXAA/SMAA or MSAA only when idle) on phones | memory + resolve cost; better phone edges | low | S |
| 5 | Shader-side building instances (16 B/instance), FrontSide terrain, precompute lowest route bit in ribbons | ~10 MB less GPU memory, lighter vertex work | low | M |
| 6 | Offline block-merged far LOD and hidden-face removal | further -50% far tris | medium: new data pipeline | L |
| 7 | Split polygon mesh per tile for culling | helps zoomed-in views | low | S |

Quality notes: items 1, 3, 4 do not change the still image. Item 2 changes sub-pixel detail only; verify with before/after screenshots at overview and mid zoom.

## 5. Interaction with fog work (other thread)

- Fog tier governor already steps down at >22 ms median. Building LOD lowers scene cost, so the governor should hold higher tiers longer; coordinate the 22 ms threshold.
- Render-on-demand must respect fog accumulation: refinement passes need frames after the camera stops (use a "still-settling" dirty flag), and flow/advect animation counts as motion.
- Dynamic resolution must scale the scene target and fog buffer together, otherwise fog buffers and depth mismatch.
- Scene target is rendered with MSAA 4x on desktop, tier 3: antialias change must not touch it.
- No fog shader or voxel code changes proposed here.

## 6. Measuring stage (real phones)

1. Use the existing HUD (?beta=1&hud=1, frame ms median/p95, draws, tris, GPU name). Add GPU timer queries (EXT_disjoint_timer_query_webgl2, where available) around terrain, buildings, ribbons, fog, composite; fall back to toggling layers and diffing frame ms.
2. Fixed camera paths (overview, mid, street, route flyover) driven by a script, run for 20 s each, record median/p95/1% low, draws, tris, pixel ratio.
3. Devices: one recent iPhone, one mid Android, one old/low Android, plus a laptop iGPU. Also 5 min sustained run to see thermal throttling (FPS at minute 1 vs minute 5).
4. Record before/after screenshots of the three static views for each change; keep a results table in the repo docs.
5. Success target (proposal, adjust with data): overview >= 45 fps on mid Android, p95 < 33 ms, no drop of more than 20% over 5 minutes.

## 7. Addendum: city streets (live ribbons)

Facts (counted from site/v3/live/ribbons_city.bin and microclimate/live/ribbons.py): 72,985 samples, 12,509 separate strips, 363k indices (~121k triangles), one centre line per street, a sample every ~40 m (stride 2). One draw call, `frustumCulled = false`, so all of it is drawn at every zoom. It is alpha blended (about 0.85), no depth write, double sided.

Why it looks chunky:
- The generator cuts a strip wherever the OSM way changes, at gaps over 45 m, and at any corner sharper than the mitre limit. Each strip is a flat butt-ended band, so junctions show gaps on the outside of angled joins and double-darkened overlaps where bands cross (alpha blending twice).
- Curves are straight segments every ~40 m, so bends look faceted.

Fixes, smallest first:
1. **Exactly-once blending (stencil or depth trick).** Removes double-dark overlaps at junctions and cuts overdraw. Needs a stencil buffer on the fog scene target; coordinate with the fog thread. Low visual risk.
2. **Round caps / junction hubs at strip ends.** Closes the gaps without changing data: extra cap vertices reuse the end sample's wetness. Replaces the "split at sharp corners" rule with a round join. Generator change only; sample count can stay the same.
3. **Smooth centre lines.** Insert extra vertices along a Catmull-Rom curve through the samples. Each new vertex stores two sample ids plus a weight (aInfo.w is unused today), shader lerps wetness, so the gradient stays continuous. Roughly 2x vertices on curves only; use straight runs unchanged.
4. **Merge strips through plain joins** (same street, small turn) so there are fewer chunks and no seam at way boundaries.
5. **Cull by tile.** The file already stores a bounding sphere per strip (the loader reads and discards it). Group strips into km tiles, draw ranges per tile, enable frustum culling. Zoomed in, only a few percent of the geometry is drawn; no gain at the whole-city view.
6. **Vertex shader:** precompute the lowest route index per sample on the CPU instead of the 16-iteration loop with exp2 on every vertex. Small win, mainly weak phones.
7. If OSM road class is available in the static data, thin or hide service and residential streets beyond a distance (fewer overlapping bands when zoomed out).

Performance honesty: streets are not the bottleneck. The city set is ~121k triangles against ~1.8M for buildings, so expect streets to be a few percent of GPU time (estimate). The real gains are visual (1-4) plus cheap culling (5). Overdraw at the overview is the main street cost: bands are widened with distance (up to 8x, ~50 m), so streets cover a large share of pixels, and fix 1 reduces that.

Coordination risk: city sample order is tied to the live job's column map (live_ribbons.npz, day*.u8 columns). Any change to sample count or order (fixes 3 and 4) must regenerate the bin, meta and npz together and deploy with the live job, or the viewer rejects the data and falls back. Fixes 1, 2 (if sample count unchanged), 5 and 6 do not touch the data contract.

Suggested order: 1, 2, 5, 6 first (no data contract change), then 3 and 4 together as one regenerate-and-deploy step. Measure with the Stage 0 harness before and after.
