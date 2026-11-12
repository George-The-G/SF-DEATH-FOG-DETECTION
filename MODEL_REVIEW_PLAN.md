# Model review and accuracy plan

Status: review and plan only (2026-11-12). Nothing here is built. Based on reading `microclimate/wetness.py`, `fogtop.py`, `live/forcing.py`, `live/config.py`, `fog.py`, `wind.py`, `street.py` on `v3-dev`. Data sources in section 4 are named from knowledge; access, latency and terms need the same kind of source check as PLAN_LIVE_DATA.md step 1 before any is used.

## 1. Verdict

- The pipeline shape is right: forecast model, nudged by observations, downscaled by precomputed wind fields, then a per-street water balance with sun and shadow.
- The arithmetic is correct. Wind direction conventions, u/v conversion, circular means, the Magnus humidity formula, the fade and blend functions all check out.
- The physics is a proxy, and four of its simplifications are large enough to give wrong answers in common SF conditions (section 2, items A to D).
- The biggest limit on accuracy is not a formula. The model has never been compared with a real wet or dry road, so every constant is a guess. Calibration data (section 5) is worth more than any single physics upgrade.

## 2. Where the physics is wrong or missing, in order of impact

### A. Cloud base is ignored: every street under the stratus counts as "in fog"
`fog_top_ramp` makes a street fully fog-exposed whenever the fog top is above it. Marine stratus usually has a base 100 to 400 m up. Streets below the base are under cloud, not in it, and stay dry unless it drizzles. Streets between base and top (Twin Peaks, Golden Gate Heights, upper Potrero on a low-base night) are the ones inside the cloud. This is the main mechanism behind "the hills are soaked, the flats are dry", and the model cannot produce it.
- Fix: estimate cloud base per time step and treat a street as in cloud only when base <= street elevation <= top.
- Base: lifting condensation level from the nudged surface T and Td (about 125 m per degC of T minus Td), corrected by METAR ceilings (KSFO, KOAK, KHAF) when available. The code already computes a ceiling floor for the top; the same data gives the base.
- The current `s(t)` term (surface T minus Td gap) partly stands in for this, but it is one citywide number. Replace it with the per-street test.

### B. No drizzle
Stratus thicker than about 300 m commonly drizzles. Drizzle wets streets below the cloud base, is often the main wetting on summer nights, and is mostly absent from model precipitation (it is below 0.1 mm/h).
- Fix: drizzle rate as a function of cloud thickness (top minus base), turned on above a thickness threshold; confirm with METAR present-weather codes (DZ, BR, FG) in observed hours.

### C. Pavement temperature has no memory
`Ts = Ta - dT` with dT up to 4 degC responds instantly. Real pavement stores daytime heat. In the evening the road is warmer than the air, often for the whole night in a city, so dew on asphalt is much rarer than on car roofs or grass. The model therefore overstates dew on clear calm nights and gets its timing wrong (real dew, when it happens, comes late).
- Fix: carry a surface temperature state per street. A two-layer force-restore scheme is enough: net radiation (shortwave absorbed with shadow and slope, longwave from sky scaled by sky view and cloud, longwave emitted), sensible heat to the air scaled by local wind, latent heat, and conduction to a deep layer. This is what road-weather models (for example METRo) do.
- Then dew, frost and evaporation all come from one quantity: the difference between vapour pressure of the air and saturation vapour pressure at the surface temperature.
- Cooling should use total cloud (weighted by height), not low cloud only.

### D. Fog only comes from the west
`onshore_weight` is zero for wind from 0 to 180 degrees. That fits summer advection fog. It gives zero fog for winter cases: tule fog drifting in from the bay and delta on light east wind, and fog that forms in place on calm clear nights. Those are the cold-season death-fog cases, and it is November.
- Fix: keep the advected term for onshore flow, and add an in-place term driven by local saturation (per-street T minus Td near zero, light wind), independent of direction. In observed hours, GOES low cloud over the city and bay plus KOAK/KSFO visibility should be able to turn fog on regardless of wind direction.

