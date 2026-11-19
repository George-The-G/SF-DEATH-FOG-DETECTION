# GOES-18 anchored fog replay: implementation plan

Repo George-The-G/SF-DEATH-FOG-DETECTION, branch v3-dev at e21fa98 (read 2026-11-14). Plan only, no code changed. "Estimate" means reasoned, not measured.

## 1. How the fog is made today (from the code)

The fog is **model-driven, with the satellite used only as an hourly correction**. It is not drawn from satellite images.

1. **Shape (offline, once):** `microclimate/fog.py` runs a steady advection-diffusion "fog tracer" on a stored mean wind over the terrain, for several wind directions. Result: how readily fog reaches each 80 m cell for a given wind direction (`tr80`).
2. **Timing (each run):** `live/forcing.py` builds a fog source series s(t) at the coast from the RRFS model's low cloud (via Open-Meteo). Observations nudge it: the GOES upwind-ocean low-cloud fraction and KHAF METAR, fading into the forecast over about 3 h.
3. **Tiles (each run):** `live/outputs.fog_frames` makes one 214 x 203 tile (80 m cells) **per hour**: 255 x s(t minus travel lag from the coast) x tracer for that hour's wind direction. For past hours it multiplies by a GOES clear-sky factor: where GOES sees no low cloud, opacity is capped at 30 % (`CLEAR_MAX_OPACITY`).
4. **GOES use (`live/goes.py`):** cloud mask ACM (2 km) and cloud-top height ACHA (10 km), **only the scan nearest each hour**. Low cloud = cloudy and top below 1000 m. `cloud_score.py` also scores the model against GOES each run.
5. **Fog top:** from Open-Meteo humidity/inversion profiles (`fogtop.py`), with a METAR ceiling floor.
6. **Viewer (`site/v3/fog2.js`):** turns each hourly tile plus the fog top into a 3D voxel volume on the GPU (density plus light from above), blends the two hours around the shown time, and drifts noise detail along a terrain-aware wind solved in the browser (`fogflow.js`).

So the satellite currently vetoes fog where it sees clear sky, once per hour, and never adds fog or moves an edge. That is why the hourly frames looked nearly static to the earlier thread.

**Probed today on AWS:** both GOES products we already use come every 5 minutes. ACM CONUS is about 3.8 MB per scan and ACHA about 0.34 MB, so 12 scans/hour is about 50 MB/hour of downloads (whole files), which is fine for the Action.

## 2. Your idea, and where I would adjust it

Yes: GOES scans as reality anchors every 5 minutes, local wind fields carrying the fog between them, and smooth blending instead of snapping. Three adjustments make it work in practice:

- **The satellite sets where the fog is at km scale; our tracer sets the 80 m detail.** One GOES pixel is 2 km, so SF is about 8 x 8 pixels. We should not stretch satellite pixels over the city. We upsample the satellite field using the tracer and terrain as a guide, so edges follow valleys and ridges.
- **Use a predict-then-correct loop, not a pure blend.** Each 5-minute step: advect the previous fog state with the wind (predict), then pull it toward the new scan by an amount set by how much we trust that scan (correct). The fog then cannot jump, and noisy scans (mask flicker, dawn/dusk switch-over) are damped automatically. Over the past few hours a backward pass uses later scans too, so the replay is the best fit to both sides.
- **At 5-minute spacing, motion inside a gap is small.** 5 m/s for 5 minutes is 1.5 km, less than one GOES pixel. Wind advection matters most for the 80 m detail and for carrying the field forward past the newest scan. The real gain over today is that edges move and fog can appear and clear between hours.

## 3. Pipeline changes (the Action)

**3.1 Ingest every scan** (`goes.py`). Storage layout and low-cloud rule shared with validation: see goes-crop-format.md.
- Replace "nearest scan to each hour" with "all scans since the last run" (about 3-6 per run).
- Store only the SF crop (about 70 x 60 pixels, a few KB per scan, estimate) in state, never the raw files. `GOES_CACHE_H = 40` hours of whole files would be about 2 GB.
- Keep a rolling 24-48 h of crops on the data branch.

**3.2 Turn each scan into a continuous observation, not a 0/1 flag**
- low-cloud probability p from the ACM class (clear 0, probably clear 0.25, probably cloudy 0.75, cloudy 1) and the top height (low below 1000 m).
- confidence c: 0 where high cloud hides the low layer, where the mask is missing, or at the terminator (sun near the horizon, when the day and night algorithms swap). Low but nonzero at night.
- **Parallax correction:** GOES-18 sees SF at about 46 degrees from vertical (computed from the satellite position, estimate), so a fog top at 400 m appears about 0.4 km too far northeast. Shift by top height x tan(zenith) toward the satellite (southwest).
- Later (Stage 4): add visible band 2 (0.5 km) by day and band 7 minus 13 by night for sharper, continuous fog scores. We probed those too: band 2 is about 60 MB per CONUS scan, so it needs range reads of only the SF chunks.

