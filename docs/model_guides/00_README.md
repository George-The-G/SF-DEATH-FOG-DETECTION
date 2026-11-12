# Model accuracy guides (revision 2, 2026-11-12)

Follow-up to `MODEL_REVIEW_PLAN.md`. Revised after the user's direction:
- No field testing for a while. Check the model against free weather stations placed at their real coordinates and against satellite cloud, and show both on the page.
- Pavement keeps heat from run to run; nothing restarts from "dry and cool" each run.
- Sunlight is the clear-sky maximum for that street and moment (shadows included), scaled down by current cloudiness, and is shown on the model.
- Slippery and water depth are tracked separately: a thin film is already fully slippery; the extra water and rain only decide how long drying takes.

One guide per piece of work. Nothing here is built.

## Rules for every guide
- Branch `v3-dev`. No force-push. LF, UTF-8 without BOM. Usual commit trailer. Do not edit `main`; the live step runs `live_ci/refresh.sh` and `microclimate/` from `v3-dev`, so most work needs no change there. If a guide does need a change on main, stop and ask.
- The page is live. Every model change sits behind a named physics switch (guide 01) and is off for the public build until the user approves it. With all switches off the output must equal today's, bit for bit.
- Viewer changes (anything in `site/v3`): show the user a mockup first and wait for two confirmations before building. Guides mark these as "UI (mockup first)". Data files for the viewer can be produced before the UI exists.
- Each guide ends with: its unit tests, the full suite green (`python -m pytest`), and a compare report (guide 01) in the hand-back.
- New constants go at the top of `wetness.py` (model) or `live/config.py` (data handling) with a one-line comment; write "first guess" where no measurement backs the value.
- Without ground truth, "done" means physically consistent, tested on synthetic cases, and checked against stations or satellite where the guide says so. Say this in the hand-back.
- Run-time budget for the live build: under 3 minutes and under 3 GB on the GitHub runner (today 37 s, 0.7 GB). First-paint download must not grow; new viewer data goes in files loaded on demand.

## Order
| # | Guide | What it gives | Size |
|---|---|---|---|
| 01 | Physics switches and compare report | Safe on/off for every change | S |
| 02 | State carried between runs; run archive | Heat and water memory; history for scoring | M |
| 03 | Stations at their coordinates, in the model and on the map | Spatial corrections; model-versus-station view | M |
| 04 | Satellite cloud score | Automatic rating of the model's cloud and fog | M |
| 05 | Slippery versus water depth, rain tracking | The two outputs the user asked for | M |
| 06 | Clear-sky sun, shadows, cloudiness; sunlight data for the viewer | Correct and visible sunshine | M |
| 07 | Pavement temperature with heat memory | Realistic dew and drying | M-L |
| 08 | Cloud base and street-level temperature | Hills in cloud, flats under it | M |
| 09 | Fog-front lag speed | Correct fog arrival time in sheltered streets | S |
| 10 | Evaporation by vapour pressure; total cloud in cooling | Interim fix until 07 is on | S |
| 11 | Drizzle | Wetting below thick stratus | S-M |
| 12 | In-place fog for any wind direction | Bay-side and calm-night fog | M |
| 13 | Dry-spell and humidity grease factors | Extra slipperiness, unverified | S |

Dependencies: 01 first. 05 and 07 need 02. 07 needs 06 and 05. 11 and 12 need 08. 13 needs 05. 03 and 04 are independent of the physics guides and can run in parallel with them.

## Still not in a guide, and why
| Item | Why | What it needs |
|---|---|---|
| Wind simulation with the inversion | Solver has no temperature; a lidded library is 27 citywide runs plus 5 m patches | Several GPU days and hill wind readings to check |
| Fog evaporating over warm land | Cannot be baked into fixed wind fields | Tuning against the satellite fog edge once guide 04 has history |
| Calibrating the 5 m wind correction | No street-level wind data | Guide 03 station winds help a little; real fix needs an anemometer |
| Trees, paint, metal, pavement type | Canopy data means recomputing 286,000 skylines; surface type is sparse in OSM | A day of compute for canopy; a route survey for surfaces |
| Ensemble probability | Run time times six or more; new display | Source check and a UI decision |
| Satellite nowcast (0-6 h) | Cloud tracking project | Worth it once guide 04 shows fog timing is the main error |
| Calibration against real roads | No field data for now | Loggers and reports later; guide 02 keeps the history ready |
| Networks needing accounts (PurpleAir, Synoptic) | API keys as repo secrets | User creates accounts; they slot into guide 03 |
