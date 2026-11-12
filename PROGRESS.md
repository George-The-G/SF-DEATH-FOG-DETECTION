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

