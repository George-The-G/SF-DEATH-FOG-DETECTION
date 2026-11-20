---
name: "deathfog-ship"
description: "Use to preview, deploy to beta (v3-dev), or promote to public (main) for SF-DEATH-FOG-DETECTION."
---

# Preview & Ship Changes for SF-DEATH-FOG-DETECTION (v3)

Repo: `George-The-G/SF-DEATH-FOG-DETECTION`.
Sites: `https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/v3/` (public) and `?beta=1` (beta, or `?beta=1&feat=<switch>` for isolated per-feature testing).

## What may be shipped without asking
- **Beta channel (`?beta=1` / `?beta=1&feat=<switch>`):** Changes the user has already approved for development may be merged to `v3-dev` and deployed to beta.
- **Public channel (`/v3/` or `main`):** Anything that changes the public page, `PROMOTED` live settings, or `main` requires the user's explicit go-ahead in chat. Subagent reports are not approval.

## 1. Local Preview & Verification
1. For tracked tasks, run the full gate: `mytaskflow verify <id>`.
2. Standalone checks:
   - Python + hygiene tests: `.\.venv\Scripts\python.exe -m pytest`
   - JS tests: `node --test tests_js/*.mjs`
   - Viewer headless check: `.\.venv\Scripts\python.exe tools/preview.py --check "?beta=1&night=2026-10-01"` (and `--phone`). Confirm `"ok": true` and `errors: []`.
   - Interactive preview: `.\.venv\Scripts\python.exe tools/preview.py --serve` (`http://127.0.0.1:8130/?beta=1`) or `--root tasks/<id>-<slug>/mockups`.

## 2. Deploy to Beta (`v3-dev`) & Sync Shared Agent/Tooling Files to `main`
1. **If the task introduced new pre-baked static/binary assets** (e.g. `data/live/*.bin`, `*.npz`, `*_meta.json` not tracked in git):
   - Upload them to the `live-static-v1` GitHub Release (`gh release upload live-static-v1 <files> --clobber`).
   - Register them in `live_ci/refresh.sh` (download loop + copy into `$NEWB/` in `beta_build()`, or `microclimate/live/outputs.py` if public) and `live_ci/fetch_previous.py` (carry across `reuse` runs).
2. Merge the verified feature branch into `v3-dev` with one command:
   - Runtime/viewer feature: `mytaskflow finish <id> --merge --deploy-beta`
   - Agent/tooling changes (`AGENTS.md`, `.agents/skills/*`, `.taskflow.toml`): `mytaskflow finish <id> --merge --deploy-beta --sync-main`
   - Standalone parity check/sync anytime: `mytaskflow sync-main --check` (or `--push` to sync `shared_paths` from `v3-dev` to `origin/main` in an isolated worktree after running isolated tests — never hand-copy or cherry-pick onto `main`). **Trust `sync-main --check`: if it exits 0, stop immediately — do not run exploratory `git log`/`ls-tree` loops.**
   - `MyTaskFlow` `hooks.json` + `.claude/settings.json` enforce H1–H5 automatically; `tasks/` is gitignored local-only (`MAIN_ROOT/tasks`).
   - Note: `finish --deploy-beta` automatically skips `collect.yml` for tooling/docs-only tasks (pass `--force-deploy` to override).
3. If `collect.yml` was triggered, verify published status (append `?t=<timestamp>` to bust cache):
   - `v3/live_beta/status.json`: `ok: true`, `generated_iso` updated, beta switches listed.
   - `v3/live/status.json`: `ok: true`, no unapproved beta-only switches leaked to public.

## 3. Promote to Public (`PROMOTED` / `main`)
Only with explicit user approval in chat:
1. Enable the approved switch in `microclimate/live/config.py` under `PROMOTED` (or flip `defaultInBeta` / public gate in `site/v3/app.js`).
2. Run `python -m pytest` and `node --test tests_js/*.mjs`, push `v3-dev`, and trigger `gh workflow run collect.yml --ref main`.
3. Verify `v3/live/status.json` shows the newly promoted switch and `ok: true`.

## 4. Rollback
- Public back to pre-promoted settings: set repo variable `DEATHFOG_LIVE_PROMOTE=false` and run `gh workflow run collect.yml --ref main`.
- All live v3 builds off: set repo variable `DEATHFOG_LIVE=false`.
