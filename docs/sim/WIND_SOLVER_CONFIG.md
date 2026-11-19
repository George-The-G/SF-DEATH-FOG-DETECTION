# Wind solver (`microclimate/wind.py`): why it is set up this way

The script shows what the settings are. This note keeps what the script cannot: the reasons, the traps, and the tests that guard them. Written 2026-11-15 after the wall investigation (branch `wind-cube-check`, report in `spikes/windcube/REPORT.md`).

## Setup in one paragraph
XLB 0.3.2, Warp backend, D3Q19, Smagorinsky BGK, omega 1.95, FP32. 20 m cells, 855 x 811 x 40 (17 x 16 km, 800 m tall). Every direction is run as wind from 270 on a rotated grid (`windrot.py`) and rotated back. 40,000 steps, mean of the last 8,000 sampled every 100. Output is velocity divided by the inflow speed (U_LAT = 0.05), float16.

## Boundaries and why
| Where | Condition | Reason |
|---|---|---|
| inflow face | Regularized velocity, uniform speed, no profile | simplest; the city builds its own boundary layer (the literature note suggests a log-law profile as the next improvement, not done) |
| outflow face | fixed-pressure equilibrium (`outlet="eq"`) | the zero-gradient `ExtrapolationOutflowBC` reflected back pressure and slowed an empty sea box to 0.13 at 6 km |
| top and sides parallel to the wind | equilibrium far field (rho 1, free-stream u) | a free-slip lid (`box_slip=True`) was tried: similar result, no gain |
| buildings, terrain | no-slip, halfway bounce-back on the one-cell solid shell | works; the 20 m grid fills sub-cell buildings, so street scale needs 5 m |
| open sea floor | free-slip (specular reflection, `slipbc.py`, `sea_slip=True`) | a no-slip floor on 20 m cells grows a thick artificial boundary layer: 30 m speed 0.13 after 6 km, real sea stays about 0.85 to 0.9 |
| box edge lines | full-way bounce-back | corners where two faces meet |

## Traps (each one cost hours)
1. **Stale Warp kernel cache.** Warp keys the XLB stepper module by a hash that does not see the BC set, so a kernel compiled by any earlier script is reused and the wall nodes stay at free-stream speed (no walls, no wakes; the whole first wind library was built that way). `run()` therefore compiles into a private temp cache dir every run. Never remove that. Test: `tests_mc/test_wind_walls.py::test_cube_has_wake_and_stagnation`.
2. **Slip floor next to a wall or a box face blows up** (NaN within 250 steps in the rotated 112.5 and 135 frames). The slip floor is kept off cells within 2 cells of any solid column and within 3 cells of the box faces, and directions parallel to the plane use bounce-back.
3. XLB's BC id registry is process-global; `run()` resets it (see the comment in the code).
4. `cache_kernels = False` alone does not help; the cache directory has to change.

## Checks that say the setup is right
- Cube (20 cells, floor): stagnation ux about 0.44 in front, reversed flow about -0.2 behind, side speed-up about 1.3.
- Empty sea, 8 km: speed at 30 m stays 0.9 to 0.93 of the inflow, 0.99 aloft (`test_empty_sea_keeps_its_speed_over_fetch`).
- City, 270: median ratio 0.19 at 50 m above roofs, 0.56 at 110 m. Not yet compared with observed station wind.

## Known limits
- Low Reynolds number (cell Re about 12): wakes are longer than at city Reynolds numbers.
- The box lid raises the flow aloft (median about 1.19 at 410 m).
- Uniform inflow: ratios are to a height-independent inflow speed, not to a 10 m reference.
- 157.5 degrees is an accepted gap in the library (NaN at step 13,000 in the old setup; not retried with the new one).
