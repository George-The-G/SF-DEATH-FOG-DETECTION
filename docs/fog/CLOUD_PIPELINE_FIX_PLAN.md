# Cloud Render Pipeline Fix & Unification Plan (`CLOUD_PIPELINE_FIX_PLAN.md`)

Purpose: Fix the 3 pipeline discrepancies (H5/H4/M3, Macro Top Decode, and Guide 14 Pop-In), unify cloud smoothing across the 3 sources (1h `fog3d`, 5m `fog5`, and 5m `macro`) so they never fight each other, and replace magic numbers with self-documenting named constants.
Status: current (implemented 2026-11-18)
Last verified: 2026-11-18

---

## 1. Root Cause: Why the Three Cloud Sources Fight Each Other Today

Today the viewer renders cloud/fog from **three separate sources** that overlap in both **time** and **space** without agreeing on boundaries, heights, or floor masks:

| Source | Time Resolution & Span | Spatial Domain | How It Computes Cloud / Fog | Why It Fights the Others |
|---|---|---|---|---|
| **1. Hourly 3D Voxel Fog (`fog3d_<fff>.png` + `fog{d}.png`)** | **1-hour steps**, full 78h window (`-24h` to `+54h`). | City grid (`17.1 x 16.2 km`, 80m tiles, 24 vertical layers x 25m = 600m). | `amp = lagged(s) * GOES_hourly_fac`. Applies 3D cloud floor (`fogbase`) and lid (`fog_lid`). With `tile_from_3d=True`, the 2D tile is level 0 of the 3D volume. | Outside the `fog5` window (or at its boundary), hourly frames step `w_d` and `fog_top` once per hour, and empty hours have `files[k] = null` (causing stationary cross-fades from 0% to 100%). |
| **2. 5-Minute City Fog (`fog5/tile_*.png` + `fog5/vol_*.png`)** | **5-minute steps**, tiles cover `[-6h, +2h]`, but 3D volumes (`vol_*.png`) only cover **`[-3h, +1h]`**. | Same City grid (`17.1 x 16.2 km`). | `A(x,t)` from semi-Lagrangian carry + GOES-18 5-min correction, temporally smoothed (`SMOOTH_SIGMA=2.5`) and blended 30% (`HOURLY_MIX=0.3`) with its *own* hourly sub-samples—**not** the actual hourly `amps[f]`! | **(a) Temporal boundary jump:** At `-3h` and `+1h`, `fogtravel.js::buildLine` switches abruptly between `fog5` (`A`) and hourly (`amps`), which have different GOES factors (`A != amps[f]`).<br/>**(b) Floor mismatch (H5):** `fog5` 2D tiles (`[-6h, -3h]` and `[+1h, +2h]`) do **not** apply `fogbase` or `fog_lid`, so elevated stratus drops to street level wherever 2D tiles are used. |
| **3. 5-Minute Macro Satellite Deck (`macro/<ts>.png`)** | **5-minute steps**, observed window (`[-6h, now]`, `slackMs = 15 min` past newest scan). | Wide region (`120 x 120 km`, 400m cells). | Analytic slab in `macro.js` from GOES-18 `R=prob, G=conf, B=top/8m`. Fades out over `FADE_M = 2000m` inside the city grid, **except** when `sky=1` where it draws over the city if `top > fogTop + 100..250m`. | **(a) Height bug (`macro.js:32`):** Decodes `top = c0.b * 8. * uEX` (`0..8 m` instead of `0..2040 m`), collapsing the macro deck to sea level and only sampling `c0.b` when looking *up* (`rd.y > 0`).<br/>**(b) Spatial/temporal seam:** `fog2.js` shrinks `edgeFade` from `3000m` to `500m` as soon as `macro` is enabled—even at forecast hours (`> now + 15m`) when `macro` turns **off** (`show = false`), leaving the city fog with a clipped 500m edge! |

---

## 2. Unifying the 3 Cloud Sources (Zero Fighting in Time or Space)

