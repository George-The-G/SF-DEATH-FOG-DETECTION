# Cloud replay engine: feasibility review and staged plan

Repo: George-The-G/SF-DEATH-FOG-DETECTION, branch v3-dev. Date: 2026-11-14. Planning only, no code changed.

**What this is based on.** The ChatGPT plan text George pasted (section 10 compares it), the earlier project notes (fog survey, render perf plan, MADIS review), `collect.yml` on v3-dev, and one real probe of the GOES-18 bucket on AWS. I did not read the viewer code or run anything on a phone. Everything labelled "estimate" is reasoning, not measurement.

## 1. Verdict

Feasible, and cheaper than it sounds, if we keep three honest limits in mind:

1. **Satellites see 2D cloud tops, not 3D fog.** GOES gives where low cloud is and roughly how cold its top is. It does not give street-level density, base height, or anything at 40 m. The "sparse voxels" are therefore built from a 2D coverage field plus a few scalars (base, top) that the terrain turns into 3D. That is the strength of the idea, not a weakness (section 3).
2. **True sparse voxel octrees are not worth it in WebGL2.** Fog here is a thin layer hugging terrain. A 2D column representation plus a max-height mip pyramid gets the same empty-space skipping with far simpler shaders (section 4).
3. **"Simulating until the next image" means two different things.** For past hours (replay) both endpoints are known, so this is motion-compensated interpolation and is quite reliable. For the newest gap (nowcast) it is extrapolation, it can be wrong, and the next run should overwrite it. Label the two differently on screen.

**Go/no-go gate before building anything (Stage 0):** another thread measured near-zero fog movement between hourly frames, but that is the model's fog. Real fog fronts over SF commonly move km per hour (estimate, 5 m/s is 18 km/h). If sub-hourly satellite frames show motion that the hourly bake plus crossfade misses, the engine earns its keep. If they match, stop at Stage 1.

## 2. GitHub Actions and Pages limits

Existing setup: `collect.yml` fetches, scores, builds `site/`, uploads it as a Pages artifact and deploys. State lives on a force-pushed `data` branch, so repo history does not grow. The copy of `collect.yml` I read on v3-dev has cron `*/10`, not the :07/:37 you described, so check which file actually runs. Either way the design below assumes about 30 minutes.

| Constraint | Reality | Impact |
|---|---|---|
| Scheduled runs | Run from the **default branch only**, can start late (often minutes, sometimes much more), and public repos' schedules are auto-disabled after 60 days without repo activity (from memory, verify) | Stamp frames with data time, never run time. Make the viewer tolerate a stale manifest. Confirm the cron lives on the default branch. |
| Runner | ubuntu-latest, 2 vCPU class, ~7 GB RAM, job timeout set to 10 min in the workflow (platform max 6 h) | Plenty for 2D numpy/scipy work (estimates below). Not enough for any real 3D weather model. |
| Compute budget (estimate) | Fetch crops 10-40 s, motion estimate under 5 s, advect 72 frames of 2D fields under 30 s, distance/shadow hints for hourly keyframes 10 s to 2 min | Target under 5 min total with pip cache. Keep a fallback: if the satellite fetch fails, republish the last good replay. |
| Pages size | Site max 1 GB, soft 100 GB/month bandwidth (from memory, verify) | Budget the replay payload at **20 MB or less**, each frame 300 KB or less. |
| Pages caching | Cache-Control is fixed (about 10 min) and we cannot set headers | Content-hash frame filenames. Only `manifest.json` changes name-stably, and 10 min staleness is fine. |
| Artifact churn | Each deploy re-uploads the whole site | 20 MB x 48 runs/day is about 1 GB/day of uploads (estimate). Fine for a public repo as I understand it, verify storage rules. |

## 3. Data model: columns, not volumes

Today the bake is a 428 x 406 x 24 voxel volume, about 4.17 M voxels, 8.3 MB as RG8 per hour (from the fog survey). A fog layer is determined by:

