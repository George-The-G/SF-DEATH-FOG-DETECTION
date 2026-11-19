# Guide 12: 5 m street wind, station validation (2026-11-16)

Verdict: NOT validated. Nothing promoted; rel_wind stays 1; live libraries and refined/ untouched.

## 20 m library vs stations (script deathfog-data/spikes/street5m_v3/station_check_20m.py, 40 days, ratio = station / buoy 46026)
Only 3 wind stations lie inside the 20 m grid (FTPC1, GGBC1, PXOC1). Weighted mean |log(sim/obs speed ratio)| over 36 station x direction cells:
new (walls) library 0.75, old pre-walls table 0.94 (old was ~1.0 everywhere, bias +0.93 in log; new +0.19). Rank skill is weak (corr 0.30).
Big misses in the dominant NW sector: PXOC1 at 315 sim 1.02 vs obs 0.34; GGBC1 at 315 sim 0.19 vs obs 0.75; FTPC1 at 337.5 sim 0.74 vs obs 0.24.
The buoy is a crude inflow reference (the Gate channels and turns the flow), so treat this as indicative.

## 5 m nested patches centred on the stations (script station_patches.py, fixed solver, 20 m field as boundary and sponge, 8 directions)
Weighted mean |log| against observation: 20 m 0.77, 5 m 0.94 (24 cells). The 5 m run is too slow at the sensor (0.02 to 0.3 of inflow
against observed 0.25 to 0.6): fine-grid wall layer plus low Reynolds number over-damp absolute speed. Only the building effect
(corr_bldg, fine run with buildings / terrain-only twin) is usable as a ratio, and no station sits in dense streets, so it cannot be validated here.

## Fixes made
refine.py had the same stale Warp kernel cache trap as wind.py: it now compiles into a private cache dir per process. New options out_dir, lib_dir, only.
Smoke run (3 Potrero patches, direction 270): about 40 s per patch with the terrain-only twin; corr_bldg median 0.23 (5-95 %: 0.04 to 0.88).

## Scope if George wants to go on
Full city at one direction: 297 patches x about 40 s = 3.3 GPU hours; 15 directions about 50 h. The 5 m tile approach (guide) is about 5 GPU hours but has uniform inflow.
Before any build: calibrate the fine run's absolute speed (log-law wall treatment) or use only corr_bldg; find a dense-street ground truth (none inside the 20 m grid).