### 2.1 Temporal Handshake: `1h Hourly` <-> `5m fog5` <-> `5m Macro`
1. **Anchor `travel.py` to the True Hourly `amps` and Blend the Window Edges:**
   - Currently `travel.py::smooth_blend` constructs its "hourly line" (`hl`) by sub-sampling its own 5-minute `sm` array every 12 frames (`HOURLY_MIX = 0.3`). At the start (`-3h`) and end (`+1h`) of the `fog5` 3D volume window, `A(t)` does not equal the hourly `amps[f]` used by `outputs.write_fog3d`, causing a visible density jump when scrubbing across `now - 3h` or `now + 1h`.
   - **Fix:** Pass the true hourly `amps` series into `fog5.write` / `travel.smooth_blend`:
     - Use the true hourly `amps` (interpolated to 5-min steps) as the `HOURLY_MIX` reference line.
     - Add a **cosine taper (`WINDOW_TAPER_FRAMES = 6`, i.e., 30 minutes)** at the start and end of the active `fog5` window so `A(t)` blends to **100% agreement (`weight = 1.0`) with the hourly `amp` at the window boundaries**. Scrubbing across the `1h <-> 5m` boundary becomes mathematically C1-continuous (zero pop).
2. **Continuous Input Interpolation (`fog_top` & `weights`) Across Both `fog5` and Hourly Volumes:**
   - In `fog5.py#L128,136`, `top[int(round(iof(t)))]` steps discontinuously every 10 minutes instead of using `_interp_series(top, iof(t))`. We switch `fog5.py` to continuous linear interpolation of `top` and `base`.
3. **Macro Window Handshake (`macro.js` <-> `fog2.js` `edgeFade`):**
   - In `macro.js`, `edgeFade` was shrunk from `3000m` to `500m` whenever `st.on` was true—even when `show` was `false` (outside the 6-hour observed satellite window, i.e., all forecast hours!).
   - **Fix:** Only shrink `fog2.js` `edgeFade` to `MACRO_ACTIVE_EDGE_FADE_M` (`500m`) during frames where the macro slab is **actually visible (`show == true`)**; smoothly restore `DEFAULT_EDGE_FADE_M` (`3000m`) when scrubbing into forecast hours where `macro` has no satellite frames.

---

### 2.2 Spatial & Vertical Handshake: Floor, Lid, and Sky Layers
1. **Fix Discrepancy H5 (`fog5` 2D Tiles Must Apply Cloud Floor & Lid):**
   - In `microclimate/live/fog5.py`, compute the surface-level weight `w0 = w3[0]` from `fogbase` (cloud floor) and `lid_weight` for every 5-minute frame, and multiply the 2D tile by `w0` (`tile = round(255 * A[k] * trb * w0)`).
   - Now 1h 3D volumes, 1h 2D tiles, 5m 3D volumes, and 5m 2D tiles all share the **exact same cloud base and lid rule**. Elevated stratus never drops to the street in 2D tiles.
2. **Fix Macro Cloud Top Decode & Ray-From-Above/Below (`macro.js`):**
   - Define `GOES_TOP_SCALE_M = 2040.0` (`255 * 8.0 m/step`, matching `goes5.py::macro_rgba` where `B = top_m / 8`).
   - Fix the ray-plane intersection in `macro.js` (`FRAG`):
     - Use `abs(rd.y) > RAY_DIR_EPS` so rays looking **down from above** (`rd.y < 0`, the default camera!) as well as rays looking **up from street level** (`rd.y > 0`) sample `c0.b` at the reference plane `tm`.
     - Decode `top = clamp(c0.b * GOES_TOP_SCALE_M, MIN_MACRO_TOP_M, MAX_MACRO_TOP_M) * uEX` (`150m..4000m`) and `base = max(top - BASE_THICK_M * uEX, MIN_MACRO_BASE_M * uEX)`.
3. **Prevent Macro / Sky Layer Double-Drawing Inside the City Grid (`macro.js`):**
   - Outside the city grid, `seam(p.xz) = 1.0` and fades to `0.0` over `FADE_M = 2000m` inside the city boundary.
   - Inside the city grid, when `sky = 1` (`uSky > 0.5`), `macro.js` only draws an upper deck when the satellite top `tm_m` is well above the city voxel fog top (`uTopDef + SKY_SEPARATION_BUFFER_M`), AND clamps the macro slab's `base` inside the city to `max(base, (uTopDef + SKY_SEPARATION_BUFFER_M) * uEX)`. That guarantees **the macro slab and the city voxel volume never occupy the same vertical altitude band**.

---

