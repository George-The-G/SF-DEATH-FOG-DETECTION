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
