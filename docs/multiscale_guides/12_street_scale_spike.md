# Guide 12: street-scale wind and fog detail: a time-boxed spike with a go/no-go

Read `00_README.md` first. Needs guide 04 in beta. PC with GPU (one GPU job at a time) plus a throwaway viewer branch. **Experiment only: nothing merges. Time box: 5 working days plus GPU hours. The deliverable is a report, pictures and a recommendation.**

## Question to answer
Does a 5 m wind simulation of one neighbourhood give visibly and measurably better street-level fog behaviour than the 20 m fields, at a data size and frame cost a phone can take?

## Background (from the code and PROGRESS.md)
- The 20 m solid grid fills a cell up to terrain + the MEAN building height of the cell, so streets narrower than 20 m do not exist in the wind fields.
- XLB on the RTX 4080 SUPER ran about 3,550 million cell updates a second; the 27 M-cell city took 6-7 minutes per direction.
- `microclimate/wind.py::run` only supports inflow "from the left"; other directions run in a rotated frame (`windrot.py`).
- Street points already get a "5 m wind correction" of some kind (`city.wind_refine`): read it first and report what it does, so this spike does not duplicate it.

## Steps

1. **One tile, one direction.** Potrero zone (decision D5), a 2 x 2 km tile centred on 20th and Kansas plus a 400 m margin on each side, 5 m cells, 0-300 m high: 560 x 560 x 60 = 18.8 M cells. Wind from 270.
2. **Geometry at 5 m.** Terrain from the 3DEP tile resampled to 5 m (bilinear); buildings rasterised from `raw/bldg.geojson` with each footprint's own height (`hgt_median_m`), not cell means. A cell is solid if its centre is inside a footprint and below the roof, or below the terrain. Report the share of street cells that are open at 5 m against 20 m.
3. **Boundary values from the 20 m field.** Simplest workable approach for a spike: run the 5 m tile with the same uniform inflow profile approach as the city run, but set the inflow speed profile on the upwind face to the 20 m field's mean profile at that face (sample `library/wind_2700.npz` along the face, average across it). Full one-way nesting on all faces is a later refinement; say so.
4. **Run** (GPU): same solver settings as the city run scaled to the cell size (keep the lattice velocity and relaxation as in `wind.py`; the step count scales with the number of cells across the domain: start with 30,000 steps and check convergence of the mean near-ground speed over the last 5,000). Save the mean field as float16 and 40 instantaneous snapshots of the lowest 60 m at 10 m resolution, 200 steps apart. Estimated time 20-40 minutes (unmeasured): time it and report.
5. **Compare with the 20 m field** on the same area: speed ratio maps at 2 m and 10 m above ground; a list of streets where the 5 m run shows channelling or shelter the 20 m run lacks; the distribution of speed-up factors.
6. **Derived maps for the viewer** on a 10 m grid, 6 levels (2.5, 7.5, 15, 25, 40, 60 m above ground): flow vector (2 x int8, as a ratio of inflow speed) and a shelter factor (local mean speed / open-terrain speed at the same height, 0..1, uint8). Size for the tile: 200 x 200 x 6 x 3 bytes = 720 KB raw. Report the compressed size, and multiply out for 6 directions and 10 neighbourhoods against Pages and R2 limits.
7. **Viewer prototype** (branch only, lab switch): near the camera (under 600 m) and below 60 m above ground, modulate the baked fog density by the shelter factor (`dens *= mix(1, 0.6 + 0.8 * shelter, k)`, first guess) and advect the fine noise with the 10 m flow instead of the 80 m wind. No new simulation in the browser. Take street-level screenshots and a short recording with the snapshots looped, with and without.
8. **Ground truth, such as it is:** any station inside the tile (list the ones in `C.STATIONS` that fall in it; likely none or one). If there is one, compare its wind speed ratio to the coast station with both fields over the archive.

## Go/no-go
**Go** if all three hold: (a) the 5 m field differs from the 20 m field by more than 30 % in near-ground speed on at least a quarter of street cells (otherwise it adds little); (b) George prefers the street-level pictures with it; (c) the data for the priority neighbourhoods and 6 directions fits in 100 MB compressed and the prototype costs under 2 ms a frame on a real phone. **No-go** otherwise. A no-go with a clear reason is a good result.

## Report
Timings, sizes, the comparison maps, screenshot and recording paths, the go/no-go table, and what a full build would need (GPU hours for N tiles x 6 directions, hosting, the nesting refinement).

## Pitfalls
- XLB keeps boundary-condition ids in a process-global registry; `wind.run` resets it. Start each run in a fresh process anyway.
- 18.8 M cells need about 3 GB of GPU memory by the benchmark's ratio; check free memory first.
- The fields are neutral (no inversion). At street level under a fog deck that matters less than at hill scale, but say it in the report.
- Do not write into `deathfog-data/library/`; use `deathfog-data/spikes/street5m/`.
