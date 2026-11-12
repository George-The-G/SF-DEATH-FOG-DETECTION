# v3 MVP progress

Spec: Build spec for the MVP in the plan doc (https://claude.ai/artifact/TRnCH9dJoSe6cBdHMPJRDJ).
Branch: `v3-dev`. Never touch `main`, `data` or v2 code.

## Go-ahead
- 2026-11-11 16:39 PT: user said "Okay get started" (first confirmation).
- 2026-11-11 PT: user answered "Yes, start the MVP" to the second-confirmation question. Both recorded.

## M0: Setup and benchmark: IN PROGRESS
- Done (cloud): v3-dev branch, microclimate.toml, package skeleton, config test.
- BLOCKED: no PC workspace yet. Access to the parent folder of the SmartHome project was denied by the auto-mode classifier. The user must add that folder in the desktop app ("Add folder") or give its path.
- Next: with the folder connected, clone this branch there, record PC specs, create venv, benchmark XLB.

## M0 finding (2026-11-11, PC folder connected)
- Workspace: C:\Users\George\source\repos\SF-DEATH-FOG-DETECTION (v3-dev snapshot, copied in; no .git history yet because the bridge shell has no network).
- The Claude bridge shell is a Linux VM, NOT Windows: 2 cores, 3 GB RAM, no GPU, no internet, Python 3.10 (config.py needs 3.11+ for tomllib). It cannot run the RTX 4080 benchmark or download city data.
- GPU and download steps need a real Windows shell on the PC. desktop-commander MCP on the PC fails: 'npx' is not recognized (Node.js missing or not on PATH).
- Leftover: v3-dev.tar.gz in this folder (could not be deleted without delete permission); safe to delete by hand.

## M0 RESULT: PASS (2026-11-11)
- PC: AMD Ryzen (Family 25 Model 97, 7800X3D class), 31.7 GB RAM, NVIDIA GeForce RTX 4080 SUPER 16 GB, driver 610.88, CUDA toolkit 13.4 per Warp, Windows, 160 GB free on C:.
- Software: Node v26.13.1 (installed), Python 3.12.10 and git 2.56 (already present), .venv with numpy scipy shapely pyproj trimesh requests pillow rasterio pvlib warp-lang 1.19.0 xlb 0.3.2 (pinned in requirements-mc.txt). See INSTALLED_PACKAGES.md.
- Bridge: desktop-commander plugin now connected (Windows PowerShell). The Linux bridge shell is not used for compute.
- XLB Warp backend, D3Q19 BGK FP32, flow past a cube, GPU benchmark: ~3,550 MLUPS at every size tested. Full domain 840x800x40 = 26.9M cells: 7.5 s per 1,000 steps, +4.4 GB GPU memory (5.5 GB total used). Smaller: 8.4M cells 2.4 s/1,000 steps. Projected full-domain memory well under the 14 GB limit. A 50,000-step steady run is about 6-7 minutes per wind direction.
- Decision: cell_m stays 20, no fallback solver needed. Env: bench/xlb_bench.py reproduces this.
- Data source reachability: GitHub and PyPI reachable from the PC. Others not yet checked (DataSF, USGS 3DEP, Overpass).

## M1: NEXT
Check data sources (DataSF building footprints field name, 3DEP elevation endpoint, Overpass), then build geometry per the spec.

## M1 (part 1 of 2): grids DONE (2026-11-11)
- Data: DataSF building footprints dataset ynuv-fyni (177,023 buildings, field hgt_median_m in metres, lidar 2023, data_as_of 2023-09-11), saved as ~/deathfog-data/raw/bldg.geojson (131 MB). USGS 3DEP 1/3 arc-second tile USGS_13_n38w123.tif (223 MB) from prd-tnm.s3.amazonaws.com, saved as raw/dem_n38w123.tif. Reachable from PC: DataSF, S3, Open-Meteo, GitHub, PyPI. Overpass main server timed out (use the kumi mirror as v2 does); tnmaccess API returned 403 (not needed, the S3 path works).
- Grid: 855 x 811 x 40 = 27.7M cells at 20 m, EPSG:32610, z above sea level. Outputs in ~/deathfog-data/grids/ (solid_20m.npz, terrain_20m.npy, bldg_mean_h_20m.npy, bldg_max_h_20m.npy, grid_spec.json, geometry_summary.json). Run: python -m microclimate geometry (about 14 s).
- Checks: 177,023 of 177,023 buildings loaded (pass, within 5%). Terrain: Twin Peaks cell 255 m (20 m averaging lowers the 277 m peak; accepted), bay water 0 m, 20th & Kansas 79 m. Domain terrain max 372 m is San Bruno Mountain at the south edge, not an error.
- SPEC CORRECTION: the spec said Salesforce Tower should read about 320 m. This dataset stores the MEDIAN lidar height per footprint, so the tallest value anywhere is 218 m and the downtown cells around Salesforce read 130-175 m. New criterion: downtown cells 100-230 m and tallest building anywhere 200-230 m. Spires and tapers are underestimated. Fine for a 20 m MVP.
- Known limit: a 20 m cell is solid up to terrain + MEAN building height of the cell (partial coverage counts proportionally), so narrow streets between buildings are not resolved. This is expected at 20 m.

## M1 (part 2 of 2): NEXT
OSM streets and bus stops for the Potrero zone (Overpass kumi mirror), trace the route from 20th & Kansas downhill to the bus stop, sample street points both sides every 10 m with 2 m terrain, viewer meshes (terrain.glb, buildings.glb), top-down PNG for the user to confirm the route.

## M1 part 2: DONE (2026-11-11)
- Potrero zone from OSM (Overpass needs a User-Agent header; kumi mirror timed out, overpass-api.de worked): 633 street ways, 14,116 street sample points (every 10 m, both sides, 5 m offset), 180 bus stops nearby. Files in ~/deathfog-data/grids/zone/ (potrero_points.csv, route.geojson, route.png, zone_summary.json); route.geojson, route.png and the summary are also copied to site/v3/data/.
- ROUTE (please confirm): start = 20th & Kansas OSM intersection (37.75963, -122.40308, 81 m); finds the cheapest downhill street path to bus stops at the bottom, picks the shortest one that reaches the full drop: down 20th to Kansas, down Kansas St through 19th, 18th, 17th to 15th St, ending at the bus stop "Rhode Island Street & 15th Street" (37.76747, -122.40298). 930 m, drop 76.5 m, no climbing. Alternatives within 77 m drop: 100 Hooper St (1,397 m), Division & Townsend St. Spec bug fixed: a name match on "kansas" also matched Arkansas Street, now exact prefix match.
- Meshes (site/v3/data): terrain.glb 2.7 MB (60 m cells), buildings.glb 1.2 MB (only buildings of 15 m and taller, footprint over 150 m2, simplified 3 m; houses omitted to stay under the size budget; full-detail Potrero buildings can be added later). Added mapbox-earcut to the venv (logged).
- Next: M2 wind solver (hardest step).

## M3: street features, horizon and sun (2026-11-11)
Commands (from repo root, PYTHONPATH=repo): `python -m microclimate street`, `python -m microclimate sun`, `python -m microclimate sun --check` (PASS/FAIL, writes grids/zone/sun_check.json, svf_map.png, sunlit_dec21.png). Runtime: street 2 s, sun 22 s, check about 25 s (CPU).
- grids/zone/street_features.csv (row i = potrero_points.csv row i): idx, wind_ratio (speed / 5 m/s inflow at the first informative fluid layer, k = highest solid + 2 with the forced floor k=0 counted solid, same convention as wind.py near_ground_mean), wind_ratio_k1 (wall-adjacent layer, kept for reference), fog_b0..fog_b3 (fog tracer 0-1 in the first fluid cell above terrain + mean building height, bands 0-150, 150-300, 300-500, 500-800 m ABOVE SEA LEVEL, same z convention as fog.py), residence (1 / max(wind_ratio, 0.1)), k_surface, z_sample_m (centre height ASL of the nearest column's first fluid cell), refine_corr (corr_bldg from refined/patch_corr_2700.npz at the nearest 20 m cell, NaN if the file is absent; NOT applied to any other column). Bilinear over the 4 surrounding columns.
- grids/zone/street_horizon.npy [N,72] float16 degrees, azimuth i*5 deg clockwise from north; street_svf.npy [N] float32 = mean cos^2(max(h,0)); street_sun_meta.npz (bcover30, z_obs, shift_m); street_sunlit_dec21.npy [N] hours; street_surface.csv (idx, way, osm_surface, surface_src own|nearest|none, porous, porous_factor 0.6 for paving_stones/sett/cobblestone/unpaved/gravel else 1.0).
- Surface model: 2 m window (zone centre +/- 1700 m: DEM bilinear + footprints at hgt_median_m), outside it the 20 m terrain + mean building height; rays to 3 km, step 2 m near, 4 % of distance mid, max 25 m; observer 1 m above DEM. 280 points that fall inside a footprint were moved to the nearest non-building 2 m cell (up to 30 m).
- API: sun.sunlit(horizon_deg, sun_az, sun_el), sun.sunlit_hours(horizon_deg, date, step_min) (pvlib 37.76, -122.40, America/Los_Angeles), sun.sun_track(date).
- Caveat: at 20 m the 270 degree wind ratio at street columns is almost uniform (p5-p95 0.999-1.008) because street columns have mean building height of only 0-5 m; the feature carries little information until the refined correction or a finer field is merged.

## M4 + M6: wetness, replay, validation (2026-11-11)
Commands: python -m microclimate replay (writes site/v3/replay/), python -m microclimate validate (PASS/FAIL, validation.json, overview.png). Tests: tests_mc/test_wetness.py. Nine nights replayed (7 fog, 2 dry), refined wind applied after normalisation (USE_REFINED). Validation: a,b,c,d1,d2 PASS; d3 FAIL (tracer bands 2-3 not converged); f skipped. Full write-up in MVP_REPORT.md. Replay is 13.6 MB raw, 4.2 MB gzipped.


## M5: 3-D viewer (site/v3/): DONE (mock data)
- site/v3/index.html + app.js + style.css (three.js 0.160.0 via jsdelivr importmap). Preview: python -m http.server 8000 --directory site/v3 then open http://localhost:8000/ (?src=mock forces mock data). Tries replay/ then replay_mock/.
- Replay contract as in the brief; fog.png tile row 0 = SOUTH unless fog_grid.row0=='north'; streets.u8 size must equal n_frames*14116. Packed street points: data/points.bin + points.json (tools/make_points.py reads ~/deathfog-data/grids/zone/potrero_points.csv). Mock: tools/make_mock_replay.py -> replay_mock/.
- Note: site/ is in .gitignore, so site/v3 files are added with git add -f.

- Viewer update (v3-dev): buildings now ship as data/buildings.bin (all ~169k footprints: instanced boxes + polygons; buildings.glb is no longer loaded). Fog is site/v3/fog.js: 6 stacked terrain-following noise-drifting sheets, sqrt visual stretch of the fog.png value (PX_REF 16), near-camera fade, street points drawn after the fog, Fog density slider. Checked merged build on 2026-10-01 at 04:00 (phone + desktop, both toggles, no console errors).


## Fog height treatment (2026-11-11, v3-dev)
- microclimate/fogtop.py (new), wetness.py, weather.py, fog.py, validate.py; tests_mc/test_fogheight.py. Switch `wetness.FOG_HEIGHT_MODE` = 'profile' (default) | 'band0' (old). Details, constants and the before/after elevation table are in MVP_REPORT.md 'Fog height treatment'.
- Fog top (m ASL) = max(grid elev + 50 m, min(RH < 80 % crossing, first temperature inversion)) from Open-Meteo 1000-900 hPa levels (weather.fetch_extra caches <weather>_prof.json; missing data -> 300 m). Street wet weight = smoothstep ramp +/-40 m around the top; tracer = 4 band fields sampled at street z + 10 m and interpolated between band centres (fogtop.sample_bands works for any point table with x, y, z: pass data_dir or set wetness.DATA_DIR).
- Fog bands 2 and 3 (270 deg) rerun with 6 transits: drift 0.0018 and 0.0090 (was 0.054, 0.127); validate d3 now PASS (band 3 marginal). Old fields kept as library/fog_2700_[23].bak.npz (not committed). fog.rebuild_bands / fog.converge_band do the rerun.
- validate g1 (W vs elevation, band0 vs profile), g2 (fog top forced 90 vs 700 m), g3 (Twin Peaks / Mt Davidson dry on shallow, wet on deep fog nights; by construction) all PASS. Nothing calibrated; v2 observed fog nights not compared.

## Multi-zone routes (routes.toml)

- `routes.toml` is the single source for zones and routes. `python -m microclimate routes` runs geometry, street, sun, replay, viewer data and validate, skipping steps whose inputs are unchanged.
- Zones: Potrero keeps the legacy disc builder (rows byte-identical, hashes in `tests_mc/baseline_potrero.json`); Golden Gate Heights uses a 250 m corridor around its routes (no service roads). Combined `points.csv` has 21,210 rows (Potrero 14,116 + GGH 7,094). Sun windows are per zone (1700 m / 2100 m half-width) and the surface nearest-way lookup is per zone, so adding a zone never changes another zone's rows.
- points.bin layout (supersedes the earlier note): float32[N,4] at 0, side u8 at 16N, on_route u8 at 17N, street u16 at 18N, route_mask u16 at 20N, zone u8 at 22N. `streets.u8` size is n_frames * N (now 21,210).
- Five Golden Gate Heights routes were map-matched from a hand-drawn screenshot (waypoints are intersections, listed top of hill first so every route runs downhill; the first attempt had them reversed).
- Viewer: route picker grouped by zone, camera fly-to, highlighted route, "Fly the route" chase camera with pause/stop and 1x/2x, readout of street and wetness. `?route=<id>&fly=1` deep-links.
- Per-route dawn wetness is in `replay/validation.json` and `index.json` (`w_dawn_by_route`); gated validation checks still run on the Potrero subset only.
- Known: `sun --check` criterion (c) fails on an upper bound for the median SVF (0.884 vs 0.8); the Potrero SVF is unchanged by this work (regression confirms), so this is a stale threshold, not a regression.

## Street ribbons (data side of the continuous-ribbon renderer)

- Selection (user decision): each route plus ONE street block ("edge" = chain of OSM segments between two consecutive intersection nodes, degree >= 3 or dead end) out of every junction the route passes through, whole block, cut at 250 m with the last 30 m faded (`SIDE_EDGE_STEPS = 1` is a constant in make_ribbons.py). Streets: residential, tertiary, secondary, primary, unclassified, living_street, trunk (no service, alleys, steps, footways, links). Global `[ribbons]` table in routes.toml holds only `width_m` (6.5) and `highways`.
- Result: S = 4,718 samples, 312 strips, 10.26 km of route, 16.9 km of side blocks (sum over routes, shared blocks counted per route). One block (15th Avenue, 322 m, ggh-yellow) is capped. All side-block segments already had simulation points (disc/corridor sets), so points.csv is unchanged; nothing was appended.
- Files: `site/v3/data/ribbons.bin` (RIB1 v1, layout in the make_ribbons.py docstring), `ribbons_meta.json`, `replay/<night>/streets_r.u8` ([97, S], smoothed along each strip, 255 = missing), index.json gets `streets_r`, `ribbons`, `ribbons_n` per night and `schema: 2`; the old `streets` key stays. `python -m microclimate routes` regenerates them as its last step (reslice_replay.py keeps index.json's mtime so the replay/validate steps are not re-triggered).
- Notes: end vertices repeat the src id of their neighbouring sample; point spacing is 10-20 m (L/floor(L/10)), so the 12 m test target became 22 m. mask = routes that use the segment OR whose side block it is; cls 0 wins when any route runs over the segment. Tests: tests_mc/test_ribbons.py. Overlays: grids/zone/routes/ribbons_<zone>.png.
## District gradients, dashed routes, flyover skateboarder (v3-dev, 2026-11-12)

- Part 1, district-wide gradients: every drivable street (routes.toml [ribbons].highways) of both zones is a ribbon (cls 1), always visible; the Side streets Off/1x/2x/3x selector is replaced by a "District streets" layer chip (default on). Potrero disc rows unchanged (14,116; already covers the whole district, 59.6 km of streets); Golden Gate Heights gets a new `district_m = 600` corridor in routes.toml (rows 7,094 -> 14,902; 68.8 km of streets, was 24.3 km with the 3x side blocks). S = 19,444 ribbon samples (was 8,267), ~20 m spacing on district streets, route streets keep a cls 1 copy underneath the (dashed) route. Row ids of GGH changed; the whole pipeline was rerun on CPU in about a minute (`python -m microclimate routes`; side-effect free outputs for Potrero, regression hashes pass). Page one night gz: 3.40 MB -> 4.17 MB (three.js CDN excluded).
- Part 2: not-selected routes (and all routes in Whole city) are ONE thin dashed centre line in the ribbon shader (dash length 30 m x the zoom width scale, route colour inside, wetness ramp on the edge band); the selected route is the thick solid-centre path as before. The old route tubes are hidden in ribbon mode.
- Part 3: site/v3/skater.js, a procedural low-poly skateboarder shown only during the flyover, placed on the street ahead of the chase camera just above the glass controls and sized as a fixed share of the screen height. Wobble: t = smoothstep(20, 100, wetness); side-to-side amplitude = t x 2 character widths (CHAR_W 0.5 m, so up to 1 m in model units); frequency 0.8 -> 2.2 Hz; roll lean with the sway; arms counter-swing. Wetness is the interpolated route wetness at the skater (time and arc).
- Pipeline note: run it against a copy of deathfog-data (set USERPROFILE) so the shared data dir and the wind agent are untouched.

## Street ribbons: viewer side (site/v3/ribbons.js, v3-dev)
- Default renderer when the selected night has `streets_r` + `ribbons` (schema 2); `?ribbons=0` forces the old quad InstancedMesh (not built at all in ribbon mode), `?ribbons=1` toasts if a night lacks the data. `?src=synth` loads `replay_synth/` made by `tools/make_synthetic_ribbons.py` (stand-in fixture, not real data).
- One BufferGeometry mesh, one draw call, render order 10. Vertices are lifted once to the terrain mesh (max of centre and both lane edges, +1 m, 1.5x exaggeration). Wetness is a frame-major R8 texture (4096 wide); the vertex shader fetches the two bracketing frames and mixes them (255 = missing), the fragment shader applies the ramp to the interpolated value, so scrubbing is three uniform writes. Width scale = clamp(dist/450, 1, 8); side blocks (cls 1) are 0.75x wide, alpha 0.8 x fade, fade out in the whole-city view and always show with a route selected; the selected route's mask bit draws a route-coloured outline. The route tubes are only drawn beyond 2200 m (they would hide the ribbons).
- CPU: `wetNow[S]` is refreshed lazily (only for the tooltip and fly readout); picking projects the S samples per pointer move.
- UI (same release): full-screen canvas with translucent glass header (brand + PoC pill + route chip + menu) and bottom stack (weather bar, legend strip, time bar); menu sheet holds night, speed, layers (Fog, Buildings, Side streets), fog density, legend; desktop pins the sheet on the right (open/closed remembered in localStorage `df3.sheetOpen`).

## Wind direction library (branch v3-wind-dirs, 2026-11-12)
- Fields: ~/deathfog-data/library/wind_<dir*10>.npz + fog_<dir*10>_0..3.npz for 180, 202.5, 225, 247.5, 270 (original), 292.5, 315, 337.5, 360. Validated list: library/wind_dirs.json; per-direction numbers: library/wind_dirs_validation.json (`python -m microclimate.validate_dirs` is a function module: `validate_dirs.run(D, [dirs])`). Not computed on purpose: 90-157.5 (offshore / easterly wind brings no advected fog; the onshore weight is zero there).
- Validation criteria a-h (validate_dirs.py docstring). Criterion b was wrong in the first version (read the zero boundary-condition cells and assumed a divergence-free field); it is now relative to the 270 reference, see the commit message. g (fog tracer sanity) and h (rotation round trip, 0.1 % u, 1 % v/w at the float16 floor, exactly 0 for 180/360) were added.
- 180 / 360 had failed (NaN abort at step 1000, then max speed 2.8x inflow at the outflow corner). Two causes: (1) wind.run's blow-up check also looked at solid cells deep inside terrain (no boundary condition, never coupled to the flow), which can drift to garbage: the check now looks at fluid cells only (a run with max fluid speed 0.06 had been aborted); (2) axis-aligned frames put the Marin headlands / East Bay terrain directly on the outflow and lateral boundaries: windrot.frame now adds 50 cells of tapered filler on both lateral sides and downwind for axis-aligned directions (AXIS_PAD). Oblique frames unchanged. Failed outputs are kept as *.unstable.* / *.failed1.json / *.suspect in library/.
- Runtimes (wind / incl. compile, GPU memory): 180 and 360 36.7 M cells about 530 s run (865-870 s total), 202.5 51.8 M 738 s run (1408 s total), 225 and 315 60.3 M 718-811 s run (about 1400-1500 s total); GPU use 7-8 GB at 37 M cells, about 13 GB at 60 M cells. Fog bands 17 s (0-300 m) to 50 s (500-800 m, 6 transits) per direction.
- Replay effect (library/dirs_replay_compare.json from `python -m microclimate.compare_dirs`, nothing under site/v3 touched): see the table in the commit / report; Potrero Hill moves a lot on nights with direction other than due west (dawn mean wetness changes of up to 20 points on the Potrero route), Golden Gate Heights by 0-4 points.
