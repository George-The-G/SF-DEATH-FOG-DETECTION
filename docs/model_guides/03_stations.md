# Guide 03: stations at their coordinates, in the model and on the map

Problem: the model compares itself with each station at the station's location but then applies one citywide correction (one wind ratio from Fort Point, one mean temperature offset). Stations are not shown on the page.

## Step 1: source check (report to the user before building further)
Keyless stations inside or within 20 km of the grid, with observations at most 90 minutes old:
- NWS API: `https://api.weather.gov/points/{lat},{lon}/stations` for the grid centre and four corners; for each station `/observations/latest`.
- NOAA CO-OPS: station metadata filtered to the bay, products `wind` and `air_temperature`.
- NDBC buoys 46026 and 46237: `https://www.ndbc.noaa.gov/data/realtime2/<id>.txt` (air and sea temperature, wind).
- Airports already used (KHAF, KSFO, KOAK) plus KSQL, KNUQ, KHWD via aviationweather.gov.
Write `reports/live_check/stations_<date>.md`: id, name, lat, lon, elevation, variables, typical age, terms. The user picks the list. Accounts and keys are out of scope.

## Step 2: fetch
- `live/config.py`: `STATIONS = {id: dict(kind, name, lat, lon, elev_m, vars)}`.
- `live/fetch.py`: `ndbc(id)`; `coops()` and `nws()` take a list. One station failing is non-fatal and listed in `status.json`.

## Step 3: placement in the model
For every station compute once: grid `x, y` (EPSG:32610), whether it is inside the 20 m domain, terrain height there, and the nearest street point.
- Model value at the station = lattice value moved to the station height (-6.5 K/km for temperature and dew point) and, for wind inside the domain, multiplied by the wind ratio of the nearest street point's cell at roof level.
- Hourly difference per station (observation minus model) with the existing 3-hour rolling median.
- Quality control: drop a station-hour that differs from the median of the others by more than 5 K, or a wind ratio outside 0.33-3.
- Correction field on the lattice: `corr(p) = sum_i w_i * off_i / (sum_i w_i + W0)`, `w_i = exp(-(d_i / L)^2)`, `L = 6 km`, `W0 = 0.3`. Wind: the same on log speed ratio and on direction offset. Fade after the last observation with the existing e-folding times.
- Controlled by `C.NUDGE_SPATIAL` (default False until the user approves). With today's stations and a very large `L` it must reproduce the current citywide correction.

## Step 4: data for the page
`live/stations.json` (small, loaded on demand): for each station id, name, kind, lat, lon, x, y, z, and for each hour of the observed part: observed T, dew point, wind speed and direction, visibility, ceiling, sea temperature where present; model value before correction; model value after correction.
Also per station a 24-hour summary: mean absolute error before and after correction.

## Step 5: UI (mockup first)
Markers on the 3-D view at the station positions; tap for observed versus model at the selected hour and the 24-hour error; a "Stations" layer switch in settings. Do not build until the mockup is confirmed twice.

## Step 6: check
Extend `python -m microclimate.live check`: leave-one-station-out error over `--since 30d` for raw model, citywide correction, spatial correction. Table per station and variable.

## Tests
- One station: correction equals its offset at the station and decays with distance; far away it tends to zero.
- Outlier dropped. Large `L` equals the citywide mean.
- `stations.json` schema and that model-after equals observation within the rolling-median tolerance at a lone station.

## Done when
The leave-one-out table is in the hand-back and `stations.json` is produced by the scheduled build. `NUDGE_SPATIAL` stays off until the user says otherwise.
