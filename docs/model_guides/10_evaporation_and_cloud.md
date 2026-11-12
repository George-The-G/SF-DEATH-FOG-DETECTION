# Guide 10: evaporation by vapour pressure deficit; total cloud in cooling (switches `vpd_evap`, `total_cloud`)

## Part A: `vpd_evap`
Problem: `evap = (K_EVAP_BASE + K_EVAP_WIND * wind) * (1 - RH)`. At the same RH, warm air dries a surface far faster than cold air.

Change in `wetness.step`:
- `es(t) = 0.6108 * exp(17.625 * t / (t + 243.04))` kPa (same Magnus constants as `magnus_rh`). Add as `sat_vp_kpa`.
- `vpd = max(es(temp) - es(dew), 0)`.
- `evap = (K_EVAP_BASE + K_EVAP_WIND * wind) * vpd / VPD_REF_KPA`, `VPD_REF_KPA = 1.40` (= es at 12 degC, so the new form equals the old one at 12 degC and the existing constants keep their meaning).
- When guide 08 (cloud base) is on, `temp` and `dew` are already the street-level values.

Tests: at 12 degC / 70 % RH new equals old within 1 %; at 25 degC / 70 % new is 2.2-2.3 times old; at 5 degC / 70 % new is 0.60-0.65 times old; RH 100 % gives 0.

## Part B: `total_cloud`
Problem: surface cooling uses `(1 - cloud_low / 100)`. Middle and high cloud also reduce night cooling.

Change:
- `live/forcing.py`: `forcing_fn` also returns `cloud` (total cover, already gridded as `CC`). Replay path: pass `ser["cloud_cover"]` in the scalar dict when present.
- `wetness.step`, when `"total_cloud" in P` and `"cloud" in f`: `eff = clip(cloud_low + CLOUD_HIGH_WEIGHT * max(cloud - cloud_low, 0), 0, 100)`, `CLOUD_HIGH_WEIGHT = 0.5` (first guess); use `eff` in place of `cloud_low` in the `dT` line only.

Tests: low 0 / total 100 gives half the clear-sky cooling; low 100 gives none; total missing falls back to the old formula.

## Hand-back
Compare report for each switch separately. Expect slower drying on cold nights and faster drying on warm afternoons from A; slightly less dew under high cloud from B.

Note: once guide 07 (pavement temperature) is on, evaporation and dew come from the surface energy balance and these two switches only affect streets where that model is off.
