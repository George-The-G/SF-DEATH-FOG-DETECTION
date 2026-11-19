# Documentation Index (`docs/INDEX.md`)

Purpose: Map of all documentation in `SF-DEATH-FOG-DETECTION`, grouped by topic.
Status: current
Last verified: 2026-11-18

---

## Core Entry Points
- [`../README.md`](../README.md) — Project overview, live URLs, quickstart commands, and data sources.
- [`../AGENTS.md`](../AGENTS.md) — Rules for AI agents, self-contained directory layout, frozen production paths, and test commands.
- [`AGENT_ERGONOMICS_PLAN.md`](AGENT_ERGONOMICS_PLAN.md) — Plan for drift-safe code navigation (`tools/codemap.py`), token efficiency, local previewing, and agent skills.
- [`STATUS.md`](STATUS.md) — Current production/beta status and active engineering backlog.
- [`PIPELINE_DISCREPANCIES.md`](PIPELINE_DISCREPANCIES.md) — Graded audit (H1–H5, M1–M4, L1–L3) of code and data vs. guides and config.
- [`../data/MANIFEST.md`](../data/MANIFEST.md) — Inventory of the local `data/` directory (`$DEATHFOG_DATA`), file hashes, and regeneration commands.

## Architecture & Pipeline Atlas
- [`architecture/branches_and_deploy.md`](architecture/branches_and_deploy.md) — How `main`, `v3-dev`, `data`, and `live-archive` interact in GitHub Actions.
- [`fog_pipeline.html`](fog_pipeline.html) — Self-contained visual atlas of the fog pipeline (deployed at `/v3/pipeline.html`).
- [`pipeline_doc/README.md`](pipeline_doc/README.md) — Generator scripts (`build.py`, `gen.py`, `shot.py`, `probe.py`, `facts.json`) for the pipeline atlas.

## Multiscale Voxel Fog Guides (`multiscale_guides/` & `multiscale/`)
- [`multiscale/multiscale-plan.md`](multiscale/multiscale-plan.md) — Master plan for the multi-scale 3D voxel fog and wind pipeline.
- [`multiscale_guides/00_README.md`](multiscale_guides/00_README.md) — Overview and switch table for Guides 01–12.
- [`multiscale_guides/01_tracer_bands.md`](multiscale_guides/01_tracer_bands.md) — Guide 01: 4-band height-resolved fog tracer runs.
- [`multiscale_guides/02_tracer3d_asset.md`](multiscale_guides/02_tracer3d_asset.md) — Guide 02: Packing `live_tracer3d.npz`.
- [`multiscale_guides/03_live_fog3d.md`](multiscale_guides/03_live_fog3d.md) — Guide 03: Live 3D fog volume builder (`fog3d_<fff>.png`).
- [`multiscale_guides/04_viewer_fog3d.md`](multiscale_guides/04_viewer_fog3d.md) — Guide 04: 3D fog volume rendering in the viewer.
- [`multiscale_guides/05_cloud_floor_urban_heat.md`](multiscale_guides/05_cloud_floor_urban_heat.md) — Guide 05: Cloud floor (`fog_base`) and urban heat lift.
- [`multiscale_guides/06_fog_top.md`](multiscale_guides/06_fog_top.md) — Guide 06: Multi-source fog top height fusion.
- [`multiscale_guides/07_goes5_macro.md`](multiscale_guides/07_goes5_macro.md) — Guide 07: 5-minute GOES-18 low-cloud store and macro frames.
- [`multiscale_guides/08_fog_travel.md`](multiscale_guides/08_fog_travel.md) — Guide 08: 5-minute fog advection and travel between hourly frames.
- [`multiscale_guides/09_macro_view.md`](multiscale_guides/09_macro_view.md) — Guide 09: Macro satellite cloud view in the 3D viewer.
- [`multiscale_guides/10_compute_hosting.md`](multiscale_guides/10_compute_hosting.md) — Guide 10: Cloudflare R2 and Worker hosting.
- [`multiscale_guides/11_marine_layer_spike.md`](multiscale_guides/11_marine_layer_spike.md) — Guide 11: Marine layer physics spike.
- [`multiscale_guides/12_street_scale_spike.md`](multiscale_guides/12_street_scale_spike.md) — Guide 12: 5m street-scale wind refinement spike.
- [`multiscale_guides/13_sky_layers.md`](multiscale_guides/13_sky_layers.md) — Guide 13: Sky layers module and cloud-above-fog-top rule ([plan](multiscale_guides/13_sky_layers_plan.md)).

