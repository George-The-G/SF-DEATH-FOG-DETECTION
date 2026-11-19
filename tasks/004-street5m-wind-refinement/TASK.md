---
id: "004"
slug: "street5m-wind-refinement"
title: "Guide 12 5m street wind refinement and nesting validation (H2)"
issue: null
epic: null
status: "ready"
branch: "feat/004-street5m-wind-refinement"
switch: "street5m_wind"
depends_on: []
owned_paths:
  - "microclimate/wind.py"
  - "microclimate/refine.py"
gpu_required: true
deliverables:
  - "tasks/004-street5m-wind-refinement/PLAN.md"
  - "tasks/004-street5m-wind-refinement/evidence/verify.json"
  - "microclimate/wind.py"
  - "microclimate/refine.py"
---

# Task 004: Guide 12 5m street wind refinement and nesting validation (H2)

## Problem & Goal
Rebuild corr_bldg / rel_wind against 20m walls wind library with verified inflow/nesting checks.

## Scope
- **In scope:** microclimate/wind.py, microclimate/refine.py
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 004` passes with `ok: true`
