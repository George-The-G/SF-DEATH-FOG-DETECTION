---
id: "001"
slug: "fog2-grid-artifact-fix"
title: "Fix 320m voxel ray-march grid artifact in fog2.js (Option F2)"
issue: null
epic: null
pr: null
pr_url: ""
status: "done"
branch: "feat/001-fog2-grid-artifact-fix"
switch: "col_top_clamp"
depends_on: []
owned_paths:
  - "site/v3/fog2.js"
gpu_required: false
deliverables:
  - "tasks/001-fog2-grid-artifact-fix/PLAN.md"
  - "tasks/001-fog2-grid-artifact-fix/evidence/verify.json"
  - "site/v3/fog2.js"
---

# Task 001: Fix 320m voxel ray-march grid artifact in fog2.js (Option F2)

## Problem & Goal
> **Already implemented:** Option F2 (`if (!emp && rd.y < -1e-5) tE = min(tE, t + (p.y - colTop) / -rd.y);`) is already live in `site/v3/fog2.js` L255–L260. Closed as `done` (2026-11-18).

Clamp descending ray column-top skipping (tE) in site/v3/fog2.js per docs/fog/grid-artifact-diagnosis.md.

## Scope
- **In scope:** site/v3/fog2.js
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 001` passes with `ok: true`
