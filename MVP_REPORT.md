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
| (d3) tracer bounded and converged | FAIL: bands 0 and 1 are converged (drift 0.0000 and 0.0038), bands 2 and 3 are not (drift 0.054 and 0.127 over the last 2000 steps; 0 <= c <= 1 holds, no NaN). Higher-band fields would need longer runs; band 0 (the one used for Potrero) is fine. Exact mass conservation cannot be tested because ground deposition removes tracer. |
| (e) ablation table | INFO, see above. |
| (f) v2 observed fog nights | SKIPPED (optional). |

## Near-uniform, proxy and limits
- Near-uniform: the 20 m grid barely sees low-rise buildings, so the street-level wind ratio is about 1 everywhere (p5-p95 0.999-1.008). The refined layer exists but is over-damped; hence the normalisation.
- Proxy: W is a physical-ish index with hand-set constants (K_DEW, K_FOG, evaporation, sun), chosen only so a strong fog night reaches 50-90 on exposed points and a dry-air night stays below 15. Not calibrated against any observation; absolute W is not a probability of anything.
- Fog tracer for Potrero is small in band 0 (0.0005 to 0.045) so tracer/0.03 is clipped and square-rooted (TRACER_REF, FOG_EXP). Fog arriving over the hills in bands 1-2 (tracer 0.1-0.25 at the same streets) is NOT used because the band is chosen from point elevation + 30 m, as specified. This probably understates fog at higher streets; worth revisiting.
- Limitations: only the 270-degree (west) field exists, other onshore directions use it scaled by speed and are flagged, offshore wind (from the east, outside 180-360) gives no advected fog; no satellite data; no rain/fog microphysics; one-way coupling (wetness does not feed back); single point weather for the whole city; Open-Meteo model data, not observations; lag uses one mean wind per night.
- Sun is real (pvlib plus the street horizon), but its effect is only visible after about 07:00 in this 18:00-10:00 window.

## Size
Replay folder: 13.6 MB raw (9 nights x 1.37 MB streets.u8, 80-180 KB fog.png), about 4.2 MB gzipped. With the viewer data (4.4 MB) the gzipped site stays under the 10 MB budget; raw it does not.

## Go / no-go for the full plan
Recommendation: GO for the next phase only as a research-grade tool, with these needed first: (1) wind fields for the other onshore directions (rotated-frame wind and fog tracer, not scaling the west field), (2) a wall/building-resolved street wind model (the 20 m grid cannot see the street canyons; refined 5 m patches for the whole zone with a trustworthy correction), (3) calibration of K constants against real observations (v2 fog observations aligned to nights, road or surface wetness data), (4) converge the upper tracer bands and use the fog-deck height per night, (5) satellite or ceilometer fog-top data. Without calibration the replay is a believable demonstration of where and when the 3-D effects could matter, not a forecast.