### E. Evaporation uses (1 - RH) instead of vapour pressure deficit
At the same relative humidity, 25 degC air dries a surface about three times faster than 10 degC air. `(K_EVAP_BASE + K_EVAP_WIND * wind) * (1 - RH)` misses that.
- Fix: use `e_sat(T_surface) - e_air` in kPa. With item C this merges the separate "evap" and "sun drying" terms into one Penman-style evaporation, which is the physically correct form: sun dries the road because it heats the surface and raises its saturation vapour pressure.

### F. The fog front lag uses street-level wind
`lag_for` divides distance by `wmean * wr`, where `wr` is the local street wind ratio (down to 0.25). A sheltered street therefore gets the fog up to four times later than its neighbour. The front moves with the wind in the marine layer above the roofs, not the wind in the street.
- Fix: one transport speed for all points, from the 80 m or 925 hPa wind (nudged), with local wind kept only for deposition, cooling and evaporation.

### G. Sunlight ignores street slope and uses the wrong irradiance
The shadow test (horizon profile against sun position) is good. But direct drying is `K_SUN * SW * lit`, where SW is global horizontal irradiance.
- Use direct normal irradiance times the cosine of the angle between the sun and the road surface normal (slope and aspect from the terrain). On SF's steep streets in winter this changes drying by a factor of two or more between north- and south-facing blocks.
- Diffuse light should scale with sky view factor.
- Trees are not in the shadow model. Street-tree canopy shades and also drips fog water onto the road under it (a known local effect on foggy nights).

### H. Wetness is an index with no units
W is 0 to 100 with hand-set rates, so it cannot be checked against any measurement and rain simply pins it at 100.
- Fix: carry water film in mm (or g/m2). Storage capacity per surface type (smooth concrete about 0.2 mm, asphalt about 0.5 to 1 mm, porous more), runoff above capacity scaled by slope. Fog deposition = liquid water content x (settling speed + turbulent deposition x wind); condensation and evaporation from the vapour pressure difference; rain in mm/h directly.
- Then map film thickness to a slipperiness index for display. That fixes the rain saturation issue properly: rain becomes a thick film that takes realistic time to drain and dry.

### I. Slippery is not the same as wet
What skaters call death fog is a thin film mixed with oil, rubber and dust. Three factors the model does not have:
- Days since last rain: the first wetting after a long dry spell is the slickest.
- Sea-salt and grime films pull moisture from the air at about 75 to 80 percent humidity, before any visible water. Near the coast, high humidity alone can make a road greasy.
- Surface type: painted lines, metal plates, rails, polished concrete versus rough asphalt.

### J. Wind simulation limits
- The lattice-Boltzmann runs are neutral (no temperature). The marine layer is capped by a strong inversion, which makes air go around hills and through gaps (Golden Gate, Alemany Gap) instead of over them, and dries the lee side. A neutral run sends too much fog over the hills.
  - Cheaper step: a library per inversion height (for example lids at 250, 400, 600 m) and pick or blend by the estimated fog top.
  - Fuller step: add buoyancy (a temperature field) to the solver.
- The tracer loses fog only by deposition. Real fog also evaporates as it moves over warmer land, fastest by day. Add a loss term that grows with land-minus-sea temperature and sunlight; that gives the daytime retreat toward the coast.
- The 5 m correction is, as documented, over-damped and normalised by a heuristic. It needs wind measurements to calibrate (section 4).
- Temperature and dew point on streets come from a 3 km lattice with no height adjustment. Adjust per street elevation using the profile (cooler with height inside the layer, warmer above the inversion). Items A and D depend on this.

### K. Smaller points
- One wind station (Fort Point) scales the wind for the whole city; one average offset corrects temperature. SF has strong west-east gradients. Use several stations and interpolate the correction in space.
- Forecast-hour fog timing has typical errors of 2 to 3 hours. Show uncertainty: run the model on the RRFS ensemble (REFS) members, or perturb fog timing, and display a probability instead of one value.
- For the next 0 to 6 hours, moving the observed GOES fog edge with the wind usually beats the model. Blend that nowcast into the first hours.

## 3. What is already sound
- Forecast plus observation nudging that fades into the forecast.
- Precomputed direction library with blending (the right way to afford street-scale wind).
- Deposition form `a + b * wind` (settling plus turbulent deposition).
- Horizon-profile shadows.
- Sounding and ceiling used to correct the fog top.
- Fog top from humidity profile and inversion base.

