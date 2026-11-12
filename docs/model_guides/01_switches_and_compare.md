# Guide 01: physics switches and compare report

Goal: every later model change can be turned on or off by name, and old versus new can be compared on the same inputs.

## Changes
1. `microclimate/wetness.py`
   - `simulate(..., physics=None)`: `P = frozenset(physics or ())`. Pass `P` to `step(..., physics=P)`.
   - `KNOWN_PHYSICS = ("cloud_base", "lag_uniform", "vpd_evap", "total_cloud", "drizzle", "insitu_fog", "sun_slope", "film", "grease")`. Unknown names raise `ValueError`.
   - No behaviour change in this guide. Later guides add `if "name" in P:` branches only.
2. `microclimate/live/config.py`: `PHYSICS = ()` (what the scheduled job runs). `MODEL_VERSION` unchanged.
3. `microclimate/live/forcing.py`: `build(..., physics=())`, stored as `info["physics"]`.
4. `microclimate/live/build.py`: `run(..., physics=None)` uses `C.PHYSICS` when None; passes it to `forcing.build` and `wetness.simulate`; writes `physics` into `status.json`.
5. `microclimate/live/__main__.py`:
   - `build --physics a,b` (comma list; `--physics none` for empty).
   - New command `compare --physics a,b [--static PATH] [--out reports/physics_compare]`: fetch once, build forcing and simulate twice (current `C.PHYSICS` versus the given set) with the same fetched inputs and the same `now`. Write `compare_<UTC time>.md` and `.json`.
6. Compare report contents (both runs, and the difference):
   - Mean and 90th percentile of W over all frames, split by street elevation band (0-50, 50-150, 150-300, >300 m) and by class (district rows, city rows).
   - The same for observed hours only and forecast hours only.
   - Share of street-hours with W >= 20 and W >= 50.
   - Per-term totals from `diag` (`fog_total`, `dew_total`, `rain_total`, `evap_total`, plus any new terms).
   - Ten largest per-street differences with x, y, z.

## Tests (tests/test_all.py)
- `simulate(physics=None)` and `simulate(physics=())` equal the pre-change output on a fixed synthetic case (store a small hash of the array before editing).
- Unknown switch name raises.
- `compare` with identical sets reports zero difference.

## Done when
Suite green; replay night outputs byte-identical (re-run one night and diff); a compare report with an empty switch list shows all zeros.
