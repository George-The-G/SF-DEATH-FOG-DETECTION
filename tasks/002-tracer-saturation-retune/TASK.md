---
id: "002"
slug: "tracer-saturation-retune"
title: "Retune TRACER_REF for walls 3D fog tracer contrast (H3)"
issue: null
epic: null
status: "ready"
branch: "feat/002-tracer-saturation-retune"
switch: "tracer_ref_v2"
depends_on: []
owned_paths:
  - "microclimate/wetness.py"
gpu_required: false
deliverables:
  - "tasks/002-tracer-saturation-retune/PLAN.md"
  - "tasks/002-tracer-saturation-retune/evidence/verify.json"
  - "microclimate/wetness.py"
---

# Task 002: Retune TRACER_REF for walls 3D fog tracer contrast (H3)

## Problem & Goal
TRACER_REF=0.03 saturates on beta kd=20,kdv=8 walls tracer (median 0.271), flattening street-to-street fog contrast.

## Scope
- **In scope:** microclimate/wetness.py
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 002` passes with `ok: true`
