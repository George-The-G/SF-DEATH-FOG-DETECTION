---
id: "007"
slug: "macro-shader-lod-perf"
title: "Adaptive macro cloud shader LOD, early city-interior discard & async PNG decode"
issue: null
epic: null
pr: null
pr_url: ""
status: "in-progress"
branch: "feat/007-macro-shader-lod-perf"
switch: "macro_lod_opt"
depends_on: []
owned_paths:
  - "site/v3/macro.js"
gpu_required: false
deliverables:
  - "tasks/007-macro-shader-lod-perf/PLAN.md"
  - "tasks/007-macro-shader-lod-perf/evidence/verify.json"
  - "site/v3/macro.js"
---

# Task 007: Adaptive macro cloud shader LOD, early city-interior discard & async PNG decode

## Problem & Goal
macro.js runs a 9-tap B2-spline/Gaussian filter plus 3D billow erosion over the full 60km slab every frame whenever dist > 4km (even inside the city interior where cityFade is near 0 or during fast camera motion) and decodes macro PNGs synchronously via canvas drawImage. Add early fragment discard inside the city interior, adaptive 4-tap moving / 9-tap still filtering, and createImageBitmap async decode.

## Scope
- **In scope:** site/v3/macro.js
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 007` passes with `ok: true`
