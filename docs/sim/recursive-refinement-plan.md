# Plan: recursive grid refinement to 2.5 m near buildings and streets (plan only, nothing run)

Read from branches `v3-dev` and `v3-refine` of the repo (`microclimate/wind.py`, `street.py`, `refine.py`) and the project notes. Cost numbers are my estimates from the 3,550 M cell updates/s benchmark in `PROGRESS.md`. The building and surface share of the city is an assumption (ranges shown), not measured.

## 1. What we have now
- **City run** (`wind.py`): one uniform 20 m grid, 855 x 811 x 40 = 27.7 M cells, XLB 0.3.2, Warp, D3Q19, Smagorinsky LES-BGK, omega 1.95, 40,000 steps (8,000 averaged). About 5 to 7 min per wind direction on the RTX 4080 SUPER (16 GB). A 20 m solid cell is filled to terrain plus the cell-mean building height, so streets narrower than 20 m do not exist.
- **Street values** (`street.py`): sampled from the 270 degree 20 m field, then multiplied by a refinement correction `refine_corr` where one exists.
- **Refinement already exists** (`refine.py`, branch `v3-refine`), and it is the same idea as yours but one level and not recursive:
  - Tiles of 30 x 30 coarse cells (600 m core plus 100 m padding), chosen where there are many building cells.
  - Each tile is a uniform fine grid (default 5 m, 2.5 m used for Potrero) with real footprints and heights over 3DEP terrain.
  - One-way nesting: the 20 m field is interpolated onto the tile and pushed into a sponge layer at the edges. Nothing flows back to the 20 m run.
  - Each tile is run twice, with and without buildings, and the output is the ratio `corr_bldg`, which cancels the fine grid's own wall profile. This is a good design and should be kept.
  - Resumable per tile and cached. The tile shape and the 100 m headroom are fixed.

## 2. The fractal idea, made concrete
Refine by distance to a solid surface, in rings: 20 m everywhere, 10 m within about 60 m of any building or ground surface, 5 m within about 25 m, 2.5 m within about 10 m. Each level halves the time step (acoustic scaling, so the lattice speed stays 0.05), so a level that is twice as fine costs 8 x 2 = 16 times more per unit volume. Refine only where the geometry is, never in open air or over water.

Cost per coarse time step is the sum over levels of (cells x substeps). Assumed here: 80 km² of built land, surface area about 2.3 times the land area (ground, roofs and walls), 164 bytes per cell (from the 3 GB per 18.8 M cells figure), 40,000 coarse steps, 30% overhead for the refinement interfaces.

| Scenario | 2.5 m cells | Total work vs now | GPU memory | Time per direction | 16 directions |
|---|---|---|---|---|---|
| Now (uniform 20 m) | none | 1x | 4.4 GB | 5 to 7 min | about 1.5 h |
| 5 m floor, whole city (no 2.5 m) | none | about 4x | about 8 GB | about 0.5 h | about 8 h |
| 2.5 m within 5 m of surfaces, whole city | about 59 M | about 20x | about 16 GB | about 2.3 h | about 36 h |
| **2.5 m within 10 m, whole city** | **about 118 M** | **about 38x** | **about 27 GB** | **about 4.3 h** | **about 70 h** |
| Same, pessimistic building share (A=100 km², S=3) | about 192 M | about 62x | about 42 GB | about 7 h | about 110 h |
| Same, optimistic (A=60 km², S=1.8) | about 69 M | about 23x | about 17 GB | about 2.6 h | about 41 h |
| Downtown plus Potrero only (about 10 km²) | about 18 M | about 6x | about 7 GB | about 0.7 h | about 12 h |
| Reference: uniform 2.5 m everywhere, 100 m high | about 512 M | about 150x | about 84 GB | about 13 h | about 200 h |

