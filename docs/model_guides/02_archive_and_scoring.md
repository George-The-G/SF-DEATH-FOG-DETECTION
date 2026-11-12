# Guide 02: run archive and scoring

Goal: keep what each live run predicted so it can be scored later against field reports and loggers. Without this no change can be shown to help.

## Part A: archive (needs a PR to main; get user approval before opening it)
1. `microclimate/live/archive.py`, `write(out_dir, W, F, S, status)`:
   - Sample set: all route points, plus every 50th district row, plus every 200th city row (fixed indices; save them once as `sample_idx`).
   - Save `run_<UTC yyyymmddThhmm>.npz` (compressed): `sample_idx`, `W` for the sample at all output frames as uint8, frame times, `observed_until_frame`, the coast series (T, Td, wind, direction, low cloud, precipitation, shortwave), `s_series`, `fog_top`, cloud base when guide 03 is on, and `physics`, `model_version`.
   - Target size under 150 KB per run. Assert it.
2. Workflow step after the live build: check out branch `live-archive` (orphan, created on first run) into a temp folder, add the file under `yyyy/mm/dd/`, commit, push (normal push, no force). `continue-on-error: true`.
3. Retention: a weekly job step deletes files older than 120 days in a normal commit. No history rewriting.
4. Guard with repo variable `DEATHFOG_ARCHIVE` (default on).

## Part B: ground-truth table
1. `reports/ground_truth.csv` on `v3-dev`: `time_utc, lat, lon, state (dry|damp|slick|wet), source (form|logger|manual), note`.
2. `python -m microclimate.live truth pull`: read the existing Google Form response CSV (the URL is the repo variable `DEATHFOG_FORM_CSV_URL`, already used by v2; see `reports.py` on main for the column matching) and append new rows. Zone-only reports get the zone centre and `note=zone`.
3. Logger files (later): same CSV columns, `source=logger`.

## Part C: scoring
`python -m microclimate.live score --since 30d`:
- For each truth row, find the archived runs whose observed frames cover its time; take the nearest sample point within 150 m (else skip and count it); read W.
- Also score forecast lead times 3, 6, 12, 24 h from older runs.
- Output `reports/score_<date>.md`: counts, confusion table for wet (state in slick|wet) versus W >= 20 and W >= 50, hit rate, false-alarm rate, Brier score using W/100, mean onset timing error where a dry-to-wet pair exists. Split by physics set and model version.

## Tests
- Archive round trip (write, read, values equal after uint8 rounding).
- Scoring on a synthetic truth file gives the expected confusion table.

## Done when
Two scheduled runs have added files to `live-archive`; `score` runs without truth rows and says so plainly.
