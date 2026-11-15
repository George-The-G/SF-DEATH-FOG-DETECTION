# Wind solver check: why the 20 m wind library is near-uniform (2026-11-15)

Branch `wind-cube-check` (worktree `C:\Users\George\AppData\Local\Temp\wt_windcube`), not pushed. Nothing in `library/` was changed.

## Cause
Not the wall treatment, the inflow or the averaging. The wall nodes next to obstacles never ran their boundary condition, because `wind.run` used a stale compiled GPU kernel from Warp's on-disk cache (`C:\Users\George\AppData\Local\NVIDIA\warp\Cache\1.19.0`).
- Warp names the XLB stepper module `nse_stepper_<hash>`, and the hash does not see which boundary-condition functions the kernel was built with. A kernel compiled earlier by some other script (a test, bench or spike with a different BC set) has the same hash (`e7826d1`) and is loaded silently ("took 1 ms (cached)"). The cached `.cu` differs from a fresh compile of the same `wind.run` (it carries an extra equilibrium function), and the BC ids no longer map to the right functions.
- Symptom, measured on a 100 x 50 x 30 box with a 10-cell cube: the fluid cells touching the cube are exactly (1.0, 0, 0) from step 0 and never change, so every obstacle is wrapped in a skin at free-stream speed (a cube leaves a 3 % lee deficit, roof uz about -0.03 into the cube). With an empty cache dir the same nodes go to about 0 and the flow separates.

## Cube benchmark (20 x 20 x 20 cube on the floor, 200 x 100 x 60 box, 12,000 steps, omega 1.95, wind from 270)
| quantity (mean field) | default cache (old) | fresh kernel (fixed) |
|---|---|---|
| ux in front of the cube, x/H = -0.25, mid height | 1.02 | 0.44 |
| ux in the lee at x/H = 0.5 / 1 / 2 | 0.97 / 0.97 / 0.98 | -0.15 / -0.20 / -0.15 |
| reversed-flow length at k = 2 | none | 4.0 H (laminar, cell Re about 230; published thin-boundary-layer cubes at Re 4e4 give about 1.6 H) |
| max side speed-up | 1.03 | 1.30 (published 1.3 to 1.5) |
| mean ratio 2 cells above ground | 0.98 | 0.34 |
Stagnation, reversed lee flow and side acceleration are now right in sign and size. The recirculation length is longer than the high-Re benchmark because this run is low Reynolds number; the LES term does not make it a turbulent run. That is a separate, known limit, not a bug.

## City, wind from 270 (855 x 811 x 40 cells, 40,000 steps, 342 s, no NaN)
Speed ratio at k cells above the highest solid cell of each column, 5th / 50th / 95th percentile, old library vs fixed run (`C:\Users\George\deathfog-data\scratch\windcube_city2700_fixed.npz`):
| k (height above roof or ground) | old | fixed | share of cells with reversed flow, fixed |
|---|---|---|---|
| 1 (about 30 m) | 1.00 / 1.00 / 1.00 | 0.00 / 0.04 / 0.11 | 4.6 % |
| 2 (about 50 m) | 1.00 / 1.00 / 1.02 | 0.02 / 0.10 / 0.24 | 2.8 % |
| 3 (about 70 m) | 1.00 / 1.02 / 1.06 | 0.04 / 0.19 / 0.38 | 1.9 % |
| 5 (about 110 m) | 1.00 / 1.05 / 1.10 | 0.09 / 0.35 / 0.61 | 1.0 % |
| 8 (about 170 m) | 1.01 / 1.08 / 1.11 | 0.21 / 0.59 / 0.85 | 0.1 % |
| 12 (about 250 m) | 1.03 / 1.09 / 1.11 | 0.41 / 0.83 / 1.03 | 0 |
Domain-mean ratio at 60 / 120 / 200 / 400 m above sea level: old 1.01 / 1.03 / 1.05 / 1.01, fixed 0.22 / 0.37 / 0.57 / 0.93. The diagnostic `mean_speed_ratio_near_ground` goes from 1.003 to 0.164. All 16 library directions have 1.006 to 1.008, so all of them were computed with the stale kernel.

## Fix (in this branch)
`microclimate/wind.py`: `run()` points `wp.config.kernel_cache_dir` at an empty temporary folder for the run and deletes it afterwards (a fresh compile costs about 10 s here). `cache_kernels = False` alone did not help. `tests_mc/test_wind_walls.py`: 12-cell cube, 3 s on the GPU; passes with the fix and fails without it (checked).

## What this means before anyone acts on it
- The whole wind library (16 directions) and everything built from it (fog tracer bands, street wind, the 5 m spike comparison) used fields with no walls. The fixed field is strongly sheltered at street level (median 0.04 to 0.10 at 30 to 50 m above roofs) because the inflow is uniform, so the city builds its own boundary layer over about 17 km. Whether those magnitudes are right is not shown here: there is no validation against station wind yet, and any calibration that was tuned on the old near-uniform field needs a look.
- Recomputing is about 6 to 12 minutes per direction on the 20 m grid for the 16 directions, then the fog bands again (the roughly 2.9 GB tracer set). That needs George's OK. The 157.5 gap (NaN at step 13,000) may behave differently with working walls.
