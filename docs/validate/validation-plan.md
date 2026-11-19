# Validating the fog model against real nights: plan

Repo George-The-G/SF-DEATH-FOG-DETECTION, v3-dev at e21fa98, read 2026-11-14. Planning only, nothing built. "Estimate" means reasoned, not measured. Sources: guides 00, 03, 04, 08, 09, 11, 12 in `docs/model_guides`, and the two replay notes in `/mnt/project-files/replay/`.

## 1. Short version

1. **Do not wait for fog season to collect nights.** Past GOES-18 scans (NOAA archive on AWS) and past METARs (IEM archive) can be pulled for any date, so we can hindcast last summer's fog nights now. What cannot be recovered is the forecast as it was issued, so the one thing worth archiving from today on is our own model runs.
2. **The GOES buffer is cheap and mostly the other thread's job.** The replay thread already plans a 5-minute crop store (goes-anchor-plan 3.1: SF crop of about 70 x 60 px, a rolling 24-48 h on the `data` branch). Validation reuses that exact format. We add only a long-term cold copy if we need it, and we probably do not (section 3).
3. **Score night by night with a fixed card** (section 4), against three baselines (persistence, hour-of-day climatology, today's public model), so "better" means something.
4. **Tune few constants, one block at a time, mostly from direct observation** (section 5). About 30 fog nights plus about as many no-fog nights are the minimum for the whole set; fewer for single constants (section 6).
5. **Everything stays behind switches.** Tuned values go in as new defaults only after your approval, beta first, per the standing rules.

## 2. What the model claims, and what we can check it against

| Model piece (guide) | What it claims | Observed truth available | Truth quality |
|---|---|---|---|
| Fog source s(t) and tiles (existing) | Fog present or absent over the city and upwind, with arrival and clearing times | GOES ACM and ACHA low cloud (2 km, 5 min); KSFO/KOAK/KHAF visibility | GOES sees cloud tops, so a stratus deck over hills looks like fog. Airports are 3 points. |
| Cloud base (08) | Street in cloud only above base = zm + 125 (T - Td) + offset | Airport ceilings (already used to set the offset, so hold one out); hill stations; Oakland sounding | Good for height, sparse in space |
| Lag speed (09) | Front travels at 1.3 x the 10 m wind, one speed for all streets | Arrival time at coast, KSFO, KOAK, hill stations; GOES edge motion | GOES gives edge position at 2 km only; stations give timing at points |
| Drizzle (11) | Rate by cloud thickness; wets streets below base | METAR `DZ`, station rain gauges, hourly precip at CWOP/RAWS (small amounts, poor gauges) | Weak. Drizzle is rare and under-measured. |
| In-place fog (12) | Fog with any wind direction when T - Td < 1 K and visibility is low | Station visibility, T and Td at onset | Good where stations exist |
| Street wetness (the end product) | Slippery or wet roads | None | No ground truth. We validate the drivers, not wetness itself. |

Honest limit to say on the page and in the hand-back: the end product is validated only through its drivers (fog presence and timing, cloud base, drizzle occurrence, in-place fog). Guide 00 already says "done" means physically consistent plus checked against stations or satellite.

## 3. Collecting and storing the data (GitHub-only hosting)

### 3.1 What we actually need to keep

| Data | Recoverable later? | Keep it ourselves? |
|---|---|---|
| GOES-18 ACM/ACHA scans | Yes, public AWS archive, any date (this was probed for current scans; confirm the multi-year archive for 2024-2025 with one request) | Rolling 48 h for live replay only (other thread). Cold copy not required; pull per night on demand into a local cache. |
| METAR, NDBC, CO-OPS, IEM stations | Yes (IEM archive; NDBC and CO-OPS history) | Pull on demand, cache next to the night's table |
| Model forecast as issued (RRFS via Open-Meteo) | Probably not as issued. Open-Meteo has an archived "previous model runs" style API, which I have not verified; it would give past forecasts at fixed lead times, not our run. | **Yes. Start now.** |
| Our own model output per run (fog tiles, lattice low cloud, base, top, switch set) | No | **Yes. This is guide 02 part B, "run archive".** The README says beta never writes the archive; check what public archives today. |

### 3.2 Size (estimate)

- GOES crop per scan: ACM about 4 KB (1 byte per pixel), ACHA about 8 KB (2 bytes), so about 12 KB raw, 288 scans a day is about 3.5 MB a day, probably 0.5-1 MB compressed. A rolling 48 h is about 2 MB. Trivial for the `data` branch, which is force-pushed so history does not grow.
- If we want a cold copy: a year is about 200-350 MB compressed. Do it as **monthly pack files attached to a GitHub release** (the repo already uses release assets such as `live-static-v1`), not committed to a branch and never on Pages. Optional bands for sharper scoring (visible by day, 7 minus 13 at night) add about 2.4 MB a day per band at 16-bit for the same crop (estimate).
- Model archive per run: fog tile averages for the 30 lattice boxes plus ocean and bay boxes per lead hour, base, top, switch set: a few KB per run. 48 runs a day is under 1 MB a day. A year is under 300 MB. Same monthly-pack approach.
- Pages stays free of all this. Only a small `cloud_score.json`-style scorecard (guide 04, under 100 KB, rolling 14 days) is published.

### 3.3 Retention proposal

Live buffer 48 h (replay thread). Model-run archive and observed truth tables: keep at least 2 summers. GOES cold copy: only the fog nights we score, about 3-5 MB each.

### 3.4 Who owns what (avoid duplicating the replay thread)

- **Replay thread owns:** 5-minute ingest in `live/goes.py`, the crop format, the 48 h store, parallax correction, continuous low-cloud probability and confidence, the 5-minute tiles.
- **This work owns:** the scoring harness, the night selection, the model-run archive, the hindcast runner, the tuning loop, and the scorecard JSON.
- **Shared contract (needs agreement):** one crop file layout and one low-cloud definition (`LOW_TOP_M` 1000 m and the confidence rule from the replay plan) so scoring and replay never disagree on what "satellite fog" means. Until the replay ingest lands, the harness pulls its own crops offline with the same definition.
- Where it runs: the nightly scorecard is a light step in the Action (under a minute, estimate). Hindcasts and tuning run on the PC in a fresh worktree, no GPU needed. Heavy work never goes in the 3-minute live budget.

## 4. Scoring night by night

**Night window:** local 18:00 to 10:00, so evening arrival, overnight deck and morning burn-off sit in one unit. A "fog night" is a night where the coast box shows low cloud fraction at or above 0.5 for 3 or more hours, or a station reports visibility under 3 mi or a ceiling at or below 1000 ft for 2 or more hours (thresholds are first guesses, to be set after the V0 inventory). No-fog nights are scored too, for false alarms.

**The scorecard, one row per night and per model variant:**

1. **Satellite match** (guide 04 numbers, but the 5-minute buffer makes timing 5-minute resolution instead of hourly): hit rate, false-alarm ratio, critical success index, and mean absolute error of the fraction for the model fog sheet, RRFS raw, and source, on the coast, upwind-ocean and bay boxes. Exclude hours hidden by high cloud and count them. Day and night separately.
2. **Timing:** minutes between modelled and observed arrival and clearing at the coast box (first of two consecutive yes scans), and at three or four interior points where a station gives the onset (KSFO, KOAK, hill stations). Report the signed error as well as the absolute, because lag speed is a bias question.
3. **Station match:** ceiling error against KSFO, KOAK and KHAF with leave-one-station-out (the base offset already uses these), visibility category agreement (the IFR and LIFR categories), T and Td mean absolute error, and drizzle: `DZ` hit rate and false alarms per night.
4. **In-place fog:** share of fog-evidence hours where the model gives fog when wind is from 0-180 degrees.
5. **Variant comparison:** each switch on and off, paired by night (guide 01 compare report). A switch is judged on the paired difference over nights, not on pooled hours, because hours within a night are correlated.

**Baselines:** persistence (last observed state held), hour-of-day climatology for that month, and today's public model. Skill means beating persistence at 1 to 3 h and climatology beyond.

**Forecast versus hindcast.** Hindcast forcing uses archived model output that may be closer to an analysis than a true forecast, so hindcast scores flatter the model at long leads. Use hindcasts to tune constants and to find gross errors. Report true forecast skill only from the live archive, by lead time 1, 3, 6, 12, 24 h as guide 04 describes.

**Satellite pitfalls to control for:** stratus top over hills looks like fog, the cloud mask is weaker at night and flickers near the day-night switch (use the replay plan's confidence rule, and drop the 1-hour terminator window from scoring), and GOES-18 views SF from about 46 degrees so a 400 m fog top sits about 0.4 km northeast (parallax correction, replay plan 3.2).

## 5. Which constants to tune, and how

Principle: first estimate directly from observations where a constant has a physical meaning, then run the model only for the constants that need it. Do not tune physics that is already fixed by thermodynamics.

| Constant (guide) | First guess | How to set it | Needs a model run? |
|---|---|---|---|
| `LCL_M_PER_K` (08) | 125 m/K | Check only: observed ceiling minus site elevation against T - Td at ceiling-reporting hours. Do not tune, it is physics. | No |
| `base_offset` rule (08) | median of three airports, clip 300 m | Leave-one-airport-out ceiling error; check the clip and the 3 h median window | Light |
| `BASE_RAMP_M` (08) | 60 m | Compare to base uncertainty seen in the ceiling error above (spread of residuals). Set to about the 1-sigma residual, bounded 40-120 m. | No |
| `GAP_INSITU` (12) | 1.0 K | Distribution of T - Td at stations when visibility is under 1 mi. Direct estimate. | No |
| Visibility to evidence map, 5000 m and 4000 m (12) | first guess | Station visibility against model `visibility` over the nights; fit the map by isotonic or two-point fit | No |
| In-place correction weight 0.5 (12) | first guess | Skill of the citywide correction in leave-one-station-out | Light |
| `LAG_SPEED_FACTOR` (09) | 1.3 | One-dimensional scan: 0.8 to 2.0 in steps of 0.1, minimise the signed timing error at interior onsets and GOES edge position | Yes, cheap |
| `LAG_MAX_H`, `WIND_LAG_MIN` | existing | Check against the longest observed coast-to-interior delays only | No |
| `CLEAR_MAX_OPACITY` 0.3 and `LOW_TOP_M` 1000 (existing) | existing | Score hit rate and false alarms across the settings; check ACHA top against Oakland sounding inversion height | Yes, cheap |
| `GAP_FOG`, `CLOUD_FOG` (existing source) | existing | Hit and false-alarm trade-off on source against GOES and visibility | Yes |
| `FOG_E_FOLD_H` 3 h, `C.WIND_E_FOLD_H` | existing | Score the forecast hours after the last observation by e-fold from 1 h to 6 h | Yes, needs archived leads |
| Drizzle `DRIZZLE_T0`, `DRIZZLE_T1`, `DRIZZLE_MAX`, `K_DRIZZLE` (11) | 200 m, 500 m, 0.10 mm/h, 150 | Only the threshold pair is observable: thickness at which `DZ` turns on (hit rate versus false alarm). `DRIZZLE_MAX` and `K_DRIZZLE` have no truth source, keep them as stated first guesses and say so. | Yes |
| Replay `Kmax` 0.5 (other thread) | 0.5 | The other thread's leave-one-out. Our harness supplies the nights; do not duplicate. | n/a |

**Method for the ones that need runs:** one block at a time, in the order above, hold everything else at first guess; grid search over a short physically bounded range; score the paired night difference; pick the value inside the flat part of the curve nearest the first guess (do not chase the single best point); confirm on nights not used for the scan (section 6); then run the block's guide 01 compare report and put the new value behind its switch.

**Stop rules:** if a tuned value lands at the edge of its allowed range, report instead of widening it. If a change helps one night type and hurts another (advective summer versus bay-side winter), keep the first-guess value and say why.

## 6. How many foggy nights

Rules of thumb and estimates, not measured. Hours within a night are strongly correlated, so a night is the unit.

| Goal | Nights needed (estimate) | Why |
|---|---|---|
| Find gross errors (sign of timing bias, always-too-wide fog, wrong ceiling) | 5-10 mixed nights | Big errors show up fast |
| Set one constant that has a clear score (lag speed, `GAP_INSITU`) | 10-15 nights where that behaviour occurs | The scan has one parameter; the signed error shrinks about as 1 over the square root of the number of nights |
| Compare two variants honestly (paired by night) | 20-30 fog nights | With about 30 nights the standard error on a hit-rate or CSI difference is about 0.08 (estimate), enough to see differences larger than about 0.15 |
| Whole set, with a held-out check | About 30 fog nights to tune plus 15 more held out, plus an equal number of no-fog nights for false alarms | Alternate blocks (for example odd and even weeks), never random hours |
| Drizzle | 10-15 nights with at least one `DZ` report | Too rare in Nov-Apr to meet this quickly; likely only from summer hindcasts |
| In-place and bay-side fog | 8-10 events | Rare and winter-weighted; we may only reach a sanity check |

**Where the nights come from:** one summer (about June to September) of marine layer in SF typically gives dozens of fog nights, so one hindcast season should reach about 30-50 (estimate, check with the V0 inventory). In-place and bay-side fog, and drizzle, are the scarce classes and will probably set the real pace. Today is mid November, so live collection will add few advective nights until next summer; this is the main reason to hindcast.

## 7. Stages

| Stage | Work | Rough size (estimate) | Gate |
|---|---|---|---|
| V0 | Inventory: query IEM and AWS for the last two summers; list candidate fog, no-fog, drizzle and bay-side nights by the definition in section 4; list what model data exist (run archive, Open-Meteo archived runs); confirm GOES archive depth. Report counts. | 1 day | Enough nights per class to continue? |
| V1 | Offline scoring harness on the PC: pulls crops and observations for a night, runs the model (public settings and beta switches), writes the scorecard; baselines; 5 nights first | 3-4 days | Table reproduces guide 04 numbers on the current run |
| V2 | Model-run archive in the Action (guide 02 part B for public and beta) and a nightly scorecard JSON; adopt the replay thread's crop store when it lands | 2-3 days | Action under +1 min; `cloud_score.json`-style file published after two builds |
| V3 | Tuning in section 5 order, one switch at a time, paired-night compare reports; hand-back with values, curves, held-out scores | 1-2 weeks | Beats or matches first guess on held-out nights, paired difference reported |
| V4 | Promote approved defaults to beta, then public on your approval; recheck after next season | n/a | Your call |

## 8. Risks

- **Too few nights of the rare classes**: say which constants are untested instead of tuning on one event.
- **Hindcast forcing flatters the model**: label it, tune on it, never quote it as forecast skill.
- **Satellite truth is cloud-top truth**: pair it with station visibility before declaring a miss; sheet fog versus stratus above hills.
- **Station truth is sparse**: three airports for ceilings, a handful of piers and hills for wind and T. Always use leave-one-out.
- **Overfitting 10 constants to 30 nights**: tune one at a time, pull towards first guess, hold out nights.
- **No ground truth for street wetness**: nothing here validates wet or slippery directly.
- **Opening data licences**: only open, keyless sources; MADIS and webcams stay out unless approved.

## 9. Open questions for George

1. Is a hindcast on past summers acceptable as the first step, given mid-November has few fog nights live? (My recommendation: yes, labelled as hindcast.)
2. Storage: monthly release assets (what I recommend) or a separate `archive` branch, and may the Action write release assets? Which does the repo use now for the run archive of guide 02?
3. Please confirm with the replay thread, via the coordinator, that its 5-minute crop store is the shared format and that scoring reads it, so neither duplicates the other.
4. Does any street-level truth exist (your own route reports, wet-road photos, loggers)? Even 5-10 dated reports would let us check the end product once.
5. Are the fog-night thresholds in section 4 close to what you would call a fog night, or do you want a stricter or looser bar?