### 2.3 Guide 14: Eliminating Hourly Forecast Pop-In & `null` Hour Gaps
Outside the `[-3h, +1h]` 5-minute 3D volume window (i.e., the 50+ forecast hours):
1. **No Isolated `null` 3D Hours Inside a Fog Event (`outputs.py` / `fog2.js`):**
   - In `fog2.js::prepVoxel`, when blending between two hours `kA` and `kB` where one hour has a 3D file (`d3A`) and the adjacent hour has `files[k] = null` (0 fog aloft), `fog2.js` previously aborted 3D mode for *both* hours (`if (sA.e && sB.e)` failed unless both had files, falling back to 2D tiles!) or popped.
   -Verify/ensure `d3State` returns an explicit empty 3D volume (`emptyEntry`) for `files[k] == null` so a 3D hour smoothly fades/advects into clear air without dropping out of `data3d` mode into the 2D flat tile.
2. **Motion-Compensated Advection Shift on Hourly Blends (`fog2.js` + `fogflow.js`):**
   - For hourly forecast steps (`span = 1.0 h`), `VOX_FS` shifts `uVolA` by `dA = -wv * uMixT` and `uVolB` by `dB = +wv * (1. - uMixT)` along the terrain-aware wind `uWind` (`fogflow.solveWind`).
   - Currently `uDriftS = 3600 * span * P.advect` can shift coordinates up to `~20 km` (more than 100% of the 17 km grid!) over a 1-hour span, pulling mirrored edge copies into the city.
   - **Fix:** Clamp the hourly volume coordinate advection shift to `MAX_VOLUME_SHIFT_FRAC = 0.35` of the grid (or fade density at the upwind boundary using the wind projection) so fronts slide smoothly along the terrain-aware wind without wrapping mirrored copies across the domain.

---

## 3. Magic Numbers Audit & Replacement Table

We will replace hard-to-read magic literals across Python and WebGL shaders with named constants at the top of each module (and inject shader constants via JS template literals `${CONST_NAME}` so GLSL stays readable and dry):

### 3.1 `site/v3/macro.js` & `microclimate/live/goes5.py`
| Current Magic Number | Location | Replacement Named Constant | Meaning |
|---|---|---|---|
| `8.0` / `8.` / `2040.` | `goes5.py:332`, `macro.js:32, 50` | `GOES_TOP_STEP_M = 8.0` / `GOES_TOP_MAX_M = 2040.0` | GOES cloud-top encoding in Blue channel (`uint8 * 8 m = 0..2040 m`). |
| `.85` | `macro.js:30` | `REF_PLANE_TOP_FRAC = 0.85` | Fraction of `uTopDef` used as the initial ray-plane height to look up `c0.b`. |
| `100.`, `150.`, `3000.`, `4000.` | `macro.js:50` | `SKY_GAP_MIN_M = 100`, `SKY_GAP_RAMP_M = 150`, `SKY_CIRRUS_FADE_START_M = 3000`, `SKY_CIRRUS_MAX_M = 4000` | Altitude separation above `uTopDef` required for an upper sky deck over the city, and cirrus fade-out ceiling. |
| `10000.`, `15000.` | `macro.js:42` | `NOISE_FADE_NEAR_M = 10000`, `NOISE_FADE_FAR_M = 15000` | Distance range (m) over which close-up billow noise fades out into the smooth satellite deck. |
| `30 * EX` | `macro.js:85` | `CITY_HOLE_DROP_M = 30` | How far (m) wide terrain vertices drop beneath the detailed city terrain footprint to prevent z-fighting. |
| `500` | `macro.js:141` | `MACRO_CITY_EDGE_FADE_M = 500` | `fog2.js` `edgeFade` distance when the macro cloud deck is actively surrounding the city. |
| `6000`, `4000`, `300000` | `macro.js:155` | `MACRO_FETCH_DIST_M = 6000`, `MACRO_FETCH_ALT_M = 4000`, `INDEX_POLL_MS = 300000` | Camera distance/altitude thresholds to start fetching macro frames, and 5-min index poll interval. |

