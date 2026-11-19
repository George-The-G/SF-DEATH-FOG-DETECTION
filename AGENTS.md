# Death Fog — AI Agent Guidelines (`AGENTS.md`)

Purpose: Rules and layout guide for AI coding agents working on `SF-DEATH-FOG-DETECTION`.
Status: current
Last verified: 2026-11-18

---

## 1. Self-Contained Repository Layout (Option 1)

Everything for Death Fog lives inside `C:\Users\George\source\repos\SF-DEATH-FOG-DETECTION\`:

- `microclimate/` — Python package: wind/fog simulation, 3D tracer advection, and the v3 live pipeline (`microclimate/live/`).
- `deathfog/` — Python package: v2 station collector, wetness scoring, alerts, and status page.
- `site/v3/` — 3D WebGL viewer (`index.html`, `pipeline.html`, JS modules, replay data, static meshes).
- `edge/` — Cloudflare Workers (`bus-proxy`, `cron-trigger`).
- `live_ci/`, `scripts/`, `tools/` — CI runner helpers, state backup scripts, and dev/viewer tools.
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

## 3. Running Tests

Run both test suites before committing or merging to `v3-dev`:

1. **Python tests (v2 + v3 microclimate):**
   ```powershell
   .\.venv\Scripts\pytest.exe tests tests_mc
   ```
   *(Note: Cloud sessions without `data/` skip data-dependent tests automatically; unit tests use synthetic fixtures.)*
2. **JavaScript tests (viewer + Cloudflare workers):**
   ```powershell
   node --test tests_js/*.mjs
   ```

---

## 4. Branch & Worktree Discipline

- **Active trunk:** `v3-dev`. Create short-lived branches (`feat/<topic>`, `sim/<NN>-<topic>`, `fix/<topic>`) off `v3-dev`, and delete them after merging.
- **Worktrees:** Do not create worktrees in `AppData\Local\Temp` or as sibling folders in `source\repos\`. If parallel work requires a worktree, place it in `_worktrees/wt_<topic>` and remove it (`git worktree remove`) immediately after merge.
- **Large files:** No binary over 5 MB or new daily generated output in git. Store simulation data in `data/` (`$DEATHFOG_DATA`) and document it in `data/MANIFEST.md`.
