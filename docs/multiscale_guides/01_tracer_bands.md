# Guide 01: finer inflow bands for the fog tracer (PC GPU)

Read `00_README.md` first (rules, band edges). This guide USES THE GPU. Only one GPU job at a time.

## Goal
For each of the 9 west wind directions, compute 11 fog tracer fields (one per inflow band) instead of 4, so a fog layer with any top height can be built as a weighted sum. Nothing in the live site changes in this guide.

## Background (from the code)
- `microclimate/fog.py`: `run_band(u, solid, spec, direction_deg, band, ...)` solves a steady tracer on the stored wind `library/wind_<dddd>.npz`. The inflow face is 1 where the cell centre `zc = (k + 0.5) * 20` satisfies `band[0] <= zc < band[1]`, 0 elsewhere. `converge_band` reruns with more transits until the drift over the last 2000 steps is under `tol`.
- Existing files `library/fog_<dddd>_<b>.npz` (b = 0..3, key `c`, float16 `[855, 811, 40]`) feed street wetness through `fogtop.sample_bands`. **Do not touch, rename or recompute them.** Do not change `[library].fog_bands_m` in `microclimate.toml`.
- The solver is linear in the inflow values (upwind advection, diffusion, linear loss; the clamp to 0..1 never bites for inflow in 0..1). So the sum of the fields of adjacent bands equals the field of the merged band.

## Steps

1. **Config.** Add to `microclimate.toml` under `[library]`:
   `fog_bands3d_m = [[0,40],[40,100],[100,140],[140,200],[200,260],[260,300],[300,360],[360,420],[420,500],[500,640],[640,800]]`
   Extend `tests_mc/test_config.py` to assert: 11 bands, contiguous, every edge a multiple of 20, first 0, last 800, and that edges 140, 300, 500 are present.
2. **Command.** Add `python -m microclimate fog3d [--dirs 270,...] [--bands 0,1,...] [--tol 0.01] [--dry-run]` (wire it where the other subcommands live, `microclimate/__main__.py`). It calls a new `fog.build_bands3d(cfg, grids_dir, lib_dir, directions, bands, tol)` that mirrors `build_dirs` but:
   - reads `fog_bands3d_m`,
   - writes `library/fog3d_<dddd>_<bb>.npz` (key `c`, float16) and `.json` (the diag, plus `band_m`, `attempts`),
   - skips files that already exist (so it can be stopped and resumed),
   - writes a status file `library/runs/fog3d_status.json` after every band (`done`, `total`, `current`, `started`, `eta_s`),
   - takes and releases the GPU lock exactly as the existing GPU jobs do (read how `winddirs` does it; follow the skill's GPU check before starting).
   - `--dry-run` prints the list of runs and the mapping of each band to its inflow cells (`k` range), without the GPU.
3. **Time one run first.** Run direction 270, band 0 only. Record seconds and steps from the diag. Estimate the total for 99 runs and report it to George before starting the rest. (My estimate: 5-15 minutes per run, 8-25 hours total; unmeasured.)
4. **Run the rest** direction by direction, 270 first. Leave it running; check the status file at long intervals.
5. **Validation command** `python -m microclimate fog3d --check` (CPU only), writing `reports/fog3d/bands_check.json`:
   - a. No NaN; min >= 0; max <= 1.001 in every file.
   - b. `drift_last_2000_steps < tol` in every diag.
   - c. **Nesting test**, per direction and per old band `B` in 0..3: `S = sum of the new bands that make up B` (0-2, 3-5, 6-8, 9-10), `O = fog_<dddd>_<B>.npz`. Over fluid cells: `max |S - O| < 0.05` and `mean |S - O| < 0.01`. (Both sides are converged only to `tol`, so do not expect exact equality.)
   - d. Full-column test for 270: the sum of all 11 bands at the inflow face rows 2-5 cells inside the domain is within 0.02 of 1 below 800 m.
   - e. Monotone test: for each direction, the mean concentration below 100 m of band b decreases as b goes up from band 3 (higher air reaches the street less). Report, do not fail, if it does not hold.
6. **If a direction fails c or b:** rerun that band with the next entry of the transit schedule. If it still fails, report which and stop; do not loosen the tolerance.

## Tests (CPU, in `tests_mc/test_fog3d_bands.py`)
- Band-to-cell mapping: for `nz = 40`, `dx = 20`, each of the 11 bands selects the expected `k` range, the ranges are disjoint and cover 0..39.
- A tiny synthetic linearity test is not possible without the GPU kernel; skip it and rely on step 5c. Say so in the report.

## Done when
- 99 files exist, `bands_check.json` passes a-d, the full suite is green.
- Report: seconds per run (min, median, max), total GPU hours, disk size of the new files, the worst nesting error per direction, any band that needed extra transits.

## Pitfalls
- `run_band` picks `dev = "cuda:0"`. Never run two at once.
- Direction file stems are `int(round(d * 10))` zero-padded to 4 (`2025` for 202.5, `3600` for 360).
- 360 and 180 are axis-aligned; the others use the rotated-frame wind but the tracer runs on the original grid with the stored field. Do not add rotation here.
- Disk: 99 files of float16 `[855, 811, 40]` compress well but check free space before starting (need about 10 GB free, estimate).
- Do not commit library files. Only code, config, tests and the small report JSON.