- **coverage/opacity** per ground column (2D, from satellite and obs),
- **base height** and **top height** per column (2D, smooth, from ceiling obs, soundings, model PBL, satellite top),
- **terrain**, already in the repo.

Fog over hills is basically "water level in terrain": the visible fog is where terrain lies between base and top, with the 3D detail added by noise at render time. So the per-frame payload becomes a few 2D textures at the 40 m grid (estimate, 428 x 406 = 174k px):

| Texture | Content | Raw size |
|---|---|---|
| A (RGBA8) | coverage, base, top, optical depth (8-bit each, or 16-bit as two channels) | 0.7 MB |
| B (RGBA8) | ground sun transmittance, ambient visibility, wind u/v at fog level | 0.7 MB |

Estimate: 100-300 KB each after compression, so a 6 h window at 5-10 min spacing (36-72 frames) is roughly 10-20 MB. That is far below keeping 3D volumes, and removes the 3D texture fetch per ray step. The satellite resolution matters: GOES is 2 km in IR, 0.5 km visible, so the whole city is only about 8 x 8 IR pixels. The satellite domain must be a regional crop (say 100 km square including offshore upwind) and downscaled to 40 m using terrain and the base/top scalars, not by pretending the satellite resolves it. That also explains why the upwind ocean matters: you see fog before it arrives.

**Real probe, 2026-11-14, GOES-18 on AWS (reachable from here):** CONUS imagery is a scan every 5 minutes (12/hour). Per scan file: band 13 (IR) about 3.8 MB, band 7 (3.9 um) about 4.1 MB, band 2 (0.5 km visible) about 60-65 MB. We only need a crop, so use HTTP range reads of the chunked netCDF (h5py with fsspec, estimate that this cuts it to a few MB) or stick to IR bands for the first version. All of those are free and public domain.

Satellite caveats: visible is useless at night (use band 7 minus 13, the night fog signal), high cirrus can hide fog, low-cloud top height from the standard product is poor, and a 500 m top is displaced about 0.5 km by viewing angle (estimate), which terrain/top correction can fix.

## 4. Renderer: hybrid without true SVO

Recommended stack (aligns with ideas 1, 2, 3 and 5 in the fog survey):

1. **Slab clip + 2D max-top mip pyramid** from texture A. Clip each ray to [ground, local top], leap empty tiles, spend the step budget only inside the occupied interval. This is the "sparse" part.
2. **Analytic profile** inside the slab (density from base/top/optical depth) with the 3D noise detail added only near the camera, flow-mapped by the wind in texture B.
3. **Analytic far field** from the 2D optical depth for distant pixels.
4. **Temporal reconstruction** (1-in-16 reprojection already exists), and **render on demand** when nothing moves.
5. **Time interpolation:** blend two adjacent precomputed frames per ray (2 texture fetches per ray, not per step). At 5 min spacing, a plain linear blend is close to warping, so no flow lookup is needed at runtime.

Why not SVO/sparse voxel bricks: WebGL2 has no compute, SSBOs or buffer textures, so an octree has to be packed into textures with branchy, divergent traversal, which is slow on mobile GPUs. A thin terrain-hugging layer gains little over columns. If WebGPU is later guaranteed on iOS, revisit; do not depend on it (verify current support).

Phone memory (estimate): keep a sliding window of 4-6 frames resident (3-4 MB) rather than all 72. Do not use PNG for 16-bit data: browsers decode to 8-bit and may premultiply alpha. Ship raw `.bin.gz` and decompress with `DecompressionStream`, or pack 16 bits across two 8-bit channels and decode with `createImageBitmap(..., {premultiplyAlpha:'none'})`.

## 5. Replay and nowcast logic (offline, in the Action)

