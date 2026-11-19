# Death Fog — Current Status & Engineering Backlog (`STATUS.md`)

Purpose: Single source of truth for current deployment state, recently completed work, and open engineering items.
Status: current
Last verified: 2026-11-18

---

## 1. What Is Live Today

- **Public v2 Status Page (`/`):** Running on a 10-minute schedule from `main`, storing state on the `data` branch.
- **Public v3 3D Viewer (`/v3/`):** Rebuilt every 30 minutes (`7,37 * * * *`) using code copied from `v3-dev` with `--promoted` layers (`DEATHFOG_LIVE_PROMOTE=true`).
- **Beta v3 3D Viewer (`/v3/?beta=1`):** Runs alongside the public build using the full `microclimate.live.config.BETA` stack:
  - **Guides 01–04:** 4-band 3D fog tracer (`live_tracer3d.npz`, `kd=20, kdv=8`, 20m walls wind library) and 3D GPU ray-marched voxel fog (`fog3d_<fff>.png`, `site/v3/fog2.js`, `fog3dview.js`).
  - **Guide 05:** Cloud base / floor height model (`fogbase.py`, `fog_lid`, `fog_base_2d`, `tile_from_3d`).
  - **Guide 06:** Multi-source fog top fusion (`topfusion.py`: RRFS profile + Oakland sounding + GOES-18 CTH + airport ceilings).
  - **Guide 07 & 09:** 5-minute GOES-18 low-cloud macro deck (`goes5.py`, `macro.js`) with surrounding terrain kept visible when zoomed in.
  - **Guide 08:** 5-minute fog advection & time-smoothed travel (`travel.py`, `fog5.py`, `SMOOTH_SIGMA=2.5`, `HOURLY_MIX=0.3`).
  - **Guide 10:** Cloudflare R2 (`deathfog-live`) & Workers (`edge/bus-proxy` with D1 GPS log binding, `edge/cron-trigger`).
  - **Guide 13:** Sky layers table & cloud-deck separation (`skylayers.py`, `site/v3/sky.js`, Fog Lab switch).
  - **Pipeline Explainer:** Interactive stage-by-stage technical atlas live at `/v3/pipeline.html` (linked from the `(i)` popup, source in `docs/fog_pipeline.html` and `docs/pipeline_doc/`).

---

## 2. Active Engineering Backlog

### A. Pipeline Discrepancies ([`docs/PIPELINE_DISCREPANCIES.md`](PIPELINE_DISCREPANCIES.md))
Audited while building `pipeline.html`:
- **H1 (FIXED on `v3-dev` 2026-11-18):** Merged `wind-cube-check` / `claude/project-thread-87g86t` solver fixes (`microclimate/wind.py` private Warp kernel cache, free-slip sea, fixed-pressure outlet) into `v3-dev`.
- **H2 (Open — Guide 12 5m Street Wind):** Beta `live_static.npz` currently has `rel_wind = 1` everywhere because old `corr_bldg` was computed against the pre-walls 20m wind field. Full 5m refinement across directions was not run yet due to GPU cost and the need for inflow/nesting checks first (see [`docs/reports/REPORT-20261116T075144-8343.md`](reports/REPORT-20261116T075144-8343.md) and [`docs/sim/STREET5M_VALIDATION_2026-11-16.md`](sim/STREET5M_VALIDATION_2026-11-16.md)).
- **H3 (Open — Tracer Saturation):** `TRACER_REF = 0.03` in `wetness.py` was tuned on the old tracer; on beta's `kd=20, kdv=8` walls tracer (median 0.271), `sqrt(tr / 0.03)` saturates at 1.0 across nearly the entire city, flattening street-to-street fog contrast. Needs retuning.
- **H4 (Open — Config Diffusivity):** `microclimate.toml` still specifies `eddy_diffusivity_m2s = 2.0` (no vertical key), whereas beta assets were built with `kd = 20, kdv = 8`.
- **H5 (Open — `fog5` 2D Tile Floor):** `fog5` 2D tiles do not apply the cloud floor/lid (`fogbase`), causing them to show surface fog even when the hourly 3D tile is completely above the terrain.

### B. Guide 14 — Fog Advection & Forecast Extension ([`docs/fog/guide-14-plan-v2.md`](fog/guide-14-plan-v2.md))
- Diagnose and fix "fog appearing from nowhere" when stepping across the observed-to-forecast boundary, and evaluate extending 5-minute `fog5/` tiles to 12–24h forecast windows.

### C. Voxel Ray-March Grid Artifact Fix ([`docs/fog/grid-artifact-diagnosis.md`](fog/grid-artifact-diagnosis.md))
- Option F2 shader fix in `site/v3/fog2.js`: clamp column-top ray skipping (`tE`) to the point where descending rays enter `colTop`, eliminating the 320m occupancy lattice pattern in steep/top-down views.
