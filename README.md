# SF-DEATH-FOG-DETECTION
This is a certified BS2 classic.


Free, GitHub-only tool that watches San Francisco weather and flags the cold, calm, near-saturated
conditions that leave roads covered in a slick film of mist ("death fog") for skateboarders. It
publishes a status page, can push phone alerts, and checks itself against field reports.

* Runs every ~10 minutes on **GitHub Actions** (free for public repos). No server, no paid hosting.
* **No accounts for weather data**: NWS, Open-Meteo and the Iowa Mesonet archive are all keyless.
* Status page on **GitHub Pages**; field reports through a **Google Form** (optional).
* First run builds a **30-day baseline**: a replay of the detector over last month.

## Live pages

* **Status page (v2, live conditions):** https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/
* **3-D microclimate preview (v3, proof of concept):** https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/v3/ - San Francisco in 3-D with Potrero Hill streets coloured by death-fog chance per street side, and a timelapse of replayed real nights. It is a 20 m simulation of west-wind fog, not a forecast; see below.

## v3: 3-D microclimate (proof of concept)

A one-way chain, all in microclimate/ and developed on the 3-dev branch (the v3 page is copied from that branch into the published site):

1. **Geometry:** DataSF building footprints + USGS 3DEP terrain on a 20 m grid for the whole city.
2. **Wind:** GPU lattice-Boltzmann (XLB) flow for a west wind, then a fog tracer carried through that wind in four height bands. A 5 m nested run refines street-level wind in Potrero.
3. **Streets:** per street side, sky view and shadows (pvlib sun path over 2 m surface), wind and fog deposition.
4. **Wetness model:** dew, fog deposition, rain, evaporation and sun drying replayed over real nights from Open-Meteo history.
5. **Viewer:** site/v3/ (three.js, static files).

Known limits: only the west wind is simulated (other directions are approximated and flagged), the wetness constants are hand-set and not yet calibrated against field reports, and the 20 m grid barely resolves low-rise buildings. Details, validation and a go/no-go note are in MVP_REPORT.md and PROGRESS.md on 3-dev. Run with python -m microclimate <geometry|zone|meshes|wind|street|sun|replay|validate> (needs a CUDA GPU for the wind step; see equirements-mc.txt).

## Setup (about 10 minutes)

1. Push this code to a **public** repo's `main` branch (private repos run out of free Actions minutes).
2. **Settings > Pages > Build and deployment > Source: GitHub Actions.**
3. **Actions tab > collect > Run workflow.** The first run backfills 30 days and prints when death fog
   could have happened; the page appears at `https://<you>.github.io/<repo>/`.
4. Optional extras, all under **Settings > Secrets and variables > Actions**:

   | name | kind | what it does |
   |---|---|---|
   | `DEATHFOG_REPORT_URL` | variable | your Google Form link; shown as a button on the page and in alerts |
   | `DEATHFOG_FORM_CSV_URL` | variable | the form's response sheet, *File > Share > Publish to web > CSV*; reports are pulled in automatically |
   | `DEATHFOG_NTFY_TOPIC` | secret | a hard-to-guess topic name; people subscribe in the free ntfy app to get alerts |
   | `DEATHFOG_USER_AGENT` | secret | contact for NWS, e.g. `(deathfog, you@example.com)`; defaults to the repo URL |

5. **Google Form** (use your own Google account; responders need none): turn off "collect email addresses",
   don't add a file-upload question (that forces sign-in). Question titles only need to *contain* these words:
   zone (dropdown of the zone labels in `config.toml`), slick (0 dry / 1 damp / 2 slick / 3 death fog),
   mist (yes/no), sky, grip (1-5), surface temperature (C or F in the title), side of street, street, cover, notes.

If the page ever shows a "data is stale" banner, open the Actions tab: GitHub disables scheduled workflows
after 60 days without repository activity, and there is an **Enable workflow** button.

## Street map and versions

* The page has a collapsed **Street map** section: streets around each zone from OpenStreetMap (free, no account),
  coloured by the community's rules of thumb (east-west blocks, trees, parked cars, porous surfaces; the south side of
  east-west streets is marked). Weights live in `[streets]` in `config.toml`; the street data is downloaded once and cached on the
  `data` branch (`python3 run.py streets --force` refreshes it), so tuning never re-downloads.
* The page shows its version (`[site] version`). **v1** is kept as a branch: to roll back, Actions > collect > Run workflow >
  *Use workflow from: v1*, or ask to reset `main` to `v1`. Temperatures on the page and in alerts are in Fahrenheit.

## How the code is layered

```
sources/      adapters: provider API -> normalised Reading        (nws, openmeteo, purpleair, snapshot, + plugins)
store (db)    SQLite: readings, evaluations, alerts, reports
states.py     Readings from ANY mix of sources -> WeatherState    (merge policy, 24h high, 3h ago, recent rain)
detectors/    WeatherState -> score + reasons (pure, pluggable)   (death_fog_v1, + plugins)
engine.py     zones, susceptibility, scoring, baseline replay
alerts.py     when does a score become a notification
site.py       static status page         baseline.py   30-day backfill + replay         reports.py   field reports + verify
```

A detector never sees a provider name; it gets a `WeatherState`. A source never sees a detector.

* **Add a source:** drop a file in `plugins/` using `@register_source("name")` (see `plugins/example_local_csv.py`),
  then add `[sources.x] type = "name"` to `config.toml`. Output `Reading`s; nothing else changes.
* **Add a detector:** `@register_detector("name")` on a `Detector` subclass with `score(state) -> (0-100, reasons)`,
  then set `[detector] type = "name"`. Everything else in `[detector]` is passed to it as parameters.
* **Source priority:** `[state] obs_sources` lists which measurements win; the model fills in whatever they lack.

## Commands (run locally too)

`python3 run.py` + `collect` | `evaluate` | `baseline [--show]` | `site` | `status` | `estimate [--interval N]` |
`report` | `import-reports FILE|--url URL` | `verify` | `nws-stations` | `plugins`.
Needs Python 3.11+, nothing to install. Tests: `python3 -m unittest discover -s tests`.

## Know the limits

* Free model data is ~3 km at best. It can't see shade, trees or parked cars, so `susceptibility` per zone and your
  field reports carry that part; the Quintara & Fanning spot is only an observation that it tends to stay dry, so its report log is the check on whether that holds. A cheap sensor on your block beats any free feed.
* Thresholds are educated guesses. Expect to retune after ~20-30 field reports.
* Open-Meteo's best-match model over North America is NOAA's RRFS, replacing HRRR (feed retires 2026-11-30).
* Weather data: Open-Meteo.com (CC BY 4.0), NOAA/NWS, Iowa Environmental Mesonet. Streets: OpenStreetMap contributors (ODbL). Experimental, not safety advice.
