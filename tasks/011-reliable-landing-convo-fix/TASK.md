---
id: "011"
slug: "reliable-landing-convo-fix"
title: "Reliable Landing, Main/Beta Parity & Antigravity Convo Button Fix"
issue: null
epic: null
pr: null
pr_url: ""
status: "beta"
branch: "feat/011-reliable-landing-convo-fix"
switch: "reliable_landing_convo_fix"
depends_on: []
owned_paths:
  - "tools/task.py"
  - "tools/task_viewer/server.py"
  - "tools/task_viewer/index.html"
  - "tests/test_task_workflow.py"
  - ".agents/skills/deathfog-ship/SKILL.md"
  - ".agents/skills/deathfog-task-workflow/SKILL.md"
gpu_required: false
deliverables:
  - "tasks/011-reliable-landing-convo-fix/PLAN.md"
  - "tasks/011-reliable-landing-convo-fix/evidence/verify.json"
  - "tools/task.py"
  - "tools/task_viewer/server.py"
  - "tools/task_viewer/index.html"
  - "tests/test_task_workflow.py"
  - ".agents/skills/deathfog-ship/SKILL.md"
  - ".agents/skills/deathfog-task-workflow/SKILL.md"
exec_tier: "hydrogen"
merged_sha: "09789233ed58"
merged_into:
  - "v3-dev"
---

# Task 011: Reliable Landing, Main/Beta Parity & Antigravity Convo Button Fix

## Problem & Goal
Make shared agent/tooling files portable and auto-synced between v3-dev and main via tools/task.py sync-main, harden cmd_finish, move landing instructions into deathfog-ship skill (zero token bloat), and fix broken Windows conversation:// links in Task Viewer.

## Scope
- **In scope:** tools/task.py, tools/task_viewer/server.py, tools/task_viewer/index.html, tests/test_task_workflow.py, .agents/skills/deathfog-ship/SKILL.md
- **Out of scope:** Unrelated modules outside `owned_paths`.

## Acceptance Criteria
- [ ] Technical plan written in `PLAN.md`
- [ ] Implementation stays strictly within `owned_paths`
- [ ] `python tools/task.py verify 011` passes with `ok: true`
