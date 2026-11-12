# Plan: live, whole-city death fog (implementation plan, revised 2026-11-12)

Status: plan only, nothing started. Written by Opus for Sonnet to implement step by step.
Scope now: the model, the data and the 3-D render data. UI changes are only documented (section 9), not built.
Decisions from the user: live window = tonight plus the next 2 days; cover the whole city.

## 0. Ground rules for the implementer
- Repo: George-The-G/SF-DEATH-FOG-DETECTION. Work on branch v3-dev (worktree C:\Users\George\AppData\Local\Temp\wt_v3dev). Never force-push. Commit trailers as instructed by the session.
- Windows PC via desktop-commander (PowerShell). Write files LF, UTF-8 without BOM. site/ is gitignored: use `git add -f`.
- One GPU job at a time; never blanket-kill python. The live pipeline itself must NOT need the GPU or the home PC.
- main (v2 page) changes only through a PR the user approves (needed once, step 6.4).
- Track any installed package in INSTALLED_PACKAGES.md.
- Each step below has a "Done when" check. Do not start the next step until it passes. Stop and report if a step needs more than ~6 h of compute.

## 1. What already exists (do NOT redo)
- 9 validated wind directions 180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5, 360 with 4 fog tracer bands each, in C:\Users\George\deathfog-data\library (wind_<dir*10>.npz, fog_<dir*10>_<band>.npz, wind_dirs.json). Validation reports in reports/wind_dirs/ on branch v3-wind-dirs. No new wind or fog-tracer runs are needed.
- Per-step direction blending already exists: microclimate/dirlib.py (`available`, `weights`) and wetness.simulate uses it when DEATHFOG_DIR_LIBRARY != "0".
- 5 m street correction (297 patches, west wind only) and 2.5 m Potrero: patch_corr_2700*.npz. Not re-run for other directions in this plan (optional, section 8).
- Fog top from pressure-level humidity/inversion: fogtop.estimate_fog_top(ser).
- Wetness model: wetness.simulate(pts, ser, ...) with dew, fog, rain, evaporation and sun terms; sun via per-point horizon [N,72].
- Street points, features, sun horizons and ribbons for Potrero Hill and Golden Gate Heights (S = 19,444 ribbon samples) on v3-dev.
- Replay writer replay.write -> site/v3/replay/index.json (schema 2) + per night streets.u8, streets_r.u8, fog.png, weather.json.
- Deploy: main's .github/workflows/collect.yml copies site/v3 from v3-dev with `git archive` and publishes GitHub Pages.

## 2. Viability (summary)
- Fog presence/height from live data: viable, high value (free satellite, airport observations, NWS RRFS model).
- Live wind into the city model: viable by choosing/blending the 9 precomputed fields per time step and scaling to live speed. The LBM itself never runs live.
- Whole city: viable offline (street points, horizons, tables are one-off CPU jobs); the live run is CPU numpy and fits GitHub Actions. The render data size is the main constraint (section 5).
- Street wetness accuracy: cannot be verified until wet-road data exists (data collection is later). Keep calling it an index.
- Fog burn-off: covered now through data (see 3.4); a custom burn-off model is deferred.

## 3. Live inputs and how each feeds the model

