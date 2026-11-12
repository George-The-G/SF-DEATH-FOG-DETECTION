# Guide 02: state carried between runs; run archive

Problem: every live run starts 6 h before 18:00 PT with dry streets (`W0 = 0`) and no memory. Rain from yesterday, or heat stored in the road, is lost. Nothing is kept for later scoring.

## Part A: carried state
Design: an "anchor" state valid at the window start `t0`.
- The window start moves once a day (at 18:00 PT, `t0` becomes 12:00 PT of that day).
- During each run, when the simulation passes `t0 + 24 h` and that time is at or before the observed/forecast boundary, save the full model state at that step as the next anchor.
- Each run begins from the anchor whose time equals its `t0`. If none exists, or it is from another `MODEL_VERSION`/physics set, cold start (dry, pavement at the mean air temperature of the previous 24 h) and add the flag `cold start` to `status.json`.
- Observed hours are re-simulated every run from the anchor with the newest nudged forcing, so late observations still improve the past frames.

Changes:
1. `microclimate/live/state.py`: `save(path, time, physics, model_version, arrays)` and `load(path)`. Arrays today: `W` (float16). Later guides add `film` (05), `ts`, `tdeep` (07), `dry_days`, `rain_since_wash` (05, 13). Compressed npz; assert under 3 MB.
2. `wetness.simulate`: `state0=None` (dict of arrays replacing `W0`), `save_at=None` (step index) returning `diag["state_at"]`. No behaviour change when both are None.
3. `live/build.py`: look for `state_<t0 as yyyymmddThh>.npz` in the output folder; load; pass `state0`; compute `save_at`; write `state_<t0+24h>.npz`; keep the two newest, delete older. Record `state: {start: "anchor"|"cold", anchor_time, saved}` in `status.json`.
4. `live_ci/refresh.sh` (on `v3-dev`): the state files must survive. In `build` mode download the previous run's `state_*.npz` from the published site before building (list them in `index.json` under `state_files` so the names are known). In `reuse` mode carry them like the other live files. A missing file means a cold start, never a failure.
5. State files are public on the Pages site. They contain only model values.

Tests:
- Run A over 0-48 h in one go; run B over 0-24 h saving state, then 24-48 h from it: identical output for 24-48 h.
- Wrong model version or physics set: cold start with the flag.
- `save_at` beyond the observed boundary: nothing saved.

## Part B: archive for scoring
1. `microclimate/live/archive.py`: fixed sample (all route points, every 50th district row, every 200th city row). Per run save `run_<UTC yyyymmddThhmm>.npz`: sample index, model outputs at the sample for all frames (uint8), frame times, observed boundary frame, coast series, `s_series`, fog top, cloud base when present, lattice low cloud (for guide 04), station table (guide 03), physics, model version. Assert under 200 KB.
2. `live_ci/refresh.sh build`: after a successful build, add the file to branch `live-archive` under `yyyy/mm/dd/` with a normal commit and push (the job already has `contents: write` and pushes the `data` branch the same way; reuse that remote URL). Failure is logged and ignored.
3. Weekly: delete files older than 120 days in a normal commit.
4. Repo variable `DEATHFOG_ARCHIVE=false` turns it off.

Tests: archive round trip; size assertion.

## Part C (later, when field data exists)
`reports/ground_truth.csv` and `python -m microclimate.live score` as in revision 1 of this guide. Not needed now.

## Done when
Two consecutive builds on the runner show `state.start = "anchor"` after the daily window change, and two archive files exist on `live-archive`.
