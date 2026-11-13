# Guide 12: in-place fog for any wind direction (switch `insitu_fog`; needs guide 08)
Note (guide 14): the east wind fields (22.5..135, `WIND_EAST`) are in the live build for street wind only. Their tracer rows are zero and `wetness.onshore_weight` is zero for wind from 0..180, so east / offshore wind still gives no fog. This guide is where that changes; 157.5 is an accepted gap of the library.

Problem: `onshore_weight` is zero for wind from 0-180 degrees, so winter fog from the bay side and fog that forms in place never wet a street.

## Physics
Keep the advected term as it is. Add a second fog source that does not use wind direction or the tracer:
- Saturation at the street: `sat = clip(1 - (T_st - Td_st) / GAP_INSITU, 0, 1)`, `GAP_INSITU = 1.0` K, with the street-level values from guide 08.
- Fog evidence `ev` in 0..1:
  - Model: RRFS `visibility` (already in `SURFACE_VARS`; grid it like the other variables): `ev_model = clip((5000 - vis_m) / 4000, 0, 1)` per lattice point, interpolated to streets.
  - Observed hours: station evidence from KSFO, KOAK, KHAF visibility, `ev_stn = clip((3 - vis_mi) / 2, 0, 1)`; hourly mean over stations with data; correction `0.5 * (ev_obs - ev_model_at_stations_mean)` added citywide and faded with `C.FOG_E_FOLD_H`. Clip the result to 0..1.
- `insitu = sat * ev * fog_top_ramp(top, z)`.
- Street fog exposure: `local = max(local_advected, insitu)`. Deposition formula unchanged.

## Changes
1. `live/forcing.py`: grid `visibility`; build the evidence correction; `forcing_fn` returns `fog_ev` ([N]) when the switch is on; `info["insitu"] = {hours_with_station_vis, mean_ev}`.
2. `wetness.simulate`: after the cloud-base step, `if "insitu_fog" in P and "fog_ev" in f:` compute `sat` from `f["temp"]`, `f["dew"]` and apply the `max`. Track `diag["insitu_share"]` = share of fog deposition that came from the in-place branch.
3. Constant `GAP_INSITU` in `wetness.py`.

## Tests
- Wind from 90 degrees, T = Td, visibility 300 m: fog deposition > 0 with the switch on, exactly 0 with it off.
- Wind from 270 degrees with visibility 10 km: result equals the switch-off result.
- Visibility 300 m but T - Td = 3 K at the street: no in-place fog.
- Street above the fog top: none.

## Hand-back
Compare report, plus `insitu_share` for the current run and for a re-run of the `live check` period if cached inputs exist. If RRFS visibility is missing or constant in the response, stop and report (the model term then has no basis).
