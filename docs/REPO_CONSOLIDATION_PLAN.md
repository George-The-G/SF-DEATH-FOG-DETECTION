# Death Fog: repo consolidation and reorganization plan

Status: PLAN ONLY. Nothing has been moved, deleted, committed or pushed. Written 2026-11-18.
Scope: bring everything that lives outside the repo back into `George-The-G/SF-DEATH-FOG-DETECTION`, and give the repo a layout that is easy for people and AI agents to navigate.

Numbers below come from a read-only scan of George's PC and of `git` in the main checkout (branch state is as of the last `git fetch`, so refresh before acting).

---

## 1. Inventory: what lives where

### 1.1 The main checkout `C:\Users\George\source\repos\SF-DEATH-FOG-DETECTION` (2.0 GB)

| Part | Size | Notes |
|---|---|---|
| `.venv/` | 1.7 GB | ignored, regenerable (`requirements-mc.txt`) |
| `bench/` | 229 MB | ignored |
| `site/` | 69 MB | listed in `.gitignore`, yet 107 files are tracked because someone used `add -f`. This is a trap (see 3.4) |
| `.git/` | 68 MB | 316 tracked files on the checked-out branch, healthy size |
| code | about 3 MB | `microclimate/` (sim + live pipeline), `deathfog/` (alerts, collector), `tests*`, `tools/`, `spikes/`, `edge/`, `live_ci/`, `reports/`, `docs/` |

State of the checkout:
- It is on branch `claude/project-thread-87g86t` (11 commits ahead of `origin/v3-dev`, 68 behind). Not on `v3-dev`.
- Uncommitted: modified `live_ci/fetch_previous.py`. Untracked: `install_tools.bat`, `pip.err`, `pip.log`, `edge/bus-proxy/.wrangler/`.
- Root is cluttered with 8 planning/report markdown files (`PROGRESS.md` 28 KB, `PLAN_LIVE_DATA.md`, `MODEL_REVIEW_PLAN.md`, `MVP_REPORT.md`, `PROMOTE_PLAN.md`, `UI_REDESIGN_PLAN.md`, `INSTALLED_PACKAGES.md`, `README.md`) plus four config files and `run.py`.
- Existing `docs/`: `model_guides/` (16 files), `multiscale_guides/` (1), `skate/`, `perf/`, `mockups/`, plus two root reports (`PLAN_SIM_VISUALIZER.md`, `STREET5M_VALIDATION_...`, `WIND_SOLVER_CONFIG.md`).

### 1.2 Other git working copies on the PC

22 registered worktrees (`git worktree list`). All show "prunable" only because the VM cannot see Windows paths; treat as unverified.

| Location | Branch | Size |
|---|---|---|
| `source\repos\worktrees\wt_ship03, ship05, ship06, sim01, sim03, sim04, sim05, sky13` | ship-*, sim-0x, sky-13-layers | 8 x about 68 MB = 564 MB |
| `source\repos\SF-DEATH-FOG-DETECTION-deploy` | v3-deploy | 0.4 MB |
| `source\repos\SF-DEATH-FOG-DETECTION-refine` | v3-refine | 0.3 MB |
| `source\repos\SF-DEATH-FOG-DETECTION-wind` | v3-wind-dirs | 34 MB |
| `source\repos\SF-DEATH-FOG-DETECTION-g08` | detached HEAD e21fa98 (same as `claude/project-thread-692lor`) | 67 MB |
| `AppData\Local\Temp\wt_*` (cf, fetchretry, goes5, macrovis, sim08, sim0809, topfusion, windcube, winde, ship10) | 10 branches | existence not verified, Temp may have been cleaned |
| `source\repos\_ui_stage` | not git | 34 files, 3 MB: old public-UI snapshot (`app.js`, `style.css`, `UI_REDESIGN_PLAN.md`, `03_stations.md`...). Superseded by `site/v3` (to confirm) |
| `source\repos\SmartHome` | unrelated | 500 MB, leave alone |

### 1.3 The data folder `C:\Users\George\deathfog-data` (about 23 GB)

The code finds it through `Path.home() / "deathfog-data"` (hard-coded in about 12 modules, see 4.2).

