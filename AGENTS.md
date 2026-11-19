# Death Fog — AI Agent Guidelines (`AGENTS.md`)

Purpose: Rules and layout guide for AI coding agents working on `SF-DEATH-FOG-DETECTION`.
Status: current
Last verified: 2026-11-18

---

## 1. Self-Contained Repository Layout (Option 1)

Everything for Death Fog lives inside `<repo_root>/` (the repository root):

- `microclimate/` — Python package: wind/fog simulation, 3D tracer advection, and the v3 live pipeline (`microclimate/live/`).
- `deathfog/` — Python package: v2 station collector, wetness scoring, alerts, and status page.
- `site/v3/` — 3D WebGL viewer (`index.html`, `pipeline.html`, JS modules, replay data, static meshes).
- `edge/` — Cloudflare Workers (`bus-proxy`, `cron-trigger`).
- `live_ci/`, `scripts/`, `tools/` — CI runner helpers, state backup scripts, and dev/viewer/task tools (`tools/codemap.py`, `tools/preview.py`, `tools/task.py`).
- `tasks/<id>-<slug>/` — **Gitignored** local-only feature task packets (`TASK.md`, `PLAN.md`, `mockups/`, `evidence/`), resolved via `MAIN_ROOT / "tasks"` (never duplicated in worktrees).
- `.agents/hooks.json`, `.claude/settings.json`, `tools/hooks/` — Cross-agent `PreToolUse` guardrails (H1–H5: clean/force-push/worktree/self-approval/owned-path & pre-build gate guards).
- `.agents/skills/` — Generic, model-agnostic agent skills (`deathfog-task-workflow`, `deathfog-agent-brief`, `deathfog-ship`).
- `spikes/<topic>/` — Experiment scripts and markdown reports (`REPORT.md`). Never commit `.npz` data here.
- `docs/` — All documentation, architecture notes, numbered guides, runbooks, and plans. Start at [`docs/INDEX.md`](docs/INDEX.md) and [`docs/STATUS.md`](docs/STATUS.md).
- `data/` — **Gitignored** local data root (except tracked [`data/MANIFEST.md`](data/MANIFEST.md)). Holds `library/`, `live/`, `raw/`, `grids/`, `refined/`, `spikes/`, `sky_inputs/`. Resolved via `microclimate/paths.py` (`DEATHFOG_DATA`, default `<repo_root>/data`).
- `_archive/` — **Gitignored** cold backups (`2026-11-18/` git bundle, old library `.tar` files, archived project files).
- `_worktrees/` — **Gitignored** directory for any temporary parallel git worktrees (`_worktrees/wt_<topic>`).

> **CRITICAL RULE:** **NEVER run `git clean -fdx` or `git clean -fdX` at the repository root.** Both `data/` (~10 GB of simulation libraries) and `_archive/` (~7 GB of cold backups) live inside the repo root as gitignored folders.

---

## 2. Frozen Production Paths (Do Not Move or Rename)

Production is driven by `.github/workflows/collect.yml` on the `main` branch, which runs every 10/30 minutes and **copies files directly from `origin/v3-dev`**:
```bash
git fetch --depth=1 origin v3-dev
git archive FETCH_HEAD site/v3 | tar -x
git archive FETCH_HEAD live_ci microclimate | tar -x
bash live_ci/refresh.sh build
```
Therefore, **`v3-dev` is live production code**. Never rename or move any of these paths on `v3-dev` without a coordinated workflow update on `main`:
- `site/v3/` (and `site/v3/data/bus_schedule.json` committed by `bus_schedule.yml`)
- `live_ci/` (including `live_ci/refresh.sh`)
- `microclimate/` (and `microclimate/live/`)
- `deathfog/`
- `run.py`
- `scripts/state_restore.sh` and `scripts/state_save.sh`
- `config.toml`, `microclimate.toml`, `routes.toml`, `routes_drawn.json`

Never reorganize or force-push the service branches `data` (v2 rolling state) or `live-archive` (live run history).

---

## 3. Fast, Token-Efficient Navigation & Local Preview

Before reading large files (`site/v3/app.js`, `site/v3/fog2.js`, `microclimate/wetness.py`, `microclimate/live/forcing.py`), use the on-demand code & contract inspector:

1. **Repo or file outline with live `[Lstart-Lend]` line numbers:**
   ```powershell
   .\.venv\Scripts\python.exe tools/codemap.py
   .\.venv\Scripts\python.exe tools/codemap.py site/v3/app.js
   ```
2. **Live feature switches (`PROMOTED` vs `BETA`) and `.npz` schemas:**
   ```powershell
   .\.venv\Scripts\python.exe tools/codemap.py --contracts
   ```
3. **Local viewer server & headless Playwright verification:**
   ```powershell
   .\.venv\Scripts\python.exe tools/preview.py --serve
   .\.venv\Scripts\python.exe tools/preview.py --check "?beta=1&night=2026-10-01"
   ```
4. **Comment convention (enforced by `tests/test_repo_hygiene.py`):**
   - Add concise 1-line docstrings (Python) or `///` / `/** ... */` comments (JS) on functions, and `# @sec: <name>` / `// @sec: <name>` section markers in files over 350 lines so `tools/codemap.py` always indexes them with zero drift.

---

## 4. Running Tests

Run both test suites before committing or merging to `v3-dev`:

1. **Python + repo hygiene tests (v2 + v3 microclimate):**
   ```powershell
   .\.venv\Scripts\pytest.exe
   ```
   *(Configured via `pyproject.toml`; runs `tests/` and `tests_mc/`, including `tests/test_repo_hygiene.py`.)*
2. **JavaScript tests (viewer + Cloudflare workers):**
   ```powershell
   node --test tests_js/*.mjs
   ```

---

## 5. Task Lifecycle, Worktree & Deploy Discipline (`.agents/skills/`)

- **Skills (model-agnostic):** Follow [`.agents/skills/deathfog-task-workflow/SKILL.md`](.agents/skills/deathfog-task-workflow/SKILL.md), [`.agents/skills/deathfog-agent-brief/SKILL.md`](.agents/skills/deathfog-agent-brief/SKILL.md), and [`.agents/skills/deathfog-ship/SKILL.md`](.agents/skills/deathfog-ship/SKILL.md). Do not add vendor-specific skill folders (like `.claude/skills/`).
- **Active trunk:** `v3-dev`. Scope major features into DAG tasks via `.\.venv\Scripts\python.exe tools/task.py new ...` (starts at `brainstorm` gate), inspect conflict-free parallel waves with `tools/task.py dag`, advance human-approved gates with `tools/task.py approve <id>` (`brainstorm -> plan -> ready`), and start each approved task in its own worktree (`_worktrees/wt_<id>-<slug>`) with `tools/task.py start <id>`.
- **Stage gates & Plan-Heavy / Execute-Cheap flow:** Never edit repo code or start a worktree while a task is in `brainstorm` or `plan` — keep `PLAN.md` updated live during brainstorming (recording only `[Lstart-Lend]` `codemap.py` ranges already read; delegate UI mockups in `mockups/` to `invoke_subagent(TypeName="self", Model="flash", ...)`), and wait for formal human plan approval (`tools/task.py approve <id> --approve-plan` or Task Viewer **Approve Plan & Unlock Build**, which stamps `plan_approved: true` and `plan_approved_sha`). **Agents must never self-approve a plan** (`--approve-plan` / `plan_approved: true`) without explicit human approval in chat or the Task Viewer. `--force` on `start` does not bypass the formal plan approval gate or post-approval `PLAN.md` drift. For small tasks, use `tools/task.py new --quick` (starts at `plan`, `exec_tier: flash`). `tools/task.py start <id>` auto-prints the canonical `--worker` brief (omit with `--no-brief`): use `swarm` for multi-worker features (e.g., `Hydrogen` + `flash` with `workspace: "share"`), pinning `Hydrogen`/`Sonnet`, or parallel Wave 1 tasks (batch `swarm(title)` + `swarm(add)` and `swarm(kill)` + `finish`; skip `swarm(models)`), and use `invoke_subagent(..., Model="flash")` for standalone `flash` tasks (reusing mockup agents via `send_message`). Workers batch independent `read_file` calls, combine targeted `pytest` + `verify` runs, and run `.\.venv\Scripts\python.exe tools/task.py pr <id>` right after `verify <id>` passes so the PR is visible in GitHub and the Task Viewer (never merging without approval). Keep the coder agent idle (`invoke_subagent` idle or `swarm` `waiting` — zero token cost while idle) during PR review so it can handle PR feedback/plan revisions via `send_message` or board `@mention` with warm context; only `swarm(kill)` once `finish` merges the PR. When worker `evidence/verify.json` has `"ok": true` matching `HEAD`, the coordinator reviews `tools/task.py diff <id>` + `verify.json` without re-running tests. Always **ask the user** before running `/compact` or starting a fresh chat — after `finish` when more tasks are queued, and before >2m review pauses on >60k-token threads (never compact silently). Link the active conversation via `tools/task.py link <id> --conv <id> --tool antigravity|claude`. `HANDOFF.md` is strictly **optional & overwrite-only** (never a running log).
- **Self-contained task packets:** Keep each task's `TASK.md`, `PLAN.md`, `HANDOFF.md`, `conversations.json`, `mockups/`, and `evidence/` inside gitignored local-only `MAIN_ROOT/tasks/<id>-<slug>/` (resolved automatically across worktrees; `.agents/hooks.json` + `.claude/settings.json` enforce H1–H5 automatically). Gate new viewer features behind `featOn('<switch>')` (`?beta=1&feat=<switch>`) so parallel features can deploy to Beta without colliding.
- **Sync & test restraint:** Trust `.\.venv\Scripts\python.exe tools/task.py sync-main --check`; if exit 0, stop immediately without exploratory `git log`/`ls-tree` loops. Do not add new tests to `tests/test_task_workflow.py` unless `PLAN.md` explicitly requires new testable behavior.
- **Verification & cleanup:** Run `.\.venv\Scripts\python.exe tools/task.py verify <id>` before resolving any task, then `tools/task.py finish <id> --merge` (add `--deploy-beta` to trigger Beta CI) to merge and remove `_worktrees/wt_<id>-<slug>`.
- **Worktrees:** Do not create worktrees in `AppData\Local\Temp` or as sibling folders in `source\repos\`. Always place them in `_worktrees/wt_<id>-<slug>` (`microclimate/paths.py` and `tools/preview.py` auto-resolve `data/` and `site/v3/live*` from the parent checkout).
- **Large files:** No binary over 5 MB or new daily generated output in git. Store simulation data in `data/` (`$DEATHFOG_DATA`) and document it in `data/MANIFEST.md`.