### 3.1 Sources (all free; step 1.1 verifies each)
| Feed | Source | Use | Notes |
|---|---|---|---|
| Forecast + recent past, gridded | NWS RRFS 3 km via Open-Meteo forecast API (`past_days=1`, multi-point request) | all forcing variables per point, hours -24..+66 | HRRR on Open-Meteo is discontinued 2026-11-30: do not use HRRR. Look up the exact RRFS model id on https://open-meteo.com/en/docs/gfs-api. RRFS CONUS: hourly data, 84 h forecast, updated every 6 h; RRFS 15-minutely: 18 h, updated hourly. Stitch: 15-min/hourly product for 0-18 h, 84 h product beyond. |
| Observed wind (in domain) | NOAA CO-OPS station 9414290 San Francisco (= NDBC FTPC1, Fort Point/Presidio) met data | nudge wind speed/direction for past hours and first forecast hours | Verify id and fields (wind speed, direction, gust, air temp). |
| Observed coast/airports | METAR KHAF (Half Moon Bay, coastal), KSFO, KOAK via https://aviationweather.gov/data/api/ (`/api/data/metar?ids=...&format=json&hours=...`) | fog presence (visibility, ceiling), ceiling height, T/Td/wind | 100 req/min, 30-day history, set a custom User-Agent. One request per run is enough. |
| Other stations | NWS api.weather.gov `/stations/{id}/observations` | backup T/Td/wind; find any station inside SF | Set User-Agent. Synoptic/MesoWest free tier is .edu-only: not usable. |
| Marine layer depth | Oakland upper-air sounding (00Z, 12Z) | fog-top check/override | Find a free JSON/CSV source in step 1.1 (e.g. University of Wyoming text, NOAA IGRA/SPC). Optional if RRFS profile is good. |
| Satellite low cloud | GOES-18 (GOES-West) AWS bucket noaa-goes18, CONUS sector, every 5 min | observed fog extent offshore and over the city | Preferred: L2 clear-sky mask (ABI-L2-ACMC) + cloud-top height (ABI-L2-ACHAC): low cloud = cloudy and top < ~1 km. Fallback night: brightness-temperature difference band13 - band07 from ABI-L2-CMIPC. Verify product names in the bucket. The L2 Fog/Low Stratus product is listed at NCEI/CLASS, not confirmed on AWS: do not depend on it. ~2 km pixels. |
| Wet pavement (later, calibration) | Caltrans RWIS (cwwp2.dot.ca.gov), field essSurfaceStatus | not used now | Step 1.1 only checks whether any station near SF exists. |

### 3.2 Forcing becomes spatial
Today one Open-Meteo point drives the whole city (ser = dict of [n] series). For the whole city:
- Request RRFS on a lattice of points covering the domain (EPSG:32610 x0=540100, y0=4171500, 855 x 811 cells of 20 m; roughly lat 37.70-37.83, lon -122.52 to -122.35): about 6 x 5 points at ~3 km spacing, one Open-Meteo call with comma-separated latitude/longitude lists.
- Per street point, interpolate (bilinear in lat/lon) temperature_2m, dew_point_2m, wind_speed_10m, wind vector (interpolate u/v, not degrees), cloud_cover_low, precipitation, shortwave_radiation, plus the pressure-level variables used by fogtop.
- Keep the scalar series too: the fog SOURCE strength s(t) is computed at the coast (upwind), not at each street. Define the coast series as the mean of the lattice points west of x = 545000 m (Ocean Beach strip), and for observed hours combine with KHAF + GOES (3.3).
- Memory: n = 433 steps (72 h at 10 min) x N (~100k points) float32 = 173 MB per variable. Do not hold all variables as [n,N]: hold lattice series [n, L] (L ~ 30) and interpolate per step inside the time loop with a precomputed [N, 4] weight/index table.

### 3.3 Observations nudge the forcing (past hours, fading into the forecast)
- Wind: at the CO-OPS station compute ratio r = observed speed / RRFS speed (rolling 3 h median, clipped 0.5-2.0) and direction offset d = observed - RRFS (vector mean, clipped +-45 deg). Apply r and d to all lattice points for past hours; for forecast hour h apply r^(exp(-h/6)) and d*exp(-h/6).
- Temperature/dew point: same idea with additive offsets from the nearest station; fade with 6 h e-folding.
- Fog source s(t): for past hours set s_obs from (a) GOES low-cloud fraction in an upwind sector box 2-15 km off the coast, chosen by the hour's wind direction, and (b) KHAF visibility <= 3 mi or ceiling <= 1000 ft. s_used = 0.5*s_model + 0.5*s_obs when both exist, s_obs when the model is missing; fade the correction into the forecast with a 3 h e-folding. Keep the weights as constants in a new `microclimate/live/config.py` so they can be calibrated later.
- Fog top: if a sounding from the last 6 h exists, use its inversion base as fog top for those hours (overrides estimate_fog_top), else estimate_fog_top on the interpolated RRFS profile. If a METAR ceiling at KHAF/KSFO is reported, the fog top must be >= ceiling + 100 m.