| Folder | Size | What it is |
|---|---|---|
| `library/` | 7.4 GB | the wind and fog-tracer fields the live pipeline uses (`wind_<dir>.npz`, `fog_<dir>_<band>.npz`, `fog3d_*.npz`, `runs/`) |
| `scratch/` | 5.4 GB | `library_walls_tracer_k20v8` 3.6 GB, `k20v8_data` 1.3 GB, `live_static_*.npz`, `windcube_*.npz`, 8 `shots*` screenshot folders, loose logs and patch scripts, and `pipeline_doc/` |
| `library_original_fog/` | 1.9 GB | older fog library (backup) |
| `raw/` | 1.9 GB | downloaded sources: 5 DEM GeoTIFFs, building GeoJSON, OSM json, weather json. Regenerable by download |
| `library_walls/` | 1.7 GB | intermediate library |
| `library_backup_prewalls_2026-11-16/` | 1.6 GB | backup |
| `library_walls_v1/` | 1.3 GB | intermediate library |
| `live/` | 177 MB | the current live assets (`live_static.npz` 49 MB, `live_tracer3d.npz`, `ribbons_city.bin`, `goes_cache/`...) |
| `spikes/` | 266 MB | `street5m` (v1, v2, v3 = guide 12, 5 m street wind) |
| `refined/` | 75 MB | 637 files, 5 m refinement patches |
| `grids/` | 30 MB | terrain and solid grids |
| `notes/`, `sky_inputs/` | under 1 MB | `13_sky_layers.md`, `PERFORMANCE_GAPS.md`, `viewer_scripts/`, sky collector output |
| loose files | tiny | `dbg.py`, `explore*.py`, `probe.py`, `tmp_conv.py`, `*.log`, `*.err`, `geom_audit.json` |

`C:\Users\George\home_live\deathfog-data` (2.6 GB): a second data root with `grids`, `library` (1.97 GB), `raw`, `refined`. Looks like a copy used so the live agent could run with a different `USERPROFILE` (PROGRESS.md mentions this trick). Probably mostly duplicate.

`C:\Users\George\deathfog-data\scratch\pipeline_doc` (9.7 MB): the generator for the pipeline explainer page: `gen.py`, `probe.py`, `shot.py`, `terrain_wide.bin`, and `beta/`, `live/`, `live_beta/`, `mc/`, `out/` (22 files, includes the built HTML).

Searches of Desktop, Documents, Downloads and OneDrive (depth 3) found nothing named deathfog or fog.

### 1.4 GitHub (`origin`)

