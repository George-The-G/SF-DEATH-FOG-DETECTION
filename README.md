# SF-DEATH-FOG-DETECTION
This is a certified BS2 classic.

Free, GitHub-only tools that watch San Francisco weather for "death fog": the thin, slick film of
moisture that fog and dew leave on streets, which matters to skateboarders. Experimental. Not safety advice.

| Page | What it is |
|---|---|
| [Status page](https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/) (v2) | Scores a few zones every ~10 minutes, can push phone alerts, checks itself against field reports. |
| [3-D street view](https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/v3/) (v3) | Every street in the city coloured by a modelled wetness index, for the last hours, tonight and the next two days. Skate routes with flyover. |

Everything runs on GitHub Actions and GitHub Pages. No server, no paid hosting, no weather accounts.

## The 3-D street view (v3)

* **Live (beta):** rebuilt on a schedule (twice an hour when GitHub's scheduler keeps up; the page shows the data time and warns
  when it is more than 90 minutes old). 78 hourly frames: past hours are the forecast model corrected by observations, later hours are forecast.
* **What is drawn:** about 286,000 street points across the city, coloured 0 (dry) to 100 (wet); a fog layer; terrain and buildings;
  routes in Potrero Hill District and Sunset District with length, drop, climb and wetness along the route.
* **Fog and sky:** the fog is a voxel volume (one baked volume per forecast hour, ray-marched on the GPU) lit by a live sun, moon and sky.
  The small cloud button at the bottom right opens the fog menu: density, fog-top shape, wind drift, light, and an Advanced section for performance
  (skip empty air, still-frame refinement, cheaper drawing while the camera moves). Settings stay in the browser; "Reset to default" restores them.
  Code: `site/v3/fog2.js` (fog), `fogflow.js` (terrain-aware wind for the drift), `foglab.js` (menu), `sky.js`. `?fog=sheets` and `?sky=0` switch back to the old sheets / no sky.
* **Past nights:** nine replayed nights stay in the Night list.
* **Take control (side feature):** during a route flyover, tap Take control to skate the street yourself with an on-screen joystick or WASD.
  Collisions with terrain and buildings, gravity downhill, slick when wet. It is a separate side feature with its own docs and feature list in `docs/skate/`; it does not change any wetness data.

How the wetness index is made, every 10 minutes for every street point:

`wetness += dew + fog + rain - evaporation - sun drying` (kept between 0 and 100)

* **Weather:** NOAA's RRFS forecast on a ~3 km lattice over the city, nudged toward station, airport, balloon and satellite observations;
  the corrections fade out over a few hours into the forecast.
* **Wind and fog at street scale:** precomputed wind simulations on a 20 m grid for nine wind directions (south through west to north),
  blended by the current direction and scaled by its speed. A street-level correction from 297 nested 5 m patches exists for the west-wind
  field only. A fog tracer carried by those winds, in four height layers, gives each street's exposure; an estimated fog top lets hills rise above shallow fog.
* **Sun and shade:** each street point has a skyline in 72 directions, so the sun's real position says sunlit or shaded.

### Limits
* The index is a physical-style proxy with hand-set constants. It has never been compared with measured wet roads. It is not a probability.
* Fog only arrives with onshore wind (south through west to north); fog from the bay side or fog forming in place is not modelled.
* Cloud base, drizzle, trees, parked cars, pavement heat storage and road slope are not modelled. See `MODEL_REVIEW_PLAN.md` on `v3-dev`.
* Forecast hours are only as good as the weather model's fog timing, which is often off by hours.
* GitHub can delay or skip scheduled runs, so the live data can be late.

## The status page (v2)

Flags the cold, calm, near-saturated conditions that leave roads slick, per zone.

1. Push this code to a **public** repo's `main` branch.
2. **Settings > Pages > Build and deployment > Source: GitHub Actions.**
3. **Actions > collect > Run workflow.** The first run builds a 30-day baseline and publishes the page.
4. Optional, under **Settings > Secrets and variables > Actions**:

   | name | kind | what it does |
   |---|---|---|
   | `DEATHFOG_REPORT_URL` | variable | Google Form link shown on the page and in alerts |
   | `DEATHFOG_FORM_CSV_URL` | variable | the form's published response sheet (CSV); reports are pulled in automatically |
   | `DEATHFOG_NTFY_TOPIC` | secret | hard-to-guess ntfy topic for phone alerts |
   | `DEATHFOG_USER_AGENT` | secret | contact string for the NWS API; defaults to the repo URL |
   | `DEATHFOG_LIVE` | variable | set to `false` to stop the v3 live build (the pages keep deploying) |

