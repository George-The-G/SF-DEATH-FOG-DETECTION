# Guide 05: slippery versus water depth, rain tracking (switch `film`; needs guide 02)

User's rule: once a surface is wet it is fully slippery. Water beyond that does not make it more slippery; it only decides how long drying takes. Rain is tracked the same way.

## State
`F` = water on the road in mm, per street point, carried between runs (guide 02).

## Rates (mm/h; literature-order first guesses)
- Fog deposition: `FOG_LWC * (V_SETTLE + C_TURB * wind) * 3.6 * local_fog * porous`, `FOG_LWC = 0.2` g/m3, `V_SETTLE = 0.02` m/s, `C_TURB = 0.004` (calm fog about 0.014 mm/h; 5 m/s about 0.03 mm/h).
- Dew: `DEW_MM_PER_K * max(Td - Ts, 0) * porous`, `DEW_MM_PER_K = 0.01`, at most `DEW_MAX = 0.04` (until guide 07 replaces it).
- Rain and drizzle: the rate itself, times `porous`.
- Evaporation: `EVAP_A * (1 + EVAP_B * wind) * vpd`, `EVAP_A = 0.108` mm/h per kPa, `EVAP_B = 0.54` s/m, `vpd = es(T) - es(Td)` in kPa (`es` as in guide 10).
- Sun drying: `SUN_MM_PER_KW * sw_eff`, `SUN_MM_PER_KW = 0.8` mm/h per 1000 W/m2; `sw_eff` from guide 06 when on, else `GHI * lit + 0.125 * GHI`.
- Storage: `cap = CAP_MM[surface] * (1 - min(|slope|, 0.15) / 0.30)`, `CAP_MM` = 0.5 asphalt (default), 0.3 concrete, 0.2 porous. Water above `cap` runs off.

## Outputs per street and frame
1. `slip` (0-100) = `100 * (1 - exp(-F / FILM_SLICK))`, `FILM_SLICK = 0.02` mm. So 0.01 mm shows 39, 0.02 shows 63, 0.05 shows 92, 0.1 and above show 99-100. This is what colours the streets (it replaces today's index in the same files, so the viewer works unchanged).
2. `depth` = `F` in mm.
3. `dry_in` = hours until `F` reaches 0.005 mm, read from the simulated future frames of the same run (not an estimate from the current rate); "not within window" when it never does.
4. `cause` = the largest source over the previous 3 hours: fog, dew, drizzle or rain.
5. Rain tracking, per street: `rain_mm_24h`, `hours_since_rain` (rate >= 0.1 mm/h), and `rain_since_wash` for guide 13. Carried in the state.

## Files
- `day*.u8` keeps its layout and now holds `slip`.
- New on-demand files `depth*.u8` (uint8 = `round(255 * log1p(F / 0.01) / log1p(1.0 / 0.01))`, so 0.01 mm resolution near zero and 1 mm at 255) and `dry*.u8` (hours, 255 = not within window).
- `index.json`: `film: {slick_mm, depth_scale, files}`. First-paint size must not change.

## Changes
1. `wetness.py`: `step_film(...)` next to `step`; `simulate` uses it when `"film" in P`, returns `out` = slip and `diag["film_mm"]`, term totals in mm, runoff.
2. Surface class and slope per point come from the extra static file of guide 06; without it every street is flat asphalt.
3. `live/outputs.py`: write the new files and index fields. `live/state.py`: add `film` and the rain trackers.

## UI (mockup first)
Tooltip and route line: "Slippery" (from `slip`), "Water 0.08 mm", "Dries in about 3 h", "From: fog". Legend wording. A depth view is optional.

## Tests
- Mass balance: 1 h of 0.2 mm/h rain on flat asphalt gives 0.2 mm; 10 h gives `cap` with the rest as runoff.
- Calm fog, exposure 1, 3 h: 0.04-0.05 mm, slip 87-92.
- Drying: 0.1 mm, vpd 0.5 kPa, wind 2 m/s, no sun: dry (below 0.005) in 0.8-1.0 h; with 800 W/m2: under 10 minutes.
- Slip values above; slip is identical for 0.3 mm and 0.6 mm (both 100) while `dry_in` differs.
- `dry_in` equals the first later frame below 0.005 mm.
- Switch off: identical to today.

## Hand-back
Compare report in slip and in mm. State that `FILM_SLICK` and the rates are unverified.
