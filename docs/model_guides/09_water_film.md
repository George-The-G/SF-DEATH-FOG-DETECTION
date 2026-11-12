# Guide 09: water film in millimetres (switch `film`; after guides 05 and 06)

Problem: wetness is an index with made-up rates. It cannot be compared with a measurement, and rain just pins it at 100.

## Design
Carry a second state `F` = water film on the road in mm, alongside nothing else: with the switch on, `F` is the model state and the 0-100 value shown on the page is derived from it. With the switch off nothing changes.

All rates in mm/h. Values are literature-order first guesses, to be calibrated (guide 02):
- Fog deposition: `dep = FOG_LWC * (V_SETTLE + C_TURB * wind) * 3.6 * local_fog * porous` mm/h, with `FOG_LWC = 0.2` g/m3, `V_SETTLE = 0.02` m/s, `C_TURB = 0.004` (so calm fog gives about 0.014 mm/h, 5 m/s about 0.03 mm/h). `local_fog` is the existing 0-1 exposure.
- Dew: `dew = DEW_MM_PER_K * max(Td - Ts, 0) * porous`, `DEW_MM_PER_K = 0.01`, capped at `DEW_MAX = 0.04` mm/h. (`Ts` from the existing cooling formula until a pavement temperature model exists.)
- Rain and drizzle: rate in mm/h directly, times `porous`.
- Evaporation: `evap = EVAP_A * (1 + EVAP_B * wind) * vpd` with `EVAP_A = 0.108` mm/h per kPa, `EVAP_B = 0.54` s/m (Penman's wind function, per hour), `vpd` in kPa from guide 05.
- Sun drying: `sun = SUN_MM_PER_KW * sw_eff` with `SUN_MM_PER_KW = 0.8` mm/h per 1000 W/m2 (absorbed energy over latent heat, times the usual 0.6 share that goes to evaporation at 15 degC); `sw_eff` is the direct-plus-diffuse sum from guide 08, or the old `GHI * lit + 0.125 * GHI` when that switch is off.
- Storage and runoff: `cap = CAP_MM[surface] * (1 - min(|slope|, 0.15) / 0.30)`; `CAP_MM` = 0.5 (asphalt, default), 0.3 (concrete), 0.2 (porous surfaces: they drain). After each step `F = clip(F, 0, cap)`; the excess is runoff (tracked in `diag`).
- Displayed index: `W = 100 * (1 - exp(-F / FILM_REF))`, `FILM_REF = 0.05` mm (0.02 mm shows 33, 0.05 shows 63, 0.2 shows 98).

## Changes
1. `wetness.py`: `step_film(F, ..., physics)` next to `step`; `simulate` picks it when `"film" in P`, keeps `out` as the derived index (so every writer and the viewer keep working), and adds `diag["film_mm"]` at output frames (float16) plus per-term totals in mm.
2. Surface class per point: reuse the OSM surface lookup behind `porous_factors` in `city.py` to export a small integer class (asphalt/concrete/porous/other) into `live_slope.npz` from guide 08 (same publishing note). Without the file every street is asphalt.
3. `live/outputs.py`: add `film_ref_mm` and the term totals to `index.json`; nothing else changes on the page.
4. Archive (guide 02): store `F` for the sample points.

## Tests
- Mass balance: with no losses, 1 h of 0.2 mm/h rain on asphalt gives F = 0.2; 10 h gives F = cap and runoff = the rest.
- Calm fog for 3 h with exposure 1 gives 0.04-0.05 mm.
- Drying: F = 0.1 mm, vpd 0.5 kPa, wind 2 m/s, no sun dries in 0.8-1.0 h; in 800 W/m2 direct sun it dries in under 10 minutes.
- Index mapping values above; F = 0 gives 0.
- Switch off: identical to before.

## Hand-back
Compare report in both index and mm. State clearly that the thresholds for "slick" on the page still come from the index and are not calibrated.
