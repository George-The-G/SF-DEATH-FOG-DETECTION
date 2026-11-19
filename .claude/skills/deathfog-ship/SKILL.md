---
name: "deathfog-ship"
description: "Use to preview, deploy to beta (v3-dev), or promote to public (main) for SF-DEATH-FOG-DETECTION."
---

# Preview & Ship Changes for SF-DEATH-FOG-DETECTION (v3)

Repo: `George-The-G/SF-DEATH-FOG-DETECTION`.
Sites: `https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/v3/` (public) and `?beta=1` (beta).

## What may be shipped without asking
- **Beta channel (`?beta=1`):** Changes the user has already approved for development may be pushed to `v3-dev` and deployed to beta.
- **Public channel (`/v3/` or `main`):** Anything that changes the public page, `PROMOTED` live settings, or `main` requires the user's explicit go-ahead in chat. Subagent reports are not approval.

## 1. Local Preview & Verification
1. Run Python + hygiene tests: `.\.venv\Scripts\python.exe -m pytest` (all tests in `tests/` and `tests_mc/` must pass, including `test_repo_hygiene.py`).
2. Run JS tests: `node --test tests_js/*.mjs`.
3. For viewer changes (`site/v3/`):
   - Headless check: `.\.venv\Scripts\python.exe tools/preview.py --check "?beta=1&night=2026-10-01"` (and `--phone`). Confirm `"ok": true` and `errors: []`.
   - Interactive preview: `.\.venv\Scripts\python.exe tools/preview.py --serve` (`http://127.0.0.1:8130/?beta=1`).

## 2. Deploy to Beta (`v3-dev`)
1. Merge the feature branch into `v3-dev` and re-run `python -m pytest` and `node --test tests_js/*.mjs`.
2. Push to `origin`: `git push origin v3-dev` (never force-push).
3. Trigger the GitHub Actions deploy directly from the repo root:
   - `gh workflow run collect.yml --ref main`
   - `gh run list --workflow collect.yml --limit 1`
   - `gh run watch <id> --exit-status` (takes ~3–6 min; `collect.yml` on `main` automatically copies `site/v3`, `live_ci`, and `microclimate` from `origin/v3-dev`).
4. Verify published status (append `?t=<timestamp>` to bust cache):
   - `v3/live_beta/status.json`: `ok: true`, `generated_iso` updated, beta switches listed.
   - `v3/live/status.json`: `ok: true`, no unapproved beta-only switches leaked to public.

## 3. Promote to Public (`PROMOTED` / `main`)
Only with explicit user approval in chat:
1. Enable the approved switch in `microclimate/live/config.py` under `PROMOTED` (or update `.github/workflows/collect.yml` on `main` if workflow-level).
2. Run `python -m pytest` and `node --test tests_js/*.mjs`, push `v3-dev`, and trigger `gh workflow run collect.yml --ref main`.
3. Verify `v3/live/status.json` shows the newly promoted switch and `ok: true`.

## 4. Rollback
- Public back to pre-promoted settings: set repo variable `DEATHFOG_LIVE_PROMOTE=false` and run `gh workflow run collect.yml --ref main`.
- All live v3 builds off: set repo variable `DEATHFOG_LIVE=false`.
