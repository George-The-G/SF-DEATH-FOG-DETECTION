# Guide 03: hourly 3D fog files in the live build (switch `fog3d`)

Read `00_README.md` first. Needs `live_tracer3d.npz` (guide 02). Runs in the Action; develop and test on the PC or in a container (no GPU).

## Goal
Each live run also writes one small 3D fog file per hourly frame, built from the same timing and satellite factor as today's 2D tile. The 2D tiles, street wetness and everything public stay byte-identical.

## Check first
- `live/outputs.py::fog_frames` builds, per frame, `tile = 255 * lagged(s, i - lag) * trb * fac`. Confirm this is still the code. If it changed, adapt step 2 and say so.
- How `east.apply` extends `S["dirs"]` and `S["tr80"]` with zero rows when `wind_east` is on: the direction list used for weights can have 15 entries while the asset has 9.
- Which files `live_ci/finalize.py` and `refresh.sh` copy into the published folder, and how `meta` sizes are logged. New files must be published and must not count toward first paint.

## Steps

1. **Switch.** `C.FOG3D = False`, `C.FOG3D_FILE = "live_tracer3d.npz"`, `C.BETA["fog3d"] = True`, handled in `build.settings()` and set for the duration of one build like `WIND_EAST`. `status.json` lists it (`fog3d: {applied, reason}`), following `east.apply`'s info pattern. If the asset is missing or its `schema`, shape or `dirs` do not match: `applied = False`, a reason string, and the build carries on without 3D files.
2. **Refactor `fog_frames` without changing its output.** Split the per-frame product into `amp = lagged(s, i - lag) * fac` (float `[203, 214]`, `fac = 1` when there is no GOES hour) and `tile = 255 * amp * trb`. Return the tiles as today and, when asked (`return_amp=True`), also the list of `amp` arrays and the per-frame direction weights `Wt`. Run the frozen-hash tests and a byte comparison of `fog0.png` before and after on saved inputs (`build --inputs`).
3. **New module `microclimate/live/fog3d.py`:**
   - `load(path) -> T | None` (reads the asset, checks the contract, keeps `tr3d` as uint8).
   - `band_weights(top_m, lo_m, edges) -> [11]` using the formula in `00_README.md`.
   - `dir_rows(dirs_used, Wt, T["dirs"]) -> [(row, weight)]`: map each direction with non-zero weight to its row in the asset by value (tolerance 0.01 degrees); directions not in the asset contribute nothing.
   - `volume(T, Wt_rows, a, amp) -> float32 [24, 203, 214]`: `C3 = sum_d w_d * sum_b a_b * tr3d[d, b] / 255`, then `vol = amp[None] * C3`. Only bands with `a_b > 0` and directions with weight > 0 are touched (at most 2 directions).
   - `encode(vol) -> uint8`: `round(255 * clip(vol, 0, 1))`.
   - `atlas(u8) -> PIL 'L' image`: 6 columns x 4 rows of 214 x 203 tiles; level k at column `k % 6`, row `k // 6`; inside each tile row 0 = north. Image size 1284 x 812.
4. **Top and base inputs.** `top = F["fog_top"]` at the frame's step (the same series `weather.json` gets). `lo = 0`. Hooks for guide 05: `volume` takes an optional `weight3d` `[24, 203, 214]` multiplied in at the end (default none).
5. **Write** in `outputs.write`, only when the switch applied: `fog3d_<fff>.png` (`fff` = frame index, 3 digits), saved with `optimize=True`. Add to `index.json` through the existing `extra_fields` path:
   ```json
   "fog3d": {"files": ["fog3d_000.png", ...], "nz": 24, "dz_m": 25, "z0_m": 0, "cols": 6, "rows": 4,
             "w": 214, "h": 203, "top_layer": [..per frame: highest level with any value > 0, -1 if none..],
             "ref": 16}
   ```
   Frames with nothing above 0 get `null` in `files` and no PNG.
6. **Budget.** Log total bytes of the 3D files. Target under 12 MB for the 72 frames. If over: write 3D only for frames from 6 h before `now` to 30 h after, `null` for the rest (the viewer falls back to 2D there), and report the numbers. Run time added must be under 15 s; memory under +400 MB.
7. **refresh.sh:** add `live_tracer3d.npz` to the optional static download loop and to its comment. No other change. `collect.yml` is not touched.
8. **Run archive:** do not add the 3D files to the archive.

## Tests (`tests_mc/test_fog3d_live.py`)
- `band_weights`: top 0 gives all zeros; top 800 all ones; top 170 gives `[1, 1, 1, 0.5, 0, ...]`; `lo = 100, top = 300` gives `[0, 0, 1, 1, 1, 1, 0, ...]`; monotone in top.
- `volume` with a synthetic asset: one direction, constant 255 in band 0 only, top 40 gives exactly `amp` in every level; two directions with weights 0.25 / 0.75 blend linearly.
- `dir_rows`: a 15-entry direction list with east weights yields only west rows; weights of missing directions are dropped, not renormalised.
- `atlas`: level 7 lands at pixel offset `(214 * 1, 203 * 1)`; row 0 of the tile is the north row.
- Switch off: `index.json` has no `fog3d` key and no `fog3d_*.png` exists; `fog0.png` bytes equal the pre-change bytes on the saved inputs fixture (reuse the fixture style of `tests_mc/test_beta.py`).
- Asset missing with the switch on: build succeeds, `status.json` says not applied.

## Gate
- Beta build on the runner: added time and memory within budget; file sizes reported.
- Sanity numbers in the report for one foggy saved input: mean of `max over levels` of the 3D file against the 2D tile (they should correlate, r > 0.8, since both use the same `amp`); share of columns where fog exists aloft but the 2D tile is under 4/255 (this is the new information; report it).

## Pitfalls
- `amp` already holds the GOES clear-sky factor. Do not apply it twice.
- Do not renormalise direction weights when a direction is missing; east wind must give zero fog as today.
- Keep everything float32; `tr3d` stays uint8 until the two or three needed slices are converted.
- PNG `optimize=True` on 72 files costs time: measure; drop `optimize` for the 3D files if it costs more than 5 s.
