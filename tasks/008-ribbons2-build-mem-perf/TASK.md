---
id: "008"
slug: "ribbons2-build-mem-perf"
title: "Reduce ribbons2.js boot CPU spike & peak memory with pre-sized typed arrays"
issue: null
epic: null
pr: null
pr_url: ""
status: "ready"
branch: "feat/008-ribbons2-build-mem-perf"
switch: "ribbons_mem_opt"
depends_on: []
owned_paths:
  - "site/v3/ribbons2.js"
gpu_required: false
deliverables:
  - "tasks/008-ribbons2-build-mem-perf/PLAN.md"
  - "tasks/008-ribbons2-build-mem-perf/evidence/verify.json"
  - "site/v3/ribbons2.js"
---

# Task 008: Reduce ribbons2.js boot CPU spike & peak memory with pre-sized typed arrays

## Problem & Goal
ribbons2.js builds both full-city and per-tile index buffers using growable JS arrays during bootLive (ribbons.bin + ribbons_city.bin), causing startup CPU spikes and GC pressure on phones. Pre-size typed buffers, cut per-strip allocations, and tune tile culling thresholds while keeping street rendering bit-identical.

## Scope
- **In scope:** site/v3/ribbons2.js
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 008` passes with `ok: true`
