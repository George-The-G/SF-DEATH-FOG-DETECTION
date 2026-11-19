# Guide 09: the macro view: terrain and cloud banks out to 60 km

Read `00_README.md` first. Needs guide 07 (macro frames). Part A on the PC (CPU), part B in the viewer (`app.js`, `fog2.js`: one agent at a time). Beta only. **This adds new visible things, so part B starts with a mock for George.**

## Goal
Pull the camera back and see the region: the coast from Point Reyes to Half Moon Bay, the bay, and the fog bank offshore as the satellite sees it, updated every 5 minutes. Near the city it joins the detailed fog without a visible seam.

## Part A: wide terrain (PC, CPU)

1. **Data.** USGS 3DEP 1/3 arc-second tiles from the same S3 path the repo already used for `USGS_13_n38w123.tif` (see PROGRESS.md M1). Box `[-123.3, 37.2, -121.9, 38.3]` needs tiles n38w123, n38w124, n39w123, n39w124 and n38w122, n39w122 (a tile is named by its north-west corner; confirm by reading each tile's bounds before use). About 220 MB each; keep them in `deathfog-data/raw/`, never in git.
2. **Grid.** Resample to a 400 m grid in EPSG:32610 covering the box (about 310 x 305 cells). Sea = 0. Use `rasterio` (already installed) with average resampling.
3. **Mesh.** New command `python -m microclimate meshes-wide` writing `site/v3/data/terrain_wide.bin`: header (magic `TWD1`, nx, ny, x0, y0, cell, z scale), then uint16 heights in 0.5 m steps. No triangles in the file: the viewer builds a grid mesh. Target size under 200 KB raw (95k cells x 2 bytes = 190 KB) and it compresses well.
4. **Hole for the city.** The file includes the city area too; the viewer lowers or hides wide cells that the detailed terrain covers (part B step 2).
5. **Check picture:** a shaded top-down PNG with the city box outlined, for George.
6. Tests: header round trip; height at Mount Tamalpais (37.9235, -122.5965) between 650 and 790 m at 400 m averaging; ocean cells 0; Mount Diablo is outside the box (east edge -121.9 is close: report what the east edge shows).

## Part B: viewer

1. **Mock first.** One static picture (can be composed in the running beta with temporary code on a branch that is not merged) of the pulled-back view with a fog bank offshore, phone and desktop. George confirms before the rest is built.
2. **Wide terrain mesh.** Built on demand the first time the camera is higher than about 6 km or the far plane needs it. One `PlaneGeometry`-style grid (95k vertices, about 190k triangles, one draw call), same material family as the city terrain but flatter shading. Vertices inside the city terrain's footprint (shrunk by one wide cell) are dropped 30 m so the detailed terrain covers them without z-fighting. Camera limits (`controls.maxDistance`, far plane) extend only in beta. Check depth precision: with a far plane at 150 km and the near plane unchanged, look for z-fighting on buildings; raise the near plane with camera height if needed.
3. **Macro cloud layer** (in `fog2.js`, lab parameter `macro: 0 | 1`, default 0 until approved):
   - Load `macro/index.json` and the two frames around the shown time (on demand, a few KB each). Decode R = probability, G = confidence, B = top / 8 m. Check that the PNG decode leaves the values exact (same caution as guide 04).
   - Draw as an analytic slab, not a voxel march: for each view ray, intersect the layer between `base` and `top` (top from B, or the fused fog top where B is 0; base = top - 250 m, never below 0); sample the cover at 6-8 points along the in-slab segment (a 2D texture fetch each); optical depth = `sum(cover) * segment * k`. Add the existing noise only within about 15 km of the camera.
   - Light it with the same sun and ambient terms as the voxel fog so the two match in brightness.
   - Low-confidence pixels (G small): draw with reduced opacity; do not invent cloud.
   - **Seam:** inside the city grid the voxel fog is the truth. Fade the macro layer out over the outer 2 km of the city grid (weight 1 outside, 0 inside), and fade the voxel fog's own mirrored edge copies (the "blocky shapes past the grid edge" noted in the grid-artifact diagnosis) to 0 beyond the edge when `macro` is on.
   - Time: blend the two frames linearly. Outside the 6 h macro window: no macro layer (hourly forecast macro frames are a later step).
4. **Cost control.** The macro pass runs at the fog buffer's resolution in the same pass or one extra low-resolution pass; skip it entirely when no macro pixel is on screen (camera low and looking down).

## Verify
- Screenshots: pulled-back view, the coast-to-city transition from 5 km up, and street level looking west (the bank on the horizon), phone and desktop, `macro` 0 and 1.
- No seam visible at the city grid edge at three zoom levels; report any you see.
- Frame time at the default camera with `macro` 1 against 0 on a real GPU: target under +1 ms desktop, +2 ms phone.
- Public page unchanged; first-paint download unchanged (wide terrain and macro frames load on demand).
- JS tests for the pure parts: header parse, slab-ray intersection, seam weight.

## Gate
George approves the look from the screenshots, then `macro` becomes a beta default. By eye against a GOES visible loop of the same hour on three days: is the bank where the satellite shows it? Report mismatches.

## Pitfalls
- The satellite shows cloud top, so a high stratus deck looks like a fog bank. The macro layer is honest about the top (B channel) but its base is a guess (top - 250 m). Say so in the label or the about page.
- Scene axes: x = east, z = -north; scene height = metres x `EX`. The wide mesh must use the same origin as the city terrain (read how `app.js` places `terrain.glb`).
- At night the macro frames come from the infrared products at 2 km: expect blockier edges. Smooth with bilinear sampling, do not sharpen.