**3.3 Downscale to 80 m**
- prior(x, t) = today's model tile (tracer x lagged source). It already knows the terrain.
- obs80(x, t) = p upsampled with a joint bilateral filter guided by the prior and terrain height. Satellite edges then snap to physical edges, such as the fog stopping at the Twin Peaks ridge.
- Fog top from the existing series, raised or lowered by ACHA where it is valid.

**3.4 Predict-correct over 5-minute steps** (new `live/replay5.py`, numpy, 2D)
- Wind: the terrain-aware 2D wind already in `fogflow.solveWind` (pure JS that runs in Node, so it can be called from the Action, or ported to numpy). Use the station-corrected hourly wind, interpolated to 5 minutes.
- Predict: semi-Lagrangian advection of the state F by 5 minutes along that wind. At the inflow edges, feed from the satellite over the ocean, which is how fog arrives through the Gate.
- Correct: F = F + K (obs80 - F), with K = Kmax x c. Kmax is about 0.5 (to tune), so a single scan moves the fog halfway and two agreeing scans almost all the way, which damps one-scan glitches.
- Backward smoothing pass over the observed window (the same rule run in reverse, then average). This is for the replay only.
- After the newest scan: prediction only, with K = 0, relaxing toward the model forecast over about 1-3 h. This matches the existing 3 h e-fold for s(t). Flag these frames as "forecast".

**3.5 Output**
- 5-minute tiles for the observed window plus 1-2 h past the newest scan; hourly tiles after that, as today.
- Size estimate: a tile is 43 KB raw, about 10-20 KB as PNG. 24 h at 5 minutes = 288 tiles, about 3-6 MB. Pack 12 tiles (one hour) per sprite sheet to limit requests, with content-hashed names.
- `index.json` gets a frame list with `t`, `kind` (observed / interpolated / forecast) and `conf`.

**3.6 Run time** (estimate): download 6 x 4 MB = 10-30 s; crop, parallax and downscale under 5 s; 12 x 24 predict-correct steps on 43k cells under 5 s. Well within the 10-minute job.

## 4. Viewer changes (no snapping)

1. **Continuous time.** Sample the 2D field at fractional time with a smooth 4-frame curve (Catmull-Rom, clamped to 0..1), not a straight line between two frames. A straight-line blend changes speed abruptly every 5 minutes, which reads as a pulse. The 2D blend runs per frame on a small 214 x 203 texture, so it is cheap.
2. **3D bake on change.** `fog2.js` bakes one 3D volume per data hour today. Feed the bake from the blended 2D texture and rebake when the time moves by more than about 1 minute, or progressively a few layers per frame. Measure with the existing lab `bench()` before deciding. While playing fast, fall back to 15-minute keyframes.
3. **Detail motion** stays as is: noise advected along `fogflow` wind. Use the same 5-minute wind the pipeline used, so the drift agrees with how the edges move.
4. **New data while the page is open.** Every few minutes check the manifest. If a new deploy rewrote frames that are on screen, blend from the old field to the new over about 20-30 seconds of wall time. Never jump.
5. A small label: "observed 5 min ago" / "estimated" / "forecast".

## 5. How we will know it's better

- **Leave-one-out:** hide one real scan, predict it from its neighbours, and score it against the hidden scan. Compare with today's hourly method and with a plain crossfade. Extend `cloud_score.py` (its `fog_sheet_nosat` field is already the honest measure) to 5-minute scoring.
- **Flicker:** mean frame-to-frame change. It should be smooth, not stepped every 5 minutes.
- **Spot checks** against METAR visibility and ceiling at KSFO/KHAF and, where allowed, webcams.

## 6. Stages

| Stage | Work | Rough size (estimate) | Gate |
|---|---|---|---|
| 0 | Script: pull 1-2 foggy days of 5-minute ACM/ACHA crops and compare to our hourly tiles | 1 day | Does the satellite show sub-hourly edge motion we miss? |
| 1 | 5-minute ingest, crop store, continuous p and c, parallax | 2-3 days | Action time under +1 min |
| 2 | Downscale + predict-correct + 5-minute tiles + manifest, behind `?beta=1` | 4-6 days | Leave-one-out beats the hourly method |
| 3 | Viewer: smooth time curve, rebake on change, reconcile blend, labels | 3-5 days | No visible pulses or jumps on a real phone |
| 4 | Sharper inputs: visible band by day, 7-13 by night, 0.5-2 km | 3-5 days | Edge placement improves in scoring |
| 5 | Promote to public after you approve | n/a | Your call |

## 7. Risks

- **High cloud blinds the satellite.** We then run on wind and model only, and say so on screen.
- **ACM changes behaviour at dawn and dusk** (day and night algorithms). Lowering confidence there avoids a false clearing or filling. This needs tuning on real days.
- **The satellite sees the cloud top.** A stratus deck above the hills looks like fog to it. The fog top and terrain decide where it touches the ground, as today.
- **The live edge is 10-40 minutes old** (estimate: scan latency plus Action schedule). The last stretch is always a short forecast.
- **The scheduled job must run from the default branch;** check that before relying on cadence.
