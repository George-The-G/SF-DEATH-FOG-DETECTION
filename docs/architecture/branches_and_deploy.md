# Branches and Deployment Architecture

Purpose: Document how GitHub Actions builds and deploys both the public site and the beta v3 viewer across branches.
Status: current
Last verified: 2026-11-18

---

## 1. The Two-Branch Deploy Model (`main` + `v3-dev`)

Scheduled GitHub Actions workflows always execute the workflow file on the default branch (`main`). Death Fog uses a deliberate split between `main` and `v3-dev`:

| Branch | Role | What Lives Here |
|---|---|---|
| `main` | **Deploy Controller & v2 Host** | `.github/workflows/collect.yml` (two crons: `*/10 * * * *` for v2 station status, `7,37 * * * *` for the v3 live build), v2 `deathfog/` collector, and `run.py`. |
| `v3-dev` | **Active Production & Beta Code Trunk** | `site/v3/` (the 3D viewer and `pipeline.html`), `microclimate/` (wind/fog simulation and live pipeline), `live_ci/` (`refresh.sh`), `edge/`, and `docs/`. |
| `data` | **v2 State Service Branch** | Single force-pushed commit holding SQLite station state and the 30-day baseline (`scripts/state_restore.sh`, `scripts/state_save.sh`). Never touch manually. |
| `live-archive` | **v3 Run Archive Branch** | Historical live build snapshots and verification records. Never reorganize. |

### How Every Deploy Works
1. Every 10 minutes (`*/10 * * * *`), `collect.yml` runs on `main`:
   - Restores v2 state from `origin/data`.
   - Runs `python3 run.py collect` and `python3 run.py site` (writes v2 HTML into `site/`).
   - Saves v2 state back to `origin/data`.
2. Next, in the same job, `collect.yml` fetches `origin/v3-dev` and overlays `site/v3`, `live_ci`, and `microclimate`:
   ```bash
   git fetch --depth=1 origin v3-dev
   git archive FETCH_HEAD site/v3 | tar -x
   git archive FETCH_HEAD live_ci microclimate | tar -x
   ```
3. On the `7,37 * * * *` cron (or manual `workflow_dispatch`), it downloads/caches the static tables from GitHub Release `live-static-v1` and runs `bash live_ci/refresh.sh build`. On the intermediate 10-minute ticks, it runs `bash live_ci/refresh.sh reuse` to carry forward the latest live output.
4. Finally, `actions/upload-pages-artifact` uploads `site/` (which contains both v2 at `/` and v3 at `/v3/`) and deploys to GitHub Pages. Additionally, Cloudflare R2 (`deathfog-live`) and Cloudflare Workers (`edge/`) host 5-minute GOES/fog5 and bus proxy assets.

---

## 2. Public vs. Beta (`?beta=1`)

- **Public (`/v3/`)**: Runs `live_ci/refresh.sh build` with `--promoted` (controlled by repo variable `DEATHFOG_LIVE_PROMOTE`, default `true`), writing to `site/v3/live/`.
- **Beta (`/v3/?beta=1`)**: Built alongside public using `microclimate.live.config.BETA` switches (`fog3d`, `fog_base`, `fog_travel`, `top_fusion`, `fog_lid`, `fog_base_2d`, `tile_from_3d`, `goes5`, `wind_east`), writing to `site/v3/live_beta/`.

---

## 3. Emergency Rollback Switches (No Code Change Needed)

In GitHub **Settings > Secrets and variables > Actions > Variables**:
- `DEATHFOG_LIVE_PROMOTE = false` — Rolls the public `/v3/` build back to pre-promoted layer settings.
- `DEATHFOG_LIVE = false` — Disables the v3 live Python build step completely while keeping the static viewer and v2 status page deploying.
