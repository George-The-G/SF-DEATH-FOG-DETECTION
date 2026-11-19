# Plan B: merged far buildings and hidden-wall removal (script first, cheap agents only for checking)

For: a Sonnet agent to run, with optional Haiku helpers for visual checks. Author: Claude (render thread), 2026-11-14. Approved by George in chat as a plan; building it needs his go-ahead per stage.

Depends on: plan A stage 0 (measuring harness) and stage 1 (distance LOD switch). Hidden-wall removal (B2) also depends on plan A item 5 (compact instances) OR uses the fallback in B2 step 4.

Numbers marked "estimate" were not measured.

## Short answer: a script, not a swarm

This work is pure geometry on one input file (`bldg.geojson`, about 168k footprints). Each step has a clear rule, so a Python script using shapely does all of it in minutes, and running it twice gives the same result. Splitting it among agents would add cost and inconsistency at tile borders without adding anything the script cannot do.

Cheap agents (Haiku) are useful in one place only: looking at before/after render pairs of flagged tiles and saying whether a difference is acceptable, in a fixed JSON answer. They never edit geometry. Even that step is optional, because an image-difference score catches most problems.

## 0. Standing rules (deathfog-agent-brief skill)

- Windows PC via desktop-commander, fresh worktree from `origin/v3-dev`, branch `bld-merge-<stage>`, Python `C:\Users\George\source\repos\SF-DEATH-FOG-DETECTION\.venv\Scripts\python.exe`. No GPU.
- Keep `site/v3/data/buildings.bin` byte-identical. Write NEW files only (`buildings_far.bin`, `buildings_mask.bin`). `skate.js` and `buses.js` read the near building data through `buildingsMesh.userData.col`; it must not change.
- Viewer use is beta only (`?beta=1`), public unchanged.
- Tests: `python -m pytest -q tests tests_mc`, plus new tests for the new script (below).

## Inputs and where the code goes

- Source: `microclimate/meshes.py` (`pack_buildings`, `build_buildings`) reads `raw/bldg.geojson` and `grids/terrain_20m.npy`, writes `site/v3/data/buildings.bin` (format BLD1: boxes and true polygons, positions in 0.3 m steps, heights in 0.1 m steps).
- New module: `microclimate/meshes_lod.py` with a CLI entry `python -m microclimate meshes-lod [--tile N] [--check]`.
- Re-use the same box/polygon split and quantisation as `pack_buildings`, so a building's id, position and height match the near file exactly.
- Work per 1 km tile (same key as the viewer: `floor(x/1000)*1000 + floor(z/1000)`, 150 tiles) with `multiprocessing.Pool`. Each worker gets its tile's footprints plus a 30 m border ring of neighbours (read-only) so merges and wall tests are correct at tile edges. A building belongs to the tile holding its centroid; only the owning tile writes it.

## B1. Measure first (script only, ~half a day)

Before writing any viewer code, the script reports, per tile and in total:
- how many box side walls are fully hidden (rule in B2),
- how many triangles a block-merged far version would have (B3) vs today,
- the projected saving at the whole-city camera (combine with plan A stage 1 numbers).

Output `docs/perf/bld_lod_report.json` and a short table in the PR. Go/no-go for B2 and B3 is George's call on these numbers (my estimate: 15-30% of near walls hidden; far triangles down 70-90% vs roof-only LOD, but roof-only LOD from plan A may already be enough).

## B2. Hidden-wall mask (near buildings)

1. For each box building, build its 4 side faces from the oriented rectangle (centre, width, depth, angle from BLD1).
2. Query neighbours with `shapely.STRtree`. A face is hidden when the neighbour footprints (buffered by 0.4 m) cover at least 90% of the face's length AND those neighbours are at least as tall as this building minus 0.5 m (compare tops: base + height).
3. Write `buildings_mask.bin`: magic `BLDM`, uint32 count (must equal BLD1 nBox), then one uint8 per box (bits 0-3 = walls +x, -x, +z, -z in the box's own frame, matching the viewer's unit box face order in `buildBuildings`).
4. Viewer (beta): add a per-vertex face id to the unit box (0..4) and a per-instance mask attribute; in the vertex shader (Lambert via `onBeforeCompile`) collapse a hidden face to a degenerate point. With compact instances (plan A item 5) the mask lives in spare bits; without it, use an extra `InstancedBufferAttribute` (1 byte per box).
5. Polygons: skip in the first version (5k of 168k buildings).

Checks: unit tests with three synthetic layouts (row houses sharing walls, a tall building beside a short one, a gap of 1 m) asserting the expected masks. Render check: street view and mid view with mask on vs off must be pixel-identical except at roof-height steps (report the diff).

## B3. Block-merged far LOD

1. Group buildings into blocks: union of footprints buffered +1 m then -1 m (closes party-wall gaps), split by the street network (blocks never cross a street centre line; use the drivable streets already in the static tables).
2. Inside a block, split into height bands (start: 6 m steps of real height) so a tower in a low block keeps its height. For each band: union, simplify with tolerance 2 m (topology-preserving), drop parts under 30 m².
3. Height of a band polygon = area-weighted mean of its members' tops; base = lowest member base (same terrain sampling as `pack_buildings`, so bases sit on the drawn terrain).
4. Extrude to walls + roof triangles, indexed, positions quantised like BLD1. Write `buildings_far.bin` (magic `BLDF`): per tile: vertex count, index count, positions (uint16 x3 with tile origin), per-vertex tint (uint8, same tint rule as the viewer: taller = lighter), indices (uint16 per tile).
5. Viewer (beta, plan A stage 1 switch): far tiles draw the merged tile mesh instead of roof-only quads. One draw call per far tile.

Checks:
- Per tile triangle counts (report).
- Image diff per tile: render each tile from the over and mid cameras at phone and desktop size with near detail vs merged far mesh (Playwright, headless). Score with mean absolute difference and SSIM. Flag tiles with SSIM below 0.97 (start value; tune after the first run).
- Automatic retry for flagged tiles: smaller simplify tolerance (1 m), finer height bands (4 m), re-render, re-score. Still failing tiles fall back to roof-only (plan A) and are listed in the report.

## Optional: Haiku visual check of flagged tiles

Only if the image scores leave more than ~10 flagged tiles that the retry did not fix.

- One Haiku call per flagged tile, run by the Sonnet agent (no free-roaming agents, no file edits).
- Input: the two images (near vs far), the tile id, the scores.
- Output, JSON only: `{"tile": 123, "verdict": "ok" | "wrong_height" | "missing_building" | "blob" | "other", "note": "<12 words"}`.
- The Sonnet agent decides what to do with the answers (adjust parameters for that tile, or fall back to roof-only). Haiku answers are hints, not decisions.
- Cost estimate: under 20 calls per run.

## Deliverables and finish

- `microclimate/meshes_lod.py`, its tests, the two new binary files, `docs/perf/bld_lod_report.json`, viewer changes in `app.js` behind `?beta=1`.
- One PR per step (B1 report, B2 mask, B3 far mesh) into `v3-dev`. Do not merge or deploy; George decides, deploy via the deathfog-ship skill.
- Report: files, tests, triangle counts before/after per view, flagged tiles and what happened to them, file sizes (download cost on phones), risks.
