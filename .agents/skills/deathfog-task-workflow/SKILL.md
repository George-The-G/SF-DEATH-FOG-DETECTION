---
name: "deathfog-task-workflow"
description: "Use to scope major features into DAG tasks, run parallel worktrees with self-contained plans/mockups/evidence, verify deliverables, and merge to Beta without collisions."
---

# Self-Contained Parallel Task & Worktree Workflow

All feature work that needs a plan and branch uses **`tools/task.py`** so plans, mockups, and verification proofs stay self-contained in gitignored local-only `MAIN_ROOT/tasks/<id>-<slug>/` (never duplicated in worktrees `_worktrees/wt_<id>-<slug>`). Lifecycle guardrails H1–H5 are enforced automatically via `.agents/hooks.json` and `.claude/settings.json` (`tools/hooks/guard.py`). Do not add new tests to `tests/test_task_workflow.py` unless `PLAN.md` explicitly requires new testable behavior.

## 1. Stage-Gated Scoping (Scope -> Plan -> Approved)
1. Inspect affected modules first with `.\.venv\Scripts\python.exe tools/codemap.py <path>` (never read monoliths blindly).
2. Optionally create a GitHub Epic:
   ```powershell
   .\.venv\Scripts\python.exe tools/task.py epic --title "Feature Name" --summary "Goal"
   ```
3. Decompose the feature into atomic tasks with **non-overlapping `owned_paths`**, explicit `--depends`, and a per-feature `--switch` (defaults to `status: scope`):
   ```powershell
   .\.venv\Scripts\python.exe tools/task.py new --title "..." --slug my-feat --epic 40 --owns site/v3/fog2.js --switch my_feat
   ```
   *(Add `--local` if working offline, or `--quick` for small changes to start at `plan` with `exec_tier: flash`.)*
4. **Gate 1 (`scope`):** Flesh out goals, decisions, and dead ends live in `tasks/<id>-<slug>/PLAN.md`. Do **not** edit repo code or spin up a worktree. If a UI mockup is needed, write a concise layout/behavior spec in `PLAN.md` and delegate `tasks/<id>-<slug>/mockups/<name>.html` to `invoke_subagent(TypeName="self", Model="flash", ...)` (or a `flash` swarm member collaborating after `start`). Link the active conversation (`tools/task.py link <id> --conv <uuid> --tool antigravity|claude`). **Ambiguity Gate:** both `scope -> plan` and `plan -> approved` block if `- **Open Questions:**` has `TBD`/`TODO`/unresolved items — self-check, record open ambiguities in `PLAN.md`, and ask concise clarifying questions until `- **Open Questions:** None` before advancing. Advance to `plan` only after user approval (`tools/task.py approve <id>` or Task Viewer, which auto-triggers a compact chat prompt).
5. **Gate 2 (`plan`):** Fill in architecture, exact `[Lstart-Lend]` `codemap.py` ranges/symbols already read during drafting (avoid extra coordinator reads), owned paths, `- **Open Questions:** None`, and verification in `PLAN.md`. On threads >60k tokens, **ask the user** before stepping away for a long review pause (>2m, e.g., mockup/plan review) whether to `/compact` first to avoid cold-cache re-reads (never compact silently). Wait for formal human **Plan Approval & Build Go-Ahead** (`tools/task.py approve <id> --approve-plan` or Task Viewer **Approve Plan & Unlock Build**, which stamps `plan_approved: true` and `plan_approved_sha` and sends a compact build trigger to chat). **Agents must never self-approve** (`--approve-plan` / `plan_approved: true`) without direct human approval; `start --force` cannot bypass plan approval or post-approval `PLAN.md` drift.
6. Inspect the conflict-free parallel execution waves:
   ```powershell
   .\.venv\Scripts\python.exe tools/task.py dag
   ```

## 2. Executing a Single Task (Plan-Heavy -> Implement-Cheap -> Diff-Review)
For each approved (`approved`) task in **Wave 1** (one agent per task when running in parallel):
1. **Start & brief:**
   ```powershell
   .\.venv\Scripts\python.exe tools/task.py start <id>
   ```
   This enforces the pre-build gate (`scope`/`plan` tasks are blocked), checks dependencies and path/GPU collisions, creates branch `feat/<id>-<slug>` + worktree `_worktrees/wt_<id>-<slug>`, and auto-prints the canonical `--worker` brief + recommended delegation (pass `--no-brief` to omit, or re-generate later with `tools/task.py resume <id> --worker`).