## Wetness Model Guides (`model_guides/`)
- [`model_guides/00_README.md`](model_guides/00_README.md) — Wetness index review roadmap and layer promotion status.
- [`model_guides/01_switches_and_compare.md`](model_guides/01_switches_and_compare.md) — Config switches and A/B comparison harness.
- [`model_guides/02_state_and_archive.md`](model_guides/02_state_and_archive.md) — Rolling state and `live-archive` persistence.
- [`model_guides/03_stations.md`](model_guides/03_stations.md) — Station observations and spatial nudging.
- [`model_guides/04_satellite_cloud_score.md`](model_guides/04_satellite_cloud_score.md) — GOES-18 low-cloud scoring.
- [`model_guides/05_slippery_and_depth.md`](model_guides/05_slippery_and_depth.md) — Water film depth and slipperiness curve.
- [`model_guides/06_sun.md`](model_guides/06_sun.md) — Clear-sky solar radiation and skyline shading.
- [`model_guides/07_pavement_heat.md`](model_guides/07_pavement_heat.md) — Pavement thermal inertia model.
- [`model_guides/08_cloud_base.md`](model_guides/08_cloud_base.md) — LCL cloud base estimation.
- [`model_guides/09_lag_speed.md`](model_guides/09_lag_speed.md) — Wind-speed dependent inland transport lag.
- [`model_guides/10_evaporation_and_cloud.md`](model_guides/10_evaporation_and_cloud.md) — Aerodynamic evaporation and cloud cover.
- [`model_guides/11_drizzle.md`](model_guides/11_drizzle.md) — Thick-fog gravitational drizzle.
- [`model_guides/12_insitu_fog.md`](model_guides/12_insitu_fog.md) — In-situ radiative fog formation.
- [`model_guides/13_grease_factors.md`](model_guides/13_grease_factors.md) — Dry-spell road grease accumulation.
- [`model_guides/14_wind_east.md`](model_guides/14_wind_east.md) — East/bay-side wind direction coverage.

## Wind Simulation (`sim/` & `reports/`)
- [`sim/WIND_SOLVER_CONFIG.md`](sim/WIND_SOLVER_CONFIG.md) — XLB/Warp LBM wind solver setup, boundary conditions, and cache traps.
- [`sim/wind-sim-settings.md`](sim/wind-sim-settings.md) — Sea-surface and city boundary condition research.
- [`sim/warp-cfd-comparison.md`](sim/warp-cfd-comparison.md) — Comparison of Autodesk Warp CFD / XLB with our wind simulation setup.
- [`sim/recursive-refinement-plan.md`](sim/recursive-refinement-plan.md) — Plan for nested grid refinement (20m to 5m).
- [`sim/STREET5M_VALIDATION_2026-11-16.md`](sim/STREET5M_VALIDATION_2026-11-16.md) — Station validation notes for 5m street wind.
- [`sim/PLAN_SIM_VISUALIZER.md`](sim/PLAN_SIM_VISUALIZER.md) — Plan for interactive voxel and wind-vector simulation visualizer.
- [`reports/REPORT.md`](reports/REPORT.md) — Discovery report on the Warp kernel cache bug that left obstacles without wall BCs.
- [`reports/REPORT-20261116T075144-8343.md`](reports/REPORT-20261116T075144-8343.md) — Guide 12 5m street-wind rerun report with working walls.

