# SF death-fog 3-D microclimate MVP: report (M4 wetness + M6 validation)

Branch `v3-dev`. Reproduce: `python -m microclimate replay` (writes `site/v3/replay/`), `python -m microclimate validate` (prints PASS/FAIL, writes `site/v3/replay/validation.json` and `overview.png`), `python -m unittest discover tests_mc`.

## What was built
- `microclimate/wetness.py`: vectorised road-wetness proxy W in [0,100] over 14,116 Potrero street points, 10 min steps (dew from radiative surface cooling driven by sky view, advected-fog deposition from the 270-degree fog tracer with a west-to-east arrival lag, rain, evaporation by humidity and wind, direct and diffuse sun drying using the real horizon profiles). All constants are at the top of the file and also written to `index.json`.
- `microclimate/replay.py`: Open-Meteo history (cached `weather_37.76_-122.4.json`), night selection (`weather.pick_nights`: 7 fog and 2 dry nights, 18:00-10:00 Pacific, only complete nights), model run, viewer files (`streets.u8`, `weather.json`, `fog.png`, `index.json`).
- `microclimate/validate.py`: checks (a)-(e) below.
- Nights replayed: fog 2026-11-10, 10-08, 10-01, 09-30, 09-24, 09-22, 09-20; dry 2026-10-16, 10-03. All 7 fog nights met the strict criteria (tier 0). The dry control on 10-16 needed the relaxed gap (> 4.5 C instead of > 5 C).
- Nights 11-10, 09-22 and 09-20 are flagged `approx_direction` (mean fog-weighted wind more than 22.5 degrees off west), 10-16 (dry) too.

## What worked
- Plausible, varied behaviour: strong fog nights (10-01, 09-30) reach W 80-100 on exposed points and are still 50-80 at 09:00; weak fog nights peak 30-60; dry nights stay at exactly 0.
- The 3-D pieces matter. Ablation at 09:00 (mean over fog nights, W std across points; baseline 19.8): uniform fog tracer 9.3 (pattern correlation with baseline 0.49), sun term off 20.1 (corr 0.96), SVF forced to its median 18.8 (corr 0.87), refined wind off 23.2 (corr 0.84), all four off 0.2. The fog tracer (terrain blocking of the western inflow) carries most of the street-to-street pattern; sky view and sun shading are second order for the pattern but decide dew and morning drying side by side on one street.
- The refined 5 m wind correction is applied after normalisation: rel = clip(refine_corr / 0.91, 0.25, 1), NaN to 1 (raw values are over-damped: median 0.26, 95th percentile 0.91, so used raw they would stall evaporation and lag everywhere). It scales local wind in cooling, fog-deposition factor, evaporation and the lag (switch `USE_REFINED`). Turning it off changes mean 09:00 W by 11 points and std from 19.8 to 23.2; streets that change most (more wet without refined damping means drier with it): Southern Heights Ave, York St, Folsom St, 21st St, Dr. David Sanchez Way, Wisconsin St, Potrero Ave, 23rd St (about -10 to -14 W when off, i.e. refined damping keeps wind low and fog longer on these). Absolute magnitudes stay uncalibrated.

## Validation (honest results)
| Check | Result |
|---|---|
| (a) E-W vs N-S streets | PASS with a tiny effect: expected E-W drier at 09:00 (morning sun runs along them). E-W 36.1 vs N-S 38.0 (0.06 pooled SD). The sign is as predicted but the size is negligible. |
| (b) shadier side of E-W segments wetter at 09:00 | PASS 76.6 % over 10,379 segments with at least 20 min sun difference. Largely built in via the sun term; it tests plumbing and horizon data, not reality. |
| (c) dry nights, 95th percentile W < 20 | PASS (0.0 on both). |
| (d1) deposition follows tracer | PASS (x7.2 top vs bottom 20 %), by construction. |
| (d2) tracer outer Sunset > Mission | PASS (0.51 vs 0.004). |
| (d3) tracer bounded and converged | PASS after the rerun (see 'Fog height treatment'): drift 0.0000, 0.0038, 0.0018, 0.0090 for bands 0-3 (before: bands 2 and 3 were 0.054 and 0.127 and the check FAILED). 0 <= c <= 1, no NaN. Band 3 is only just under the 0.01 limit. Exact mass conservation cannot be tested because ground deposition removes tracer. |
| (g1-g3) fog height | PASS, new; see 'Fog height treatment'. |
| (e) ablation table | INFO, see above. |
| (f) v2 observed fog nights | SKIPPED (optional). |

## Near-uniform, proxy and limits
- Near-uniform: the 20 m grid barely sees low-rise buildings, so the street-level wind ratio is about 1 everywhere (p5-p95 0.999-1.008). The refined layer exists but is over-damped; hence the normalisation.
- Proxy: W is a physical-ish index with hand-set constants (K_DEW, K_FOG, evaporation, sun), chosen only so a strong fog night reaches 50-90 on exposed points and a dry-air night stays below 15. Not calibrated against any observation; absolute W is not a probability of anything.
- Fog tracer for Potrero is small in band 0 (0.0005 to 0.045) so tracer/0.03 is clipped and square-rooted (TRACER_REF, FOG_EXP). (Fixed in the fog height treatment below: bands are now interpolated by street elevation and limited by an estimated fog top.)
- Limitations: only the 270-degree (west) field exists, other onshore directions use it scaled by speed and are flagged, offshore wind (from the east, outside 180-360) gives no advected fog; no satellite data; no rain/fog microphysics; one-way coupling (wetness does not feed back); single point weather for the whole city; Open-Meteo model data, not observations; lag uses one mean wind per night.
- Sun is real (pvlib plus the street horizon), but its effect is only visible after about 07:00 in this 18:00-10:00 window.