Google Form: turn off "collect email addresses" and do not add a file upload. Question titles need to contain: zone, slick
(0 dry / 1 damp / 2 slick / 3 death fog), mist, sky, grip, surface temperature, side of street, street, cover, notes.

GitHub disables scheduled workflows after 60 days without repository activity; the Actions tab has an **Enable workflow** button.

## Where things live

| Where | What |
|---|---|
| `main` | v2 code, and the one workflow (`.github/workflows/collect.yml`) that builds and deploys both pages |
| `v3-dev` | the 3-D viewer (`site/v3`), the model (`microclimate/`), plans and guides; the workflow copies from here at deploy time |
| `data` | v2 state (database, baseline) as one force-pushed commit |
| release `live-static-v1` | `live_static.npz`: per-street geometry, sky view, skylines and fog exposure used by the live build |
| `v1` | the first version, kept for rollback |

v2 layers: `sources/` (provider adapters) -> `store` (SQLite) -> `states.py` (merged weather state) -> `detectors/` (score and reasons)
-> `engine.py`, `alerts.py`, `site.py`, `baseline.py`, `reports.py`. Add a source or detector as a file in `plugins/`
(`@register_source`, `@register_detector`) and name it in `config.toml`.

## Commands

* v2 (Python 3.11+, nothing to install): `python3 run.py collect | evaluate | baseline [--show] | site | status | report | verify | streets | plugins`.
  Tests: `python3 -m unittest discover -s tests`.
* v3 live build (on `v3-dev`; needs numpy, pvlib, pandas, pyproj, requests, h5py, Pillow):
  `python -m microclimate.live build --out site/v3/live --static <path to live_static.npz>`.
  Source health check: `python -m microclimate.live check --since 30d`. Tests: `python -m pytest`.
* Adding a route (on `v3-dev`): add a `[[route]]` to `routes.toml` (id, name, zone, colour, waypoints from the top of the hill down),
  run `python -m microclimate routes`, check the overlay images in `grids/zone/routes/`, then commit
  (`site/v3` is gitignored: `git add -f site/v3/data site/v3/replay`). Append new routes at the end.

## Sources and what each is used for

| Source | Used for | Terms |
|---|---|---|
| [Open-Meteo](https://open-meteo.com/) serving NOAA's RRFS model | v3: forecast and recent-hours weather on the city lattice (temperature, dew point, wind, cloud, rain, sunshine, visibility) and the temperature and humidity profile for the fog top; weather history for the replayed nights. v2: model fill-in for whatever the stations lack | CC BY 4.0 |
| [NOAA CO-OPS](https://tidesandcurrents.noaa.gov/) station 9414290 (San Francisco) | v3: observed wind and air temperature that correct the model | US government, public |
| [Aviation Weather Center](https://aviationweather.gov/data/api/) airport reports (KHAF, KSFO, KOAK) | v3: observed temperature and dew point; visibility and cloud ceilings that correct the fog source and fog top | US government, public |
| [NWS API](https://www.weather.gov/documentation/services-web-api) | v2: station observations per zone. v3: downtown temperature and dew point (SFOC1) | US government, public |
| [NWS Storm Prediction Center](https://www.spc.noaa.gov/exper/soundings/) Oakland balloon sounding | v3: height of the marine inversion, which sets the fog top | US government, public |
| [NOAA GOES-18 on AWS](https://registry.opendata.aws/noaa-goes/) (clear-sky mask, cloud-top height) | v3: observed low cloud upwind of the city and over it, for the fog source and the drawn fog layer | NOAA open data |
| [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/) | v2: archived airport observations for the 30-day baseline | Public archive |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) via Overpass | Streets, street names and surface tags for both pages | ODbL |
| [USGS 3DEP](https://www.usgs.gov/3d-elevation-program) | v3: terrain elevation | US government, public |
| [DataSF Building Footprints](https://data.sfgov.org/) | v3: about 177,000 building outlines with lidar roof heights | DataSF open data terms |
| [XLB](https://github.com/Autodesk/XLB) on [NVIDIA Warp](https://github.com/NVIDIA/warp) | v3: the wind simulations (run offline on a GPU, results reused) | Open-source software |
| [pvlib](https://pvlib-python.readthedocs.io/) | v3: sun position | Open-source software |
| [three.js](https://threejs.org/) | v3: 3-D drawing in the browser | Open-source software |
| [ntfy](https://ntfy.sh/), Google Forms | v2: optional phone alerts and field reports | Their own terms |
| PurpleAir, a GOES-West image link | v2: optional extra sources, off by default | Their own terms |