### 3.2 `site/v3/fog2.js`
| Current Magic Number | Location | Replacement Named Constant | Meaning |
|---|---|---|---|
| `40000.` | `fog2.js:86, 282, 316` | `MAX_RAY_DIST_M = 40000.0` | Maximum ray-march distance (40 km) and 16-bit fog depth normalization scale. |
| `.0105 / EX * 2.2 * 1.5` and `.55` | `fog2.js:139, 690` | `BASE_EXTINCTION_PER_M = 0.03465`, `SHADOW_EXTINCTION_RATIO = 0.55` | Volumetric extinction coefficient $\sigma_t$ ($\text{m}^{-1}$) and forward-scattering shadow attenuation ratio. |
| `120.`, `230.` | `fog2.js:220, 222` | `FLOW_CYCLE_SEC = 120.0`, `DETAIL_NOISE_TILE_M = 230.0` | Two-phase Vlachos flow-map reset period (s) and 3D detail noise spatial period (m). |
| `.12` | `fog2.js:192` | `GROUND_BILLOW_BIAS = 0.12` | Applied **only** in 2D tile mode (`uUse3 < 0.5`); set to `0.0` in 3D mode (`uUse3 > 0.5`) so it doesn't fight the 3D cloud floor (`fogbase`). |
| `150`, `3000` | `fog2.js:832, 835` | `MOTION_LINGER_MS = 150`, `STILL_REFRESH_MS = 3000` | How long after camera movement stops before still-frame accumulation starts, and periodic refresh interval. |

### 3.3 `microclimate/live/` & `microclimate.toml`
| Current Magic Number / Config | Location | Replacement Named Constant | Meaning |
|---|---|---|---|
| `eddy_diffusivity_m2s = 2.0` | `microclimate.toml` | `eddy_diffusivity_m2s = 20.0`, `eddy_diffusivity_v_m2s = 8.0` | Document the actual horizontal (`kd=20`) and vertical (`kdv=8`) diffusivity used by the walls tracer library (Discrepancy H4). |
| `0.5` in `travel.py:114` | `travel.py:114`, `smooth_blend` | `WINDOW_TAPER_FRAMES = 6` (30 min) | Smooth boundary taper between `fog5` (`A`) and hourly `amps` at window edges. |

---

## 4. Implementation Steps & Verification

1. **Step 1 (`microclimate.toml`, `fog5.py`, `travel.py`, `build.py`):**
   - Fix **Discrepancy H4** in `microclimate.toml` (`kd = 20.0`, `kdv = 8.0`).
   - Fix **Discrepancy H5** in `microclimate/live/fog5.py`: apply the `fogbase` cloud floor and `lid_weight` to 5-minute 2D tiles (`w3[0]`) and linearly interpolate `fog_top` at 5-minute resolution.
   - Unify `travel.py` and hourly `amps`: pass the hourly `amps` reference into `fog5.write` / `travel.smooth_blend` and taper the window edges (`WINDOW_TAPER_FRAMES = 6`) so `fog5` and `1h` never jump at `-3h` or `+1h`.
2. **Step 2 (`site/v3/macro.js`):**
   - Fix the **Macro Cloud Top Decode bug** (`GOES_TOP_MAX_M = 2040.0`), fix ray intersection from above (`abs(rd.y) > 1e-4`), prevent vertical overlap with city voxel fog when `sky = 1`, only shrink `edgeFade` when the macro slab is actively visible (`show == true`), and replace all magic literals with named constants.
3. **Step 3 (`site/v3/fog2.js`, `site/v3/foglab.js`):**
   - Update stale `sky` switch comments/hints (Discrepancy M3).
   - Remove the `+0.12` low-altitude bias (`GROUND_BILLOW_BIAS`) when `uUse3 > 0.5` so `BAKE_A` respects the 3D cloud floor (`fogbase`).
   - Clamp hourly wind-drift UV shift (`MAX_VOLUME_SHIFT_FRAC = 0.35`) so 1-hour forecast steps slide smoothly without wrapping mirrored ocean fog across the city.
   - Replace shader/controller magic numbers with named constants.
4. **Step 4 (Verification):**
   - Update and run `.\.venv\Scripts\pytest.exe` (including new tests in `tests_mc/test_fog5.py` and `tests_mc/test_travel.py` verifying floor application on 5m 2D tiles and boundary continuity).
   - Run `node --test tests_js/*.mjs`.
   - Run headless Playwright verification (`.\.venv\Scripts\python.exe tools/preview.py --check "?beta=1&night=2026-10-01"`).
