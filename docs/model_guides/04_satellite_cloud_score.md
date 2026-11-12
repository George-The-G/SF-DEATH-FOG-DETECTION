# Guide 04: satellite cloud score

Goal: rate the model's low cloud and fog against GOES-18 automatically, every run, and keep the history. This is the stand-in for field checks.

## What is compared
`live/goes.py` already builds, per hour, a `LowCloud` object on the 2 km GOES grid over the bay: `low` (cloudy with top below `C.LOW_TOP_M`), `cloudy`, `valid`.
Three model fields are scored against `low`, all on the 30 lattice cells (about 3 km boxes) plus an upwind ocean box and a bay box:
1. `rrfs`: raw RRFS low cloud cover (fraction).
2. `source`: the fog source actually used after corrections (`s_series`), as a coast value. Note it is nudged with the same satellite data, so it measures consistency, not skill.
3. `fog_sheet`: the fog layer drawn on the page (the hourly tiles from `outputs.fog_frames`, averaged to each box).
Observed fraction per box = mean of `low` over valid pixels in the box.

## Rules
- An hour is not scorable for a box when more than 30 % of its pixels are cloudy with tops above `LOW_TOP_M` (high cloud hides the fog) or fewer than half are valid. Count these separately.
- Day and night are scored separately (sun elevation above or below 0) because the satellite cloud mask is weaker at night.
- Forecast skill needs earlier runs: read the lattice low cloud saved by the archive (guide 02 part B) for lead times 1, 3, 6, 12, 24 h. Until the archive has history, score the current run's past hours only.

## Numbers produced
For each field, lead time, day/night:
- mean absolute error of the fraction;
- yes/no at fraction >= 0.5: hits, misses, false alarms, correct clears; hit rate, false-alarm ratio, critical success index;
- timing: at the coast box, hours between modelled and observed arrival (first of two consecutive yes hours) and clearing, when both exist in the window;
- count of scorable hours.

## Changes
1. `live/goes.py`: `LowCloud.box_fraction(lon0, lat0, lon1, lat1)` returning (low fraction, high-cloud fraction, valid fraction).
2. `live/cloud_score.py`: builds the table for the current run; merges into a rolling `live/cloud_score.json` (last 14 days, daily rows plus a 24 h and 7 d summary; keep under 100 KB). Carried between runs like the state files (guide 02, `refresh.sh`).
3. `live/build.py`: call it after the forcing is built; never fatal.
4. `python -m microclimate.live cloudscore --since 14d`: prints the summary from the archive.
5. Satellite layer for the viewer: write the observed low-cloud field for each past hour as tiles in the same layout as the fog tiles (`sat0.png` ...), loaded on demand.

## UI (mockup first)
- A line in About or the status row: "Cloud match, last 24 h: NN % (N scorable hours)".
- A "Satellite low cloud" layer switch that draws the observed field for past hours, so model fog and satellite can be compared by eye.

## Tests
- `box_fraction` on a synthetic field.
- A perfect model scores hit rate 1 and error 0; an always-clear model has hit rate 0 and no false alarms.
- High-cloud hours are excluded and counted.
- Rolling file keeps 14 days and stays under the size limit.

## Done when
`cloud_score.json` is on the published site after two scheduled builds and the hand-back shows the first table.
