# Guide 06: clear-sky sun, shadows, cloudiness; sunlight data for the viewer (switch `sun_clear`)

User's design: the sun a street can get at a given moment with no cloud is known exactly (sun position, skyline shadow, road slope). That is the maximum. Current cloudiness scales it down. Sunshine should be visible on the model.

What exists: each point has a skyline in 72 directions (`horizon`), so sunlit or shaded is known for any sun position on any date; that already covers the whole year without a separate year-long trace. What is missing: the clear-sky maximum, slope, cloudiness from observations, and any export for display.

## Physics
- Clear-sky irradiance from pvlib for the city centre: `Location.get_clearsky(times, model="ineichen")` gives `dni_clear`, `dhi_clear`, `ghi_clear`.
- Road normal from bearing `b` and along-street slope `s` (rise over run along `b`): `n = (-s sin b, -s cos b, 1) / sqrt(1 + s^2)` (east, north, up). Sun vector `(cos el sin az, cos el cos az, sin el)`. `cos_inc = max(n . sun, 0)`.
- Maximum for the street: `S_max = dni_clear * cos_inc * lit + dhi_clear * svf`.
- Cloudiness factor `k` (0.05-1), per lattice cell and step:
  - forecast: `k_model = clip(SW_model / ghi_clear, 0.05, 1)` where the sun is more than 5 degrees up (hold the last value below that);
  - observed hours: `k_sat` from the GOES cloud mask in the cell's box: clear 1.0, probably clear 0.9, probably cloudy 0.6, cloudy 0.35 (first guesses), averaged over pixels. During the source check look for the GOES downward-shortwave product on the same bucket; if it is there and current, use `k_sat = DSR / ghi_clear` instead;
  - correction `0.5 * (k_sat - k_model)` per cell in observed hours, faded into the forecast with `C.FOG_E_FOLD_H`.
- Under cloud the direct beam is lost first: `direct = dni_clear * max((k - 0.35) / 0.65, 0)`, `diffuse = k * ghi_clear - direct * sin(el)`, floor 0. Street value: `S = direct * cos_inc * lit + diffuse * svf`.
- Drying uses `S` (`K_SUN / 1000 * S`, no separate `K_DIFF`); guides 05 and 07 take `S` as `sw_eff`.

## Static data
- `city.py`: export `bearing`, `slope` (clip to +-0.35) and a surface class (asphalt, concrete, porous, other; from the OSM surface lookup behind `porous_factors`) for every row into `live_extra.npz` (about 1.5 MB). Check the slope sign on three known streets facing north, south and west; record it in the hand-back.
- Publish it as an extra asset on the `live-static-v1` release and download it in `live_ci/refresh.sh` (cache next to `live_static.npz`). Missing file: flat asphalt and a flag.
- Optional reference table for display and for guide 07 spin-up: clear-sky daily energy (kWh/m2) per street for the 15th of each month, computed from the skyline and slope (`python -m microclimate.live sunmax`; 12 values per point, float16).

## Data for the viewer (on-demand files)
- `sun*.u8`: per frame and street sample, `round(100 * S / 1000)` capped at 250 (so 0 = dark or night, 80 = 800 W/m2).
- `index.json`: sun elevation and azimuth per frame and the mean `k`, for day/night lighting.

## UI (mockup first)
Sunlit streets drawn brighter and shaded ones darker; scene light follows sun elevation (day, dusk, night); a "Sunshine" layer switch. Not built until confirmed twice.

## Changes
`live/forcing.py` (clear-sky series, `k` field, `forcing_fn` returns `dni`, `dhi`), `wetness.simulate` (precompute `n`; per step `cos_inc`; branch in `step`), `live/build.py` (`point_table` adds bearing, slope, surface), `live/outputs.py` (sun files, index fields), `live/goes.py` (mask classes per box).