2. **Delegate implementation to the right worker model (do NOT code directly on Neon/Opus when `PLAN.md` is approved):**
   - **Multi-agent `swarm` (large/collaborative features) vs `invoke_subagent` (fast path):**
     - Use `swarm` whenever a task benefits from **multiple collaborating agents** (e.g., `Gemini 4 Hydrogen (Medium)` backend worker + `flash` UI/mockup/test helper on the shared board with `workspace: "share"`), when pinning `Hydrogen`/`Sonnet` by name, or across **Wave 1 parallel DAG tasks** in separate worktrees. **Swarm turn hygiene:** batch `swarm(title)` + `swarm(add)` in one turn (skip `swarm(models)` since exact model names are listed here), keep workers idle (`waiting`) during PR review, and batch `swarm(kill)` with `finish` once merged.
     - Use `invoke_subagent(TypeName="self", Model="flash", ...)` for standalone `flash` tasks and standalone mockups; if the same `flash` agent will also implement after plan approval, reuse it via `send_message(Recipient=<conv_id>, ...)` to preserve mockup context.
     - `exec_tier: standard` / `pro` -> `swarm(action="add", model="Claude Sonnet 5.5 (Medium)" | "Gemini 4 Neon (Medium)", ...)` or direct execution when architecture/subtle debugging requires it.
   - Workers batch independent `read_file` calls together, combine targeted `pytest` + `verify` runs cleanly (avoid sequential single-command turns), and run `python tools/task.py pr <id>` once `verify <id>` passes so the PR is immediately visible in GitHub and the Task Viewer (never merging into `v3-dev`/`main` without approval).
   - `HANDOFF.md` is **optional & overwrite-only** — do not keep a running log; only write a short sticky note if pausing mid-task with non-obvious dead ends.
   - Put any HTML/CSS/shader mockups in `tasks/<id>-<slug>/mockups/` (preview locally with `.\.venv\Scripts\python.exe tools/preview.py --serve --root tasks/<id>-<slug>/mockups`).
   - Edit only files listed in `owned_paths`. Put new viewer behavior behind `featOn('<switch>')` (`?beta=1&feat=<switch>`) or a named switch in `microclimate/live/config.py` (`BETA`).
   - Review the finished work cheaply with `.\.venv\Scripts\python.exe tools/task.py diff <id>` instead of re-reading whole files; when the worker's `evidence/verify.json` has `"ok": true` matching `HEAD`, skip duplicate coordinator `pytest`/`verify` runs. Keep the coder agent idle (`invoke_subagent` idle or `swarm` `waiting` — zero token cost while idle) during PR review so it can handle PR feedback or plan revisions via `send_message` / board `@mention` with warm context; only `swarm(kill)` once `finish` merges the PR.

## 3. Verification Gate (Required Before Resolution)
Never mark a task done until the automated gate passes:
```powershell
.\.venv\Scripts\python.exe tools/task.py verify <id>
```
This checks `TASK.md` schema, required `deliverables`, `owned_paths` scope, `pytest` (including `test_repo_hygiene.py`), `node --test tests_js/*.mjs`, and headless Playwright desktop+phone previews (for `site/v3/` tasks), writing `tasks/<id>-<slug>/evidence/verify.json`.

## 4. Open PR, Merge to Beta & Cleanup
Once `verify.json` has `"ok": true` matching `HEAD`:
- **Open a PR for review** (workers run this automatically right after `verify <id>` passes; pushes branch, creates GitHub PR against `v3-dev`, and records `pr` & `pr_url` back into `tasks/<id>-<slug>/TASK.md`):
  ```powershell
  .\.venv\Scripts\python.exe tools/task.py pr <id>
  ```
- **Or merge directly to Beta & clean up the worktree** (zero-token **Land on Beta** button in Task Viewer, or CLI):
  ```powershell
  .\.venv\Scripts\python.exe tools/task.py finish <id> --merge --deploy-beta
  ```
  `finish --merge` guards that `MAIN_ROOT` is on `v3-dev`. Add `--sync-main` if the task touched any shared agent/tooling files (`AGENTS.md`, `.agents/skills/*`, `tools/task.py`, `tools/task_viewer/`). Tooling-only tasks automatically skip `collect.yml`. Never promote runtime viewer/physics changes to public (`/v3/` or `PROMOTED`) without explicit user approval in chat.
- **Land on Main (zero-token button in Task Viewer):** Once in Beta, `land_task_to_main` runs `sync_shared_to_main(push=True)` for `SHARED_MAIN_PATHS` tasks, triggers `collect.yml --ref main` (requiring confirmation if `unpromoted_switch` is true) for `site/v3/`/`microclimate/` tasks, or stamps `merged_into: [..., "main"]` + `status: done` for docs/other tasks.
  After `finish`, if more tasks are queued, **always ask the user** whether to run `/compact` or start a fresh conversation before starting the next task (never compact silently).