### 3.4 Fog burn-off (now: from data; later: own model)
- The current model already dries streets after sunrise: shortwave raises T - Td, which lowers the fog source s(t) in wetness.fog_inflow, and the sun/evaporation terms dry the surface. With a 3-day window the morning hours are simply part of the run (today the replay stops at 09:00).
- Observed hours: GOES and METAR show when the fog actually cleared (3.3), so burn-off timing is observed, not modelled.
- Forecast hours: RRFS cloud_cover_low and visibility carry the model's own burn-off physics.
- Deferred (easy to add later, only if section 7 shows burn-off timing errors): a local burn-off term using marine-layer depth vs. solar heating per neighbourhood. It plugs into the per-step fog-top/source series without other changes.

## 4. Implementation steps

### Step 1. Prepare (about 1 day)
1.1 Source check script `tools/live_sources_check.py` (not shipped): fetch 24 h from each source in 3.1 for SF, print latency (newest timestamp vs now), fields present, and save samples under bench/live_samples/. Record findings in PROGRESS.md (RRFS model id, CO-OPS fields, GOES product names, sounding source, RWIS near SF yes/no).
1.2 Merge branch v3-wind-dirs into v3-dev. Conflicts are expected only in PROGRESS.md and microclimate/wetness.py/replay.py (v3-dev changed routes/ribbons; v3-wind-dirs changed wetness/replay/fog). After merging, set the default of DEATHFOG_DIR_LIBRARY to "0" for the replay writer only (replay must not change until the user decides), and use the library always in the live path. Run the 42 unit tests (install pytest into the venv if missing and note it).
Done when: tests pass; `python -m microclimate routes --only replay` regenerates byte-identical replay files (git shows no diff in site/v3/replay); source findings are in PROGRESS.md.

### Step 2. Whole-city street points and static tables (offline, PC; about 2-3 days)
2.1 City streets: fetch OSM highways for the domain bbox with routes.fetch_bbox (classes = routes.RIDE). Clip to the model domain and to land (terrain > 0.5 m). Sample every 20 m along each way (routes.densify). New zone id "city" in routes.toml ([zone.city] mode = "bbox"). Keep the existing Potrero and GGH rows first and unchanged (their row ids stay stable); city rows follow, excluding samples within 5 m of an existing district sample.
2.2 Street features for city rows: street.build (wind ratio, fog bands, refine_corr; NaN refine -> 1.0 as today).
2.3 Sun horizons: sun.py currently computes a 2 m surface only around ZONE_CENTER. Generalise: tile the city into 1.5 km tiles, build fine_surface per tile with a 700 m apron, cast the 72 rays (3 km) through fine surface then the 20 m surface. Benchmark one tile first; if the projected total is > 6 h, use a 4 m surface for tiles without district points and report.
2.4 Static live table `live_static.npz` (float16 where possible) with per point: x, y, z, svf, porous, wind_ratio, rel_wind (from refine_corr), horizon [N,72] uint8, zone id, and the precomputed profile-mode tracer per direction: tracer [9, N] = fogtop.points_tracer(pts, data_dir, d) for each validated direction (this removes the need to ship the 100 MB library fields). Also the 80 m city tracer per direction for fog sheets: tr80 [9, H, W] (replay.city_tracer_80m).
2.5 Publish live_static.npz as a GitHub Release asset (tag live-static-v1); the live job downloads it. Do not commit it to git.
Done when: N_city and file size are reported; a run of wetness.simulate on the static table for one replay night reproduces the shipped district values (max abs diff <= 1 on the 0-100 scale) for the 29,018 existing district rows (Potrero 14,116 + Golden Gate Heights 14,902).

### Step 3. Make the wetness core live-ready (about 2 days)
Edit microclimate/wetness.py and fogtop.py; keep the replay path behaviour identical (regression test from step 2).
3.1 Arbitrary window: add weather.series(data, start_utc, end_utc, step_min=10) generalising night_series (which stays as a wrapper for 16 h).
3.2 Table input: simulate(..., tracer_dirs=None) where tracer_dirs is the [9, N] table from 2.4; when given, skip fogtop.points_tracer and the library file reads.
3.3 Spatial forcing: allow the per-step forcing dict f in the loop to hold arrays [N] (interpolated from the lattice, 3.2) instead of scalars; `step()` already broadcasts, check each term. Keep the coast source s(t) scalar.
3.4 Time-varying lag: today the lag uses one mean flow direction and mean wind for the whole night. For a 72 h window compute it per 3 h block (mean direction and speed of that block, dirlib-consistent) and use the block of the current step.
3.5 Output: return W at the output cadence only (hourly for the live run) to save memory; keep the internal 10 min step.
3.6 Remove any import of warp/xlb from modules the live path imports (fog.py imports warp at module top: the live path must not import fog.py).
Done when: unit tests for weather.series, table input and per-block lag pass; replay regression unchanged; a 72 h run on the full city table finishes in < 5 min and < 2 GB RAM on the PC CPU.

