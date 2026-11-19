# Guide 05: cloud floor and urban heat in the 3D fog (switches `fog_base`, `urban_lift`)

Read `00_README.md` first. Needs guide 03. Action side only; the viewer of guide 04 shows it with no further change.

## Goal
The 3D fog files get a physical floor: fog only where the air is saturated (above the cloud base), and a higher floor over warm, built-up ground unless the fog is thick and well supplied. Street wetness is NOT changed here (it already uses the cloud base when `cloud_base` is on).

## Background (from the code)
- Guide 08 is built: `forcing.py` computes per street point `base = zm + LCL_M_PER_K * max(T - Td, 0) + cb_off[i]` (m above sea level; `zm` = model terrain height at the point, `cb_off` = offset fitted to airport ceilings, `base_offset_hourly`). `fogtop.cloud_base_ramp(base, z)` gives the in-cloud weight: 1 at or above the base, falling to 0 over `BASE_RAMP_M` (60 m) below it.
- Guide 07 (pavement) keeps a road surface temperature `ts` per street point when `pavement` is on.
- Why heat matters: near 13 C, warming air by 1 K lets it hold about 0.6 g/kg more vapour, and fog holds only 0.1-0.3 g/kg of droplets. In cloud-base terms: 1 K of extra warmth lifts the base by `LCL_M_PER_K` = 125 m.

## Check first
- Where `T`, `Td` on the 6 x 5 lattice and `cb_off` live at the time `outputs.write` runs, and whether the cloud-base pieces exist when the `cloud_base` physics switch is off (public may run without it). `fog_base` requires `cloud_base` in the physics set; if it is missing, do not apply and say so in `status.json`.
- Where `ts` (road temperature) and the air temperature per street point are available per output frame (`diag`, or `tsurf` outputs).

## Steps

1. **Switches** as in `00_README.md`: `C.FOG_BASE`, `C.URBAN_LIFT` (both False), `BETA["fog_base"] = True` first; `BETA["urban_lift"]` only after the gate of part A. `urban_lift` without `fog_base` raises a clear error in `build.settings()` (follow `check_physics`).
2. **Part A: base on the tile grid.** New `fog3d.base_field(...) -> float32 [203, 214]` per frame:
   - Precompute once a bilinear index and weight table from the lattice to the 43,442 tile centres (reuse the helper that builds the `[N, 4]` table for street points; tile centre coordinates from `outputs.tile_lonlat()` or the formula in `00_README.md`).
   - `T`, `Td`, `zm` interpolated to the tiles; `base = zm + LCL_M_PER_K * max(T - Td, 0) + cb_off[i]`.
   - Use the same corrected `T`, `Td` the street model sees at lattice level (after the observation nudging), not the raw forecast.
3. **Apply.** `weight3d[k] = fogtop.cloud_base_ramp(base, z_m[k])` for each level (broadcast to `[24, 203, 214]`), passed to `fog3d.volume` (the hook from guide 03). Also zero any level with `fluid == 0`.
4. **Thickness guard.** If `top - base < 30 m` in a column the layer would vanish because of small errors. Set `base = min(base, top - 30)` where the column has `amp > 0.5` (strong fog signal from source and satellite). First guess; constant `FOG_MIN_THICK_M = 30`.
5. **Part B: urban lift.** `base += LCL_M_PER_K * dT_u`, with
   `dT_u = DT_URBAN_K * (urban / 255) * heat * vent * (1 - SUPPLY_DAMP * amp)`
   - `DT_URBAN_K = 1.0` (first guess: extra near-ground warmth of fully built-up ground with hot pavement and calm air).
   - `heat` = per frame scalar or coarse field in 0..1: `clip(mean over street points of (ts - Ta) / 10 K, 0, 1)` when `pavement` is on. Compute it per lattice box and interpolate with the same table, so sunny districts differ from shaded or fog-covered ones. Without `pavement`: `heat = clip(S / 600, 0, 1)` from the frame's mean street sunlight if `sun_clear` is on, else 0 (and note it in `status.json`).
   - `vent = 1 / (1 + U / 3)` with `U` the frame's wind speed (m/s): wind mixes the warm layer away.
   - `SUPPLY_DAMP = 0.7`: thick, well-fed fog (`amp` near 1) cools the surface and overwhelms the heat, so the lift shrinks. When the city is fully covered, `heat` itself also drops because the pavement gets no sun. Together this gives "thinner near the ground over streets unless the city is fully covered".
   - Cap `dT_u` at 1.5 K.
6. **Outputs for inspection.** Add to `index.json` under `fog3d`: `"base_m": [per frame median base over land]`, `"lift_m": [per frame mean lift over cells with urban > 128]`. Write one debug PNG per run only in beta: `fog_base_dbg.png`, the base field of the newest observed frame, 4 m per grey level.

## Tests (`tests_mc/test_fog_base.py`)
- `base_field` on a synthetic lattice with uniform `T - Td = 2` and `zm = 0`: base = 250 + offset everywhere.
- Ramp applied: a column with base 200 has weight 0 at 112.5 m (more than 60 m below), about 0.5 near 170 m, 1 at 212.5 m and above.
- Monotone: more `T - Td` never lowers the base; more `urban`, more `heat`, less wind, less `amp` never lower it.
- `dT_u` is 0 where `urban == 0`, 0 at night with a cooled road (`heat == 0`), and never above the cap.
- Switches off: 3D files byte-identical to guide 03's output on the fixture. `fog_base` on without `cloud_base`: not applied, build succeeds.
- Thickness guard only acts where `amp > 0.5`.

## Gate
- **Part A:** leave-one-airport-out ceiling check. For hours where KSFO or KOAK report a ceiling at or below 3000 ft, compare the base of the nearest tile cell (they lie outside the 80 m grid: use the lattice-interpolated base at the airport's coordinates with the same code path) with the reported ceiling, with that airport left out of `cb_off`. Report mean and median absolute error over at least 5 nights from the archive or saved inputs. Target: under 120 m. This mostly re-tests guide 08, but through the new tile path.
- **Part B:** there is no direct truth. Report: (1) the range of lift produced on a sunny afternoon, an overcast afternoon and a night (expected: tens of metres, about zero, zero); (2) satellite score unchanged (the 2D sheet is untouched by this guide, so this is a sanity check only); (3) three screenshots for George from guide 04's viewer: afternoon fog edge over the Mission and SoMa with the switch off and on. George decides whether `urban_lift` stays on in beta.
- State clearly in the report that the urban constants are first guesses with no measurement behind them.

## Pitfalls
- All heights are metres above sea level. `zm` is the model's terrain, not `ground_m` of the asset: the LCL formula was fitted with `zm`. Do not swap them.
- `cb_off` is indexed by model step, frames by hour: use the frame's step index (`idx = f * spf`).
- Do not feed the lifted base back into street wetness or drizzle.