1. Ingest the last N satellite scans (crop) and normalize to a low-cloud/fog score per pixel.
2. Estimate motion between consecutive scans (cross-correlation or optical flow), regularised by the existing terrain-aware wind field and model wind at about 925-950 hPa.
3. **Replay segment (both ends known):** advect scan A forward and scan B backward along the flow and blend, producing 5-min frames. Evaluate by leave-one-out: drop a real scan, interpolate it from its neighbours, compare to the held-out scan, versus plain crossfade as the baseline. That is the honest skill metric.
4. **Nowcast segment (newest gap):** advect the last scan forward with decaying confidence, clearly flagged. The next run replaces it with a replay segment.
5. Convert the 2D score to columns with base/top scalars, then to textures A and B. Derive the occupancy pyramid at load (it is cheap), and bake sun transmittance for hourly keyframes only and interpolate, to save Action minutes.
6. Publish `manifest.json` plus hashed frame files; the viewer picks frames by timestamp.

Note: this reuses and extends the thread's earlier idea to export per-spot fog arrival delay and a sub-hourly series. Satellite fronts give a way to calibrate that delay against observation.

## 6. Other live data that could help

| Source | Gives | Value | Caveat |
|---|---|---|---|
| GOES-18 ABI CMI bands 7/13/2, plus L2 cloud mask and cloud-top height | Coverage and motion, 5 min | Core | 2 km IR, coarse top height, night needs 7 minus 13 |
| HRRR sub-hourly, or RTMA-RU | 15-min wind, PBL height, low cloud, visibility fields on AWS (byte-range subsetting) | High, for the flow and base/top | About 1 h latency (estimate); model fog timing is often off |
| METAR/SPECI and ASOS 5-min (KSFO, KOAK, KHAF, KSQL) | Ceiling (base height), visibility | High for base height | Airports only |
| NDBC buoys (46026, 46237, 46013, IDs from memory) and CO-OPS 9414290 | Upwind wind, SST, pressure gradient | Medium; early warning of onshore push | A few points |
| Oakland sounding | Inversion height | High for top height | 2 per day; HRRR profile between |
| VIIRS night-fog RGB (polar orbiters) | 375-750 m fog extent, 1-2 passes/day | Medium, calibrates the terrain-resolved mask | Rare passes, extra processing |
| MADIS Integrated Mesonet (CWOP, RAWS etc.) | Block-scale temp, dew point, wind | Medium for ground truth and wind | Account approval and licence rules in the earlier review |
| Public webcams (for example ALERTCalifornia) | Ground truth: are landmarks hidden | Validation only | Terms of use unverified, do not hotlink or redistribute |
| PurpleAir | Neighbourhood temp/RH | Low | Cost and calibration, already off |

## 7. Staged plan

- **Stage 0, 1-2 days: decide.** Pull a few days of 5-min GOES-18 crops (IR bands) for SF and the offshore upwind box. Compare their sub-hourly motion to the current hourly bake and crossfade. Pass criterion: held-out interpolation beats crossfade visibly. 
- **Stage 1, about 3-5 days: ingest + sub-hourly series.** Add an Action step that caches the last 6-24 h of crops on the `data` branch, exports the per-spot arrival delay and sub-hourly coverage series. Viewer change: none or minimal. Gate: Action stays under 5 min.
- **Stage 2, about 1 week: replay frames.** Motion estimate, bidirectional advection, base/top from ceiling, sounding and model, texture A packed and published with manifest; viewer interpolates between frames behind `?beta=1`.
- **Stage 3, about 1 week: render hints.** Max-top pyramid, sun transmittance, far-field optical depth, wind texture; slab-clipped march. Measure on a real phone (the earlier notes say headless timings are meaningless).
- **Stage 4: nowcast and honesty UI.** Extrapolation with a "forecast" badge, auto-overwrite on the next image, hindcast skill versus persistence logged each run.
- **Stage 5, optional:** HRRR sub-hourly, buoys, VIIRS, MADIS, webcam validation.

