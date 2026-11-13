# Guide 07: pavement temperature with heat memory (switch `pavement`; needs guides 02, 05, 06)

User's design: the road starts cool and stores heat as the live runs go on. Today the surface temperature is recomputed from the air every step with no memory.

No road-temperature measurements exist, so the constants are textbook values and the result is unverified. Station air temperature (guide 03) is the only indirect check.

## State (per street point, carried between runs)
`ts` = surface temperature, `tdeep` = temperature of the layer below (roughly the daily mean). Cold start: both equal the mean air temperature of the previous 24 h; the flag `pavement spin-up` stays in `status.json` until three days of anchors exist.

## Energy balance (W/m2), each 10-minute step
- Absorbed sun: `(1 - ALBEDO) * S`, `S` from guide 06, `ALBEDO = 0.12`.
- Sky longwave: `L_clear = eps_a * SIGMA * Ta_K^4`, `eps_a = 1.24 * (e_a_hPa / Ta_K)^(1/7)`; with cloud `L_sky = L_clear * (1 + 0.22 * c^2)`, `c` = effective cloud fraction (as in guide 10 part B, 0-1). Streets see walls too: `L_down = svf * L_sky + (1 - svf) * EMISS * SIGMA * Ta_K^4`.
- Emitted: `EMISS * SIGMA * Ts_K^4`, `EMISS = 0.95`. Net longwave = `EMISS * L_down - emitted`.
- Sensible heat to the air: `H = h_c * (ts - Ta)`, `h_c = 5.8 + 4.1 * wind` (wind = local street wind, m/s).
- Latent heat: `LE = h_c * (es(ts) - e_a) / GAMMA`, `GAMMA = 0.066` kPa/K, vapour pressures in kPa. Positive = evaporation, only while `F > 0`; negative = condensation (dew), always allowed. Water change: `dF = -LE / 2.45e6 * 3600 * dt_h` mm (this replaces the dew, evaporation and sun-drying terms of guide 05 for streets where the switch is on; fog deposition, rain, drizzle, storage and runoff stay).
- Net flux `G = absorbed + net longwave - H - LE`.
- Force-restore: `dts/dt = 2 * G / CD - OMEGA * (ts - tdeep)`, `dtdeep/dt = (ts - tdeep) / 86400`, `OMEGA = 7.27e-5` 1/s, `CD = 2.35e5` J/m2/K (asphalt: heat capacity 2.0e6 J/m3/K, conductivity 1.0 W/m/K, damping depth 0.117 m). Concrete: `CD = 2.9e5`.
- Numerics: two 5-minute sub-steps; limit the change of `ts` to 3 K per sub-step; if `LE` would remove more water than `F`, scale it to exactly dry the surface.

## Changes
1. `wetness.py`: `step_pavement(...)` used inside `step_film` when `"pavement" in P`; constants above.
2. `live/state.py`: add `ts`, `tdeep` (int16, 0.01 K).
3. `live/forcing.py`: nothing new beyond guide 06 and the total-cloud series.
4. `live/outputs.py`: on-demand `tsurf*.u8` (`round((ts + 10) * 4)`, so -10 to 53.75 C in 0.25 K steps) and index fields. UI for it is optional (mockup first).
5. Guides 10 (evaporation, cooling) stay as the fallback when this switch is off.

## Sanity targets (tests on synthetic forcing)
- Clear calm summer day, open flat asphalt, air 15-25 C, 900 W/m2 at noon: `ts` peaks 20-30 K above air, 1-2 h after solar noon.
- The following clear night: `ts` stays above air temperature until at least midnight, and ends the night within +3 / -2 K of it.
- Overcast, windy: `ts` within 2 K of air all day.
- A shaded street stays cooler than a sunlit one by day and the gap closes by morning.
- Energy closure: stored heat change equals the summed fluxes within 2 %.
- Dew appears only when `ts` is below the dew point, and dew plus evaporation agree in sign with `LE`.
- Two half runs through a saved state equal one full run (guide 02).

## Hand-back
Compare report, the sanity table, and how often dew occurs with the switch on versus off over the archived days. State plainly that it is unverified against road measurements.

## Implementation notes (built, switch `pavement`, off in `C.PHYSICS`)
- `wetness.check_physics` rejects `pavement` without `sun_clear` and `film` (so `simulate`, `build --physics` and `compare` all raise a clear error). `step_pavement` (sub-steps of `PAV_SUBSTEP_MIN` = 5 min) is called from `step_film(..., pav=...)`; the dew, evaporation and sun-drying terms of guide 05 are not used for those streets (the `sun` part is 0, `sw_eff` = S of guide 06 is absorbed as `(1 - 0.12) * S`). Condensation is scaled by `porous`; evaporation by the water that is there (dries exactly). Constants are textbook values defined after the `CONSTANTS` snapshot so `index.json` is unchanged with the switch off.
- Effective cloud for the sky longwave: low cloud plus `CLOUD_HIGH_WEIGHT` (0.5) of the extra total cloud, as in guide 10 part B (`total_cloud` itself is not built; `forcing_fn` returns `cloud` only when `pavement` is on).
- State: `ts`, `tdeep` are added to the saved state only with the switch on, as int16 in 0.01 K (`state.CENTI`), plus `pav_days` (length of the carried chain). `status.json` notes `pavement spin-up` until the window starts from an anchor with `pav_days >= C.PAV_SPINUP_DAYS` (3); `status.pavement` has the details. With `CARRY_STATE = False` every run is a cold start, so the flag stays.
- Cold start: the mean RRFS air temperature of the 24 h before the window start t0 (RRFS carries about 2 days of past hours, so it is normally complete), per lattice cell, interpolated to the street points (`forcing.pavement_cold_start`). If fewer than 24 past hourly values exist, the first `C.HISTORY_H` (6) hours of the window fill up the 24 h. `simulate(ts0=...)` takes it; without it the mean of the first 24 h of the series is used.
- Output: on-demand `tsurf{d}.u8` (`round((ts + 10) * 4)`) and `fields.tsurf` in `index.json`; no UI. `compare --base a,b --physics c,d` runs two explicit sets on the same inputs; the report adds road temperature and dew frequency.
