---
name: "deathfog-agent-brief"
description: "Use when starting or delegating SF death-fog work: standing rules, token-saving navigation via tools/codemap.py, and prompt template."
---

# Briefing an agent for SF-DEATH-FOG-DETECTION

Read [`AGENTS.md`](../../../AGENTS.md) and [`docs/STATUS.md`](../../../docs/STATUS.md) first. Put the standing rules below in any subagent prompt, then add only the task, approved scope, and report format.

## Standing rules
- **Self-contained layout (Option 1):** Everything lives inside `<repo_root>/` (the repository root). Python: `.\.venv\Scripts\python.exe` (or `.venv/bin/python`). Data root: `data/` (via `microclimate.paths.data_dir()`, which auto-resolves from `_worktrees/wt_*`). `tasks/` is gitignored local-only (`MAIN_ROOT/tasks`, never duplicated in worktrees).
- **Automated H1–H5 hooks:** `.agents/hooks.json` (Antigravity) and `.claude/settings.json` (Claude Code) run `tools/hooks/guard.py` automatically on tool calls to block `git clean -fdx`, force-pushes to protected branches, out-of-tree worktrees, unconfirmed `--approve-plan`, pre-build main edits, and prompt on unowned worktree edits.
- **Never run `git clean -fdx` or `git clean -fdX`** at the repo root (`data\` and `_archive\` are gitignored inside the repo root).
- **Single-command `main` sync & test restraint:** Trust `tools/task.py sync-main --check` — if exit 0, stop immediately (no exploratory `git log`/`ls-tree` loops). Do not add new tests to `tests/test_task_workflow.py` unless `PLAN.md` explicitly requires new testable behavior.
- **Token-efficient navigation first:** Before reading large files (`site/v3/app.js`, `site/v3/fog2.js`, `microclimate/wetness.py`, `microclimate/live/forcing.py`), run:
  - `python tools/codemap.py <file_or_dir>` to get exact live `[Lstart-Lend]` line numbers for every `@sec:` section and function.
  - `python tools/codemap.py --contracts` to inspect `PROMOTED` vs `BETA` feature switches and `.npz` array schemas without writing probe scripts.
- **Task, stage-gate & token-efficient execution:** Respect human-approved gates (`scope -> plan -> approved -> build`); never edit code or run `start` before the user approves `approved`. Keep `PLAN.md` live during scoping with exact `[Lstart-Lend]` `codemap.py` ranges already read (no extra reads), delegate UI mockups to `invoke_subagent(TypeName="self", Model="flash", ...)` (reuse via `send_message` if the same `flash` agent implements), and link the active conversation (`python tools/task.py link <id> --conv <uuid> --tool antigravity|claude`). `tools/task.py start <id>` auto-prints the `--worker` brief (or pass `--no-brief`); use `swarm` for multi-worker/Hydrogen/Sonnet/parallel Wave 1 work (batch `swarm(title)`+`swarm(add)` and `swarm(kill)`+`finish`, skip `swarm(models)`), or `invoke_subagent(..., Model="flash")` for standalone `flash` work. Workers batch independent `read_file` calls, combine targeted `pytest` + `verify` runs, and run `python tools/task.py pr <id>` once `verify <id>` passes so the PR appears in the Task Viewer (never merging without approval). Keep the coder agent idle (`invoke_subagent` idle or `swarm` `waiting` — zero token cost) during PR review to handle feedback/plan revisions with warm context; only `swarm(kill)` after `finish` merges the PR. Coordinators review `diff <id>` + `verify.json` without re-running passing `HEAD` checks, and **ask the user** before `/compact` (on >60k threads before >2m pauses, and after `finish` if more tasks are queued). `HANDOFF.md` is optional & overwrite-only. Never create worktrees in `AppData\Local\Temp` or `source\repos\`.
- **GPU discipline:** Before any GPU job, check `Get-Process python`, the status JSONs in `data\library\runs\`, and `data\library\runs\gpu.lock`. Only one GPU run at a time.
- **Safety & switches:** Never force-push. Never touch `main` except through an approved PR. Gate new viewer features behind `featOn('<switch>')` (`?beta=1&feat=<switch>`) and new physics pieces behind a named `BETA` switch, off for public (`PROMOTED`), until the user approves.
- **Verification before merge:** Run `python tools/task.py verify <id>` (which enforces deliverables, `owned_paths` scope, `pytest`, `node --test tests_js/*.mjs`, and headless `tools/preview.py --check` for viewer changes).

## Prompt template for subagents
1. Task ID and approved scope (`tasks/<id>-<slug>/TASK.md`, `owned_paths`, what is NOT approved).
2. Worktree `_worktrees/wt_<id>-<slug>` on branch `feat/<id>-<slug>` (created via `tools/task.py start <id>`).
3. Specific constraints (switch name, GPU yes/no).
4. Verification required (`python tools/task.py verify <id>`).
5. Finish action (commit in worktree, merge into `v3-dev` yes/no, deploy beta yes/no).
6. Report format: concise; files changed, test count, numbers, evidence paths, risks.
