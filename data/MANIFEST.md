# Local Data Directory Manifest (`data/MANIFEST.md`)

Purpose: Tracked inventory of the gitignored `data/` folder (`$DEATHFOG_DATA`), critical file hashes, and regeneration commands.
Status: current
Last verified: 2026-11-18

---

## 1. Overview

Under the self-contained repository layout (Option 1), all simulation data, raw GIS inputs, and compiled static tables live in `<repo_root>/data/`.
Code resolves this path through [`microclimate/paths.py`](../microclimate/paths.py) (`data_dir()`, `library_dir()`, `live_dir()`, `grids_dir()`, `raw_dir()`, `scratch_dir()`), which defaults to `<repo_root>/data` and can be overridden with the `DEATHFOG_DATA` environment variable.

| Subfolder | Size | Purpose | Tracked in Git? |
|---|---:|---|---|
| `data/MANIFEST.md` | < 0.1 MB | This inventory file | **Yes** (`!/data/MANIFEST.md`) |
| `data/library/` | 7,629.7 MB | 20m wind fields (`wind_<dir>.npz`), 2D/3D fog tracer bands (`fog_<dir>_<band>.npz`, `fog3d_<dir>_<band>.npz`), and `runs/` logs | No |
| `data/raw/` | 1,902.6 MB | Downloaded USGS 3DEP DEM GeoTIFFs (5 tiles), DataSF building footprints GeoJSON, OSM streets JSON, Open-Meteo historical JSON | No (regenerable by download) |
| `data/spikes/` | 265.8 MB | Guide 12 5m street-scale wind experiment outputs (`street5m`, `street5m_v2`, `street5m_v3`) | No |
| `data/live/` | 176.8 MB | Compiled live pipeline inputs (`live_static.npz`, `live_tracer3d.npz`, `live_extra.npz`, `live_east.npz`, `station_wind.npz`, `ribbons_city.bin`, `goes_cache/`) | No (published to GitHub Release `live-static-v1`) |
| `data/refined/` | 75.0 MB | 297 nested 5m street-wind refinement patch outputs (`patch_corr_2700.npz`, etc.) | No |
| `data/scratch/` | 61.7 MB | `release_walls_k20v8/` (the walls-solver + `kd=20, kdv=8` static/tracer release bundle used by beta) | No |
| `data/grids/` | 30.1 MB | Computed 20m terrain (`terrain_20m.npy`), building height (`bldg_mean_h_20m.npy`), 3D solid voxel mask (`solid_20m.npz`), and street point/horizon tables (`zone/`) | No |
| `data/sky_inputs/` | 0.4 MB | Dated snapshots of METAR layers, RRFS profiles up to 300 hPa, OAK soundings, and GOES-18 5-min store for Guide 13 (`tools/collect_sky_inputs.py`) | No |
| **Total** | **10,142.1 MB** | (~10.1 GB, reduced from 25.6 GB after deduplication and archiving) | |

---

## 2. Critical Release Assets & SHA256 Hashes

GitHub Actions (`collect.yml` -> `live_ci/refresh.sh`) downloads static tables from GitHub Release **`live-static-v1`**:

| File | Size | SHA256 | Notes |
|---|---:|---|---|
| `data/live/live_static.npz` | 49.3 MB | `1CA1A384FD20CA587AB9980B230FA87D291C7A5F29A3E41A9A4235AA39F5AE03` | Public static table (285,809 street points, 72-dir horizons, pre-walls 5m `rel_wind` for 270°) |
| `data/live/live_tracer3d.npz` | 15.1 MB | `E1FC165D1640C808E421B00D8C065EB04A0BFFF0C052117396D59631B5D3290D` | Pre-walls 3D tracer table |
| `data/scratch/release_walls_k20v8/live_static_walls.npz` | 50.5 MB | `95DC58E6E47D73524ABB493B775B689DB1DBF8EC9ACBB56DCE94F0451D3372AA` | Beta static table built with 20m walls wind + `kd=20, kdv=8` tracer (`rel_wind = 1`, see [`docs/PIPELINE_DISCREPANCIES.md`](../docs/PIPELINE_DISCREPANCIES.md)) |
| `data/scratch/release_walls_k20v8/live_tracer3d_walls.npz` | 14.2 MB | `C698E9F2C1DF73D60253BF9695DE7CB783B7F37FB41F9ACEA1A221D18A422D2B` | Beta 3D tracer table (20m walls wind, `kd=20, kdv=8`) |

---

## 3. How to Regenerate Each Stage

All commands run from the repository root (`.\.venv\Scripts\python.exe`):

1. **Grids & Street Points (`data/grids/`):**
   ```powershell
   python -m microclimate geometry
   python -m microclimate zone
   python -m microclimate city points features sun
   ```
2. **20m Wind & Fog Tracer Library (`data/library/`, requires NVIDIA GPU + Warp/XLB):**
   ```powershell
   python -m microclimate winddir <direction_deg> --fog
   ```
   *(See [`docs/runbooks/RUNBOOK_WIND_EAST.md`](../docs/runbooks/RUNBOOK_WIND_EAST.md) and [`docs/sim/WIND_SOLVER_CONFIG.md`](../docs/sim/WIND_SOLVER_CONFIG.md).)*
3. **5m Nested Street-Wind Refinement (`data/refined/`, requires GPU):**
   ```powershell
   python -m microclimate.refine run
   ```
4. **Pack Live Static & 3D Tracer Assets (`data/live/`):**
   ```powershell
   python -m microclimate city static
   python -m microclimate.live tracer3d
   python -m microclimate.live stationwind
   python -m microclimate.live stationwind --east
   ```

---

## 4. Cold Archive (`_archive/2026-11-18/`)

Pre-walls and pre-tracer libraries that were superseded on 2026-11-16 are preserved as verified `.tar` archives in `_archive/2026-11-18/libraries/` (see `_archive/2026-11-18/ARCHIVE_INDEX.md` for SHA256 hashes):
- `library_original_fog.tar` (1,934.8 MB)
- `library_backup_prewalls_2026-11-16.tar` (1,577.8 MB)
- `library_walls_v1.tar` (1,286.5 MB)
- `home_live_library.tar` (2,046.3 MB)
