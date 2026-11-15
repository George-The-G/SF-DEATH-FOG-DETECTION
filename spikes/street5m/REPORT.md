# Guide 12: street-scale (5 m) wind spike, report (2026-11-15)

Code: `spikes/street5m/run5m.py` (geometry and GPU run), `compare.py`, `derive.py`. Data and pictures: `C:\Users\George\deathfog-data\spikes\street5m\` (nothing written to `library/`). Nothing merges.

## Verdict: NO-GO as measured, and a solver concern that needs a look before anyone trusts either field
- (a) FAILS: the 5 m field does not differ from "free stream" on the street cells that the 20 m grid leaves open. At 7.5 m and 12.5 m above ground, 0.0 % of street cells (core 2 x 2 km, buildings excluded) have a speed ratio below 0.7 or above 1.3, and the median 5 m / 20 m ratio on cells open in both is 1.00. The 38 % / 18 % "differing by more than 30 %" in `compare.json` is only the 20 m grid filling in streets (18.6 % / 3.3 % of street cells are solid in the 20 m grid) and its coarse speeds, not channelling or shelter found at 5 m.
- (b) not asked (no viewer prototype: there is no street-scale structure to show). (c) the data fits easily (below), the frame cost was not measured.
- Concern: the same near-uniform behaviour shows in the existing 20 m wind library: in `library/wind_2700.npz` the 5th to 95th percentile of the speed ratio over fluid cells is 1.00 to 1.00 at the first layer, 0.99 to 1.01 at k = 2, 0.99 to 1.03 at k = 3 (only the cells touching walls are below 0.9). A sanity run with one 20 x 20 x 20 cell cube in an empty 200 x 100 x 60 box with the same `wind.run` settings (12,000 steps) gives a lee deficit of under 3 % (0.97 to 0.99 for 60 cells behind the cube), where a real cube wake has a clear deficit. So the mean fields from this solver setup show almost no wakes or channelling. This may be known and accepted (the large-scale gradient is present: 0.996 at the west edge to 0.866 at the east edge in `validate_dirs`), but it limits what any resolution change can show. Worth a check of the wall treatment (the halfway bounce-back on the solid shell), the inflow (uniform, no turbulence) and the averaging, against a published cube benchmark, before the 5 m idea is judged.

## Timings and geometry
- Tile: 2 x 2 km centred on 20th and Kansas plus 400 m margin, 5 m cells: 560 x 560 x 60 = 18.8 M cells (z up to 300 m above sea level), wind from 270, 30,000 steps (5,000 steps averaged every 100).
- GPU run: 169 s for the solver, 231 s with setup and saving (the guide guessed 20 to 40 minutes). Max |u| settled at 0.062 lattice (ratio 1.24); no NaN. The mean field is float16, 63.6 MB compressed.
- Convergence: the log shows max |u| constant to 4 digits from step 8,000; the mean near-ground speed over the last 5,000 steps was not saved as a time series (a small gap).
- Geometry: 9,996 building footprints, each with its own height (`hgt_median_m`, stored as text in the file; max 45.3 m); terrain from the 3DEP tile at 5 m (0 to 99 m). Cells solid: 7.1 %; footprints cover 34.5 % of the tile; 65.5 % of cells are open street / ground; 29.1 % of those open street cells fall in 20 m cells whose mean building height exceeds 2 m (the 20 m grid fills them up to terrain + mean height).
- Inflow: uniform, as in the city run. The guide's 20 m-profile inflow and full nesting were not done.

## Derived maps for the viewer (step 6, `derive.py`)
10 m grid, 6 levels above ground (2.5, 7.5, 15, 25, 40, 60 m): flow vector (2 x int8, scale 1/64) and shelter factor (speed / inflow speed, uint8). Raw 720,000 bytes per tile and direction; compressed 64,082 bytes (0.064 MB). Six directions: 0.38 MB per tile; 10 neighbourhoods and 6 directions: 3.8 MB compressed, far below the 100 MB limit. File: `derived_270.npz` in the spike folder. The maps are nearly uniform for the reason above.

## Pictures
`C:\Users\George\deathfog-data\spikes\street5m\compare_speed_maps.png`: speed ratio of the 5 m run at 7.5 m and 12.5 m above ground, and of the 20 m field at 12.5 m, same cells.

## Not done
Viewer prototype and recordings (step 7), station ground truth (step 8: no station in `C.STATIONS` falls in the tile; the nearest, Pier 1 and Pier 17, are 3 km away), the "2 m" level (the first fluid layer above the ground carries no information in this solver, so the comparison uses the second and third layers, about 7.5 and 12.5 m).

## What a full build would need if it were a go
GPU time: about 4 minutes per tile and direction, so 10 neighbourhoods x 6 directions is about 4 GPU hours; hosting about 4 MB; the nesting refinement (boundary values from the 20 m field on all faces) and a solver check against a cube benchmark first.
