---
name: "deathfog-task-workflow"
description: "Use to scope major features into DAG tasks, run parallel worktrees with self-contained plans/mockups/evidence, verify deliverables, and merge to Beta without collisions."
---

# Self-Contained Parallel Task & Worktree Workflow

All feature work that needs a plan and branch uses **`tools/task.py`** so plans, mockups, and verification proofs stay self-contained in `tasks/<id>-<slug>/` inside an isolated worktree (`_worktrees/wt_<id>-<slug>`).

## 1. Scoping a Major Feature (Epic -> DAG Tasks)
1. Inspect affected modules first with `.\.venv\Scripts\python.exe tools/codemap.py <path>` (never read monoliths blindly).
2. Optionally create a GitHub Epic:
   ```powershell
   .\.venv\Scripts\python.exe tools/task.py epic --title "Feature Name" --summary "Goal"
   ```
3. Decompose the feature into atomic tasks with **non-overlapping `owned_paths`**, explicit `--depends`, and a per-feature `--switch`:
   ```powershell
   .\.venv\Scripts\python.exe tools/task.py new --title "..." --slug my-feat --epic 40 --owns site/v3/fog2.js --switch my_feat
   ```
   *(Add `--local` if working offline without GitHub access.)*
4. Inspect the conflict-free parallel execution waves:
   ```powershell
   .\.venv\Scripts\python.exe tools/task.py dag
   ```

## 2. Executing a Single Task (or a Parallel Wave)
For each ready task in **Wave 1** (one agent per task when running in parallel):
1. **Start & brief:**
   ```powershell
   .\.venv\Scripts\python.exe tools/task.py start <id>
   ```
   This checks dependencies and path/GPU collisions, creates branch `feat/<id>-<slug>` + worktree `_worktrees/wt_<id>-<slug>`, and prints live `codemap.py` outlines for `owned_paths`.
2. **Work strictly inside `_worktrees/wt_<id>-<slug>`:**
   - Write the technical plan in `tasks/<id>-<slug>/PLAN.md`.
   - Put any HTML/CSS/shader mockups in `tasks/<id>-<slug>/mockups/` (preview locally with `.\.venv\Scripts\python.exe tools/preview.py --serve --root tasks/<id>-<slug>/mockups`).
   - Edit only files listed in `owned_paths`. Put new viewer behavior behind `featOn('<switch>')` (`?beta=1&feat=<switch>`) or a named switch in `microclimate/live/config.py` (`BETA`).
   - Commit normally inside the worktree with standard `git add` / `git commit`.

## 3. Verification Gate (Required Before Resolution)
Never mark a task done until the automated gate passes:
```powershell
.\.venv\Scripts\python.exe tools/task.py verify <id>
```
This checks `TASK.md` schema, required `deliverables`, `owned_paths` scope, `pytest` (including `test_repo_hygiene.py`), `node --test tests_js/*.mjs`, and headless Playwright desktop+phone previews (for `site/v3/` tasks), writing `tasks/<id>-<slug>/evidence/verify.json`.

## 4. Merge to Beta & Cleanup
Once `verify.json` has `"ok": true` matching `HEAD`:
```powershell
.\.venv\Scripts\python.exe tools/task.py finish <id> --merge
```
Add `--deploy-beta` to push `v3-dev` and trigger `gh workflow run collect.yml --ref main`. Never promote to public (`/v3/` or `PROMOTED`) without explicit user approval in chat.
