# Guide 08: slope-aware sunlight (switch `sun_slope`)

Problem: drying uses `K_SUN * GHI * lit + K_DIFF * GHI`. It ignores the road's slope and aspect, and uses global irradiance for the direct beam. Trees are not covered here (see README, hard items).

## Physics
- Road surface normal from the street's bearing `b` (degrees, direction of travel) and along-street slope `s` (rise over run, positive uphill along `b`): `n = (-s * sin b, -s * cos b, 1) / sqrt(1 + s^2)` in (east, north, up).
- Sun vector from azimuth `az` and elevation `el`: `(cos el * sin az, cos el * cos az, sin el)`.
- `cos_inc = max(n . sun, 0)`.
- Split global irradiance into direct normal (DNI) and diffuse (DHI). First try Open-Meteo `direct_normal_irradiance` and `diffuse_radiation` for the RRFS model (one test request). If either is missing, use `pvlib.irradiance.erbs(ghi, zenith, times)` (pvlib is already a dependency).
- New drying term: `sun = K_SUN / 1000 * (DNI * cos_inc * lit + DHI * svf)`. `K_DIFF` is not used with the switch on.

## Changes
1. Static data: the live table has no slope or bearing. In `city.py`, export `bearing` and `slope_pct` for every row (district and city) into a small extra file `live_slope.npz` (float16; about 1 MB). Check the sign convention of `slope_pct` against the DEM on three known streets (one each facing north, south, west) and record the check in the hand-back. Clip |slope| to 35 %.
2. Publishing the file: upload it as an extra asset on the `live-static-v1` release and make the workflow download it. That is a change on main: prepare the PR, get user approval before opening it. Until then the switch falls back to flat roads when the file is absent (log a flag).
3. `live/forcing.py`: grid DNI and DHI (or derive them with erbs from the coast `SW` series and the sun position); `forcing_fn` returns `dni`, `dhi`.
4. `wetness.simulate`: precompute `n` once; per step compute `cos_inc` ([N]) from `az[i]`, `el[i]`; pass to `step`. `step`: new branch for the sun term.
5. `point_table` in `live/build.py`: add `bearing`, `slope` when present.

## Tests
- Flat road: `DNI * sin(el) + DHI * svf` with svf = 1 equals GHI within 2 % when DNI/DHI come from erbs.
- Sun due south at 30 degrees elevation: a 15 % slope facing south gets more than flat, facing north less; the ratio matches the cosine formula to 1 %.
- Night: zero. Shaded (`lit` false): only the diffuse part.
- File missing: flat-road result and a flag in `status.json`.

## Hand-back
Compare report plus a map-free summary: mean daytime drying on north-facing versus south-facing streets steeper than 8 %.
