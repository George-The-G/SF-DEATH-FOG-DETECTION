# Plan: observation- and forecast-driven fog (rough draft for review)

Status: rough plan only. Nothing here is started. For Opus to review and expand.

## Goal
Make the fog layer reflect observed and forecast conditions, instead of one Open-Meteo point and a single steady west wind.

## Where we are today
- Fog is modelled, not observed. Inputs are hourly Open-Meteo history for one point in SF (temperature, dew point, humidity, wind, cloud cover).
- Each night's fog top is estimated from the humidity/temperature profile (microclimate/fogtop.py).
- Fog motion is a tracer in four height bands carried by one steady 270 degree (west) wind field on a 20 m grid; a 5 m street-level correction is applied on top (297 patches, 2.5 m in Potrero).
- No satellite imagery or cloud-base observations are used. The nine replayed nights have never been compared with observations.
- Wetness model constants are hand-set and uncalibrated.

## Phase 0: Source check (about half a day)
Confirm access, update rate, latency and terms before building anything:
- GOES West satellite fog/low-cloud imagery via NOAA public data (about 2 km pixels, minutes cadence).
- Airport surface observations (SFO, OAK and nearby): cloud base, visibility.
- NWS HRRR hourly model output: wind, humidity, temperature profile.
- A marine-layer depth source (e.g. Oakland soundings, if available).

## Phase 1: Test current fog against observations
- For the nine replay nights, compare modelled fog (present/absent, height) with airport cloud base and visibility.
- Record where it is wrong. This decides whether and in what order Phases 2 to 4 are done.
- In the same pass, look for any real wet-road reports to start calibrating the wetness constants.

## Phase 2: Replace weather inputs
- Swap the single Open-Meteo point for HRRR wind and profile data for the replay nights.
- Re-estimate the fog top from the HRRR profile plus airport cloud base.
- Re-run Phase 1 comparison to see what improved.

## Phase 3: Add satellite fog
- Fetch GOES fog imagery for each replay night.
- Use it to set fog coverage (location of the fog edge) and to cross-check the fog top.
- Known limits: coarse pixels, cannot see through higher cloud, no street-level detail.

## Phase 4: Wind direction library (in progress on branch v3-wind-dirs)
- Finish validating additional wind directions (rotated-frame runs; 315, 225, 180, 360 first, then 247.5, 292.5, 337.5, 202.5).
- At run time, choose or blend library fields for the actual wind direction and scale to wind speed. Offshore winds bring no advected fog.

## Phase 5: Live pipeline
- A scheduled job (GitHub workflow or similar) fetches the latest wind, observations and satellite fog, builds fog and wetness frames, and publishes them to the static site with a "data as of" timestamp.
- Needs stale-data handling and fallbacks for feeds that go down.
- Live runs use precomputed direction fields with cheap scaling, not a fresh GPU simulation each time.

## Risks and open questions
- The simulation moves fog but does not model formation or burn-off; timing must come from observations and forecasts.
- Better fog will not fix wrong wetness: the wetness constants need calibration against real wet-road data.
- Scope decision: replay nights only, or live current conditions.
- Satellite and station data licensing and rate limits (verify in Phase 0).
- Forecast skill for fog timing in HRRR is limited; it may need bias correction against observations.