So: a full-city adaptive 2.5 m grid is about 40 times the work of today's run, 3 to 4 times cheaper than a uniform 2.5 m grid, and it does not fit in the 4080 SUPER's 16 GB. Dropping the ring width from 10 m to 5 m, or refining only the neighbourhoods we actually serve, brings it into reach on the current card.

## 3. Three ways to build it
1. **Tile it (extend `refine.py`).** Keep one-way nested tiles but make them chain: 20 m to 5 m to 2.5 m, restrict the 2.5 m tile height to about 60 m above the roofs, and drop the building-free baseline run to 5 m. A uniform 2.5 m tile of 800 x 800 x 100 m is about 8 M cells, about 1 GB, about 1 min per run. About 150 building tiles gives roughly 3 to 6 h per direction, the same order as the adaptive tree, but memory fits easily, tiles can run overnight or on several machines, and most of the code exists. Weakness: no refinement by distance inside a tile (wasteful over open space and roofs) and no feedback to the coarse field. **Recommended first step.**
2. **Native multi-resolution XLB.** My recollection is that newer XLB (Autodesk's Neon backend) can do multi-resolution grids and multi-GPU. I did not verify this and our pinned 0.3.2 does not have it. Checking the repo is a half-day job and would settle the question; if it works, it gives a true recursive grid with two-way coupling, but it means a newer XLB, possibly a different backend, and re-validating the 20 m results.
3. **Write our own octree or block-structured solver in Warp.** Most flexible, a multi-week job, and easy to get subtly wrong at the level interfaces. Not recommended unless 2 fails.

## 4. What 2.5 m will and will not fix
- It will resolve street canyons (a 20 m street is 8 cells across) and corner acceleration, using real footprints and heights. 177,023 buildings are in the data.
- It will not fix the open problems in `sim/wind-sim-settings.md`: log-law inflow, sea and ground roughness, low-Reynolds wake length, and no validation against stations. A finer grid on a wrong boundary layer gives precise wrong numbers. These come first.
- Building heights are median lidar per footprint and roofs are flat, so tall buildings are underestimated and street canyons will be too uniform in height. At 2.5 m the data, not the grid, may become the limit.
- Smagorinsky LES with bounce-back walls at 2.5 m still does not resolve the true wall layer. The `corr_bldg` ratio approach hides this; a direct fine-grid wind speed should not be trusted in absolute terms.
- The current runs are steady means. At 2.5 m the flow is unsteady (vortex shedding), so averaging time matters: 8,000 averaged steps at 20 m covers a different physical time than at 2.5 m.
- Storage: refined fields at 2.5 m for 16 directions will not go to the viewer as is. They need the 10 m derived maps described in guide 12 (flow vector and shelter factor), which already fit a 100 MB budget for a few neighbourhoods.

## 5. Recommended sequence
1. Finish the corrected 20 m recompute (log-law inflow, free-slip sea, fixed outlet) and validate against buoy 46026 and the SF stations. No refinement before this.
2. Look at what the existing 2.5 m Potrero tile run already shows: runtime, rho range, and how `corr_bldg` at 2.5 m differs from 5 m (`refine_compare`, `refine_check`, and `~/deathfog-data/refined/validation`, which I could not open from here). This tells us whether 2.5 m changes anything over 5 m, which is guide 12's go/no-go test.
3. If it does: chain the tiles (option 1), run Potrero and downtown for 270 degrees only (about 12 h of GPU time for all directions, less for one), compare with street-level station data if any.
4. In parallel, spend half a day on option 2 (XLB multires) so the choice for a city-wide version is informed.
5. Decide on city-wide only after 3 and 4. A realistic target is "2.5 m near the priority neighbourhoods, 5 m elsewhere".

## 6. Open questions for George
- Which neighbourhoods really need street scale? That sets the cost more than any other choice.
- Is a bigger GPU on the table (24 to 80 GB)? Only the whole-city adaptive version needs it.
- Do all 16 directions need 2.5 m, or only the few that dominate fog advection (west and north-west)? Halving the directions halves the cost.
