---
name: "deathfog-agent-brief"
description: "Use when starting or delegating SF death-fog work: standing rules, token-saving navigation via tools/codemap.py, and prompt template."
---

# Briefing an agent for SF-DEATH-FOG-DETECTION

Read [`AGENTS.md`](../../../AGENTS.md) and [`docs/STATUS.md`](../../../docs/STATUS.md) first. Put the standing rules below in any subagent prompt, then add only the task, approved scope, and report format.

## Standing rules
- **Self-contained layout (Option 1):** Everything lives in `C:\Users\George\source\repos\SF-DEATH-FOG-DETECTION\`. Python: `.\.venv\Scripts\python.exe`. Data root: `data\` (via `microclimate.paths.data_dir()`, which auto-resolves from `_worktrees/wt_*`).
- **Never run `git clean -fdx` or `git clean -fdX`** at the repo root (`data\` and `_archive\` are gitignored inside the repo root).
- **Token-efficient navigation first:** Before reading large files (`site/v3/app.js`, `site/v3/fog2.js`, `microclimate/wetness.py`, `microclimate/live/forcing.py`), run:
  - `python tools/codemap.py <file_or_dir>` to get exact live `[Lstart-Lend]` line numbers for every `@sec:` section and function.
  - `python tools/codemap.py --contracts` to inspect `PROMOTED` vs `BETA` feature switches and `.npz` array schemas without writing probe scripts.
- **Task & worktree discipline:** Use `python tools/task.py dag` to see ready parallel waves, `python tools/task.py start <id>` to spin up `_worktrees/wt_<id>-<slug>` with a self-contained `tasks/<id>-<slug>/` packet (`TASK.md`, `PLAN.md`, `mockups/`, `evidence/`), and `python tools/task.py finish <id> --merge` to merge and remove the worktree. Never create worktrees in `AppData\Local\Temp` or `source\repos\`.
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
