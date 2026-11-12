# Model accuracy guides

Follow-up to `MODEL_REVIEW_PLAN.md` (2026-11-12). One guide per fix that can be built now. Nothing here is built. Each guide needs the user's go-ahead before it is started, and a second one before its switch is turned on for the public live page.

## Rules for every guide
- Branch `v3-dev`. No force-push. LF, UTF-8 without BOM. Commit trailer as usual.
- Do guide 01 first: every later change sits behind a named physics switch and is off by default. With all switches off, `wetness.simulate` must give bit-identical results to today (replay nights and live). Tests must prove it.
- The scheduled live job keeps running the current physics until the user approves a switch. Turning a switch on in `live/config.py` (`PHYSICS`) is its own commit and bumps `MODEL_VERSION`.
- Each guide ends with: unit tests listed in the guide, full test suite green, and a compare report (guide 01) pasted into the hand-back: mean and 90th-percentile wetness by elevation band and by district, old versus new, for the latest live inputs.
- All new constants go at the top of `wetness.py` (model) or `live/config.py` (data handling) with a one-line comment and the words "first guess" where there is no measurement behind them. Add them to the About text only when a switch goes live.
- No ground truth exists yet. "Done" means physically consistent and tested on synthetic cases, not "proven more accurate". Say so in the hand-back.
- Anything that changes main (workflow, release download) needs a PR and explicit user approval. Guides that need it say so.

## Order
| # | Guide | Fixes review item | Size | Needs main PR |
|---|---|---|---|---|
| 01 | Physics switches and compare report | (tooling) | S | no |
| 02 | Run archive and scoring | section 5 | S-M | yes (archive step) |
| 03 | Cloud base and elevation-adjusted temperature | A, part of J | M | no |
| 04 | Fog-front lag speed | F | S | no |
| 05 | Evaporation by vapour pressure deficit; total cloud in cooling | E, part of C | S | no |
| 06 | Drizzle | B | S-M | no |
| 07 | In-place fog (any wind direction) | D | M | no |
| 08 | Slope-aware sunlight | G (without trees) | M | yes (extra static file) |
| 09 | Water film in millimetres | H | M | no |
| 10 | Dry-spell and humidity grease factors | I (partly) | S | no |
| 11 | More stations and spatial nudging | K (partly) | M | no |

03 to 05 are independent of each other. 06 and 07 need 03. 09 should come after 05 and 06. 10 needs 09.

## Not in a guide: the hard ones, why, and what they need
| Item | Why it is hard | What it would take |
|---|---|---|
| Pavement temperature with heat memory (C) | Needs asphalt heat capacity and conductivity, sky longwave, a multi-day spin-up, and it changes dew, frost and drying all at once. With no road-surface temperature measurements it could be confidently wrong. | One or two surface IR temperature loggers for about two weeks, then roughly a week of work for a two-layer scheme checked against them. Guide 05 is the interim fix. |
| Wind simulation with the inversion (J) | The solver has no temperature, so it needs either a buoyancy term (new solver work and re-validation) or a lid at several heights. Three lid heights x nine directions is 27 citywide runs plus the 5 m patches at about 2 h 20 min per set. | Several GPU days, a decision on lid heights, and wind readings on hills to check the result. |
| Fog evaporating over warm land (J) | The loss rate depends on land temperature and sunlight along the path, so it cannot be baked into a steady tracer field; needs a rerun or a time-varying decay tuned to something. | GOES fog-edge retreat data to tune against (comes with the nowcast item below). |
| Calibrating the 5 m wind correction (J) | Needs street-level wind measurements; none are fetched. | A handheld or fixed anemometer at a few street points, or a citizen-station feed (keys below). |
| Trees and pavement type (G, I) | The street-tree list has positions but not reliable crown sizes; LiDAR canopy is a large download and means recomputing the horizon profiles for 286,000 points (hours). Pavement type is sparse in OpenStreetMap. | LiDAR canopy processing job (about a day of compute), then a drip term; a manual surface survey of the routes for paint, metal and concrete. |
| Probability from an ensemble (K) | Ensemble access through Open-Meteo is unconfirmed; six or more members multiplies run time inside a 10-minute job; the page needs a new way to show probability. | Source check, run-time budget, and a UI decision. |
| GOES nowcast for the next 0-6 h (K) | Tracking the fog edge needs cloud-motion estimation, parallax correction and night-time low-cloud detection under higher cloud. | A multi-week project; worth it only after scoring (guide 02) shows forecast-hour timing is the main error. |
| Ground truth and calibration | Needs hardware, weeks of data and your time on the streets. Webcam wet-road detection is a project of its own. | 3-5 leaf-wetness and T/RH loggers, field reports through the existing form, then fitting once guide 02 has a few weeks of archive. |
| Station networks that need accounts (PurpleAir, Synoptic) | Need API keys stored as repository secrets, and their terms checked. | You create the accounts; then they slot into guide 11. |
