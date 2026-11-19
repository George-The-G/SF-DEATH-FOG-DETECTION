# Guide 08: fog that travels with the wind between 5-minute anchors (switch `fog_travel`)

Read `00_README.md` first, then `replay/goes-anchor-plan.md` sections 3.3-3.5 and 4 (this guide makes them concrete and extends them to 3D). Needs guides 03 and 07 in beta. Two parts: A in the Action, B in the viewer (`fog2.js`, one agent at a time). Build A, pass its gate, then B.

## Goal
Fog edges move, appear and clear between hours, carried by the local wind and corrected by each satellite scan. The viewer plays this smoothly and can show motion at the true wind speed.

## The idea in one paragraph
Today the horizontal fog signal per cell is `amp(x, t) = s(t - lag(x)) * fac(x)` (guide 03's name for it): a fixed pattern that fades in and out. This guide replaces `amp` for the recent past with a field `A(x, t)` on the same 214 x 203 grid at 5-minute steps: **predict** by carrying `A` along the wind for 5 minutes, then **correct** it toward the satellite. The 2D tile is then `A * trb` and the 3D file `A * C3 * weight3d`, exactly as before with `A` in place of `amp`. Where there is no satellite, `A` relaxes to `amp`, so the new path degrades to today's.

## Part A: the Action

1. **Switch** `C.FOG_TRAVEL = False`; needs `goes5` and `fog3d`; `BETA["fog_travel"] = True` only after the gate. New module `microclimate/live/travel.py`, numpy only.
2. **Time grid.** 5-minute steps from `now - TRAVEL_PAST_H` (6 h) to `now + TRAVEL_FWD_H` (2 h). Hourly frames outside that window stay as today.
3. **Prior at each step:** `amp5(t)`: the existing `amp` computed at 5-minute resolution (evaluate `lagged(s, i - lag)` at fractional step `i`; `s` is on a 10-minute step). No GOES factor in the prior (the satellite enters through the correction).
4. **Wind for the carry step, 2D on the tile grid, m/s.**
   - Preferred: the terrain-aware wind of `site/v3/fogflow.js::solveWind`, which is pure JS and runs in Node. Call it from Python through a small Node script (`site/v3/tools/`), once per hour of the window, with the hour's station-corrected wind speed and direction and the fused fog top; cache by `(speed, dir, top)` rounded to (0.5 m/s, 5 degrees, 25 m). Node is on the runner (check the version `collect.yml` gets; no `npm install`).
   - Fallback when Node or the script fails: uniform wind from the hour's speed and direction. Log which was used.
   - Interpolate the two hourly fields linearly to each 5-minute step.
   - Scale the 10 m wind to the fog layer with `C.TRAVEL_SPEED_FACTOR = 1.3` (the same factor guide 09 of the model guides uses for the front speed; first guess).
5. **Predict:** semi-Lagrangian, one step. For each cell centre, departure point `x - u * 300 s`, bilinear sample of `A` there. Outside the grid: take `amp5` at the nearest edge cell (the inflow value: this is how fog enters). Implement with index arithmetic and four gathers; no scipy needed. Then relax toward the prior: `A += (amp5 - A) * (300 / TAU_PRIOR_S)`, `TAU_PRIOR_S = 5400` (the model reasserts itself over about 1.5 h when nothing corrects it; first guess).
6. **Observation at 80 m:** from the crop store scan nearest the step (within 150 s), `p` and `c` from `goes.low_cloud` with parallax, sampled at the tile centres (bilinear), then sharpened with the tracer as a guide: `obs = clip(p * G, 0, 1)` where `G = clip(trb / max(local mean of trb over a 2 km box, 0.02), 0, 2)`. This puts the satellite's km-scale fog on the cells the tracer says fog can reach, and keeps the 2 km average equal to `p` to first order. The 2 km box mean is a separable 25-cell box filter (cumulative sums).
7. **Correct:** `A += K * (obs - A)`, `K = KMAX * c`, `KMAX = 0.5`. Clip to 0..1.
8. **Backward pass (observed window only):** run steps 5-7 backward in time from the newest scan (carry along `-u`), then average forward and backward results with weights proportional to closeness to their own last correction. Simple version to start: plain mean. This is for the replay; the newest step keeps the forward value.
9. **After the newest scan:** predict and relax only (`K = 0`), frames flagged `forecast`.
10. **Outputs** in `fog5/`:
    - `fog5/index.json`: `{"schema": 1, "step_min": 5, "frames": [{"t": iso, "kind": "observed" | "interpolated" | "forecast", "conf": mean c, "tile": file, "vol": file or null}]}`.
    - 2D tiles `round(255 * A * trb)` packed 12 per PNG sheet (one hour per sheet, the same tile layout as `fog{d}.png`).
    - 3D files as in guide 03 for each 5-minute frame of the last 3 h and next 1 h only (48 frames; measure the bytes). File names carry a short content hash (first 8 hex of sha1) so cached copies are never stale; `index.json` is the only name that stays fixed.
    - Budget: `fog5/` under 15 MB. If it does not fit GitHub Pages comfortably, it goes to R2 (guide 10). Report sizes before deciding.
11. **Hourly frames inside the window** (the existing `fog{d}.png`, `fog3d_*`) are left exactly as they are, so the public page and the fallback are untouched.

## Part A tests (`tests_mc/test_travel.py`)
- Predict: a blob in uniform 5 m/s westerly wind moves 1500 m east in one step (centre of mass within 40 m); total mass conserved within 2 % away from edges; with zero wind nothing moves.
- Inflow: with `amp5 = 1` on the west edge and westerly wind, fog enters at the west edge and crosses the 17 km grid in about 57 minutes at 5 m/s.
- Correct: with `c = 1`, two identical scans bring `A` to within 25 % of `obs` (`KMAX = 0.5`); with `c = 0` nothing changes.
- Guide sharpening: the 2 km box mean of `obs` equals the box mean of `p` within 15 % on a random smooth `trb`; `obs` is 0 where `trb` is 0.
- No scans at all: `A` converges to `amp5` (difference under 0.02 after 3 h).
- Determinism: same inputs give byte-identical outputs.
- Switch off: no `fog5/` folder; every existing output byte-identical.

## Part A gate (the honest skill test)
**Leave-one-scan-out:** for each scan in at least 3 fog days, withhold it, run the cycle, and compare `A` aggregated to 2 km at that time with the withheld `p` (mean absolute error where `c > 0.5`). Baselines on the same scans: (1) hold the previous scan, (2) a straight blend of the scans before and after, (3) today's hourly method. Report all four. Pass: the cycle beats (1) and (3), and is not worse than (2) by more than 10 %. Also report the flicker measure: mean frame-to-frame change of `A`, which must not spike every 5 minutes. If the cycle does not beat holding the previous scan, stop and report; do not tune `KMAX` to force a pass.

## Part B: the viewer

1. **Lab parameter** `travel: 0 | 1` (default 0 until George approves). With 1 and `fog5/index.json` present, times inside the 5-minute window use `fog5` frames; outside it, the hourly data as today.
2. **Time interpolation.** The volume cache generalises from "data hour k" to "frame id". Blend the two frames around the shown time as today. To avoid a speed change at every frame boundary, ease the blend weight with a smoothstep only when the two neighbouring intervals differ strongly; start with the plain linear blend and add easing only if a pulse is visible in a screen recording.
3. **Bake budget.** Playing at 1 hour per 10 s crosses a 5-minute frame every 0.8 s. Bake at most one volume per rendered frame (as `getVol`'s budget does) and, when playback is faster than one data frame per 0.5 s, use every third frame (15 minutes). Measure with the lab `bench()`.
4. **True-speed motion (decided by George 2026-11-14: realism wins; the old values were set by eye).** In beta, change the `DEFAULTS` to `advect: 1.0` and `flowSpeed: 1.0`, so fine detail drifts at the real wind in simulation time. Keep both as lab sliders. The public page keeps its current behaviour until promotion.
   - Check the survey's warning (`fog/cloud_tech_survey.md`, idea 3): at 1-in-16 reprojection, detail that moves more than about 1 pixel per frame ghosts, and fast time-lapse playback multiplies the speed. Lower the history weight where the flow is fast, and cap the on-screen drift per frame during fast playback. State the limits you measured.
   - This step does not depend on 5-minute data and can ship ahead of the rest of part B.
5. **Label.** A small text near the time readout: "observed" / "estimated" / "forecast" from the frame `kind`. This is new UI: send George a screenshot mock first.
6. **Data refresh while open:** when a newer `fog5/index.json` appears (poll every 5 minutes), swap frames only at a moment the affected frame is not on screen, or cross-fade over 20 s. Never jump.

## Part B verify
- Screen recordings (phone size and desktop) of 2 h of playback across the hourly-to-5-minute boundary: no jump at the boundary, no pulse every 5 minutes.
- `travel: 0` is pixel-identical to before. Replay nights and the public page unchanged.
- Frame time while playing, `travel` 0 against 1, on a real GPU.

## Pitfalls
- `lag` in `fog_frames` is in model steps (10 minutes), computed per 3-hour block; reuse it, do not recompute a different lag.
- Wind direction is where the wind comes from; the velocity is `u = -sin(dir) * speed` east, `v = -cos(dir) * speed` north, and tile rows run north to south (row index increases southward).
- `trb` changes with wind direction hour by hour; interpolate the direction weights to 5 minutes rather than switching at the hour.
- The satellite sees stratus above the hills as cloud; `A` is a column signal. The floor comes from guide 05, not from here.
