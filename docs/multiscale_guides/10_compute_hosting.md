# Guide 10: free compute and hosting: GitHub Actions, Cloudflare, the PC as backup

Read `00_README.md` first. Independent of the other guides. George's decision D2: free options only; Cloudflare; the local PC as backup. **Several steps here need George to do something in a dashboard (tokens, enabling R2). Those are marked GEORGE. Prepare everything else and hand him exact click-by-click steps.**

## What each piece is for

The revised design keeps all live computation small (weighted sums and 2D fields), so the GitHub Action stays the place where the model runs. The gaps are: late or skipped scheduled runs, a 1 GB site limit with a full re-upload on every deploy, and no fallback when GitHub is down or slow.

| Piece | Role | Free limits (verify each on the vendor's pricing page before relying on it) |
|---|---|---|
| GitHub Actions | Runs the live build every 10 min | Free for public repos; schedules can start late; job timeout is 10 min in `collect.yml` |
| GitHub Pages | Serves the site and hourly data, as today | 1 GB site, about 100 GB/month soft bandwidth |
| Cloudflare Worker (cron) | A reliable 10-minute trigger for the Action | Free plan: 100,000 requests/day, 10 ms CPU per request, cron triggers included |
| Cloudflare R2 | Stores the bulky, fast-changing files (5-minute frames, later street tiles) | Free tier: 10 GB storage, 1 M write-class and 10 M read-class operations a month, no egress fee. R2 needs a payment card on the account even at zero cost (verify) |
| The PC | Backup runner when the Action has been stale for 30+ minutes; all GPU work | Only while it is on |

A Worker cannot run the model (10 ms CPU, no numpy). Do not try.

## Part A: reliable trigger (small, do first)

1. **Check first:** `collect.yml` has `workflow_dispatch` and `concurrency: group: deathfog-collect, cancel-in-progress: false`, and whether the scheduled workflow file that actually runs is on the default branch (the replay review raised this; report what you find).
2. **New Worker** `edge/cron-trigger/` (own folder, own `wrangler.toml`; do not touch `edge/bus-proxy`): a `scheduled` handler on `*/10 * * * *` that:
   - GETs `https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/v3/live/status.json` (confirm the real published URL from the repo's README) and reads the build time,
   - if the newest build is older than 14 minutes, POSTs `https://api.github.com/repos/George-The-G/SF-DEATH-FOG-DETECTION/actions/workflows/collect.yml/dispatches` with body `{"ref": "<default branch>"}`, headers `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, `User-Agent: deathfog-cron`,
   - logs the outcome. A `fetch` handler returns only a plain "ok" (no data, no token echo).
3. **GEORGE:** create a fine-grained GitHub token limited to this one repository with only "Actions: read and write", and store it as a Worker secret (`npx wrangler secret put GH_DISPATCH_TOKEN` or in the dashboard). The token never goes in the repo, in `wrangler.toml` or in chat.
4. Keep the GitHub cron as well. The `concurrency` group already stops overlapping runs, and the freshness check stops double work.
5. Tests (`tests_js/test_cron_trigger.mjs`, mocked `fetch`): fresh status gives no dispatch; stale gives exactly one POST with the right URL and headers; a failed status fetch still dispatches; the token is never logged.

## Part B: R2 for bulky data (build when guide 08 or 12 needs it, not before)

1. **GEORGE:** enable R2 in the Cloudflare dashboard, create bucket `deathfog-live`, turn on public access through an `r2.dev` address or a custom domain, and create an API token with object read and write for that bucket only. Add four repo secrets: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.
2. **CORS** on the bucket: allow GET from `https://george-the-g.github.io` (and `http://localhost:*` for testing).
3. **Uploader** `live_ci/r2_sync.py`: R2 speaks the S3 protocol, so use `boto3` (add to `live_ci/requirements-live.txt`, record in `INSTALLED_PACKAGES.md`) with `endpoint_url=https://<account>.r2.cloudflarestorage.com`. It:
   - uploads only files that are new or changed (compare size and MD5 with the object's ETag from one `list_objects_v2` call),
   - sets `Cache-Control: public, max-age=31536000, immutable` on content-hashed files and `max-age=60` on `index.json` files,
   - deletes objects no longer referenced by the newest index, but only those older than 2 hours (a browser may still be reading them),
   - has a hard deadline of 60 s and never fails the build,
   - is skipped with a clear log line when the secrets are absent (forks, local runs).
4. **Operation budget.** Every upload and every list is a write-class operation. At 144 runs a day, 1 M a month allows about 230 operations per run. Design for under 60 per run and log the count.
5. **Viewer:** `index.json` for the data set carries a `base` URL. The viewer loads `fog5/` (and later street tiles) from that base when present, from the Pages folder otherwise. If an R2 request fails, fall back to the hourly data silently. Beta only.
6. **What stays on Pages:** everything that exists today, the hourly `fog3d_*` files and `macro/`. Only `fog5/` and future street tiles move.
7. Tests: the sync logic against a fake S3 client (changed, unchanged, stale, deadline); no secret values in logs.

## Part C: the PC as backup

Purpose: when the published data is more than 30 minutes old (GitHub outage, disabled schedule), the PC builds and publishes the same outputs.

1. **Not a self-hosted GitHub runner.** On a public repository a self-hosted runner can be made to run code from a stranger's pull request. Use a Windows scheduled task instead.
2. **Script** `live_ci/pc_backup.ps1` plus a Python entry `python -m microclimate.live backup`:
   - every 10 minutes (Task Scheduler, "run whether user is logged on or not", stop after 8 minutes),
   - read the published `status.json`; if it is younger than 30 minutes, exit at once,
   - otherwise run the same build the Action runs (read `live_ci/refresh.sh` and mirror its public and beta build commands; it is bash, so either run it under Git Bash or port the two commands, and say which you chose), in its own worktree folder that is updated with `git fetch` + `git checkout --detach origin/v3-dev` each time,
   - publish: (a) preferred, to R2 under `backup/live/` and `backup/live_beta/` with the uploader of part B; (b) without R2, do nothing but write a local log. The PC must never push to the repository or deploy Pages.
   - a lock file prevents two instances; a GPU job in progress (the `gpu.lock` of the skill) does not block it because the build is CPU only, but keep process priority below normal.
3. **Viewer side (beta only):** on load, fetch `status.json` from Pages and from `backup/live/` on R2; use the newer one as the data base. Show a small "backup data" note when the R2 copy is in use.
4. **GEORGE:** approve installing the scheduled task. Give him the one command to create it and the one to remove it. Do not create it without his go-ahead.
5. Tests: the freshness decision (fresh, stale, unreachable), the lock, and that the script exits non-zero without publishing when the build fails.

## Gate
- Part A: over 24 hours, the gap between consecutive published builds: median, 95th percentile, worst, before and after. Target: 95th percentile under 20 minutes.
- Part B: a week of operation counts and stored bytes, against the free limits.
- Part C: one drill, with George watching: disable the Action for 45 minutes, confirm the PC publishes, re-enable, confirm the viewer goes back to Pages data.

## Pitfalls
- Never print secrets; never put them in URLs.
- GitHub answers a successful dispatch with 204 and no body.
- Scheduled workflows on public repositories are switched off by GitHub after 60 days without repository activity (verify). The Worker's dispatch does not count as activity; note this in the README.
- R2 ETags equal the MD5 only for single-part uploads. Keep every file under the multipart threshold (boto3 default 8 MB) or set the threshold higher.
