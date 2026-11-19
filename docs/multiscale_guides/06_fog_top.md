# Guide 06: fog top by fusing measurements (switch `top_fusion`)

Read `00_README.md` first. Independent of guides 01-05. Action side. This switch changes the fog top series that street wetness also uses, so it follows the full physics-switch rules (compare report, guide 01 of `docs/model_guides`).

## Goal
Replace "one guess, overridden by whichever source is newest" with a weighted estimate of the fog top that carries an error bar, moves smoothly in time and never sticks at the 50 m floor or the 900 m cap unless the data say so.

## Background (from the code)
`forcing.py`, section "fog top": `fogtop.estimate_fog_top(ser)` gives a top from Open-Meteo pressure levels (1000, 975, 950, 925, 900 hPa, about 200 m apart: RH crossing and first inversion). A sounding inversion base overrides it for `SOUNDING_VALID_H` and fades over 6 h. A METAR ceiling plus `FOG_TOP_ABOVE_CEILING_M` (100 m) is a floor. The result is clipped. Each source is used as a hard override or a hard floor, so the top jumps when a source appears or expires.

## Part A: fusion of what we already fetch (build this)

1. **Switch** `C.TOP_FUSION = False`, `BETA["top_fusion"] = True` after the gate. With it off, the existing code path runs untouched (wrap the new code; do not edit the old lines).
2. **Observations per hour**, each a value `z` (m above sea level) and a standard deviation `sigma` (first guesses, constants in `live/config.py`):

   | Source | Value | sigma | Note |
   |---|---|---|---|
   | Model profile | `fogtop.estimate_fog_top` | 120 m; 250 m when its info says fallback, floor or cap was hit | every hour, forecast hours too |
   | Sounding | inversion base (`_inv(snd)`) | 50 m at the valid time, + 30 m per hour away from it, unused beyond 9 h | 2 a day |
   | Ceiling | highest valid ceiling of KHAF / KSFO (as today, at or below 3000 ft) + `STRATUS_THICK_M` | 120 m | `STRATUS_THICK_M = 250` first guess (typical marine stratus depth), replacing the hard +100 m floor |
   | GOES top | median ACHA height of low-cloud pixels in the upwind box and coast box for that hour, when at least 5 pixels | 250 m | ACHA is coarse (10 km) and biased; keep the weight low |

   A ceiling also gives a hard lower bound: the top is never below `ceiling + 30 m`.
3. **Filter in time: a one-dimensional Kalman filter then a backward smoothing pass** over the hourly series (plain numpy, about 40 lines):
   - State: top `x` with variance `P`. Start: the first model value, `P = 150^2`.
   - Predict each hour: `x` unchanged, `P += Q`, `Q = 60^2` m^2 per hour (the top can drift about 60 m in an hour; first guess).
   - Update with each available observation in turn: `K = P / (P + sigma^2)`, `x += K * (z - x)`, `P *= (1 - K)`.
   - Outlier gate: skip an observation when `|z - x| > 3 * sqrt(P + sigma^2)` and count it in the info dict.
   - Backward pass (Rauch-Tung-Striebel) over the observed hours only, so a sounding improves the hours before it. Forecast hours keep the forward result, where only the model profile updates.
   - Apply the ceiling lower bound and the existing floor and cap last.
4. **Outputs.** `F["fog_top"]` (step series, interpolated from hourly as today) and a new `F["fog_top_sigma"]`. `weather.json` gets `fog_top_sigma_m` (only with the switch on). `status.json` info: per source, hours used and hours rejected.
5. **Viewer:** nothing. `topMin: 90` and `topMax: 900` in `fog2.js` stay; with guide 04's 3D data the viewer no longer uses the top directly.

## Part B: a finer profile (probe first; build only if the probe passes)

Pressure levels 200 m apart are the weakest input. Native model levels are 20-40 m apart near the ground.

1. **Probe, report, stop.** From the PC or a container, check which of these answers today, without building anything:
   - HRRR native-level files on AWS: `https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.<yyyymmdd>/conus/hrrr.t<hh>z.wrfnatf00.grib2` and its `.idx` (byte offsets per field). Is today's date present? HRRR may be retired as RRFS takes over (verify).
   - RRFS native-level output: bucket `noaa-rrfs-pds` on AWS or NOMADS (`nomads.ncep.noaa.gov`). An August 2026 note said RRFS moved off AWS into NOAA servers (verify).
   - Open-Meteo: does the RRFS endpoint offer more pressure levels than the six in `C.LEVELS_HPA` (for example 985, 970, 960 hPa)? One request tells.
   Report: which exist, field names for temperature, specific humidity and height on the lowest 15 levels, bytes per field, latency of the newest run.
2. **If Open-Meteo has extra levels:** add them to `LEVELS_HPA` behind the switch and stop there. That is the cheap win.
3. **If only GRIB sources exist:** this needs a GRIB decoder in the Action (`eccodes` wheel, about 30 MB, record it in `live_ci/requirements-live.txt`) and range reads of about 45 fields per hour. Write up cost and risk and ask George before building. Do not add the dependency on your own.

## Tests (`tests_mc/test_top_fusion.py`)
- One source only: the output equals that source after the first step (within 1 m once `P` has settled).
- Two sources with equal sigma at the same hour: the output is their mean; with sigmas 50 and 250, it lies within 4 % of the way from the first to the second.
- A sounding at hour 12 pulls hours 9-11 toward it in the smoothed series, and by less the further away.
- A single wild value (2000 m) among steady 300 m values is rejected and counted.
- The ceiling lower bound holds in every hour that has a ceiling.
- No NaN when every source is missing for some hours (the state carries through).
- Switch off: `fog_top` byte-identical on the saved-inputs fixture.

## Gate
- **Leave-one-sounding-out:** on at least 10 soundings from saved inputs or the run archive, run the fusion without that sounding and compare the top at its valid time with its inversion base. Report mean absolute error for the old method and the new one. Pass: the new one is lower. (The old method cannot be scored the same way while it uses the sounding itself, so also run the old method with the sounding withheld.)
- Share of hours stuck at the floor or cap, old against new.
- Compare report for street wetness (guide 01 of the model guides): mean and largest change per night. George sees it before `BETA["top_fusion"]` is turned on.

## Pitfalls
- Everything is metres above sea level; ceilings are feet above the airport: `ceil_ft * 0.3048 + METAR_ELEV_M[station]`.
- The sounding's `valid` time is a datetime; hours are indexed from `t0`. Reuse the existing `tv` computation.
- Do not let forecast hours be smoothed backward by nothing: the backward pass starts at the last hour that has any non-model observation.
- Keep `FOG_TOP_ABOVE_CEILING_M` in place for the switch-off path.
