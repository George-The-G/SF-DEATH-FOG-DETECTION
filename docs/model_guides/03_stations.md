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

## Step 3: stations as anchors through the simulation (user decisions 2026-11-12)
Principle: a station is a reality anchor at its own position and height. It corrects the inputs of the physics (the macro wind that drives the simulated fields, the vertical profile), not a region of the map directly. Decisions: use the IEM mirror (optional source, the build must run without it); use hill stations as data points at their real height, wind included; pier and bridge winds are full anchors at their own location.

### 3a. One-off extraction (offline, on the PC with the wind library)
For every accepted wind station inside the 20 m grid: the simulated wind vector (u, v as a ratio of inflow speed) at the station's x, y and sensor height above sea level (ground or deck elevation + sensor height; 10 m when unknown; Golden Gate Bridge deck about 67 m), for each of the nine library directions (180 to 360 by 22.5). Save as `station_wind.npz` (`ids`, `dirs`, `uv[D, S, 2]`, `z_used`), published as an extra release asset like `live_extra.npz`. `python -m microclimate.live stationwind`.
Stations outside the grid (airports, Oakland and Marin hills, buoys) have no simulated vector: they anchor the forecast lattice wind at their own location and level instead (hill stations: the wind aloft and the inversion check in guide 08), and never scale street wind.

### 3b. Regions
`C.WIND_REGIONS`: 3 or 4 regions with a centre and a smooth weight (Gaussian, about 3 km, normalised so the weights sum to 1 everywhere): Golden Gate gap and north-west, west side, east waterfront and downtown, south. With fewer than two reporting anchors in a region, that region uses the citywide fit.

### 3c. Hourly fit of the macro wind
Unknowns per region r: inflow speed `U_r` and direction `th_r`. Predicted wind at in-grid station i: `sum_r w_r(x_i) * U_r * uv_blend_i(th_r)`, where `uv_blend_i(th)` is the linear blend of the two neighbouring library directions (as `dirlib.weights`). Minimise the squared vector error over stations plus a prior term pulling `(U_r, th_r)` to the forecast wind for that region (weight so that one good station counts about as much as the forecast; first guess), plus a smoothness term between neighbouring regions. Directions outside 180-360 cannot be represented: clamp, and flag the hour `offshore: library cannot represent`.
Quality control before the fit: drop a station-hour whose vector differs from the median of the others by more than 8 m/s.
Time: 3-hour rolling median of the fitted values; after the last observation fade to the forecast with `C.WIND_E_FOLD_H`.

### 3d. The field the model uses at this step
Per street point: direction weights `Wt[i, p] = sum_r w_r(x_p) * dirweights(th_r)` (so the tracer blend and wind ratio vary across the city) and speed `U(p) = sum_r w_r(x_p) * U_r`. `wetness.simulate` accepts per-point direction weights (`Wt` of shape [D, N] per step from `forcing_fn`, key `dir_w`) in place of the single citywide direction. Blending is smooth by construction; never switch fields at a hard edge.
Known approximation (state it in the hand-back and the About text): each library field is a complete flow for one uniform wind, so a regional blend is not exactly mass-conserving and the fog exposure, which depends on the whole upwind path, is approximate near region boundaries. A blend cannot create patterns that are in none of the nine fields (east wind, sea-breeze front, calm pool).

### 3e. Residuals
For each station and hour keep observed minus fitted (vector). Do not spread residuals over the map. Write them to `stations.json` and the archive: they show where the simulated fields are wrong.

### 3f. Temperature and humidity
No simulation layer exists for these yet, so anchors act through the vertical physics: compare each station with the lattice value moved to the station height (profile from guide 08 when on, else -6.5 K/km); fit one sea-level offset and one lapse-rate adjustment per hour from all stations (hill stations give the lapse rate and the inversion), and apply them through each street's own height. A distance-weighted horizontal correction (`L = 6 km`, pulled to zero away from stations) is the fallback only when fewer than three stations report.

Controlled by `C.NUDGE_ANCHORS` (default False until the user approves). With it False the build is identical to today.

## Step 4: data for the page
`live/stations.json` (small, loaded on demand): for each station id, name, kind, lat, lon, x, y, z, and for each hour of the observed part: observed T, dew point, wind speed and direction, visibility, ceiling, sea temperature where present; model value before correction; model value after correction.
Also per station a 24-hour summary: mean absolute error before and after correction.

## Step 5: UI (mockup first)
Markers on the 3-D view at the station positions; tap for observed versus model at the selected hour and the 24-hour error; a "Stations" layer switch in settings. Do not build until the mockup is confirmed twice.

## Step 6: check
Extend `python -m microclimate.live check`: leave-one-station-out error over `--since 30d` (vector wind error, temperature, dew point) for raw model, today's citywide correction, and the anchor fit. Table per station and variable.

## Tests
- Synthetic truth: stations generated from a known inflow (one region) are fitted back to within 0.3 m/s and 5 degrees; with two regions given different inflows, each region recovers its own.
- No stations: the fit returns the forecast. Outlier dropped. Offshore hour flagged.
- Per-point direction weights sum to 1; with one region they equal today's citywide weights and the output is identical.
- Temperature: stations generated from a known offset and lapse rate are recovered.
- `stations.json` schema and that model-after equals observation within the rolling-median tolerance at a lone station.

## Done when
The leave-one-out table is in the hand-back and `stations.json` is produced by the scheduled build. `NUDGE_ANCHORS` stays off until the user says otherwise.


## Implementation notes (steps 2, 3, 4 and 6 built 2026-11-13; step 5 not built)
- Code: `live/config.py` (STATIONS, WIND_REGIONS, constants, `NUDGE_ANCHORS = False`), `live/stations.py` (parallel fetch with a 14 s deadline, hourly tables, analysis, `stations.json`), `live/anchors.py` (regions, wind fit, temperature fit; no scipy), `live/stationwind.py` (3a), `live/check_stations.py` (step 6), `wetness.simulate` key `dir_w`.
- Station sources: CO-OPS (piers, Fort Point), IEM mirror (Golden Gate Bridge, hill RAWS, Oakland Museum, optional), NDBC (buoys), NWS (Oakland CWOP), METAR (airports, already fetched). The IEM service returns raw SHEF codes for older rows; both forms are read.
- East wind (guide 14): with `WIND_EAST` the wind anchors read `live_east.npz` (the same table for 15 directions: 22.5..135 and 180..360, 157.5 is a gap) and carry directions in [180, 540); without it winds from 0..180 are clamped to 180 / 360 as described above.
- `station_wind.npz` is a release asset (`live-static-v1`, like `live_static.npz`); `refresh.sh` downloads it when present. Without it the wind anchors stay off and `stations.json` still works.
- In-grid wind anchors today: FTPC1, GGBC1, PXOC1 (all sampled at the first informative layer, 50 m, because the 20 m grid has nothing lower). Oakland piers, airports, hills and buoys are outside the grid: they are reported against the forecast, never scale street wind. Hill stations give the lapse rate and the inversion in the temperature fit.
- Known approximation (also for the About text): each library field is a complete flow for one uniform wind, so a regional blend is not exactly mass-conserving and the fog exposure near region boundaries is approximate. A blend cannot create patterns that are in none of the nine fields (east wind, sea-breeze front, calm pool). Hours whose wind comes from 0-180 degrees are flagged offshore and clamped to 180 or 360.
- With the flag on the temperature of every street also gets the base lapse (-6.5 K/km, dew point -2 K/km) times its height above the lattice cell, so hill streets differ from today's even where no station is near.
