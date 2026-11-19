---
id: "006"
slug: "fog2-voxel-gpu-perf"
title: "Async 3D fog atlas decode, VOX_FS empty-skip & GPU benchmark verification"
issue: null
epic: null
pr: null
pr_url: ""
status: "in-progress"
branch: "feat/006-fog2-voxel-gpu-perf"
switch: "fog_gpu_opt"
depends_on:
  - "001"
owned_paths:
  - "site/v3/fog2.js"
gpu_required: false
deliverables:
  - "tasks/006-fog2-voxel-gpu-perf/PLAN.md"
  - "tasks/006-fog2-voxel-gpu-perf/evidence/verify.json"
  - "site/v3/fog2.js"
---

# Task 006: Async 3D fog atlas decode, VOX_FS empty-skip & GPU benchmark verification

## Problem & Goal
d3Load decodes 1284x812 PNG atlases via synchronous ctx.getImageData() on the main thread; VOX_FS ray-march and TAA settle cost can be reduced further, and docs/perf/PERFORMANCE_GAPS.md row 5 (GPU lattice + bake timing) remains unverified. Move atlas decode off the hot path (createImageBitmap / faster channel upload), tighten empty-air skipping, and capture repeatable GPU frame-time evidence.

## Scope
- **In scope:** site/v3/fog2.js
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 006` passes with `ok: true`