## Fog height treatment
Problem: the model picked ONE tracer band per street from elevation + 30 m (every Potrero street used band 0) and had no fog top. Now (`FOG_HEIGHT_MODE = 'profile'`, switch back with `'band0'`):
- Fog top per time step (m above sea level, `microclimate/fogtop.py`) from Open-Meteo pressure-level data: top = max(floor, min(top_RH, top_inv)). top_RH = where relative humidity (2 m, 1000/975/950/925/900 hPa) first falls below 80 %; top_inv = geopotential height of the level below the first temperature increase with height (from 1000 hPa up). floor = grid elevation 34 m + 50 m. Boundary-layer height, freezing level and LCL were looked at and not used (night PBL height is the shallow mixed layer, LCL is a cloud base). Without the pressure-level data a constant 300 m is used.
- A street counts as inside the fog with a smoothstep ramp, 1 below the top and 0 above, +/- 40 m wide, using the fog top lagged by the same front-travel lag as the inflow.
- Tracer: each of the four band fields is sampled bilinearly at the street's x, y and at street z + 10 m (solid cells raised to the first fluid cell), then interpolated between the band centres 75/225/400/650 m (no hard band choice). Works for any point table with x, y, z.
- Higher streets also get a stronger fog-deposition wind factor: +10 % per 100 m above 30 m (`ELEV_WIND_GAIN`).
- All constants are at the top of `wetness.py`; none is calibrated. Dry nights are unchanged (W stays 0.0).

Mean W at 09:00 over the 7 fog nights by street elevation (band0 = before, profile = after):

| street z (m) | n points | band0 | profile |
|---|---|---|---|
| 0-10 | 3362 | 49.8 | 50.6 |
| 10-20 | 5244 | 32.6 | 32.6 |
| 20-30 | 1724 | 39.3 | 39.6 |
| 30-45 | 1076 | 35.5 | 35.7 |
| 45-60 | 1162 | 34.6 | 34.9 |
| 60-80 | 1044 | 29.7 | 34.2 |
| over 80 | 504 | 28.7 | 41.1 |

Low streets (under 30 m) change by +0.3 W, streets over 60 m by +7.0 W: the high Potrero streets were under-wetted and now get band-1 fog; the ramp then dries them again when the fog top is low. Controlled test (same weather, fog top forced to 90 m vs 700 m): streets over 60 m 33.1 vs 40.1, streets under 20 m 39.7 vs 39.7. Potrero spans only 3-99 m, so on real nights the estimated top (84-790 m, inflow-weighted 87 m on 2026-09-30 to 458 m on 2026-09-24) is mostly above the streets and the real-night effect is limited to the highest streets; the large effect is on real hills. Hill check (qualitative, summit elevations typed in by hand, largely by construction): Twin Peaks and Mt Davidson get a fog-top ramp of 0 on 5 of the 7 fog nights (shallow fog: dry above it) and 1.0 on the two deepest (2026-09-20, 2026-09-24); the band-interpolated tracer at the summits is 0.34-0.37 versus 0.01-0.04 at Potrero streets. That matches the usual picture (shallow fog leaves the peaks clear, deep fog covers them) but nothing here was compared with observations.
- Tracer convergence: bands 2 and 3 were rerun for 270 degrees with 6 transits instead of 3 (`fog.converge_band`, `fog.rebuild_bands`; old files kept as `fog_2700_2.bak.npz` and `_3.bak.npz`). Drift over the last 2000 steps: band 2 0.0018 (was 0.054), band 3 0.0090 (was 0.127); 54 s and 51 s each on the shared GPU. Band 3 is marginal (limit 0.01).
- Honest limits: the fog top is a proxy from a 0.25 degree model profile with 5 levels (about 220 m vertical spacing; it cannot resolve fog thinner than the 50 m floor above the grid cell); nothing is calibrated; tracer bands are single-source fields (no superposition of several bands for a deep fog layer); only the west (270) field exists; v2 observed fog nights were not compared.

## Size
Replay folder: 13.6 MB raw (9 nights x 1.37 MB streets.u8, 80-180 KB fog.png), about 4.2 MB gzipped. With the viewer data (4.4 MB) the gzipped site stays under the 10 MB budget; raw it does not.

## Go / no-go for the full plan
Recommendation: GO for the next phase only as a research-grade tool, with these needed first: (1) wind fields for the other onshore directions (rotated-frame wind and fog tracer, not scaling the west field), (2) a wall/building-resolved street wind model (the 20 m grid cannot see the street canyons; refined 5 m patches for the whole zone with a trustworthy correction), (3) calibration of K constants against real observations (v2 fog observations aligned to nights, road or surface wetness data), (4) converge the upper tracer bands and use the fog-deck height per night, (5) satellite or ceilometer fog-top data. Without calibration the replay is a believable demonstration of where and when the 3-D effects could matter, not a forecast.
