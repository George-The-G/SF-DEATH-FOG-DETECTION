# Guide 02: the 3D tracer asset for the live build (PC CPU)

Read `00_README.md` first. Needs guide 01 finished. No GPU.

## Goal
Pack the 99 tracer fields into one file the GitHub Action can use: `live_tracer3d.npz`, on the 80 m tile grid with 24 height levels.

## File contract: `live_tracer3d.npz`

| Key | Type and shape | Meaning |
|---|---|---|
| `schema` | int | 1 |
| `dirs` | float64 `[9]` | 180 ... 360, ascending |
| `band_edges_m` | float32 `[12]` | the edges from `00_README.md` |
| `z_m` | float32 `[24]` | level centres 12.5 ... 587.5 |
| `tr3d` | uint8 `[9, 11, 24, 203, 214]` | `round(255 * c)`; axes: direction, band, level, row (0 = north), column (0 = west) |
| `fluid` | uint8 `[24, 203, 214]` | `round(255 * share of the cell that is air)`; 0 = fully inside terrain or buildings |
| `ground_m` | float32 `[203, 214]` | mean terrain height of the cell (from `grids/terrain_20m.npy`) |
| `urban` | uint8 `[203, 214]` | built-up share 0..255, see step 4 |
| `static_dirs_check` | float64 | the `dirs` of `live_static.npz` this was built beside, for a sanity check |

Raw size is about 103 MB. Save with `np.savez_compressed`. Report the compressed size.

## Steps

1. New module `microclimate/live/tracer3d.py` with `build(data_dir, out_path, log=print)` and a CLI `python -m microclimate.live tracer3d [--data DIR] [--out FILE] [--check]` (wire it like `stationwind`).
2. **Horizontal reduction, per direction and band.** Load `c` (float16 `[855, 811, 40]`) and `solid` (`grids/solid_20m.npz`, bool, same shape). Work one level at a time to keep memory low:
   - `air = ~solid[:, :, k]`, `num = c[:, :, k] * air`.
   - Turn both into tile orientation exactly as `replay.city_tracer_80m` does: `.T[::-1]`, edge-pad to `[812, 856]`, reshape to `[203, 4, 214, 4]`, **sum** (not mean) over the two block axes.
   - Keep `num80[k]` and `air80[k]` (air count 0..16) as float32 `[40, 203, 214]`.
   Edge padding repeats the last row and column; that is how the 2D tile is made today, keep it identical.
3. **Vertical resampling to 25 m levels.** Source centres are `(k + 0.5) * 20`. For each target centre `z`, linear weights between the two neighbouring source levels (clamp at the ends). Interpolate `num80` and `air80` separately, then `c = num / air` where `air >= 0.8` (5 % of 16), else 0. `fluid = round(255 * air / 16)`. This keeps solid cells from dragging values down.
4. **`urban` map.** `bldg_frac`: share of the 16 cells where `grids/bldg_mean_h_20m.npy > 2.0`. `road_frac`: for each street point in the city point table (read how `city.read_city_points` returns x, y), count points per 80 m cell, divide by the 95th percentile of the non-zero counts, clip to 1. `urban = round(255 * clip(0.7 * bldg_frac + 0.3 * road_frac, 0, 1))`. Water and parks come out near 0. First guess; say so in a comment.
5. **`--check`**, writing `reports/fog3d/asset_check.json`:
   - a. Shapes, dtypes and key list match the table.
   - b. Old-tile agreement: for each direction, `sum of bands 0-2` taken at the lowest level where `fluid >= 128`, compared with `tr80` of `live_static.npz` for the same direction (`city.load_static`): Pearson r >= 0.97 and mean absolute difference <= 0.04. (The old tile is at the first fluid 20 m cell, so small differences are expected. Report the numbers; if r < 0.9 the orientation is wrong: stop.)
   - c. Orientation test: the cell with the largest `ground_m` must be within one cell of the largest of the 80 m block mean of terrain computed independently, and the coast (west columns) must have `ground_m` near 0.
   - d. `fluid` is 0 below ground: for 100 random cells, levels with `z_m < ground_m - 25` have `fluid == 0`.
   - e. Print the compressed size and the load time.
6. **Save a picture for George:** `reports/fog3d/slices_270.png`: for direction 270 and a 300 m top (band weights from `00_README.md`), three horizontal slices (37.5, 137.5, 262.5 m) and one west-east vertical section through Twin Peaks (row of `y = 4177700`). Plain matplotlib is fine if installed; otherwise Pillow grey images. This is the first look at whether the tracer shows lee holes and lifted fog.

## Tests (`tests_mc/test_tracer3d.py`, no library needed)
- Synthetic `c` and `solid` (small arrays via a helper that takes shapes as parameters): block sums, the `num / air` rule, vertical weights (a field equal to `z` must come back as `z` at the target centres within 0.5 m where both neighbours are air).
- Orientation: a synthetic field with a single hot cell at the north-west corner ends up at tile `[0, 0]`.
- `urban` stays in 0..255.

## Publishing (only after George's OK)
The live job downloads static tables from release `live-static-v1` (`live_ci/refresh.sh`, the `for f in live_static.npz ...` loop). Uploading `live_tracer3d.npz` there is additive and optional, exactly like `live_east.npz`. Add the file name to that loop in guide 03, not here. Upload with `gh release upload live-static-v1 live_tracer3d.npz` from the deploy folder only when George says so.

## Done when
`asset_check.json` passes, tests green, the slices picture is sent. Report sizes and the step 5b numbers per direction.

## Pitfalls
- uint8 cuts off concentrations below 0.002; that is fine (the viewer treats 16/255 as full coverage).
- Do not normalise or stretch the values here. The Action does the weighting.
- 9 x 11 fields of 27.7 M float16 are 55 MB each on load: process one file at a time and free it.