## 4. Extra live data worth adding
| Data | Use | Notes |
|---|---|---|
| METAR ceilings and present weather, more airports (KSQL, KNUQ, KHWD, KCCR) | Cloud base (item A), drizzle and fog flags (B, D) | Already fetching KHAF/KSFO/KOAK |
| ASOS 1-minute or 5-minute data | Finer timing of fog arrival | Check access and delay |
| NDBC buoys off SF (46026, 46237) | Sea surface and marine air temperature: fog formation and layer temperature | Free text files |
| NOAA PORTS/CO-OPS met stations around the bay | More wind and air-temperature points for spatial nudging | Same API as Fort Point |
| Synoptic/MesoWest, MADIS, citizen weather stations | Many T, humidity, wind points inside the city, including hills | Open-access terms were looked at earlier; check limits |
| PurpleAir | Dense temperature and humidity (biased warm, needs correction) | Needs an API key |
| GOES fog/low-stratus product, cloud thickness, 0.5 km visible band by day, downward shortwave | Fog edge, drizzle likelihood, measured sunlight under cloud instead of model sunlight | Product availability to verify |
| Open-Meteo direct normal and diffuse radiation, 80 m wind, soil temperature | Items G, F, C | Confirm the variables exist for RRFS |
| Aircraft soundings near SFO/OAK, wind-profiler/RASS sites | Inversion height many times a day instead of two balloon launches | Access may be restricted; verify |
| Radar (KMUX) | Light rain and heavy drizzle timing | Drizzle is often below radar |
| Caltrans and public webcams | Visual check of fog, and wet-road detection at night from streetlight reflections | Free images; classification is a project of its own |
| SF street-tree list, LiDAR canopy, pavement type and condition data | Tree shade and drip, surface storage (items G, H, I) | Static; one-off processing |
| Days since rain (from the precipitation record already fetched) | Item I | No new source |
| Tide and sea spray near Ocean Beach / Great Highway | Salt film near the coast | Low priority |

## 5. Calibration and validation (highest value)
1. Ground truth. Three to five cheap loggers on your own routes (leaf-wetness sensor, surface IR temperature, air T/RH), one in each district plus one on a hill top and one in the flats. Plus field reports through the existing Google Form with time and place.
2. Archive every live run's inputs and street values for the logger sites (the skipped "live-archive" step). Without the archive nothing can be scored later.
3. Score: wet/dry hit rate, false alarms, Brier score and reliability for the probability, timing error of onset and dry-out. Score each change against the current model so improvements are measured, not assumed.
4. Fit the remaining constants to the data (start with a simple logistic or least-squares fit; keep the physical structure).
5. Compare modelled wind and temperature with the extra stations in section 4 to calibrate the wind ratios and the 5 m correction.

## 6. Suggested order
| Step | What | Effort | Why first |
|---|---|---|---|
| 1 | Archive runs; set up field reports and one or two loggers | Small | Everything else is unmeasurable without it |
| 2 | Cloud base and per-street in-cloud test, with elevation-adjusted T and Td (A, part of J) | Small to medium | Largest physical error; data already fetched |
| 3 | Fix the lag wind (F); evaporation by vapour pressure deficit (E); total cloud in cooling | Small | Cheap, clear errors |
| 4 | In-place fog for non-west winds (D) and drizzle (B) | Medium | Winter and thick-stratus cases |
| 5 | Water film in mm with storage and runoff, slipperiness mapping, days since rain (H, I) | Medium | Makes the output checkable; fixes rain saturation |
| 6 | Pavement temperature model (C) with slope-aware sunlight and trees (G) | Medium to large | Correct dew and drying |
| 7 | More stations and spatial nudging; GOES nowcast for 0 to 6 h; ensemble probability (K) | Medium | Forecast-hour skill |
| 8 | Inversion-capped wind library, fog evaporation over land (J) | Large (GPU time) | Hill and gap flow realism |
| 9 | Calibrate against the collected data; repeat | Ongoing | Turns the proxy into a tested model |

## 7. Limits that will remain
Fog timing at street scale is hard even for national centres. With all of the above, expect a good wet/dry call for the current hours where observations exist, useful probabilities for tonight, and only general guidance for days 2 and 3.
