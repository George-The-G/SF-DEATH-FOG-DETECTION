# Guide 13: dry-spell and humidity grease factors (switch `grease`; needs guide 05)

Problem: slippery is not the same as wet. The first wetting after a dry spell is the slickest, and near the coast high humidity alone makes roads greasy. Both effects are well known but the sizes below are guesses. Keep this switch off for the public page until field reports exist.

## Part A: dry spell
1. `live/fetch.py`: `rain_history()`: one Open-Meteo request for a single city point, `daily=precipitation_sum`, `past_days=30` (use the default best-match model; the archive of the RRFS feed is too short). Failure is non-fatal.
2. `dry_days` = days since the last day with at least 1 mm, capped at 30. During the simulated window, reset it to 0 once accumulated rain in the window passes 2 mm (the road has been washed) and count up again afterwards.
3. Displayed slip (guide 05) for thin films: `slip_shown = min(100, slip * (1 + GREASE_DRY * clip(dry_days / 14, 0, 1)))` (only matters for thin films, since slip is already near 100 above 0.05 mm); `GREASE_DRY = 0.5`.

## Part B: humidity film
1. Distance to the ocean per point: `x - X_WEST` is close enough (the Pacific is the west edge of the grid); `salt = exp(-dist_m / 2000)`.
2. Minimum film from humidity: `F_min = HYGRO_MM * clip((RH_st - 0.75) / 0.20, 0, 1) * salt * clip(dry_days / 7, 0, 1)`, `HYGRO_MM = 0.004` mm. Apply as a floor on the film used for the displayed index only (do not add it to the water balance).

## Changes
- `live/forcing.py`: carry `dry_days` (scalar series over the window) in `F`; `info["dry_days_start"]`.
- `wetness.simulate`: apply A and B when deriving the output index; keep the raw film in `diag`.
- `status.json`: `dry_days`.

## Tests
- 14 dry days multiplies a thin-film index by 1.5; a thick film (0.3 mm) is unchanged.
- 3 mm of rain inside the window resets the factor to 1 for later frames.
- RH 95 % at the west edge after 7 dry days shows slip of about 18 from part B alone (about 23 with part A) with no other water; 5 km inland it is under 2.
- Rain-history fetch failure: factors are 1 and a flag is written.

## Hand-back
Compare report. Say plainly that both factors are unverified.
