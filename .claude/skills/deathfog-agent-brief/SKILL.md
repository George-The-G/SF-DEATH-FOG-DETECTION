---
name: "deathfog-agent-brief"
description: "Use when starting or delegating SF death-fog work: standing rules, token-saving navigation via tools/codemap.py, and prompt template."
---

# Briefing an agent for SF-DEATH-FOG-DETECTION

Read [`AGENTS.md`](../../../AGENTS.md) and [`docs/STATUS.md`](../../../docs/STATUS.md) first. Put the standing rules below in any subagent prompt, then add only the task, approved scope, and report format.

## Standing rules
- **Self-contained layout (Option 1):** Everything lives in `C:\Users\George\source\repos\SF-DEATH-FOG-DETECTION\`. Python: `.\.venv\Scripts\python.exe`. Data root: `data\` (via `microclimate.paths.data_dir()`).
- **Never run `git clean -fdx` or `git clean -fdX`** at the repo root (`data\` and `_archive\` are gitignored inside the repo root).
- **Token-efficient navigation first:** Before reading large files (`site/v3/app.js`, `site/v3/fog2.js`, `microclimate/wetness.py`, `microclimate/live/forcing.py`), run:
  - `python tools/codemap.py <file_or_dir>` to get exact live `[Lstart-Lend]` line numbers for every `@sec:` section and function.
  - `python tools/codemap.py --contracts` to inspect `PROMOTED` vs `BETA` feature switches and `.npz` array schemas without writing probe scripts.
- **Branch & worktree discipline:** Work directly in the main checkout on a short-lived branch (`feat/<topic>`, `sim/<NN>-<topic>`, `fix/<topic>`) off `v3-dev`. Only create a worktree when parallel agents edit simultaneously, place it in `_worktrees/wt_<name>`, and remove it (`git worktree remove`) immediately after merge. Never create worktrees in `AppData\Local\Temp` or `source\repos\`.
- **GPU discipline:** Before any GPU job, check `Get-Process python`, the status JSONs in `data\library\runs\`, and `data\library\runs\gpu.lock`. Only one GPU run at a time.
- **Safety & switches:** Never force-push. Never touch `main` except through an approved PR. Each new physics piece goes behind a named switch, off for public (`PROMOTED`), until the user approves.
- **Verification before merge:** Both `python -m pytest` (which includes `tests/test_repo_hygiene.py`) and `node --test tests_js/*.mjs` must pass. For viewer changes, run `python tools/preview.py --check "?beta=1"` and `--phone`.

## Prompt template for subagents
1. Goal and approved scope (what is in, what is explicitly NOT approved).
2. Branch (or `_worktrees/wt_<name>` if parallel) based on `origin/v3-dev`.
3. Specific constraints (switch name, files owned, GPU yes/no).
4. Verification required (`python -m pytest`, `node --test tests_js/*.mjs`, `python tools/preview.py --check`).
5. Finish action (commit/push branch, merge into `v3-dev` yes/no, deploy yes/no).
6. Report format: concise; files changed, test count, numbers, screenshot paths, risks.