### Step 4. Live builder (about 2-3 days)
New package microclimate/live/: config.py (URLs, station ids, lattice, nudging constants), fetch.py (one function per source, 3 retries, timeouts, User-Agent "sf-death-fog (github.com/George-The-G/SF-DEATH-FOG-DETECTION)"), forcing.py (3.2-3.3), goes.py (3.3 low-cloud fraction from ACM/ACHA; read with xarray/netCDF4 over anonymous S3 HTTP; only the CONUS file nearest each hour; crop to the SF box before decoding), build.py (entry point).
`python -m microclimate.live build --out site/v3/live` does:
1. Window: start = previous 18:00 PT if now is before 18:00, else today 18:00 PT, minus 6 h of history; end = start + 72 h ("tonight + next 2 days").
2. Fetch all sources; any failed source is logged and skipped (fallback order: stations -> RRFS only -> previous good output).
3. Build forcing, run simulate on the city table with direction blending always on, hourly output.
4. Write outputs (section 5) plus status.json: run time, data-as-of per source, sources used/failed, observed/forecast split time, model version.
Done when: two consecutive local runs produce outputs; a run with the network blocked for METAR/GOES still produces output using RRFS only and flags it in status.json.

## 5. Render data for the whole city (3-D part)
Budget: first paint <= 5 MB gzipped (today ~4.2 MB for one replay night with the two districts).
5.1 Frames: live output is hourly (73 frames for 72 h) instead of 10-min (97 frames for 16 h). The viewer already interpolates between frames in the ribbon shader.
5.2 City ribbons: build ribbons for all city streets with make_ribbons (district streets logic, cls 1 = district street) at 40 m sample spacing for city rows (districts keep 20 m, routes 10 m). Expected S_city ~ 50k samples: verify.
5.3 Chunking: write wetness as one file per day: live/day0.u8, day1.u8, day2.u8 (frame-major [frames, S], uint8, 255 = missing). Estimate: 25 frames x 70k samples = 1.75 MB raw per day, ~0.7-0.9 MB gz. The viewer loads day0 first and the rest on demand (UI work, section 9; for now the shadow check only needs the files).
5.4 Fog sheet: hourly tiles, one PNG per day (25 tiles of 214 x 203 px) using replay.fog_sheet with the per-hour blended tr80 (direction weights per hour, not one mean direction). Multiply by the GOES low-cloud mask for observed hours (cells where GOES says clear get at most 30% opacity).
5.5 Index: live/index.json reusing the replay night schema (schema 2 fields) plus: "live": true, "step_min": 60, "frame_files": ["day0.u8", ...], "fog_files": [...], "observed_until_iso", "data_as_of", "status": "status.json". Keep ribbons geometry for the city in live/ribbons_city.bin (same RIB format as data/ribbons.bin, version bump only if needed).
Done when: total gz size of index + day0 + fog day0 + ribbons_city <= 3 MB extra on top of the existing static data; report exact sizes. If over budget: raise city spacing to 60 m, then drop to 2-hourly frames for days 1-2.