- Default branch `main` is stale (last commit 2026-11-14, #36). The real trunk is `v3-dev` (tip `d1b6af4`, 2026-11-17, also the tip of `sky-13-layers`).
- Service branches: `data` (CI state, single force-pushed commit), `live-archive` (run archive). Never reorganize these.
- Deploy/promote branches: `v3-deploy`, `v3-promote`, `v3-promote-main`.
- 38 remote branches in total. 18 have 0 commits not already in `v3-dev` (safe to delete after tagging).
- Unmerged work, commits not in `v3-dev`:

| Branch | Ahead | Contents |
|---|---|---|
| `claude/project-thread-87g86t` | 11 | refine PAUSE checkpoint, Warp cache, windcube runner, guide 12 station validation, wind solver docs, sim visualizer plan. Contains the 9 commits of `wind-cube-check` |
| `wind-cube-check` | 9 | subset of the above |
| `v3-promote-main` | 7 | promoted-layers switch, 15 min live step timeout |
| `v3-wind-dirs` | 5 | wind library validation reports, directions 180-360 |
| `v3-deploy` | 3 | README line endings |
| `v3-refine` | 3 | non-5 m run file names, refine_compare |
| `v3-winde` | 2 | RUNBOOK_WIND_EAST, winddir logging |
| `bus-d1-binding`, `project-thread-dmstt0`, `-jgs27j`, `-ocop0x` | 1 each | bus proxy D1 binding (possibly already in v3-dev as #37, check with `git cherry`), stop picker, estimated clock label, Plan B building LOD |
| `claude/bus-schedule-workflow-main` (12), `-odepek` (8), `-pj76ub` (9), `readme-live` (6) | 6-12 | based on `main`. These are the commits on `main` that `v3-dev` does not have |
| local only: `rescue/bdfbb63-about-popup`, `rescue/detached-worktree-2026-11-14` | 1 each | rescue commits, never pushed |

Guides (VERIFIED in git, update after the first draft):
- Guide 13 (sky layers) IS merged. `origin/v3-dev` (d1b6af4) contains `docs/multiscale_guides/13_sky_layers.md`, `microclimate/live/skylayers.py`, `site/v3/sky.js`, `tools/collect_sky_inputs.py`, `tools/eval_sky_layers.py` and `tests_mc/test_skylayers.py`. The `sky-13-layers` branch exists only locally (no remote branch) and its tip equals `v3-dev`, so it is just a leftover label. Nothing to rescue.
- Guide 12 (5 m street wind): spike code and report are on `v3-dev` (`spikes/street5m/`). The later 5 m refinement commits (PAUSE checkpoint, Warp cache, station validation notes) are only on `claude/project-thread-87g86t`. The data (`deathfog-data/spikes/street5m*`, `refined/`) is local only. So guide 12 is the one real piece of unmerged work.

CI and deploy (VERIFIED from the workflow files and `PROMOTE_PLAN.md`). This matters a lot for the reorg:
- Production is driven by the workflow on **`main`**: `collect.yml` with two crons (`*/10` for the v2 status page, `7,37 * * * *` for the live v3 fog build). Scheduled runs always use the default branch (`main`).
- That workflow has a step "Add the v3 test page (copied from the v3-dev branch)": every run **copies `site/v3`, `live_ci` and `microclimate` from the `v3-dev` branch** and runs `live_ci/refresh.sh` (static tables come from GitHub release `live-static-v1`). `PROMOTE_PLAN.md` states this on purpose: "code changes need no PR to main".
- Consequence: **`v3-dev` is production code.** Anything merged to `v3-dev` is live on the next cron. Renaming or moving `site/v3`, `live_ci/`, `microclimate/` or `live_ci/refresh.sh` on `v3-dev` would break the live site within 30 minutes unless the workflow on `main` is changed first or at the same time.
- `v3-dev`'s own `collect.yml` is an older copy (no live cron, no copy step). `v3-deploy` and `v3-promote-main` carry the newer ones (`v3-promote-main` = what `main` looks like plus the promote switch). It is the one place where branches disagree about the deploy file; sync or delete the stale copy.
- State is saved to branch `data` (`scripts/state_restore.sh`, `state_save.sh`), the run archive to `live-archive`; `DEATHFOG_DATA_DIR: state`, `DEATHFOG_SITE_DIR: site`. `bus_schedule.yml` commits `site/v3/data/bus_schedule.json` to `v3-dev` with `git add -f`.
- Rollbacks that exist today: repo variable `DEATHFOG_LIVE_PROMOTE=false` (old public page), `DEATHFOG_LIVE=false` (live step off).

### 1.5 The project shared folder (`/mnt/project-files`)

About 40 markdown documents and 45 images or html. Grouped:

| Item | Size | Note |
|---|---|---|
| `sim/guides/00-12` | 13 files | identical in size to `archive/2026-11-14-multiscale-fog/` copies, and overlap with repo `docs/model_guides/` |
| `sim/multiscale-plan.md` (37.6 KB) | | same size as `archive/.../01_opus_first_plan.md` |
| `sim/recursive-refinement-plan.md`, `warp-cfd-comparison.md`, `wind-sim-settings.md` | | |
| `fog/` | | cloud tech survey, grid-artifact diagnosis + 5 images + `gridfog_repro.py`, guide-14 scope and plan v2 |
| `render/` | | plans A, B, perf plan, 2 screenshots |
| `replay/` | | GOES anchor plan, crop format, replay engine review |
| `validate/validation-plan.md`, `leave_one_sounding_out.json` | | |
| `madis/data-source-review.md`, `bus/live-bus-plan.md`, `sky_layers_plan.md` | | |
| `playback/` | | 3 html mockups, 4 images |
| `pipeline-doc/` | 1.5 MB html + `work/` | the explainer page, its `build.py`, `gen.py`, `facts.json`, `mc/` copy of live modules, `beta_snapshot/` json, 20 webp assets |
| root | | `README.md`, `REPORT*.md` (2), `compare_*`, about 20 screenshots (`desktop_*`, `phone_*`, `macro_*`, `slices_270*`, `strip12`, `series`, `terrain_wide_check`) |
| `uploads/` | | user uploads, not project content |

---

## 2. Mapping: what goes where

Principle: **code, docs and small curated assets go in the repo; big or regenerable data stays outside git but is described by a tracked manifest; scratch is archived or deleted.**

### 2.1 Into the repo

| From | To |
|---|---|
| root `PROGRESS.md`, `PLAN_LIVE_DATA.md`, `MODEL_REVIEW_PLAN.md`, `MVP_REPORT.md`, `PROMOTE_PLAN.md`, `UI_REDESIGN_PLAN.md`, `INSTALLED_PACKAGES.md` | `docs/history/` (`git mv`, keep names). New short `docs/STATUS.md` replaces `PROGRESS.md` as the "where are we" page |
| `docs/PLAN_SIM_VISUALIZER.md`, `STREET5M_VALIDATION_*`, `WIND_SOLVER_CONFIG.md` | `docs/sim/` |
| project `sim/guides/*`, `archive/.../01_opus_first_plan.md`, `sim/multiscale-plan.md` | `docs/model_guides/` (guides) and `docs/multiscale/` (plan). De-duplicate first: the project copy and the repo copy of a guide may differ, diff each pair, keep the newer |
| project `sim/recursive-refinement-plan.md`, `warp-cfd-comparison.md`, `wind-sim-settings.md` | `docs/sim/` |
| project `fog/*.md`, guide-14 docs | `docs/fog/` |
| `fog/gridfog_repro.py` | `spikes/grid_artifact/` with the diagnosis as its README; its 5 images go to `docs/fog/img/` |
| project `render/*.md` + 2 screenshots | `docs/render/` |
| project `replay/*.md` | `docs/replay/` |
| project `validate/validation-plan.md`, `leave_one_sounding_out.json`, `compare_*.md` | `docs/validate/` |
| project `madis/`, `bus/live-bus-plan.md`, `sky_layers_plan.md` | `docs/data-sources/madis.md`, `docs/bus/`, `docs/multiscale_guides/13_sky_layers_plan.md` |
| project `playback/*.html` + pngs | `docs/mockups/playback/` (the html files are small; keep only the 4 pngs that the mockups reference) |
| project `REPORT*.md` | `docs/reports/` |
| `scratch/pipeline_doc/{gen.py, probe.py, shot.py}` and project `pipeline-doc/work/{build.py, gen.py, facts.json}` | `tools/pipeline_doc/` (dedupe: `gen.py` exists in both, 35 KB each, diff them) |
| `pipeline_doc/work/mc/`, `beta_snapshot/` | do not track: `mc/` is a stale copy of `microclimate/live` (make `build.py` import the real package), the snapshot json is regenerated by the script |
| `fog-pipeline.html` (1.5 MB) and its 20 webp assets (about 1 MB) | `docs/pipeline/` (built output, small enough to track; or publish via Pages) |
| `deathfog-data/notes/*.md` (`13_sky_layers.md`, `PERFORMANCE_GAPS.md`) | `docs/` (check for duplicates with `docs/multiscale_guides/13_sky_layers.md`) |
| `deathfog-data/notes/viewer_scripts/` (14 files) | `tools/viewer_scripts/` after review |
| `scratch/*.py` (`patch_*.py`, `prep_time.py`, `rt_test.py`, `val_one.py`, `inspect1.py`), `deathfog-data/*.py` (`dbg`, `explore*`, `probe`, `tmp_conv`) | review each. Keep useful ones in `spikes/<topic>/`, drop the rest |
| `spikes/*` reports that point at `C:\Users\George\...` | rewrite paths to `$DEATHFOG_DATA/...` |
| curated screenshots (`desktop_*`, `phone_*`, `macro_*`, `compare_kd20_315`, `slices_270`, `strip12`, `series`) | only those cited by a doc go to `docs/img/<topic>/` as webp or png under 300 KB. The rest are archived outside git |
| `site/` | see 3.4, fix the tracking rules, do not move |
| `_ui_stage` | diff against `site/v3`; if superseded, archive as one zip |

### 2.2 Stays outside git (described by `data/MANIFEST.md`)

| Folder | Plan |
|---|---|
| `deathfog-data/library`, `live`, `grids`, `refined`, `spikes`, `sky_inputs` | stays, becomes the single data root (`DEATHFOG_DATA`) |
| `deathfog-data/raw` | stays, regenerable by download; manifest lists sources and URLs |
| `scratch/library_walls_tracer_k20v8` (3.6 GB), `scratch/k20v8_data` (1.3 GB) | VERIFIED redundant: MD5 compare shows all 99 `fog3d_*.npz` and all 36 `fog_*.npz` are byte-identical to the files now in `library/`. Safe to delete (4.9 GB) |
| `library_walls` (1.7 GB) | 15 of 17 `wind_*.npz` are identical to `library/`. Copy the 2 that are not in `library/` into it, then delete (or archive) |
| `library_walls_v1` (1.3 GB) | older wind version: all 13 `wind_*.npz` differ from `library/` (the batch driver later "uses v2 270"). Superseded, archive then delete |
| `library_original_fog` (1.9 GB), `library_backup_prewalls_2026-11-16` (1.6 GB) | the pre-tracer originals that `spikes/finalize_k20v8.py` moved aside; most files differ from today's `library/`. This is the only copy of the old model output. Keep one cold copy (zip on an external disk), then delete locally. Needs George, see section 5 |
| `home_live\deathfog-data` (2.6 GB) | VERIFIED stale: last write 2026-11-16 11:24, no python process running, 36 of 45 library `.npz` differ from the main data root (older), `grids` and `raw` are subsets. Delete after one archive zip of `library` |
| `scratch/*.npz`, `windcube_*`, `sea_last.npy` | same: keep the ones the manifest says are inputs, archive the rest |
| `scratch/shots*` (8 folders, about 50 MB), root logs, `*.err` | archive to a single zip, delete |
| `home_live\deathfog-data` | verify it is a duplicate (hash compare), then delete. Replace the trick with `DEATHFOG_DATA` pointing at the folder you want |
| Temp and sibling worktrees | after their branches are tagged and pushed, remove with `git worktree remove` |

---

## 3. Proposed layout

### 3.1 Target tree

```
README.md                  what this is, 10-line quickstart, link to docs/INDEX.md
AGENTS.md                  rules for AI agents: where things are, how to run tests, what never to touch (also CLAUDE.md as a one-line pointer)
docs/
  INDEX.md                 map of all docs, one line each, grouped by topic
  STATUS.md                current state, next steps (replaces PROGRESS.md as the entry)
  architecture/            pipeline overview, data flow, branch and deploy model
  model_guides/            numbered guides 00-NN (existing)
  multiscale/              the multiscale plan
  sim/ fog/ render/ replay/ validate/ bus/ data-sources/ reports/ pipeline/ mockups/ img/ history/
  runbooks/                RUNBOOK_*.md (wind east, deploy, babysitting the live run)
config/                    config.toml, microclimate.toml, routes.toml, routes_drawn.json  (phase 3)
deathfog/                  Python package: alerts, collector, status page   (unchanged name)
microclimate/              Python package: sim, wind, fog, live pipeline      (unchanged name)
run.py                     CLI entry (unchanged in phase 1)
site/v3/                   the viewer (js, html, small static data)
edge/                      Cloudflare workers (bus-proxy, cron-trigger)
live_ci/ scripts/ tools/   CI helpers, shell scripts, dev tools (tools/pipeline_doc, tools/viewer_scripts)
spikes/<topic>/            experiments: code + REPORT.md, never data
tests/                     python tests; tests_mc and tests_js folded in as tests/mc, tests/js (phase 3)
data/                      gitignored; holds only a junction or symlink to the real data root, plus tracked MANIFEST.md
.github/workflows/
```

Reasoning: top level answers "what is this" (README, AGENTS), "where do I read" (docs/INDEX), "where is code" (two packages, site, edge). Everything else is one level down. Phase 1 changes only docs, so no code path can break.

### 3.2 Naming conventions

- Docs: `lower_snake.md` for new files, numbered guides keep `NN_topic.md`, runbooks `RUNBOOK_<TOPIC>.md`, dated reports `<topic>_YYYY-MM-DD.md`.
- Every folder under `docs/` and `spikes/` has either a `README.md` or is listed in `docs/INDEX.md`.
- Every doc starts with a 3-line header: purpose, status (`current`, `historical`, `superseded by X`), last verified date. This lets an AI agent skip stale files.
- Spikes: `spikes/<topic>/{README.md, run_*.py}` and the data goes to `$DEATHFOG_DATA/spikes/<topic>/`.
- Branches: `feat/<topic>`, `sim/<NN>-<topic>` for guide work, `fix/<topic>`. Retire the `claude/project-thread-xxxxxx` names after each merge. Long-lived: `main` only (see 3.5).

### 3.3 Tracked vs ignored

Tracked: code, tests, docs, configs, small curated images (under 300 KB each), `data/MANIFEST.md`, the small site data the viewer needs to load.
Ignored (extend `.gitignore`, anchored with a leading `/` where it is a root folder): `.venv/`, `/bench/`, `/data/*` (but not `data/MANIFEST.md`), `/state/`, `__pycache__/`, `*.pyc`, `*.tif`, `*.log`, `*.err`, `.wrangler/`, `pip.*`, `/scratch/`, `/artifacts/` (local screenshots and scratch output), and the v2 `/site/*` output except `!/site/v3/`. Do not add a blanket `*.npz` rule until `git ls-files '*.npz'` is checked.

### 3.4 Large data and binaries policy

- Rule: no file over 5 MB and no generated daily output goes into git. Today 9 daily `streets.u8` files (2.8 MB each) and several `.u8` day files are tracked under `site/v3/replay/` and `site/v3/live/`, plus `terrain.glb` 2.8 MB and `bus_schedule.json` 2.1 MB. These inflate history (the repo is 68 MB of `.git` already).
- Where big files go: 
  - Release inputs the CI needs (`live_static.npz` 49 MB, `live_tracer3d.npz`, `live_east.npz`): keep using whatever mechanism CI uses today (looks like GitHub release assets, guide 10 also set up an R2 bucket `deathfog-live`). Record the asset names, hashes and the regenerate command in `data/MANIFEST.md`.
  - The 7 GB `library` and `raw`: not in git, not in LFS (GitHub LFS free tier is 1 GB storage and 1 GB per month bandwidth, far below this). Keep on the PC, back up to an external disk or R2, and describe in the manifest how to regenerate (needs the GPU).
  - Use git-lfs only if George wants versioned binaries under about 100 MB, and then only for `site/v3/data/*.glb|*.bin`. Not recommended for phase 1.
- Correction after checking the workflows: do NOT untrack `site/v3/replay/*`, `site/v3/data/*` or `site/v3/live/*`. The replay nights are deployed static assets (written locally by `replay.write`, not by CI), `bus_schedule.json` is committed by `bus_schedule.yml` on purpose, and the `site/v3` folder is what `main`'s workflow copies. They stay tracked. They are 9 x 2.8 MB, acceptable. Only stop adding new daily outputs.
- Root cause of the `add -f` pain: `.gitignore` has `site/`, `data/` and `state/` as unanchored patterns. `data/` also matches `site/v3/data/`, and `site/` matches the whole viewer (`fog3dview.js` and `bustimeline.js` were each silently ignored once and never deployed). Fix: anchor them, `/data/`, `/state/` and `/site/*` followed by `!/site/v3/` (the v2 `site/` output on `main` stays ignored). Then `git ls-files` must show no change.
- Do not rewrite history to shrink `.git` (68 MB is fine, and rewriting breaks every branch). Just stop adding.

### 3.5 Branch model after cleanup

`main` = what is deployed publicly, `v3-dev` merged into it (see open question 1). `data` and `live-archive` stay as service branches. Everything else short-lived and deleted after merge.

### 3.6 Data root

One env var, `DEATHFOG_DATA`, default `~/deathfog-data`, read by one helper (`microclimate/paths.py`: `data_dir()`, `library_dir()`, `scratch_dir()`). The 12 modules that do `Path.home() / "deathfog-data"` import it. This also replaces the `USERPROFILE` trick and the `home_live` copy.

---

## 4. Ordered migration steps

Each step is separately reversible. Nothing runs until George approves, and no step deletes anything before a verified backup.

### Phase 0: freeze and back up (no repo changes)
1. Confirm nothing is running (live pipeline, wind runs, CI deploys). Disable the `collect` schedule only if a reorg PR is about to merge.
2. `git fetch --all --prune`. `git bundle create ..\deathfog-all-2026-11-18.bundle --all` (one file holding every branch and tag).
3. Tag every branch tip as `archive/<branch>` and push the tags (tags are cheap and survive branch deletion).
4. Save the uncommitted `live_ci/fetch_previous.py` change as a commit on a new `wip/fetch-previous` branch, or `git stash` and record it. Move `pip.*`, `install_tools.bat` out of the root.
5. Zip `scratch/pipeline_doc`, `_ui_stage`, `deathfog-data/notes` and the loose scripts to `D:\archive\deathfog-pre-consolidation.zip` (or any disk outside `source`). Record SHA256.
   - Rollback for all of phase 0: nothing to roll back, it only adds copies.

### Phase 1: land the unmerged work (before any layout change)
6. Merge `claude/project-thread-87g86t` into `v3-dev` (it includes `wind-cube-check`). Run `pytest tests tests_mc` and `node` tests in `tests_js`.
7. For `v3-promote-main`, `v3-wind-dirs`, `v3-refine`, `v3-winde`, `v3-deploy`, `bus-d1-binding` and the three single-commit claude branches: run `git cherry -v origin/v3-dev <branch>` to see what is truly missing, then merge, cherry-pick or abandon each. Record the decision in `docs/history/branch_log.md`.
8. Do NOT merge `v3-dev` into `main` or fast-forward it. `main` is the deploy-controller branch (workflow + v2 site), `v3-dev` is the code branch it copies from. Keep that two-branch model, write it down in `docs/architecture/branches_and_deploy.md`, and only cherry-pick the `main`-only commits (bus schedule workflow, readme, secrets scope) into `v3-dev` where they touch shared files. Replace the stale `collect.yml` copy on `v3-dev` with a one-line note or an exact copy of `main`'s.
   - Risk: merge conflicts in workflows. Rollback: branches are tagged; restore on a throwaway branch, never force-push shared branches.
8b. Freeze the production paths: `site/v3/`, `live_ci/`, `microclimate/` (package name and the `live` subpackage), `deathfog/`, `run.py`, `scripts/state_*.sh`, `config.toml` stay where they are on `v3-dev` for the whole reorg, because `main`'s workflow depends on them. Add that list to `AGENTS.md`.

### Phase 2: docs-only reorganization (safe: no code reads docs)
9. New branch `chore/repo-layout` from `v3-dev`. `git mv` the root markdown into `docs/history/`, create `docs/INDEX.md`, `docs/STATUS.md`, `AGENTS.md`, slim `README.md`.
10. Import the project-folder docs per section 2.1 (copy in, diff duplicates first). Add the 3-line status header to each.
11. Add `data/MANIFEST.md`, `docs/architecture/branches_and_deploy.md`.
12. Fix `.gitignore` per 3.3 and remove the `site/` line. Run `git status` and confirm no tracked file becomes untracked and no new large file appears.
13. Check links: `grep -rn "\.md" docs README.md` for broken relative links; fix the C:\ paths in runbooks to `$DEATHFOG_DATA`.
14. PR, review, merge into `v3-dev`. Rollback: `git revert` the merge commit (moves are plain renames, so history follows with `git log --follow`).

### Phase 3: code and path changes (the risky one, separate PR each)
15. Add `microclimate/paths.py` and switch the `Path.home()/"deathfog-data"` call sites (`microclimate/__main__.py, city.py, compare_dirs.py, fogtop.py, wetness.py, wide.py, live/backup.py, live/config.py, live/stationwind.py, live/tracer3d.py`, `site/v3/tools/make_ribbons.py`, `spikes/*`). Default stays the same, so behavior is unchanged. Add a test that the default resolves to the old path.
16. Move `spikes/` outputs and scratch scripts (2.1), rewrite their paths.
17. DROPPED from the plan: moving configs into `config/` or folding `tests_mc`/`tests_js` into `tests/`. `main`'s workflow and `run.py` read these paths, so the move risks the live site for little benefit. Revisit only together with a coordinated `main` workflow change (and then test with `workflow_dispatch`). The target tree in 3.1 should be read with `config/` and `tests/` left as they are today.
18. Path references to check before each merge (grep list): `config.toml`, `microclimate.toml`, `routes.toml`, `routes_drawn.json`, `scripts/state_*.sh`, `DEATHFOG_DATA_DIR`, `DEATHFOG_SITE_DIR`, `site/`, `live_ci/`, `edge/`, `tests*/` in `.github/workflows/*.yml`, `live_ci/*.py`, `run.py`, `tests`, `pytest.ini`/`conftest` if any, and any `wrangler.toml`.
19. CI check without touching production: run `gh workflow run collect.yml --ref chore/...` (workflow_dispatch works on any branch). Scheduled cron always runs the workflow file on the default branch, so do not merge workflow changes into `main` without a dispatch test first. Verify the beta and public `status.json` update afterwards (the `deathfog-ship` skill describes the steps).

### Phase 4: data and machine cleanup (needs explicit approval, per folder)
20. Write `data/MANIFEST.md` from a real scan (folder, size, purpose, who reads it, how to regenerate, hash of the few critical files like `live_static.npz`).
21. One data root: zip `home_live\deathfog-data\library` once (it is a stale snapshot, hash check already done), then delete `home_live`.
22. Delete in this order, re-checking hashes just before each: (a) `scratch/library_walls_tracer_k20v8` and `scratch/k20v8_data` (4.9 GB, verified byte-identical to `library/`); (b) `library_walls` after copying its 2 unique wind files into `library/`; (c) zip `library_walls_v1`, `library_original_fog` and `library_backup_prewalls_2026-11-16` to an external disk and verify, then delete locally. Keep `scratch/release_walls_k20v8` and `live/` until the GitHub release `live-static-v1` is confirmed to hold the same `live_static` (the file in `live/` has a different hash from the k20v8 one, so check which one production uses).
23. Remove worktrees: `git worktree remove` for each, then `git worktree prune`; delete merged local branches; delete merged remote branches (tag first) in batches of 5. Keep `data`, `live-archive`, `main`, `v3-dev`, `v3-deploy` and `v3-promote*` until 1 below is answered.
24. Clean the project shared folder: leave `README.md` pointing at the repo docs, move the rest to `archive/2026-11-18-pre-consolidation/` (do not delete).

### Risks in short
- CI breaks silently (cron uses the default branch's workflow). Mitigation: step 19.
- Duplicate docs differ; picking the wrong one loses content. Mitigation: diff each pair, keep both when unsure, in `docs/history/`.
- Data deletion regret. Mitigation: archive zip with hash, delete last, one folder at a time.
- A branch based on old layout conflicts after the moves. Mitigation: step 6-8 land or retire everything before step 9; any straggler rebases with `git mv` rename detection.
- Windows quirks: long paths, line endings (a past PR was needed just to fix LF), case-insensitive names. Keep `.gitattributes` with `* text=auto eol=lf` (to add).

---

## 5. Open questions, now with answers

George said he does not know, so each question was checked against git and the disk. "Verified" = found in the repo or on disk. "Needs George" = only he can decide, with my recommendation.

| # | Question | Answer | Status |
|---|---|---|---|
| 1 | Which branch must keep deploying? | `main` runs the cron and its workflow copies `site/v3`, `live_ci`, `microclimate` from `v3-dev` on every run. Both must keep working. Do not merge `v3-dev` into `main`, and freeze the production paths (step 8b) | Verified |
| 2a | Is guide 13 (sky layers) merged? | Yes, in `v3-dev`: doc, `skylayers.py`, `sky.js`, tools and tests. The local branch `sky-13-layers` equals `v3-dev` | Verified |
| 2b | Is the other unmerged work wanted? | Recommend merge `claude/project-thread-87g86t` (includes `wind-cube-check`) into `v3-dev` after tests. Check `git cherry` for the 1-commit and `main`-only branches. Tag then delete the 18 branches that are already fully in `v3-dev` | Recommendation, George confirms by approving the plan |
| 3a | Which old libraries are disposable? | Delete: `scratch/library_walls_tracer_k20v8` and `scratch/k20v8_data` (byte-identical to `library/`), `library_walls` after saving 2 files, `home_live` (stale). Archive then delete: `library_walls_v1`. Keep one cold zip: `library_original_fog` and `library_backup_prewalls_2026-11-16` | Verified by MD5, the last item needs George because it is the only copy of the old model output |
| 3b | Is `home_live` used by a running agent? | No python process is running, and the newest file is from 2026-11-16 11:24 | Verified at scan time |
| 4 | Backup target | External disk or the existing R2 bucket `deathfog-live` (guide 10). About 5 GB for the cold zips | Needs George (what hardware he has) |
| 5 | Stop tracking generated site files? | No. They are deployed assets or committed on purpose (see 3.4). Only fix the `.gitignore` patterns | Verified, plan changed |
| 6 | Rename packages / folders? | No. Keep `deathfog`, `microclimate`, `tests_mc`, `tests_js`, `config`; `main`'s workflow depends on them | Verified, plan changed |
| 7 | Do AI agents have the data? | Cloud sessions see only the repo. Recommend `AGENTS.md` assume no data, tests use fixtures, and long runs happen on the PC | Recommendation |
| 8 | Project folder afterwards | A README pointing at the repo docs, everything else moved under `archive/2026-11-18-pre-consolidation/`, nothing deleted | Recommendation |
| 9 | `_ui_stage`, `SmartHome` | `_ui_stage` has no git history and its files (`app.js`, `style.css`, `UI_REDESIGN_PLAN.md`) predate `site/v3`; zip then remove. `SmartHome` is unrelated, leave it | Mostly verified, `_ui_stage` needs a quick diff before removal |

Still needs George, in short: (4) the backup location, and a yes or no on keeping one cold copy of the old fog libraries. Everything else can proceed on the recommendations once he approves the plan.
