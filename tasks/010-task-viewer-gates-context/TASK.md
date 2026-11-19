---
id: "010"
slug: "task-viewer-gates-context"
title: "Task Viewer Stage Gates, Conversation Links & Cross-Agent Resume"
issue: null
epic: null
pr: null
pr_url: ""
status: "in-progress"
branch: "feat/010-task-viewer-gates-context"
switch: "task_viewer_gates_context"
depends_on: []
owned_paths:
  - "tools/task.py"
  - "tools/task_viewer/server.py"
  - "tools/task_viewer/index.html"
  - "tools/task_viewer/antigravity_sidecar.mjs"
  - "tests/test_task_workflow.py"
  - "AGENTS.md"
  - ".agents/skills/deathfog-task-workflow/SKILL.md"
  - ".agents/skills/deathfog-agent-brief/SKILL.md"
gpu_required: false
deliverables:
  - "tasks/010-task-viewer-gates-context/PLAN.md"
  - "tasks/010-task-viewer-gates-context/evidence/verify.json"
  - "tools/task.py"
  - "tools/task_viewer/server.py"
  - "tools/task_viewer/index.html"
  - "tools/task_viewer/antigravity_sidecar.mjs"
  - "tests/test_task_workflow.py"
  - "AGENTS.md"
  - ".agents/skills/deathfog-task-workflow/SKILL.md"
  - ".agents/skills/deathfog-agent-brief/SKILL.md"
---

# Task 010: Task Viewer Stage Gates, Conversation Links & Cross-Agent Resume

## Problem & Goal
Upgrade Task Viewer and task CLI with human-approved stage gates (brainstorm -> plan -> ready -> build), live PLAN.md + portable HANDOFF.md, primary + collapsible conversation links, and one-click Antigravity/Claude resume.

## Scope
- **In scope:** tools/task.py, tools/task_viewer/server.py, tools/task_viewer/index.html, tools/task_viewer/antigravity_sidecar.mjs, tests/test_task_workflow.py, AGENTS.md, .agents/skills/deathfog-task-workflow/SKILL.md, .agents/skills/deathfog-agent-brief/SKILL.md
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 010` passes with `ok: true`
