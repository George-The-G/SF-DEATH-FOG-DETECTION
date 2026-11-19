# Fog pop-in on beta (Tuesday 03:42 vs 04:06): diagnosis and scope for guide 14

Read from code on `v3-dev` (22b8389) and the two screenshots. I did not render the live data, so the cause of the straight wall is ranked, not proven.

## What the screenshots show
- 03:42: a straight-edged wall of fog east of the city, clear sea and clear city.
- 04:06 (24 min later): fog over the sea and the city, and the old wall still visible as a brighter rectangle with a straight west edge in the same place. The sea fog did not travel in from anywhere; it is simply there.

## Diagnosis
1. **Beta has no 5-minute fog at all (main cause).** `fog_travel` (guide 08 Part A) is not in `BETA` in `live/config.py`; its own gate has not run. So Tuesday forecast frames are the hourly `fog3d_<fff>.png` atlases only.
2. **The viewer cross-fades two hourly volumes; it does not move fog.** `fog2.js` bakes one volume per data hour and shows `mix(volA, volB, mixT)` (voxel shader around line 259). Fog that is in hour B but not in hour A therefore fades in where it is. The only motion is a small shift of each volume along the wind: `advect: 0.1` and `flowSpeed: 0.042`, so about 10 % of the wind, and 4 % for the flow-mapped detail. At NW 10 mph that is about 0.45 m/s of drift, against about 6 m/s real fog speed. Over an hour the real fog moves ~20 km, the display moves ~1.6 km.
3. **The hour-to-hour change itself has no continuity.** Each hourly frame is built independently: `amp = lagged source x GOES factor`, direction weights `w_d` of that hour's wind, band weights of that hour's fog top, then the lid. Direction weights and top step at the hour; the lagged front is a pattern, not a carried field. When the wind turns or the top moves, the whole pattern re-selects. `fogflow.motionAlongWind` (motion-compensated interpolation, guide 08 B) exists in `fogflow.js` but I found no caller in `fog2.js`, so it is not used.
4. **Past the data edge the volume wraps mirrored** (`MirroredRepeatWrapping`, shader ~line 254) with a 3 km noisy fade (`edgeFade`). That removes seams, but it means the sea and the east side show a reflected copy of the in-grid fog, not fog from upwind. An east wall can come from the grid edge or from the mirror line.
5. **The straight wall: measured on the live beta data (Mon 13:17 PT build).** Frame k is hour k after Sun 12:00 PT, so 03:42 Tue sits between k39 (03:00) and k40 (04:00), and 04:06 between k40 and k41. I decoded the column maxima of the atlases:
   - **k39 has no 3D file** (`fog3d.files[39] = null`), so the viewer treats it as an explicit empty hour. At 03:42 the picture is 30 % empty + 70 % k40.
   - **k40 covers 22 % of the tile**: fog on the west/north-west side only, ending in a **dead-straight diagonal front** (a clean line from the top edge to the south-west corner, nothing beyond it). That is the wall in the first screenshot.
   - **k41 covers 100 % of the tile**, including a **rectangular saturated block with straight edges** (about 40 x 55 cells, west part of the tile) and a straight diagonal band at the east edge. That is the bright rectangle in the second screenshot, which sits there at 04:06 (90 % k40 + 10 % k41 blend, so it is the start of k41 showing through).
   - So the front is a straight line because `lag_uniform` (BETA physics) gives the fog an arrival time that is linear across the tile, perpendicular to the wind. At the beta lag speed (wind x `LAG_SPEED_FACTOR 1.3`, about 5.8 m/s = 21 km/h for NW 10 mph) the front crosses the 17 km tile in **about 50 minutes**, i.e. less than the 60-minute step. k40 catches it a third of the way in and k41 catches it already past the whole city, so the viewer has no frame in between and just cross-fades. The wall is real model output at one instant, not a rendering artifact; the pop-in is temporal aliasing of a front that moves faster than the frame spacing.
   - Not yet checked: the fog-lab toggles (`data3d`, `skip`, lid). They need the live viewer with a GPU and would only add confirmation of render artifacts; the data above already shows the wall in the files. The straight rectangle in k41 (a hard-edged block of top density, not a lag line) is a second, separate artifact in the data that I have not traced: candidates are the fog-top lid / fused top stepping across a terrain mask, or the lag clamp (`LAG_MAX_H 3.0`). It needs the per-hour intermediate arrays (amp, top, lid) from a local build.

## Is fog moving in along the NW wind represented?
Only weakly. In the data, the direction weights select the tracer for the wind direction, and the lag shifts arrival by position. In the picture there is no carried field and no inflow from the NW edge; fog entering from the sea side is a fade-in.

## Proposal: guide 14, fog advection and temporal continuity
Do after 1-13. Order matters because 8 and 9 supply most of it.

- **Step 0 (small, partly done).** The wall is the straight lag front (above). Still open: the hard-edged block in k41; run a local build for Tue 03:00-05:00 and dump amp, top and lid per hour to find it. Also add a lag-front curvature: the front should follow terrain and the wind field, not a plane.
- **F. Sub-hourly frames for fast fronts (key fix).** Whenever the front crosses the tile in less than a step, the hourly frames cannot represent it. Either guide 08 Part A (5-minute `A(x,t)`), or at minimum compute the 3D file at 15-minute steps for hours where the lag front is inside the tile, so the front is seen at three or four positions per hour.
- **G. No empty hours inside a fog event.** `fog3d.files[k] = null` between fog hours (k39 here) makes the viewer fade from nothing; build frames from the neighbours' front position instead.
- **A. Switch guide 08 Part A on in beta** once its leave-one-scan-out gate passes. Forecast hours are predict-and-relax only; they need the NW inflow from the macro field (guide 07/09) and not the `amp` at the nearest edge cell. Without that, fog entering from the sea is a fade, not a front.
- **B. Hourly path, until A covers it:** use `motionAlongWind` in the shader for the A/B blend (sample A at -a*shift, B at +(1-a)*shift) with a confidence fade, so fog slides instead of cross-fading.
- **C. Smooth the inputs that step at the hour:** interpolate direction weights and fog top to the frame time, not per hour.
- **D. True speed:** the `advect`/`flowSpeed` change in guide 08 B4 (decided 2026-11-14), with a cap on-screen drift during fast playback.
- **E. Edge:** replace the mirrored wrap with inflow from the macro box for the upwind (W/NW) edge; keep a fade only on the downwind side.
- **Tests / gates:** a synthetic moving blob across an hour boundary must keep its centre of mass within 1 cell of the true path; mean luminance change per frame must not spike at hour or 5-minute boundaries; a fixed-camera clip of Tuesday 03:00 to 05:00 for George's review.
- **Size:** step 0 S, B + C M, A is already guide 08, E M (depends on 09). Single-agent rule for `fog2.js` applies.

Risks: faster true-speed drift ghosts under the 1-in-16 reprojection (survey idea 3); mirrored-wrap removal changes the look of the sea fog and needs George's before/after OK.
