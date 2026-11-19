---
id: "012"
slug: "plan-gate-viewer-redesign"
title: "Formal Plan Approval Gate & Task Viewer Layout Redesign"
issue: null
epic: null
pr: null
pr_url: ""
status: "in-progress"
branch: "feat/012-plan-gate-viewer-redesign"
switch: "plan_gate_viewer_redesign"
depends_on: []
owned_paths:
  - "tools/task.py"
  - "tools/task_viewer/server.py"
  - "tools/task_viewer/index.html"
  - "tests/test_task_workflow.py"
  - "AGENTS.md"
  - ".agents/skills/deathfog-task-workflow/SKILL.md"
gpu_required: false
deliverables:
  - "tasks/012-plan-gate-viewer-redesign/PLAN.md"
  - "tasks/012-plan-gate-viewer-redesign/evidence/verify.json"
  - "tools/task.py"
  - "tools/task_viewer/server.py"
  - "tools/task_viewer/index.html"
  - "tests/test_task_workflow.py"
  - "AGENTS.md"
  - ".agents/skills/deathfog-task-workflow/SKILL.md"
exec_tier: "hydrogen"
---

# Task 012: Formal Plan Approval Gate & Task Viewer Layout Redesign

## Problem & Goal
Enforce formal human plan approval before implementation can begin, fix the broken Open Convo link, add Main to the top lifecycle sequence, and redesign the Task Viewer plugin layout with logical grouping and minimal duplicate functionality.

## Scope
- **In scope:** tools/task.py, tools/task_viewer/server.py, tools/task_viewer/index.html, tests/test_task_workflow.py, AGENTS.md, .agents/skills/deathfog-task-workflow/SKILL.md
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 012` passes with `ok: true`
