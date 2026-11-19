# Guide 14 v2: fog advection and temporal continuity (re-scoped 2026-11-17, PT)

Supersedes `guide-14-fog-advection-scope.md`. Checked against `v3-dev` 30f4938 and the live beta files (build of 2026-11-18 00:12 UTC).

## What changed since the first scope
On Mon 2026-11-16 13:50 PT, guides 08 A and B landed in beta: `fog_travel` is on, the `travel` lab switch defaults on, and `fog5/` is published with 5-minute tiles and volumes, `observed/estimated/forecast` labels, true-speed drift defaults, and the lid on fog5 volumes. Guide 09 (macro view) and 13 (sky layers, off) are also in. My first diagnosis was written from a build that predated this.

## Still valid, done, or open
| Item from first scope | Status |
|---|---|
| A. Guide 08 Part A on in beta | **Done**, but see "limits" below. The leave-one-scan-out gate is explicitly deferred; it is not a pass yet. |
| D. True-speed drift | **Done** (guide 08 B4). Ghosting limits not re-measured by me. |
| B. Use `motionAlongWind` for the hourly blend | **Still open.** Nothing in the viewer calls it (only defined in `fogflow.js`). |
| C. Interpolate direction weights and top between hours | **Still open**, but only matters outside the 5-minute window. |
| E. Replace mirrored edge with real upwind inflow | **Partly open.** The 3 km noisy fade replaced the 8 km mirror, but the volume still wraps mirrored past the edge; the macro view exists now and could supply the upwind side. |
| F. Sub-hourly frames when a front crosses the tile in under an hour | **Open, and is the main remaining cause** (below). |
| G. No empty hours inside a fog event | **Open.** Current beta has `null` hours between fog hours (Wed 19-22 null, then Wed 23 cover 1.00). |
| Step 0 hard-edged block in the 05:00 hour | **Unresolved**; that forecast has since been rebuilt and Tue no longer shows fog, so it needs a new case. |

## Why the pop-in can still happen
- `fog5` only reaches **about 2 h ahead** (`TRAVEL_FWD_H = 2`), and the 3D volumes only **3 h back and 1 h ahead** (`VOL_PAST_H/VOL_FWD_H`). It is Tue 16:38 PT; everything after about 18:15 Tue, i.e. all of tomorrow, is the old hourly path. When you looked at Tuesday 03:42 from Monday afternoon, you were 14 h ahead.
- On the hourly path the whole tile still flips in one step. Coverage of the hourly 3D column maxima in the current beta: Tue 16:00 empty to Tue 17:00 **100 %**; Wed 03:00 empty to Wed 04:00 **100 %**; Wed 17:00 86 % to Wed 18:00 **0 %**; Wed 22:00 empty to Wed 23:00 **100 %**; Thu 06:00 96 % to Thu 07:00 **0 %**. Each of these is a cross-fade over an hour of fog that, if real, would arrive or leave in about 50 minutes (5.8 m/s over 17 km).
- Inside the window `fog5` forecast frames are carry-plus-relax: uniform wind (not terrain-aware), inflow = prior at the nearest edge cell, no new satellite information. A front that is not in the tile at the last scan is not predicted to enter; it arrives through the prior relaxing, over about 1.5 h (`TAU_PRIOR_S`), which looks like a fade.
- Observed 5-minute frames still show conf near 0 for hours at a time (Tue 13:15 to 14:55 PT, `conf` 0.0 to 0.13), so the cycle falls back to the prior there as well.

## Plan (do after the remaining guides)
1. **Gate first (small).** Run the deferred leave-one-scan-out on the accumulated beta scans (3+ fog days), plus the flicker measure. It decides whether the 5-minute path can stand as the base for 2 and 3. If it fails, items 2 and 3 get bigger.
2. **Extend the 5-minute path to the full forecast.** Raise `TRAVEL_FWD_H` toward 12 to 24 h for tiles only (2D tiles are cheap: one sheet per hour); keep 3D volumes at hourly plus 15-minute steps when a front is in the tile. Budget `fog5/` size (15 MB cap in guide 08). This is the actual fix for what you saw.
3. **Front-aware sub-hourly steps.** In the forecast, compute the lag front position from the hour's wind and speed and add intermediate 15-minute frames whenever the front travels more than a quarter of the tile per hour.
4. **Hourly fallback blend:** wire `motionAlongWind` into the voxel blend (confidence-faded), so any hour-to-hour blend that remains slides instead of fading.
5. **Interpolate direction weights and fog top** to the frame time for the hourly path, and **no null hours** between fog hours (take the front from the neighbours).
6. **Upwind edge from the macro field** instead of mirrored wrap, W and NW side only; keep the fade on the downwind side. Needs your before/after OK because it changes the sea look.
7. **Tests and gates:** synthetic front crossing the tile in 40 min must be placed within 1 km at any shown time; no frame-to-frame mean luminance change over 2x the median at hour or 5-minute boundaries over a recorded Tue 03:00 to 05:00-type clip; labels show `forecast` past the last scan.

## Open questions for George
- Is a 12 to 24 h tile-only forecast with a 15-minute front step acceptable for file size, or do you want fewer hours?
- Should step 1's gate be a hard block (do not extend the forecast if it fails) or just reported?

## Not verified
I did not render the viewer, so I have not re-checked the wall in the screenshots against the current look, and I did not read fog5 tiles. The coverage numbers are decoded from the hourly 3D files only. The hard-edged block in the old 05:00 hour is untraced.