## 6. Running it live
6.1 Shadow output path: site/v3/live/ (published with the site, not linked anywhere).
6.2 Workflow: scheduled workflows only run from the default branch (main), and two workflows deploying Pages would overwrite each other. So the live step goes into main's collect.yml, after the existing `git archive ... site/v3` copy: set up Python, install numpy/pvlib/requests/xarray/netCDF4/s3fs (pin versions in requirements-live.txt on v3-dev), download live_static.npz from the release, run `python -m microclimate.live build --out site/v3/live` with the microclimate code taken from v3-dev the same way site/v3 is, then upload-pages-artifact as today. `continue-on-error: true` on the live step so the v2 page always deploys. If the build fails, restore the previous live/ from the last deployed site (fetch it from the Pages URL) so nothing goes blank.
6.3 Schedule: every 30 min (cron `7,37 * * * *`). GitHub cron often starts 15-60 min late on this repo; that is acceptable because status.json carries data-as-of. Open-Meteo RRFS hourly updates appear within the hour.
6.4 This needs one PR to main (user approval). Nothing else on main changes.
6.5 Runtime limit: the job must finish in < 10 min; RAM < 7 GB (GitHub runner). Measure in step 4.
6.6 Rolling archive: keep status.json and the observed part of each run (first frames) for the last 14 days in a separate branch `live-archive` (small, one commit per day) for later calibration. Never commit full live outputs to v3-dev or main.
Done when: shadow output updates on the live site at least 40 times per day for 3 days with no failed deploy of the v2 page.

## 7. Checking against reality (one tool for backtest and daily monitoring)
`python -m microclimate.live check --since 30d` compares, per hour, model fog at the station locations (KHAF, KSFO, KOAK points snapped to the nearest street samples or the coast series) with METAR visibility/ceiling and GOES low cloud over SF. Output reports/live_check/<date>.json: hit rate, false alarms, onset/clear (burn-off) timing error in minutes, wind direction/speed bias at the CO-OPS station.
- Run once as a backtest over the 9 replay nights plus the last 30 days (RRFS/Open-Meteo history for those days, METAR 30-day history).
- Run daily in shadow mode (same tool, last 24 h).
- Use the result to (a) tune the nudging weights in live/config.py and (b) decide the open items in section 8. Do not tune wetness constants (needs wet-road data).
Done when: the backtest report exists and is summarised in PROGRESS.md.

## 8. Explicitly deferred (do not do now)
- Own burn-off model (3.4), only if section 7 shows timing errors.
- 5 m street correction for other directions (about 2-2.5 h GPU each; 225 and 315 first), only if section 7 shows direction matters at street level.
- Wetness calibration and data collection (later, by the user).
- Enabling the direction library in the 9 replay nights (user decision pending; Potrero changes a lot).
- Live data for areas outside the 20 m domain.

## 9. UI changes needed (document only, build later)
- Night list: a "Live (beta)" entry first; label "Tonight + 2 days"; later the default selection.
- Time bar: 72 h with day labels (Tonight / Tomorrow / Day after), a marker at observed_until (left = observed, right = forecast), and hourly stepping. Clock shows weekday + time.
- Load day files on demand when the scrubber enters day 1/2; show a small loading state on the bar.
- Status: "Data as of HH:MM" near the time bar; a "data delayed" banner if data_as_of is older than 90 min; a "forecast only" note if observations were missing.
- About pop-up: add a short "Live data" section (sources: NWS RRFS via Open-Meteo, NOAA GOES-18, METAR, NOAA CO-OPS; attribution lines) and the accuracy statement from section 7.
- Whole-city view: city streets drawn as wetness ribbons at 40 m; districts and routes keep their styles; a layer chip "City streets" (default on) next to "District streets".
- Weather strip: show live values for the selected hour, and which station/model they come from.
- Shadow access: ?live=1 loads site/v3/live/index.json directly before the UI exists.

Built so far (preview, 2026-11-12, `?live=1` only; the page without `live` is unchanged, checked pixel-identical with Playwright):
- app.js `bootLive()`: night list shows only "Live snapshot"; district+route ribbons (data/ribbons.bin) and city ribbons (live/ribbons_city.bin) are two meshes fed from the same frame-major day files (`ribbons.setNight(data, n, col0, stride)`); city streets stay drawn in the whole-city view (the district fade is not applied to them) and sit under the district/route ribbons; picking and tooltips cover the city samples.
- Loading: day0 (27 frames) first, the bar then starts at the frame of `generated_iso`; day1/day2 and fog1/fog2 load in the background, the three fog PNGs are stitched into one tile sheet (so the shader interpolates across day boundaries) and the slider grows from 27 to 79 frames ("loading the next days..." text meanwhile).
- Time bar (text-only additions, `#livebar` + `#livemark` created only in live mode, CSS block at the end of style.css): a yellow line at `observed_until_frame` with the words "observed" (left) and "forecast" (right) above the bar, a line "PREVIEW SNAPSHOT: not updating - Data as of <weekday HH:MM PT>" from status.json `generated_iso`, clock = hour + weekday + date. Not built yet: day labels on the bar, "data delayed" banner, About text, "City streets" chip, live weather source label.
- Size (gz): existing page data 3.24 MB + live first paint 2.31 MB = 5.55 MB (budget about 5 MB; fallback if it matters: 60 m city spacing); days 1-2 add 1.31 MB in the background.

