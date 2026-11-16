# cron-trigger: a reliable 10-minute trigger for the collect workflow (multi-scale guide 10)

A separate Cloudflare Worker (`deathfog-cron-trigger`, free plan). Every 10 minutes it reads the published v2 `status.json`; if the build is older than `STALE_MIN` (14) minutes, or unreadable, it asks GitHub to run `collect.yml` (`workflow_dispatch` on `main`). It does not touch `edge/bus-proxy`, and the GitHub cron stays on as well (the workflow's `concurrency` group stops overlaps).

Why the v2 `status.json` and not the v3 live one: the v3 live data is built only on the `7,37` cron (every 30 min), so it is up to ~35 min old by design. The v2 file is rewritten on every scheduled run and is the right heartbeat. A dispatch runs `collect.yml` with `workflow_dispatch`, which also does the full live build (`refresh.sh build`), so an extra dispatch costs one live build.

## Deploy (GEORGE steps: nothing here is deployed yet)
1. Create a fine-grained GitHub token: only repository `George-The-G/SF-DEATH-FOG-DETECTION`, permission **Actions: read and write**, nothing else. Short expiry (90 days) is fine.
2. From this folder, with wrangler already logged in: `npx wrangler deploy`, then `npx wrangler secret put GH_DISPATCH_TOKEN` and paste the token at the prompt (never into chat, the repo or `wrangler.toml`).
3. Check: Cloudflare dashboard > Workers > deathfog-cron-trigger > Logs, one line every 10 minutes ("fresh (n min): no dispatch").
4. Remove: `npx wrangler delete`.

## Notes
- GitHub answers a successful dispatch with 204 and no body.
- GitHub switches scheduled workflows off after 60 days without repository activity (verify). A dispatch is not repository activity.
- Tests: `node tests_js/test_cron_trigger.mjs` (mocked fetch; token never logged).

## Part B and C (same branch)
- `live_ci/r2_sync.py` + `live_ci/requirements-r2.txt`: R2 uploader (needs the four `R2_*` secrets; skipped without them). Not wired into `refresh.sh` or the workflow yet.
- `microclimate/live/backup.py` (`python -m microclimate.live backup`) + `live_ci/pc_backup.ps1`: PC backup runner; the scheduled-task install/remove commands are at the top of the .ps1. Not installed.
