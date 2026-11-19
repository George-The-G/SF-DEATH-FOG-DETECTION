# Promote the beta data layers to the public /v3/ page

Approved by the user 2026-11-13. Exactly this goes public: (a) the data layers UI (Settings regroup, Tap info off, sunshine default, slick/water chip lines and cards, station pins, satellite outline, readout) and (b) the stage 1 physics of the beta channel: clear-sky sun (GOES sunlight) + station anchors. NOT included: east wind, carried state, film, pavement, any other switch.

## How public and beta differ today
- `main` carries only the v2 site and `collect.yml`. Each run copies `site/v3`, `live_ci`, `microclimate` from the `v3-dev` branch (so code changes need no PR to main) and runs `live_ci/refresh.sh` (steps `continue-on-error`, off switch `DEATHFOG_LIVE=false`, static tables from release `live-static-v1`).
- Viewer: `?beta=1` loads `live_beta/` (built with `config.BETA`) and `beta_ui.js`; without it the page reads `live/` (built with `config.PHYSICS=()`, no anchors) and never loads `beta_ui.js`.
- `refresh.sh build` (every 30 min): public build, archive push, then the beta build on the same fetched inputs. `finalize.py` keeps the previous output on failure. The v2 status page at the Pages root is not touched by any of this.

## Design (smallest safe change)
1. `config.PROMOTED` = the approved set. `build --promoted` runs the public build with it and writes `"promoted": {...}` into `live/status.json`. Without the flag the build is byte-for-byte the old public build. `config.BETA` stays free to move on to the next stage (`?beta=1` keeps working as the preview channel, own folder, Beta tag).
2. `refresh.sh`: when `DEATHFOG_LIVE_PROMOTE=true` run `--promoted`; if that build fails (and not because RRFS / static tables are missing, which would fail the same way) run the old settings instead. Failure of both keeps the previous output as before. The previous public output is now fetched with `--extras` (sun files, stations.json, sunlight cache) so the 10-minute carry-only runs keep the layers. The beta build reuses the public GOES sunlight cache.
3. Viewer: the data layers turn on when `live/status.json` has `promoted` (or `?beta=1`). No flag = the old public page, so the fallback and the rollback restore the old page by themselves. No Beta tag or "not the public forecast" text on the public page; About gets a "Data layers" section with the same honest disclaimers ("modelled, not measured", "not a forecast guarantee"). The "Live (beta)" night entry reads "Live" when the layers are on.
4. `main`: `collect.yml` passes `DEATHFOG_LIVE_PROMOTE: ${{ vars.DEATHFOG_LIVE_PROMOTE != 'false' }}` to the live step and the live step timeout goes 12 -> 16 min (room for a fallback build). Unset in `refresh.sh` = off, so merging the v3-dev change alone changes nothing public; merging the main PR is the go-live.

## Rollback
- Fast: set repo variable `DEATHFOG_LIVE_PROMOTE` = false (next build run publishes the old settings and the page returns to the old UI; run `gh workflow run collect.yml` to not wait up to 30 min).
- Or revert the main PR (one revert).
- `DEATHFOG_LIVE=false` still switches the whole live step off.
