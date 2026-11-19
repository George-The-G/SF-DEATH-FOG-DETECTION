# Branch Consolidation Log (2026-11-18)

Purpose: Record the disposition of all 38 remote branches and local worktree branches during the 2026-11-18 repository consolidation.
Status: historical
Last verified: 2026-11-18

---

## 1. Backup & Safety Net

Before any branch merge or deletion:
- Full repository bundle created at `_archive/2026-11-18/git/all-branches.bundle` (SHA256: `F047AD875D08340E71D4FB691CA5DC9C2609724421EDE5CC4F0199E2F49BE788`).
- Every local and remote branch tip was tagged under `archive/2026-11-18/<branch>` and `archive/2026-11-18/remote/<branch>`.

---

## 2. Branches Merged or Landed into `v3-dev`

| Branch | Commits | Disposition |
|---|---|---|
| `claude/project-thread-87g86t` | 11 | **Merged into `v3-dev`** (`4d17536`). Includes all 9 commits of `wind-cube-check` (private Warp kernel cache fix for wall BCs, free-slip sea, fixed-pressure outlet, `refine.py` PAUSE checkpoint, `spikes/windcube/`, `docs/sim/WIND_SOLVER_CONFIG.md`). Fixes Discrepancy H1. Note: 5m street-wind full run was not executed due to GPU cost; only solver/checkpoint code landed. |
| `wind-cube-check` | 9 | **Merged** via `claude/project-thread-87g86t`. |
| `v3-winde` | 2 | **Landed into `v3-dev`** (`e267ae1`, `ba0b43e`) plus `docs/runbooks/RUNBOOK_WIND_EAST.md`. |
| `v3-promote-main` / `v3-deploy` / `main` | 7 | **Synced `.github/workflows/collect.yml`** from `origin/main` onto `v3-dev` so `v3-dev` carries the exact production workflow file. (`main` remains the separate deploy-controller branch). |

---

## 3. Branches Already Contained in `v3-dev` (Verified via `git cherry -v`)

- `sky-13-layers` (Guide 13 sky layers: `skylayers.py`, `sky.js`, `13_sky_layers.md` — identical to `d1b6af4`)
- `v3-wind-dirs` (5 commits already cherry-picked/merged in `v3-dev`)
- `claude/project-thread-dmstt0` (bus proxy stop picker — already in `v3-dev`)
- `claude/project-thread-jgs27j` (viewer estimated clock label — already in `v3-dev`)
- `bus-d1-binding` (already in `v3-dev` as PR #37 `c282973`)
- `rescue/bdfbb63-about-popup` (already in `v3-dev`)
- 18 earlier feature branches (`sim-01-tracer-bands` through `sim-10-cf-hosting`, `ship-fog3d*`, `ship-top-fusion`, `fix/fetch-previous-retry`, `macro-always-visible`, `perf-a*`, `perf-pub`, `v3-anchors`, `v3-obs`, `v3-ui`, etc.)

---

## 4. Branches Dropped / Archived (Not Merged)

| Branch | Reason |
|---|---|
| `claude/project-thread-ocop0x` (PR #25) | **Dropped per George.** Geometry script Plan B (`meshes_lod.py`); B3 `buildings_far.bin` (6.1 MB gzipped) did not go as planned. Tagged at `archive/2026-11-18/remote/claude/project-thread-ocop0x`. |
| `v3-refine` | **Superseded** by `claude/project-thread-87g86t` (`microclimate/refine.py`, `refine_check.py`, `refine_compare.py`). Tagged at `archive/2026-11-18/v3-refine`. |
| `rescue/detached-worktree-2026-11-14` | Old Nov 14 working-tree snapshot of `site/v3/app.js` and `ribbons.bin`, superseded by later `v3-dev` viewer commits. Tagged at `archive/2026-11-18/rescue/detached-worktree-2026-11-14`. |
