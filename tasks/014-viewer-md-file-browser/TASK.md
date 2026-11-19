---
id: "014"
slug: "viewer-md-file-browser"
title: "Rendered Markdown Viewer & Right-Hand Task File Browser"
issue: null
epic: null
pr: null
pr_url: ""
status: "beta"
plan_approved: true
plan_approved_at: "2026-11-19T11:22:10+00:00"
plan_approved_by: "human-cli"
plan_approved_sha: "51abb1d46696"
token_usage: "in:943.9k cache:7.41M out:87.9k (2 convos)"
branch: "feat/014-viewer-md-file-browser"
switch: "viewer_md_file_browser"
depends_on: []
owned_paths:
  - "tools/task_viewer/server.py"
  - "tools/task_viewer/index.html"
  - "tests/test_task_workflow.py"
gpu_required: false
deliverables:
  - "tasks/014-viewer-md-file-browser/PLAN.md"
  - "tasks/014-viewer-md-file-browser/evidence/verify.json"
  - "tools/task_viewer/server.py"
  - "tools/task_viewer/index.html"
  - "tests/test_task_workflow.py"
exec_tier: "hydrogen"
merged_sha: "feee1e094f5d"
merged_into:
  - "v3-dev"
---

# Task 014: Rendered Markdown Viewer & Right-Hand Task File Browser

## Problem & Goal
Add a zero-dependency rendered Markdown viewer (with Rendered/Raw/Edit toggle) and a simultaneous right-hand Task File Browser sidebar inside the Task Viewer plugin so users can browse and preview all task packet files alongside the open document.

## Scope
- **In scope:** tools/task_viewer/server.py, tools/task_viewer/index.html, tests/test_task_workflow.py
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 014` passes with `ok: true`