Durations are estimates for one person working with Claude.

## 8. Risks

- Satellite shows cloud, not fog at street level; a low stratus deck above hills can look the same as fog on the ground.
- Scheduled-run lateness plus 5-min scans means the "live" edge is realistically 30-60 min old (estimate).
- Flow estimates break down with cirrus, glint, clear-sky gaps and fast-forming fog (no motion, just appears); keep a fall-back to the current hourly crossfade.
- Pages caching and 20 MB budget need monitoring so we do not quietly grow past them.
- Any sensor-derived data shown publicly must follow each source's licence (MADIS restricted providers, webcams).

## 9. Review of the ChatGPT plan

**Keep (good, and cheap to adopt):**
- Three independent layers: weather says what the clouds are, the replay engine says where and when, the renderer says how they look. Our pipeline-to-viewer split should be exactly this.
- **Immutable, versioned packages** and a small replay API (`setTime`, `setCamera`, quality profile). On Pages this maps neatly to content-hashed files plus a manifest.
- **Determinism**: same package and timestamp gives the same state. Free if the Action writes final frames and the shader noise is seeded.
- **Acceptance criteria beyond FPS** (no popping, motion follows wind, matches satellite). Our leave-one-out interpolation test is the concrete version of "satellite consistency".
- Do not bake final sun lighting into the volume; precompute optical depth, ground sun transmittance and shadow data, apply cheap runtime corrections. Separate ground shadow maps. Sliding working set, never load everything.
- Benchmark configurations rather than rewrite: its A/B/C (dense, sparse tiled, sparse tiled + procedural detail) is sensible. I would add **D: 2.5D columns + analytic profile + noise, with slab-clipped marching**, which I expect to beat C on phones (estimate, unmeasured).

**Doesn't fit us, or is wrong for our setup:**
- **Timing claims.** It assumes forecasts every 15 min and satellite hourly. I probed GOES-18: scans are every 5 min, so the satellite is the faster feed. Forecast models mostly update hourly (15-min output is a time resolution, not a new run, estimate), and the plan's 78 h horizon matches no single source we use (verify per model).
- **78 h of 3D keyframes won't fit.** 78 h at 15 min is 312 volumes; at our 8.3 MB each that is about 2.6 GB (estimate), over the Pages 1 GB cap and hopeless on phones. At 5-min spacing for 78 h it is three times that. Column textures at about 200 KB (section 3) make 312 frames about 60 MB, still above our 20 MB budget. So use **tiered time resolution**: 5-min frames only for the recent observed window, hourly frames beyond with runtime blending, and regenerate only the changed segment each run.
- **Replay vs forecast are conflated.** Past is observed plus interpolated (reliable), the newest gap is nowcast, the future is model forecast. Each time span needs a provenance flag on screen.
- **Cloud-type classification and cirrus/cumulus profiles** are overkill. SF's target is marine stratus and fog; one stratus profile shaped by terrain and the base/top scalars does the work.
- **The advection equation with formation/dissipation sources** cannot be parameterised from our data. Use observation-nudged advection (satellite is the correction every 5 min) and let the model forecast carry changes beyond the last image.
- **WebGPU-first, compute shaders, Vulkan/DX12/Metal.** We ship a static WebGL2 page; native targets are irrelevant. Treat WebGPU as an optional later backend, not a dependency.
- **No hosting or compute budget at all:** nothing on Actions time limits, Pages size or caching, scheduled-run lateness, night imagery, data licences, or that the satellite resolves the city in only a few pixels. The plan also ends by asking for the repo, so it is generic rather than tuned to SF terrain.

Net: adopt its architecture discipline and acceptance tests; replace its data model and storage plan with sections 3-5 above.

## 10. Questions for George

1. Which `collect.yml` actually schedules the fog build, and is its cron on the default branch?
2. Is the target "better-looking live window of the last few hours" or "short forecast too"? That decides whether Stage 4 matters.
