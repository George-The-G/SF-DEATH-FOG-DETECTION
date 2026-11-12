# Guide 11: more stations and spatial nudging

Problem: one wind station (Fort Point) scales wind for the whole city, and one average offset corrects temperature and dew point. San Francisco has strong west-east and hill-valley differences.

## Step 1: source check (report before building)
Find free, keyless stations inside or within 15 km of the grid with observations at most 90 minutes old:
- NWS API: `https://api.weather.gov/points/{lat},{lon}/stations` for the four grid corners and the centre; for each station fetch `/observations/latest` and record which of temperature, dew point, wind are present and how old.
- NOAA CO-OPS: the station metadata API filtered to the bay; keep stations with `wind` or `air_temperature` products.
- NDBC buoys 46026 and 46237 (`https://www.ndbc.noaa.gov/data/realtime2/<id>.txt`): air temperature, sea temperature, wind.
Write `reports/live_check/stations_<date>.md` with id, name, lat, lon, elevation, variables, typical age. Hand this to the user; continue only with the stations they accept. Accounts or keys are out of scope.

## Step 2: fetch
- `live/config.py`: `STATIONS = {id: dict(kind="nws"|"coops"|"ndbc", lat, lon, elev, vars)}`.
- `live/fetch.py`: `ndbc(id)`; generalise `coops()` and `nws()` to a list. Each station failing is non-fatal and listed in `status.json`.

## Step 3: spatial correction (switch-free: controlled by `C.NUDGE_SPATIAL = False` until approved)
- Hourly offset per station as now (obs minus model at the station; for temperature first move the model value to the station elevation with -6.5 K/km).
- Quality control: drop a station-hour whose offset differs from the median of the others by more than 5 K (temperature, dew point) or a wind ratio outside 0.33-3.
- Field at lattice points: `corr(p) = sum_i w_i * off_i / (sum_i w_i + W0)`, `w_i = exp(-(d_i / L)^2)`, `L = 6 km`, `W0 = 0.3` (pulls the correction to zero far from any station). Wind uses log-ratio and a direction offset the same way.
- Time handling unchanged: 3-hour rolling median per station, fade after the last observation with the existing e-folding times.
- With exactly the current station set and `L` very large this must reduce to today's citywide mean: keep that as a test.

## Step 4: check
Extend `python -m microclimate.live check`: leave-one-station-out error of temperature, dew point and wind speed over `--since 30d`, for (a) raw model, (b) today's citywide nudge, (c) the spatial nudge. Report mean absolute error per station.

## Tests
- One station only: correction equals its offset at the station and decays with distance.
- Outlier station is dropped.
- Large `L` reproduces the citywide mean.

## Done when
The leave-one-out table shows (c) at least as good as (b) at most stations; hand the table to the user before turning `NUDGE_SPATIAL` on.
