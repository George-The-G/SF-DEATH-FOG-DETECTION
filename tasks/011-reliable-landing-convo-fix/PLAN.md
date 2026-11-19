# Plan — Task 011: Reliable Landing, Main/Beta Parity & Antigravity Convo Button Fix

## Brainstorm — Goals, Decisions & Dead Ends
- **Goals:**
  - Keep shared agent/tooling files (`AGENTS.md`, `CLAUDE.md`, `.agents/skills/*`, `tools/task.py`, `tools/codemap.py`, `tools/task_viewer/`, `tests/test_task_workflow.py`) **byte-for-byte consistent** between `v3-dev` (Beta) and `main` with zero manual git gymnastics.
  - Keep token cost **down** (no extra rules in always-on `AGENTS.md` — move landing automation into deterministic Python + progressive-disclosure `.agents/skills/deathfog-ship/SKILL.md`).
  - Fix the broken Windows *"Get an app to open this 'conversation' link"* popup when clicking **Open Antigravity Convo** in the Task Viewer.
- **Decisions:**
  - Single canonical `SHARED_MAIN_PATHS` list in `tools/task.py` + `tools/task.py sync-main [--check | --push]` (and `--sync-main` on `finish --merge`).
  - Make `tools/task.py` and `tests/test_task_workflow.py` branch-portable (`try/except ImportError` fallback for `microclimate.paths`, `assertIsInstance(state["tasks"], dict)`), tested in an **isolated** `main` worktree with `MAIN_ROOT` stripped from `PYTHONPATH`.
  - Harden `cmd_finish`: stage `verify.json` with `TASK.md`, remove untracked duplicate `MAIN_ROOT/tasks/<id>/` before merge, verify `git worktree remove` returncode, and skip `collect.yml` on tooling-only tasks.
  - Optional Task Viewer header badge showing live `main ↔ v3-dev` shared-file parity + 1-click **Sync Shared → main** button.
  - Replace all raw `<a href="conversation://...">` links in `tools/task_viewer/index.html` with a `<button>` using `sidecar.agent.sendMessage` (with clipboard fallback).
- **Mockup:** `tasks/011-reliable-landing-convo-fix/mockups/convo_jump_mockup.html` (viewable directly in Task Viewer → Task 011 → Mockups tab to probe `window.sidecar` live inside Antigravity!).

## Plan — Architecture, Owned Paths & Verification
- **Owned Paths:** `tools/task.py`, `tools/task_viewer/server.py`, `tools/task_viewer/index.html`, `tests/test_task_workflow.py`, `.agents/skills/deathfog-ship/SKILL.md`.
- **Verification:**
  1. `.\.venv\Scripts\python.exe tools/task.py verify 011 --skip-preview` (full `pytest` + `node --test`).
  2. `.\.venv\Scripts\python.exe tools/task.py sync-main --check` and isolated `sync-main --push` verification confirming `git diff origin/main v3-dev -- <SHARED_MAIN_PATHS>` is empty.
