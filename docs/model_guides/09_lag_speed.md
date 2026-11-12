# Guide 09: fog-front lag speed (switch `lag_uniform`)

Problem: `lag_for` in `wetness.simulate` divides distance by `wmean * wr`, where `wr` is each street's local wind ratio (down to 0.25). A sheltered street gets the fog up to four times later than the street next to it. The front travels with the wind above the roofs.

## Change
1. `wetness.py`: constant `LAG_SPEED_FACTOR = 1.3` (first guess: wind at about 80 m over the 10 m wind, onshore flow).
2. In `simulate`, when `"lag_uniform" in P`: `lag_for(mdir_, wmean_)` uses `np.maximum(wmean_ * LAG_SPEED_FACTOR, WIND_LAG_MIN)` as one scalar for all points (no `wr`). Everything else (cap `LAG_MAX_H`, block means) unchanged.
3. Optional, only if a one-line request shows Open-Meteo serves `wind_speed_80m` for the RRFS model: add it to `SURFACE_VARS`, carry the coast mean as `ser["wind_speed_80m"]`, and use it instead of `wind * LAG_SPEED_FACTOR`. If it is not served, keep the factor and note it in the hand-back.

## Tests
- Two points with the same x, y and `rel_wind` 0.25 and 1.0 get the same lag with the switch on, and lags differing by 4x (before the cap) with it off.
- Lag at the upwind edge is 0; lag grows linearly with distance; cap holds.

## Hand-back
Compare report plus the distribution of lag (minutes) before and after. Expect onset times in sheltered streets to move earlier by up to the old cap.
