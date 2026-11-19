---
id: "009"
slug: "citywide-road-ribbons"
title: "City-wide continuous SF road ribbon network & terrain-accurate mesh"
issue: null
epic: null
pr: null
pr_url: ""
status: "in-progress"
branch: "feat/009-citywide-road-ribbons"
switch: "city_roadnet"
depends_on: []
owned_paths:
  - "microclimate/live/roadnet.py"
  - "site/v3/roadnet.js"
  - "site/v3/app.js"
  - "site/v3/skate.js"
  - "tests_mc/test_roadnet.py"
  - "tests_js/roadnet.test.mjs"
gpu_required: false
deliverables:
  - "tasks/009-citywide-road-ribbons/PLAN.md"
  - "tasks/009-citywide-road-ribbons/evidence/verify.json"
  - "microclimate/live/roadnet.py"
  - "site/v3/roadnet.js"
  - "site/v3/app.js"
  - "site/v3/skate.js"
  - "tests_mc/test_roadnet.py"
  - "tests_js/roadnet.test.mjs"
---

# Task 009: City-wide continuous SF road ribbon network & terrain-accurate mesh

## Problem & Goal
Replace fragmented, runtime-patched street segments with a unified, pre-baked city-wide continuous road ribbon network and intersection mesh that preserves San Francisco's sharp flat-to-steep terrain transitions (with realistic car-lip vertical curves), retains smooth microclimate data-point coloring/blending, improves boot/render performance, and exposes a clean connected road graph for games.

## Scope
- **In scope:** microclimate/live/roadnet.py, site/v3/roadnet.js, site/v3/app.js, site/v3/skate.js, tests_mc/test_roadnet.py, tests_js/roadnet.test.mjs
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [x] Technical plan written in `PLAN.md` (covering SF terrain/grade-break algorithm, watertight junctions, data-point coloring, pre-baked binary layout, and game topology API)
- [x] Offline builder `microclimate/live/roadnet.py` builds a unified, city-wide connected ribbon + junction mesh from OSM + high-res 3DEP DEM without breaking at intersections or OSM way splits
- [x] SF vertical profile algorithm preserves flat-to-steep sustained grades (e.g. Potrero Hill) with realistic parabolic car-lip vertical curves instead of wide Gaussian smearing
- [x] Pre-baked `ROAD1` binary format uploads directly to WebGL buffers in `site/v3/roadnet.js` (zero runtime smoothing/bridging/cap rebuild), faster boot & lower peak memory than `ribbons2.js`
- [x] Microclimate data-point coloring & smooth vertex/fragment blending (`wetness`, `sun`, `road temp`, route overlays) preserved with zero regression against `live/day0..2.u8`
- [x] Game-ready road topology/surface query API integrated in `site/v3/skate.js` behind `featOn('city_roadnet')`
- [x] Implementation stays strictly within `owned_paths` and is gated behind `?beta=1&feat=city_roadnet`
- [ ] `python tools/task.py verify 009` passes with `ok: true`
