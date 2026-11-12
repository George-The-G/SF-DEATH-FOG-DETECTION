# Plan: live, observation-driven fog (revised 2026-11-12)

Status: plan only. Nothing here is started. Reviewed and revised by Opus; supersedes the rough draft.

## Goal
Make the fog layer and the street wetness reflect real conditions as closely as public data allows, for the current night and the next ~12-18 hours, so the site can show live "death fog" predictions. Ground-truth data collection (wet-road observations) comes later; this plan prepares for it but does not depend on it.

## Verdict on viability
- **Fog presence and fog height: viable, high value.** Free, near-real-time sources exist (satellite, airport observations, the NWS RRFS model). This fixes the biggest weakness today: fog is guessed from one weather point.
- **Wind direction and speed into the city model: viable.** All 9 direction fields (180-360 deg) are computed and validated on branch v3-wind-dirs. Live runs only need to choose, blend and scale them; no GPU at run time.
- **Street-level fog distribution: partly viable.** The 20 m citywide fields carry little terrain signal, and the 5 m street correction exists only for the west wind.
- **Street wetness accuracy: not verifiable yet.** The wetness constants are hand-set. Better inputs make the fog realistic, but "accurate death fog predictions" cannot be claimed until wetness is calibrated against real wet-road data. The site must keep saying so.
- **Running it live: viable on free infrastructure** if the heavy simulation stays offline and the live job is CPU-only and small (see Architecture).

## Where we are today
- Fog is modelled, not observed: hourly Open-Meteo history for one point in SF; fog top estimated from the humidity/temperature profile (microclimate/fogtop.py).
- Fog motion: tracer in four height bands. Main branch uses one 270 deg (west) field; branch v3-wind-dirs adds 8 more directions (180-360 deg), validated, not enabled. Enabling them changes Potrero Hill a lot (route means -8 to -20 on four nights) and Golden Gate Heights little (0 to 4).
- Street correction: 297 nested 5 m patches (2.5 m in Potrero), west wind only. The full 5 m run took about 2 h 20 min of wall time.
- The replay covers 18:00-09:00 on nine past nights. No satellite or cloud-base observations are used, and nothing has been compared with observations.

## Architecture (key revision)
Split the work into an offline part (heavy, GPU, run rarely on the home PC) and a live part (light, CPU, run every 15-30 min on GitHub Actions).
1. **Offline: response tables.** For every street sample point and each validated wind direction, precompute small lookup values: fog tracer concentration per height band, wind speed ratio, sheltering (5 m correction), sky view and horizon (already computed). About 21k points x 9 directions x a few floats = a few MB. Commit these with the site data. The big wind/fog fields (~100 MB each) stay on the PC.
2. **Live: CPU pipeline.** Fetch inputs, build the hourly forcing (wind direction and speed, fog presence upwind, fog top, temperature, dew point, cloud, sun), run the existing wetness model over the street table (seconds on CPU), write the night's frames in the same format the viewer already reads, publish with a "data as of" time.
3. **Viewer:** add a "Live" entry at the top of the night list (last ~6 h observed + next ~12-18 h forecast), with a clear observed/forecast divider on the time bar and a staleness banner. The nine replay nights stay as reference examples.

## Live inputs (all free; verify terms in Phase 0)
| Need | Primary source | Backup | Notes |
|---|---|---|---|
| Wind direction/speed (now) | NDBC Fort Point FTPC1 (inside the model domain), NOAA CO-OPS SF tide station met, METAR KSFO/KOAK/KHAF | NWS api.weather.gov station obs | In-domain stations can also nudge the speed scale of the chosen field. |
| Wind (forecast, profile) | NWS RRFS 3 km via Open-Meteo (hourly; 15-minutely variant) or NOMADS | GFS | **HRRR on Open-Meteo is discontinued 2026-11-30; build on RRFS, not HRRR.** RRFS is operational since 2026-08-31. |
| Fog present at the coast | GOES-18 (GOES-West) imagery, AWS bucket noaa-goes18; night fog from IR band difference (3.9 vs 11 um), daytime visible | NOAA GOES ABI L2 Fog/Low Stratus (FLS) product, if obtainable (AWS availability not confirmed; NCEI/CLASS listed) | ~2 km pixels: tells where the fog bank and its edge are, not street detail. Cannot see fog under higher cloud. |
| Fog base/visibility | METAR ceilings and visibility (aviationweather.gov Data API; 100 req/min, 30-day history, set a User-Agent) | NWS api.weather.gov | KSFO, KOAK, KHAF bracket the city; no METAR inside SF proper. |
| Fog top / marine layer depth | Oakland sounding (00Z and 12Z) | RRFS profile, boundary layer height | Twice daily only; RRFS fills the gaps. |
| Fog forecast (next hours) | RRFS cloud_cover_low, visibility, boundary layer height | - | Model burn-off physics comes for free here. |
| Wet-pavement truth (later) | Caltrans RWIS (cwwp2.dot.ca.gov) has a pavement surface status field | 511 traffic cameras (images) | Whether any RWIS station is near SF is unverified. This is the first candidate for calibration. |
Not usable: Synoptic/MesoWest free tier is limited to .edu non-commercial research.

