---
id: "005"
slug: "ondemand-render-bus-idle"
title: "Fix render-on-demand idle GPU drain with buses & hoist per-frame loop allocations"
issue: null
epic: null
pr: null
pr_url: ""
status: "beta"
branch: "feat/005-ondemand-render-bus-idle"
switch: "idle_render_opt"
depends_on: []
owned_paths:
  - "site/v3/app.js"
  - "site/v3/buses.js"
gpu_required: false
deliverables:
  - "tasks/005-ondemand-render-bus-idle/PLAN.md"
  - "tasks/005-ondemand-render-bus-idle/evidence/verify.json"
  - "site/v3/app.js"
  - "site/v3/buses.js"
---

# Task 005: Fix render-on-demand idle GPU drain with buses & hoist per-frame loop allocations

## Problem & Goal
1. `keepDrawing()` returns true whenever `BUSES` is loaded, disabling render-on-demand even when paused/idle; `aaRender()` allocates `Vector2` each frame, `placeGear` polls `getBoundingClientRect()` every 300ms, and `ROUTES.forEach` runs every frame. Add `BUSES.isMoving()`, hoist per-frame allocations, and replace the 300ms layout poll with `ResizeObserver`.
2. Buses are currently cut off early along their route because `site/v3/buses.js` filters vehicles by a fixed circle around the bottom stop (`o.sx, o.sz`, `radius_m || 1000`) instead of distance to the full route polyline (`o.R.poly` / `o.path`). Keep buses visible during their entire route.

## Scope
- **In scope:** site/v3/app.js, site/v3/buses.js
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 005` passes with `ok: true`
