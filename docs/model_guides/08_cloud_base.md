# Guide 08: cloud base and elevation-adjusted temperature (switch `cloud_base`)

Problem: a street counts as in fog whenever it is below the fog top. Streets below the cloud base are under the cloud, not in it.

## Physics
- Below cloud, a lifted parcel's T falls 9.8 K/km and its dew point about 1.8 K/km, so cloud base is about 125 m above the surface per kelvin of (T - Td).
- Base above sea level at a street: `base = zm + 125 * max(T - Td, 0) + base_offset`, where T, Td are the nudged 2 m values interpolated from the lattice and `zm` is the model terrain height at that spot (interpolate `rrfs["elev"]` with the same lattice weights, once).
- `base_offset` (m, one value per hour, citywide) corrects the formula with airport ceilings: for each of KHAF, KSFO, KOAK with a ceiling at or below 3000 ft in that hour, `obs_base = ceil_ft * 0.3048 + station_elev`, `lcl = station_elev + 125 * (T_obs - Td_obs)` from the same report, `diff = obs_base - lcl`. Take the median over stations, clip to +-300 m, 3-hour rolling median, fade into the forecast with `fade_series(..., C.FOG_E_FOLD_H, "add", 0.0)`. Station elevations: KHAF 20 m, KSFO 4 m, KOAK 3 m (`C.METAR_ELEV_M`).
- In-cloud weight for a street at elevation z: `wb = smoothstep((z - base) / BASE_RAMP_M + 1)`: 1 at or above the base, falling to 0 at `BASE_RAMP_M = 60` m below it (first guess; the base estimate is good to about +-100 m, and ground fog with T = Td must give full weight). Final weight = `wb * fog_top_ramp(top, z)`.
- The coast source `s(t)` currently includes `clip(1 - gap / GAP_FOG, 0, 1)`. With this switch that factor is dropped (it would count the gap twice): `s = sqrt(clip(cloud_low / CLOUD_FOG, 0, 1)) * onshore`, then the existing GOES/KHAF correction.
- Street air temperature for dew and evaporation: `T_st = T - 0.0098 * (z - zm)` below base, `-0.006` per metre between base and top, held constant above the top; `Td_st = min(Td - 0.0018 * (z - zm), T_st)`. Clip the height difference to +-400 m.

## Changes
1. `wetness.py`: `fog_inflow(ta, td, cloud_low, wdir, use_gap=True)`; constants `BASE_RAMP_M`, `LCL_M_PER_K = 125.0`.
2. `fogtop.py`: `cloud_base_ramp(base, z, half_width=None)`.
3. `live/forcing.py` (when `"cloud_base" in physics`):
   - `zm_pts = interp_at(rrfs["elev"], idx, w4)`.
   - hourly `base_offset` as above -> 10-minute series; store `info["cloud_base"] = {offset_last, stations_used, hours_with_ceiling}`.
   - `s_model = wetness.fog_inflow(..., use_gap=False)`.
   - `forcing_fn` returns the adjusted `temp`, `dew` and a new key `cloud_base` ([N], m above sea level). It needs `z` of the points: add a `z` argument to `build` and pass `S["z"]` from `live/build.py`.
   - Return the coast-level base series as `F["cloud_base_coast"]` (used by guide 11).
4. `wetness.simulate`: after `f = forcing_fn(i, f)`, `if "cloud_base" in P and "cloud_base" in f: local = local * fogtop.cloud_base_ramp(f["cloud_base"], pts["z"])`. Add `diag["in_cloud_hours"]` (sum of the weight * dt).
5. Replay path: untouched (switch is live-only for now).

## Tests
- Base 200 m, top 400 m: street at 50 m weight 0; at 170 m 0.5; at 200 m and 300 m 1; at 500 m < 0.05.
- T = Td everywhere: every street well below the top has weight 1 (base at ground).
- Weight is non-decreasing in z up to the top and non-increasing in base.
- `base_offset` with one station reporting a 600 ft ceiling and T - Td = 1 K gives 183 + elev - (elev + 125) = +58 m.
- Switch off: identical to before.

## Hand-back
Compare report. Expect less wetness on low flat streets on stratus nights and little change on hill tops; if hill tops also drop sharply, stop and report.
