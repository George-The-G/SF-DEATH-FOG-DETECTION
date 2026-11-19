---
name: "deathfog-task-workflow"
description: "Use to scope major features into DAG tasks, run parallel worktrees with self-contained plans/mockups/evidence, verify deliverables, and merge to Beta without collisions."
---

# Self-Contained Parallel Task & Worktree Workflow

All feature work that needs a plan and branch uses **`tools/task.py`** so plans, mockups, and verification proofs stay self-contained in `tasks/<id>-<slug>/` inside an isolated worktree (`_worktrees/wt_<id>-<slug>`).

## 1. Stage-Gated Scoping (Brainstorm -> Plan -> Approved)
1. Inspect affected modules first with `.\.venv\Scripts\python.exe tools/codemap.py <path>` (never read monoliths blindly).
2. Optionally create a GitHub Epic:
   ```powershell
   .\.venv\Scripts\python.exe tools/task.py epic --title "Feature Name" --summary "Goal"
   ```
3. Decompose the feature into atomic tasks with **non-overlapping `owned_paths`**, explicit `--depends`, and a per-feature `--switch` (defaults to `status: brainstorm`):
   ```powershell
   .\.venv\Scripts\python.exe tools/task.py new --title "..." --slug my-feat --epic 40 --owns site/v3/fog2.js --switch my_feat
   ```
   *(Add `--local` if working offline, or `--quick` for small changes to start at `plan` with `exec_tier: flash`.)*
4. **Gate 1 (`brainstorm`):** Flesh out goals, decisions, and dead ends live in `tasks/<id>-<slug>/PLAN.md`. Do **not** edit repo code or spin up a worktree. Link the active conversation (`tools/task.py link <id> --conv <uuid> --tool antigravity|claude`). Advance to `plan` only after user approval (`tools/task.py approve <id>` or Task Viewer).
5. **Gate 2 (`plan`):** Fill in architecture, exact `codemap.py` line ranges, owned paths, and verification in `PLAN.md`. Stop and wait for formal human **Plan Approval & Build Go-Ahead** (`tools/task.py approve <id> --approve-plan` or Task Viewer **Approve Plan & Unlock Build**, which stamps `plan_approved: true` and `plan_approved_sha`). **Agents must never self-approve** (`--approve-plan` / `plan_approved: true`) without direct human approval; `start --force` cannot bypass plan approval or post-approval `PLAN.md` drift.
6. Inspect the conflict-free parallel execution waves:
   ```powershell
   .\.venv\Scripts\python.exe tools/task.py dag
   ```

## 2. Executing a Single Task (Plan-Heavy -> Implement-Cheap -> Diff-Review)
For each approved (`ready`) task in **Wave 1** (one agent per task when running in parallel):
1. **Start & brief:**
   ```powershell
   .\.venv\Scripts\python.exe tools/task.py start <id>
   ```
   This enforces the pre-build gate (`brainstorm`/`plan` tasks are blocked), checks dependencies and path/GPU collisions, creates branch `feat/<id>-<slug>` + worktree `_worktrees/wt_<id>-<slug>`, and prints live `codemap.py` outlines for `owned_paths`.
2. **Delegate implementation to the right worker model (do NOT code directly on Neon/Opus when `PLAN.md` is approved):**
   - Generate a lean execution brief with `.\.venv\Scripts\python.exe tools/task.py resume <id> --worker` (or click **Copy Worker Brief** in the Task Viewer).
   - **Exact tool call by `exec_tier` (in Antigravity):**
     - `exec_tier: flash` -> `invoke_subagent(TypeName="self", Model="flash", ...)` (best for mechanical/UI/test/doc edits).
     - `exec_tier: hydrogen` -> `swarm(action="add", name="worker-<id>", model="Gemini 4 Hydrogen (Medium)", brief=<worker brief>)` (`invoke_subagent` only exposes `flash_lite|flash|pro`, so always use `swarm` to pin Hydrogen or Claude Sonnet by name).
     - `exec_tier: standard` / `pro` -> `swarm(action="add", model="Claude Sonnet 5.5 (Medium)" | "Gemini 4 Neon (Medium)", ...)` or direct execution when architecture/subtle debugging requires it.
   - Iterate with targeted tests first (`pytest tests/test_<area>.py`), then run full `tools/task.py verify <id>` once before reporting.
   - `HANDOFF.md` is **optional & overwrite-only** — do not keep a running log; only write a short sticky note if pausing mid-task with non-obvious dead ends.
   - Put any HTML/CSS/shader mockups in `tasks/<id>-<slug>/mockups/` (preview locally with `.\.venv\Scripts\python.exe tools/preview.py --serve --root tasks/<id>-<slug>/mockups`).
   - Edit only files listed in `owned_paths`. Put new viewer behavior behind `featOn('<switch>')` (`?beta=1&feat=<switch>`) or a named switch in `microclimate/live/config.py` (`BETA`).
   - Review the finished work cheaply with `.\.venv\Scripts\python.exe tools/task.py diff <id>` instead of re-reading whole files.

## 3. Verification Gate (Required Before Resolution)
Never mark a task done until the automated gate passes:
```powershell
.\.venv\Scripts\python.exe tools/task.py verify <id>
```
This checks `TASK.md` schema, required `deliverables`, `owned_paths` scope, `pytest` (including `test_repo_hygiene.py`), `node --test tests_js/*.mjs`, and headless Playwright desktop+phone previews (for `site/v3/` tasks), writing `tasks/<id>-<slug>/evidence/verify.json`.

## 4. Open PR, Merge to Beta & Cleanup
Once `verify.json` has `"ok": true` matching `HEAD`:
- **Open a PR for review** (pushes branch, creates GitHub PR against `v3-dev`, and records `pr` & `pr_url` back into `tasks/<id>-<slug>/TASK.md`):
  ```powershell
  .\.venv\Scripts\python.exe tools/task.py pr <id>
  ```
- **Or merge directly to Beta & clean up the worktree:**
  ```powershell
  .\.venv\Scripts\python.exe tools/task.py finish <id> --merge --deploy-beta
  ```
  Add `--sync-main` if the task touched any shared agent/tooling files (`AGENTS.md`, `.agents/skills/*`, `tools/task.py`, `tools/task_viewer/`). Tooling-only tasks automatically skip `collect.yml`. Never promote runtime viewer/physics changes to public (`/v3/` or `PROMOTED`) without explicit user approval in chat.