## Tests
- Flat open street under clear sky: `S` equals `ghi_clear` within 2 %.
- Sun due south at 30 degrees: a 15 % slope facing south gets more than flat and facing north less, matching the cosine formula to 1 %.
- Shaded street gets only the diffuse part; night is zero.
- `k = 1` gives the clear-sky values; `k = 0.3` gives no direct beam.
- Satellite says clear while the model says overcast: `k` moves halfway and fades.
- Switch off: identical to today.

## Hand-back
Compare report; mean daytime `S` on north- versus south-facing streets steeper than 8 %; a 24 h plot-free table of `k_model` against `k_sat`.


## Implementation notes: GOES downward shortwave (2026-11-12, stage 1 on the beta channel)
- Source check: `ABI-L2-DSRC` (CONUS) is empty on `noaa-goes18` and `noaa-goes19` (listing of the last four days, nothing). Only the full-disk `ABI-L2-DSRF` exists, a scan every 10 minutes (goes18: about 40 MB per file by day, a few MB at night). goes19 (East) also has DSRF but sees San Francisco at a large angle; goes18 is used.
- The file (`DSR`, uint16, scale 0.02289 W/m2 per count, fill 65535, units `W m-2`, `DQF` 0 good / 1 degraded or invalid) has the grid of the other ABI products: **2 km at nadir, about 2.5 x 2.4 km over San Francisco, not 25 km**. Neighbouring pixels differ (343 distinct values in 352 pixels), so the satellite sunlight is a real map and lattice cells use their own pixels (`C.DSR_BOX_SCALE` x the cell spacing), no mask-class spreading is needed. The whole scene is flagged `DQF = 1` when the sun is low (about 23Z in November) but the values are still sensible: pixels with flag 0 are used when a cell has any, otherwise the degraded ones.
- The file is HDF5 in gzip chunks of 24 full-width rows. `dsr.RangeFile` is a file-like object over HTTP range requests (32 KB blocks) that h5py reads directly (no fsspec / s3fs): metadata plus two or three chunks, **about 0.56 MB and 17 requests per scan** (measured on the PC: 0.59 MB with 32 KB blocks, 0.72 MB with 64 KB, 1.5 MB with 256 KB), instead of 40 MB.
- `k_sat = DSR / ghi_clear` with the Ineichen clear-sky GHI at the scan time (mid-scan, not the hour), clipped to 0..1.2, no value where the clear-sky GHI is below `C.DSR_MIN_GHI` (50 W/m2). It replaces the mask classes per cell and hour where it exists (`C.SUN_DSR`, only for `sun_clear` runs); the mask classes fill the other hours; the weight and the fade are unchanged (`K_SAT_WEIGHT`, `FOG_E_FOLD_H`).
- Rolling cache `dsr_cache.json` (published with the beta files, carried between runs like the state files): per hour the scan key and the city subset (about 16 x 22 pixels, ints in W/m2 plus the flag string), the pixel grid once. About 4.6 KB per daylight hour (23 KB for 5 hours). A run downloads only the hours that are not final yet (the newest `C.DSR_REFRESH_H` hours are looked up again; their file is downloaded only when the nearest scan changed). A cold start in daytime fetches at most `C.DSR_DEADLINE_S` seconds' worth (about 4 s per scan from the PC, sequential); the rest follows with the next run.
- `status.json` of a `sun_clear` run carries `forcing.sun_k.table`: the last 24 observed daylight hours with `ghi_clear`, `sw_model`, and the city mean of `k_model`, `k_mask`, `k_dsr`, `k_used`; `status.dsr` has the bytes, requests and seconds of the run.
- Finding for the first guesses: on 2026-11-12 the four-level mask was class 3 (cloudy) over almost the whole city from 20Z to 00Z (k 0.35), while DSR gave k 0.43 to 0.86 and the model 0.9 to 1.0: the mask lumps thin high cloud with fog and is too dark for sunlight. DSR is the better observed k.
