# Guide 11: drizzle (switch `drizzle`; needs guide 08)

Problem: thick stratus drizzles and wets streets below the cloud base. Model precipitation mostly misses it.

## Physics (first guesses, to be calibrated)
- Cloud thickness at the coast: `thick = max(fog_top - cloud_base_coast, 0)` (m), per step.
- Drizzle rate: `D = DRIZZLE_MAX * clip((thick - DRIZZLE_T0) / (DRIZZLE_T1 - DRIZZLE_T0), 0, 1) * cloud_presence`, with `DRIZZLE_MAX = 0.10` mm/h, `DRIZZLE_T0 = 200` m, `DRIZZLE_T1 = 500` m, `cloud_presence = sqrt(clip(cloud_low / CLOUD_FOG, 0, 1))` (no onshore factor: drizzle falls whatever the wind).
- Observed hours, from METAR present weather at KHAF, KSFO, KOAK:
  - any station reports drizzle (`DZ`) in the hour: `D = max(D, 0.05)`;
  - at least one station has a ceiling at or below 3000 ft and none reports `DZ`, `RA`, `BR` or `FG`: `D = 0.3 * D`.
  - Express as a correction to the model D and fade it with `C.FOG_E_FOLD_H` like the other corrections.
- Per street: drizzle reaches streets below the fog top; some evaporates below the base: `D_st = D * fog_top_ramp(top, z) * clip(1 - max(base - z, 0) / 600, 0.2, 1)`.
- Wetting: `add_drizzle = K_DRIZZLE * D_st * porous`, `K_DRIZZLE = 150` W units per mm (0.05 mm/h adds 7.5 per hour; compare fog at up to 20-30). With guide 05 (water film) the rate enters in mm directly.
- Do not add drizzle where model precipitation already exceeds it: use `max(precip_term, drizzle_term)` contributions, not the sum.

## Changes
1. `live/fetch.py` `metar()`: keep the present-weather string as `wx` (the aviationweather.gov JSON field is `wxString`; confirm on a live response and fall back to parsing `rawOb`).
2. `live/forcing.py`: build the `D` series (10-minute), `info["drizzle"] = {hours_with_dz, mean_rate}`; `forcing_fn` returns `drizzle` (scalar per step) when the switch is on.
3. `wetness.step`: add the term; `parts["drizzle"]`; `simulate` accumulates `drizzle_total`.
4. Constants in `wetness.py`.

## Tests
- Thickness 150 m gives 0; 350 m gives half of `DRIZZLE_MAX`; 600 m gives `DRIZZLE_MAX`.
- `-DZ BR` in the hour raises D to at least 0.05; ceiling with clear weather codes scales it by 0.3.
- A street above the fog top gets none; a street 600 m below the base gets 20 % of the rate.
- With rain at 2 mm/h the total wetting equals the rain-only case.

## Hand-back
Compare report plus the count of drizzle hours in the last 30 days of METARs (use `live check --since 30d` data) so the user can judge whether the rate is plausible.
