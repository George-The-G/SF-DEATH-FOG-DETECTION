# Guide 14: east wind directions in the live pipeline (switch `WIND_EAST`)

Status: built 2026-11-13 on branch `v3-east`, merged into `v3-dev`. Beta preview only; the public build is unchanged (switch off, bit for bit).

## What exists
- Library (PC, `C:\Users\George\deathfog-data\library`): wind fields for 22.5, 45, 67.5, 90, 112.5, 135 (east, validated, listed in `wind_dirs_east.json`) next to the 9 west directions 180..360. 0 is 360 (not a separate field).
- **157.5 is an accepted gap.** Two GPU runs failed (solver NaN at step 13000). Do not run it. The blend is linear in angle over the 45 degree gap 135..180 (`dirlib.weights`).
- Limit of the east fields: the grid ends in the middle of the Bay, so the inflow is clean open water; the East Bay hills are not represented (no wake). Street wind from the east fields is therefore the unobstructed one.

## What the switch does
`config.WIND_EAST` (public, False) and `config.BETA["wind_east"]` (True); the beta build reads only the latter, `build.run` sets `C.WIND_EAST` for the length of one build (like `NUDGE_ANCHORS`), then restores it.
With it on and `live_east.npz` present next to the static table:
1. `east.apply` replaces the direction list of the static table by the 15 directions and gives the new east directions all-zero `tracer` rows and zero `tr80` tiles. **Fog weight at east wind stays zero**: `wetness.onshore_weight` is already zero for wind from 0..180, and the zero rows make the blend safe for every angle. In-place / bay fog is guide 12.
2. The station wind vectors come from `live_east.npz` (15 directions) instead of `station_wind.npz` (9). The station-anchor wind fit (`anchors.py`, only with `NUDGE_ANCHORS`, which the beta has) carries directions in [180, 540) instead of [90, 450) and no longer clamps winds from 0..180 to 180 / 360.
3. `dirlib.weights` (used by the anchors, the tracer blend and the fog sheets) interpolates across the 157.5 gap and across north.
That is STREET WIND ONLY. The per-point wind ratio of the static table (from the 270 field) is not direction-dependent and was not touched.
Not applied (note in `status.json` notes and `wind_east.reason`): file missing, unreadable, or built for other static directions. The build then runs as without the switch.

## Files and assets
- `microclimate/live/east.py` (new), `dirlib.py` (`available(east=)`, `weights` wrap / gap), `live/anchors.py` (`carry_range`), `live/build.py`, `live/config.py`, `live/stationwind.py` (`--east`), `live/check_stations.py` (`compare_east`), `live/compare.py` (`--east`), `live_ci/refresh.sh` (downloads the extra asset).
- Release `live-static-v1`: **new asset `live_east.npz`** (2.2 KB: stations x 15 directions). `live_static.npz` and `station_wind.npz` are untouched, so the public build keeps working with the old assets. Without `live_east.npz` the beta build runs without east.
- Rebuild on the PC (CPU only): `python -m microclimate.live stationwind --east` writes `<data>/live/live_east.npz` (the 9 west rows are copied from `station_wind.npz`). Upload with `gh release upload live-static-v1 live_east.npz` (never delete the old assets). If `live_static.npz` is ever rebuilt with other directions, rebuild `live_east.npz` too (`static_dirs` inside is checked).
- `city.build_static`, `replay`, `stationwind` (without `--east`) still use the west set only (`dirlib.available(lib)` default); `wind_dirs.json` was not edited.
- First paint: nothing in `site/v3` changed; the beta `status.json` gains `wind_east` (and `beta.wind_east`). Public `status.json` has no new key.

## Checks
- `python -m pytest` (289 tests: 270 before + 19 in `tests_mc/test_east.py`, the beta settings test updated for the new key). The frozen-input hashes (`test_physics`, `test_anchors`) run with the switch off and are unchanged.
- `python -m microclimate.live compare --east` : same inputs, station anchors on, west library versus east fields (wetness difference).
- `check_stations.compare_east(now, 30, out)` (or `check --east-compare`): leave-one-station-out wind speed error at the in-grid anchors, all hours and hours with observed wind from 0..180, east off versus on. Only 3 stations are inside the 20 m grid (FTPC1, GGBC1, PXOC1): treat the numbers as a sanity check, not a calibration.
- Known small effect: the linear blend of the 135 and 180 vectors loses up to 8 percent of the speed at mid-gap (157.5), so the anchor fit may raise the inflow speed by that much there.

## Not done
Fog at east wind (guide 12); East Bay hills wake; a direction-dependent per-point wind ratio; the 157.5 field.
