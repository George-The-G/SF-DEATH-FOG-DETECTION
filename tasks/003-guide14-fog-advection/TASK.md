---
id: "003"
slug: "guide14-fog-advection"
title: "Guide 14 fog advection boundary smoothing and forecast extension"
issue: null
epic: null
pr: null
pr_url: ""
status: "ready"
branch: "feat/003-guide14-fog-advection"
switch: "guide14_advect"
depends_on: []
owned_paths:
  - "microclimate/live/travel.py"
  - "microclimate/live/fog5.py"
gpu_required: false
deliverables:
  - "tasks/003-guide14-fog-advection/PLAN.md"
  - "tasks/003-guide14-fog-advection/evidence/verify.json"
  - "microclimate/live/travel.py"
  - "microclimate/live/fog5.py"
---

# Task 003: Guide 14 fog advection boundary smoothing and forecast extension

## Problem & Goal
Fix fog appearing from nowhere across observed-to-forecast boundary and evaluate 12-24h fog5 extension per docs/fog/guide-14-plan-v2.md.

## Scope
- **In scope:** microclimate/live/travel.py, microclimate/live/fog5.py
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 003` passes with `ok: true`