UI pass built on 2026-11-12 (still `?live=1` only; page without `live` unchanged, top header and bottom controls pixel-identical to the previous build, 3-D area differs only by animation noise, same as build vs build):
- Time bar: day labels (weekday at each local midnight; a label that would collide with the next is dropped), observed/forecast line, status line "Data as of ... " plus "updated N min ago" once the job is scheduled (the static snapshot says "Preview snapshot, not updating" and shows no age, so its banner is not permanent), indeterminate loading line while days 1-2 load.
- Banners (`#lvstack`): orange "Data delayed" when age > 90 min; blue "Forecast only" when status.json mode is not observations_nudged or observed_until_frame is null. Test with `?live=1&livetest=delayed` or `&livetest=forecast`.
- Settings sheet: "Live (beta) - tonight + 2 days" night entry; "City streets" chip (default on, also stops picking on city streets) beside "District streets".
- Route selected: existing chip stats kept (length, drop, climb); new strip under the header: average and worst wetness at the selected hour, and the hour the route's mean wetness peaks in the loaded window.
- Weather grid: rain row (mm/h) and a source line (observed hours vs NWS RRFS forecast hours).
- About: "Live data" section (sources and accuracy statement) shown first when `?live=1`.
- Pill buttons: icons untouched. Their radius (999) is already the viewer's chip radius, so no change was needed.
Not built yet: scheduled-job `scheduled: true` flag in status.json (needed for "updated N min ago" and the delayed banner outside tests), "Live (beta)" as default night after go-live, About text that removes the replay wording for live, wetness source label on tooltips, observed_until_frame ~2 h late (builder), first-paint size.
## 10. Order and effort
1. Step 1 (1 day) -> 2. Step 2 (2-3 days, mostly unattended compute) -> 3. Step 3 (2 days) -> 4. Step 4 (2-3 days) -> 5. Section 5 (1-2 days) -> 6. Section 6 (1 day + 3 days shadow) -> 7. Section 7 (1-2 days; can run in parallel with section 6 shadow time).
Total about 2.5-3.5 weeks of agent work. UI (section 9) afterwards.

## 11. Risks
- RRFS is new (operational 2026-08-31) and Open-Meteo drops HRRR on 2026-11-30: expect API changes; keep model ids in live/config.py.
- GOES L2 product names/availability on AWS must be verified (step 1.1); the band-difference fallback works at night only.
- City sun horizons may be slow (step 2.3 benchmark first).
- Render size for the whole city (section 5 budget and fallbacks).
- GitHub Actions schedule delays and runner limits (section 6).
- Accuracy of street wetness is unverified until calibration; the site must say so.

## Sources checked (2026-11-12)
- RRFS implementation notice: https://www.weather.gov/media/notification/pdf_2026/SCN26-048-Updated_RRFS_and_REFS_Implementation.aaa.pdf
- Open-Meteo GFS/HRRR/RRFS API (HRRR discontinued 2026-11-30): https://open-meteo.com/en/docs/gfs-api
- NOAA GOES on AWS: https://registry.opendata.aws/noaa-goes/
- GOES ABI L2 Fog/Low Stratus: https://catalog.data.gov/dataset/noaa-goes-r-series-advanced-baseline-imager-abi-level-2-fog-low-stratus-fls
- Aviation Weather Center Data API: https://aviationweather.gov/data/api/
- Caltrans RWIS fields: https://cwwp2.dot.ca.gov/documentation/rwis/rwis-field-description.htm
- Synoptic open access terms: https://synopticdata.com/pricing/open-access-pricing/