## Fog burn-off
- **Now (in scope, cheap):** burn-off is taken from data, not modelled. For observed hours, satellite and METAR show when fog clears. For forecast hours, RRFS low cloud and visibility already include burn-off physics. Extend the live window past sunrise (to about noon) so the existing sun and evaporation terms dry the streets as the fog lifts.
- **Later (easy to add, deferred):** a local burn-off model (marine-layer depth vs. solar heating, by neighbourhood elevation and sun exposure). The architecture keeps "fog present / fog top per hour" as an input field, so this slots in later without changing anything else. Do it only if Phase 1 shows RRFS gets burn-off timing wrong for SF.

## Phases
### Phase 0: Source check (about 1 day)
- Confirm each source in the table: access, latency, update rate, terms, and a 7-day sample for SF.
- Decide RRFS access path (Open-Meteo vs NOMADS); confirm GOES FLS availability or plan the band-difference fallback.
- Find out whether any Caltrans RWIS station near SF reports pavement status.

### Phase 1: Truth test with public observations (about 2-3 days)
- For the nine replay nights plus ~30 recent nights, compare modelled fog (present, base, top, timing including burn-off) with METAR ceiling/visibility and GOES.
- Compare the west-only and the 9-direction library against observed wind at Fort Point.
- Output: a scorecard (hit rate, false alarms, timing error). This decides the order of Phases 2-4 and gives the site an honest accuracy statement.

### Phase 2: Observed and forecast forcing (about 3-5 days)
- Replace the single Open-Meteo point with: station wind (now) + RRFS (forecast), fog presence from METAR/GOES, fog top from the sounding + RRFS.
- Enable the 9-direction wind library (decide per district based on Phase 1).
- Rerun Phase 1; keep only changes that improve the scorecard.

### Phase 3: Response tables and live CPU pipeline (about 3-5 days)
- Generate the per-point, per-direction tables offline; verify that the table-based run reproduces the current replay exactly for the west wind.
- Write the live job: fetch, build forcing, run wetness, write frames, publish.
- Run it on a schedule in shadow mode (see Go-live).

### Phase 4: Satellite fog edge (about 3-5 days)
- Use GOES to set where the fog bank is at the coast and how far inland it reaches each hour; feed that as the tracer source strength by direction.
- Known limits: coarse pixels, higher cloud masks the fog.

### Phase 5: Directional street correction (optional, GPU)
- Re-run the 5 m patches for 225 and 315 deg (about 2-2.5 h each; Potrero 2.5 m extra). Do this only if Phase 1/2 show direction matters at street level.

### Phase 6: Calibration (later, when data collection starts)
- Fit the wetness constants against wet-road truth (RWIS if available, then the project's own data collection). Until then, show wetness as a relative index.

## Go-live plan (smooth rollout)
1. **Shadow mode:** the live job writes to site/v3/live/ on the existing GitHub Pages deploy, not linked anywhere. Viewable only with ?live=1. Run for 1-2 weeks; compare each morning with what actually happened (METAR, GOES, photos).
2. **Soft launch:** add the "Live (beta)" entry to the night list, default still a replay night. Show "data as of HH:MM", observed vs forecast on the time bar, and the proof-of-concept pill.
3. **Default to live** once the shadow period looks right. Replay nights stay as examples.
- **Staleness and fallbacks:** if inputs are older than ~1 h, show a "data delayed" banner; if a source is down, fall back in order (stations -> RRFS -> last good frames); never show a blank page. Any failed run keeps the previous good output live.
- **Scheduling:** GitHub Actions cron can start 15-60 min late (seen on this repo). Run every 15-30 min and design for late starts; optionally trigger workflow_dispatch from an external free cron for punctuality.
- **Repo hygiene:** publish live frames through the Pages artifact only; do not commit them to git every run (repo bloat). Keep a small rolling archive (e.g. last 7 nights) for review, and later for calibration.
- **Monitoring:** workflow failure notifications; a small status JSON (last run, sources used, staleness) shown in the About pop-up.
- **Versioning:** keep the replay schema; add a schema version for live; the viewer must keep loading old data. Main v2 page stays untouched.
- **Size budget:** a live night must stay within the current ~4.2 MB per load.

## Risks and open questions
- Wetness accuracy is unverified until calibration; say so on the site.
- RRFS is new (operational 2026-08-31); HRRR on Open-Meteo ends 2026-11-30. Expect format and access changes.
- Satellite cannot see street-level fog or fog under higher cloud.
- The 20 m grid shows little terrain speed-up; the 5 m correction is west-only.
- The live pipeline must never depend on the home PC (GPU stays offline-only).
- Rate limits and terms for each source (aviationweather.gov 100 req/min; NOAA attribution).
- Decide scope of "live": SF only, current night + next ~18 h.

## Sources checked (2026-11-12)
- RRFS implementation notice: https://www.weather.gov/media/notification/pdf_2026/SCN26-048-Updated_RRFS_and_REFS_Implementation.aaa.pdf
- Open-Meteo GFS/HRRR/RRFS API: https://open-meteo.com/en/docs/gfs-api
- NOAA GOES on AWS: https://registry.opendata.aws/noaa-goes/
- GOES ABI L2 Fog/Low Stratus: https://catalog.data.gov/dataset/noaa-goes-r-series-advanced-baseline-imager-abi-level-2-fog-low-stratus-fls
- Aviation Weather Center Data API: https://aviationweather.gov/data/api/
- Caltrans RWIS fields: https://cwwp2.dot.ca.gov/documentation/rwis/rwis-field-description.htm
- Synoptic open access terms: https://synopticdata.com/pricing/open-access-pricing/