## Fog Rendering, Performance & Replay (`fog/`, `render/`, `perf/`, `replay/`)
- [`fog/CLOUD_PIPELINE_FIX_PLAN.md`](fog/CLOUD_PIPELINE_FIX_PLAN.md) — Unification plan for the 3 cloud sources (1h `fog3d`, 5m `fog5`, 5m `macro`), discrepancy fixes (H4, H5, M3, macro top decode), and magic-number cleanup.
- [`fog/guide-14-plan-v2.md`](fog/guide-14-plan-v2.md) — Guide 14 plan: fixing fog jumps and extending the 5-minute forecast ([scope](fog/guide-14-fog-advection-scope.md)).
- [`fog/grid-artifact-diagnosis.md`](fog/grid-artifact-diagnosis.md) — Diagnosis and shader fix (Option F2) for the 320m column-skip grid pattern in `fog2.js`.
- [`fog/cloud_tech_survey.md`](fog/cloud_tech_survey.md) — Survey of real-time volumetric cloud and sky rendering techniques.
- [`render/render-perf-plan.md`](render/render-perf-plan.md) — Master rendering performance plan ([Plan A viewer perf](render/plan-A-viewer-performance.md), [Plan B building merge](render/plan-B-building-merge.md)).
- [`perf/README.md`](perf/README.md) & [`perf/PERFORMANCE_GAPS.md`](perf/PERFORMANCE_GAPS.md) — Viewer frame-time benchmarks and remaining performance gaps.
- [`replay/goes-anchor-plan.md`](replay/goes-anchor-plan.md), [`replay/goes-crop-format.md`](replay/goes-crop-format.md), [`replay/replay-engine-review.md`](replay/replay-engine-review.md) — Satellite-anchored replay architecture.

## Validation, Data Sources, Bus & Skate (`validate/`, `data-sources/`, `bus/`, `skate/`)
- [`validate/validation-plan.md`](validate/validation-plan.md) — Leave-one-station/sounding-out validation framework ([JSON results](validate/leave_one_sounding_out.json), [2026-11-12 comparison](validate/compare_20261112T120000Z.md)).
- [`data-sources/madis.md`](data-sources/madis.md) — Evaluation of NOAA MADIS mesonet stations across San Francisco.
- [`bus/live-bus-plan.md`](bus/live-bus-plan.md) — Live Muni bus tracking and GTFS schedule integration for skate districts.
- [`skate/README.md`](skate/README.md), [`skate/FEATURES.md`](skate/FEATURES.md), [`skate/AGENTS.md`](skate/AGENTS.md) — Skatable 3D world side feature (`Take control`).

## Runbooks & Mockups (`runbooks/`, `mockups/`)
- [`runbooks/RUNBOOK_WIND_EAST.md`](runbooks/RUNBOOK_WIND_EAST.md) — Runbook for executing and monitoring single-direction wind + fog library builds.
- [`mockups/ui_redesign_v1.html`](mockups/ui_redesign_v1.html) & [`mockups/playback/`](mockups/playback/) — UI and playback speed/label HTML mockups.

## Historical Plans & Logs (`history/`)
- [`history/branch_log.md`](history/branch_log.md) — 2026-11-18 branch audit and consolidation log.
- [`history/PROGRESS.md`](history/PROGRESS.md), [`history/PLAN_LIVE_DATA.md`](history/PLAN_LIVE_DATA.md), [`history/MODEL_REVIEW_PLAN.md`](history/MODEL_REVIEW_PLAN.md), [`history/MVP_REPORT.md`](history/MVP_REPORT.md), [`history/PROMOTE_PLAN.md`](history/PROMOTE_PLAN.md), [`history/UI_REDESIGN_PLAN.md`](history/UI_REDESIGN_PLAN.md), [`history/INSTALLED_PACKAGES.md`](history/INSTALLED_PACKAGES.md).
- [`REPO_CONSOLIDATION_PLAN.md`](REPO_CONSOLIDATION_PLAN.md) — The 2026-11-18 repository consolidation plan.
