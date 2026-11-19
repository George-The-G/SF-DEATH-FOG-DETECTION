---
id: "013"
slug: "task-token-usage-metadata"
title: "Zero-Token Task Token Usage Metadata on Finish"
issue: null
epic: null
pr: null
pr_url: ""
status: "beta"
plan_approved: true
plan_approved_at: "2026-11-19T11:07:17+00:00"
plan_approved_by: "human-ui"
plan_approved_sha: "333f73226cf9"
branch: "feat/013-task-token-usage-metadata"
switch: "task_token_usage_metadata"
depends_on: []
owned_paths:
  - "tools/task.py"
  - "tools/task_viewer/server.py"
  - "tools/task_viewer/index.html"
  - "tests/test_task_workflow.py"
gpu_required: false
deliverables:
  - "tasks/013-task-token-usage-metadata/PLAN.md"
  - "tasks/013-task-token-usage-metadata/evidence/verify.json"
  - "tools/task.py"
  - "tools/task_viewer/server.py"
  - "tools/task_viewer/index.html"
  - "tests/test_task_workflow.py"
exec_tier: "flash"
merged_sha: "b5a472c995f4"
merged_into:
  - "v3-dev"
---

# Task 013: Zero-Token Task Token Usage Metadata on Finish

## Problem & Goal
Capture exact per-model token usage (input, cache_read, output) from local Antigravity transcript.jsonl files at zero LLM token cost when finishing a task, store in evidence/usage.json + TASK.md summary, and display in Task Viewer Details.

## Scope
- **In scope:** tools/task.py, tools/task_viewer/server.py, tools/task_viewer/index.html, tests/test_task_workflow.py
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 013` passes with `ok: true`
