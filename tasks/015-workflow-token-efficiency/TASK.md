---
id: "015"
slug: "workflow-token-efficiency"
title: "Workflow Token Efficiency Improvements"
issue: null
epic: null
pr: null
pr_url: ""
status: "in-progress"
plan_approved: true
plan_approved_at: "2026-11-19T12:10:31+00:00"
plan_approved_by: "human-cli"
plan_approved_sha: "e41be1557600"
branch: "feat/015-workflow-token-efficiency"
switch: "workflow_token_efficiency"
depends_on: []
owned_paths:
  - "tools/task.py"
  - ".agents/skills/deathfog-task-workflow/SKILL.md"
  - ".agents/skills/deathfog-agent-brief/SKILL.md"
  - "AGENTS.md"
  - "tests/test_task_workflow.py"
gpu_required: false
deliverables:
  - "tasks/015-workflow-token-efficiency/PLAN.md"
  - "tasks/015-workflow-token-efficiency/evidence/verify.json"
  - "tools/task.py"
  - ".agents/skills/deathfog-task-workflow/SKILL.md"
  - ".agents/skills/deathfog-agent-brief/SKILL.md"
  - "AGENTS.md"
  - "tests/test_task_workflow.py"
exec_tier: "flash"
---

# Task 015: Workflow Token Efficiency Improvements

## Problem & Goal
Describe the problem and desired outcome.

## Scope
- **In scope:** tools/task.py, .agents/skills/deathfog-task-workflow/SKILL.md, .agents/skills/deathfog-agent-brief/SKILL.md, AGENTS.md, tests/test_task_workflow.py
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 015` passes with `ok: true`
