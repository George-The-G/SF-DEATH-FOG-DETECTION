# Multi-scale fog simulation: from the offshore cloud front to street level

Plan only. No code changed, no PRs. Written 2026-11-15 from the v3-dev branch (da494de) and the existing project plans: fog/cloud_tech_survey.md, replay/goes-anchor-plan.md, replay/goes-crop-format.md, replay/replay-engine-review.md, validate/validation-plan.md, madis/data-source-review.md, fog/grid-artifact-diagnosis.md, render/*.md.

"Estimate" means reasoned, not measured. "(verify)" means I could not confirm it from a primary source today.

---

## 1. Short version

1. **Your design is right in spirit, with one change of shape.** Spend resolution where fog meets the city, skip it everywhere else. At the large scales, though, fog and stratus are a thin layer (a few hundred metres deep over 100 km), so voxels are wasted there. The plan uses **layered columns (2.5D) at the big scales and true sparse 3D voxels only inside the city**, getting smaller toward the streets:

   | Level | Area | Cell | What it holds | Where it runs |
   |---|---|---|---|---|
   | L0 Macro | about 120 x 100 km (12,000 km²) | 1-2 km | cloud cover, base, top, thickness; drift with the large-scale wind | Action, every 10 min |
   | L1 Regional | about 40 x 35 km (Point Reyes to Pacifica, Gate, Marin, Bay) | 200 m | the marine layer as a moving "fluid sheet": its depth (= fog top), its flow, fog inside it | Action |
   | L2 City | today's 17 x 16 km box, 0-1 km high | 40 m voxels, sparse 8³ bricks | 3D fog water: cloud floor, top, holes, thinning over warm streets | Action (5-min state), browser (in between) |
   | L3 Street | only near the camera and in chosen neighbourhoods, 0-60 m above ground | 5-10 m voxels, sparse bricks | how streets and buildings channel, shelter and warm the fog | offline wind runs on your PC, applied in the browser |

2. **Each of your listed flaws has a specific cause in today's code** (section 2), and most get fixed by one new idea: track the fog's **water and temperature**, not just "fog yes/no". The cloud floor, the top, thinning over warm streets and the fog moving at the right speed then all come out of the same physics instead of separate guesses.
3. **Reality anchors every 5 minutes.** Each GOES scan, METAR ceiling, station reading, buoy and webcam view corrects the simulation at its own spot and scale, weighted by how much we trust it. Between anchors, the simulation carries the fog forward with live winds (section 6).
4. **Webcams are the biggest new data win, used in a specific way:** not as pictures to stitch, but as yes/no checks on known landmarks (Sutro Tower, Golden Gate towers, Salesforce Tower, hilltops, streetlights at night). Each check tells us whether fog sits along one line of sight and at what height. That pins the fog top and base far better than any model (section 5.3).
5. **It fits the free stack if we are careful:** the Action does 2.5D and 40 m work on CPU, your RTX 4080 does the one-time street-scale wind runs, and the phone draws it with the techniques from the earlier survey. Street-scale data is the one thing that may outgrow GitHub Pages (decision D4).
6. **Order of work:** quick fixes first (fog speed, cloud floor), then the wide satellite view, then the marine-layer model, then the 3D city state with anchoring, then webcams, then street scale. Each phase has a validation gate (section 10).

Decisions only you can make are in section 12.

---

## 2. Why today's fog looks the way it does

How it works today (from the code):
- **Wind:** XLB (`microclimate/wind.py`, `windrot.py`) solved a *steady, time-averaged* LES wind on a 20 m grid (855 x 811 x 40, up to 800 m) for 15 directions (22.5-135 and 180-360; 157.5 failed). The live build fits the in-city stations to pick and blend directions and a speed per region (`live/anchors.py`).
- **Fog shape:** `microclimate/fog.py` ran a steady fog tracer on each wind field (fed from the upwind edge), stored as 80 m tiles per direction (`tr80`).
- **Fog timing:** one coast fog signal s(t) from the RRFS forecast (via Open-Meteo), nudged by GOES and KHAF, delayed by a travel time per cell at 1.3 x the 10 m wind (`live/forcing.py`, guide 09).
- **Fog top:** from Open-Meteo humidity and inversion at pressure levels (1000, 975, 950 ... hPa, about 200 m apart), with a 62 m floor and a 900 m cap (`fogtop.py`).
- **Viewer:** `site/v3/fog2.js` bakes a voxel volume per hour from the 2D tile and the top, from the ground up, and blends hours.

| Your flaw | Cause in the code | Fix in this plan |
|---|---|---|
| Fog is a column; no cloud floor | The bake fills each 80 m column from `ground * follow` up to the top. There is no base: the cloud-base model from guide 08 (`base = zm + 125 (T - Td) + offset`) wets streets in the pipeline but never reaches the renderer. | The L2 state carries water and temperature per voxel, so fog only forms where air is saturated. The floor appears where air is too warm or dry (section 4.2). A first quick fix ships the guide 08 base to the viewer (Phase 0). |
| Fog top guessed | Pressure levels are about 200 m apart, so the top snaps to a few values; the code itself notes that `fog_top_m` is often the 62 m floor or the 900 m cap. One top for the whole city. | Top becomes the depth of the marine layer in the L1 model (section 4.1), corrected by HRRR native levels (about 20-40 m apart near the ground), the Oakland sounding, satellite fog edges on hillsides, and webcam landmarks. |
| No street micro-flow | 20 m cells are filled to the cell's *mean* building height (PROGRESS.md M1), so streets narrower than 20 m do not exist in the wind fields. | L3: 5 m wind runs in tiles, nested inside the 20 m fields (section 4.4). |
| Streets don't warm and thin the fog | Pavement heat (guide 07) is computed per street point for wetness but never feeds back into the fog. | Surface heat warms the lowest air layers in the L2 state. About 1 K of warming evaporates typical fog water, so the base lifts over warm areas unless the fog is thick and constantly re-supplied (section 4.3). |
| Fog does not move with live wind | (a) The fog field is a fixed pattern per direction times a delayed signal, so edges never travel; they fade in place. (b) Frames are hourly. (c) Viewer noise drifts at `advect: 0.1`, **10 % of the wind speed** (`fog2.js` line 38). (d) The steady XLB fields have no gusts or eddies. | The L1/L2 fog is genuinely carried by the wind every step and corrected by the 5-min scans (section 6). Viewer detail moves at the real local wind (Phase 0 fix). Gust and eddy motion comes from a small set of time-varying XLB snapshots (section 4.4). |

---

## 3. The nested design

### 3.1 Why layers outside, voxels inside

Marine stratus over the ocean is typically 200-600 m thick and tens of km wide, with a sharp, nearly flat top under the inversion. Over 100 km a 3D grid fine enough to show the top (about 25 m) would be 4,000 x 4,000 x 40 cells, 640 M cells, nearly all empty or nearly uniform. A column with **cover, base, top and water amount** describes it exactly and costs 4 numbers per cell. The 3D structure only matters where terrain and buildings cut into the layer, which is the city and its hills. That is where L2 and L3 put real voxels.

This also matches the earlier review's point that a true octree is a poor fit for WebGL2. The sparse structure here is a **flat brick pool with one lookup table** (section 8.2), which is the form that runs well on phones.

### 3.2 Domains

- **L0 Macro, about 120 x 100 km**, centred west of the city so it reaches about 60 km offshore: Point Reyes and Bodega in the north, Half Moon Bay and Pescadero in the south, the East Bay hills in the east. This is where approaching banks are seen 1-3 h before they arrive (5 m/s = 18 km/h). Grid: GOES pixels (2 km IR, 0.5 km visible) resampled to 1 km. Size: about 12,000 cells per field, trivial.
- **L1 Regional, about 40 x 35 km at 200 m** (200 x 175 cells): covers the Gate approach, Marin Headlands, the San Bruno gap, Daly City and the bay to Oakland. It holds the marine-layer model (section 4.1), which is what decides whether fog pours through the Gate, spills over Twin Peaks or stops at the hills.
- **L2 City, today's 17 x 16 km box, 0-1 km, at 40 m.** Dense, that is 430 x 400 x 25 = 4.3 M voxels. Sparse, only bricks inside the fog layer band plus a margin are active: typically 15-40 % (estimate), so about 1-2 M voxels.
- **L3 Street, 5-10 m**, two ways:
  - *Static*: offline wind tiles at 5 m for chosen neighbourhoods (start with Potrero, Golden Gate Heights and the drawn routes; section 4.4).
  - *Dynamic*: in the browser, only bricks within about 500-800 m of the camera at street level and 0-60 m above ground get L3 detail.

Each level takes its edge values from the level above and sends its average back up, so the levels never disagree on where fog is at the larger scale.

### 3.3 What the terrain needs

- L0 needs a wider DEM: 3DEP 1/3 arc-second (10 m) for the region is free and public domain; resample to 30 m for L1 and to 200-500 m for the macro horizon. Ocean is flat.
- The viewer's terrain is one 153k-triangle mesh today. The macro view needs **tiled terrain with distance LOD** (quadtree tiles with skirts, like map renderers): 1-2 levels for the city, 3-4 coarser rings out to 60 km. This is plain geometry work; it overlaps with render Plan A.

---

## 4. The physics at each level

### 4.1 L1: the marine layer as a moving sheet (the new core)

SF fog is the top of the marine layer: cool, moist air under a warm inversion. Its flow is well described by a **reduced-gravity shallow-water model**: one layer of depth h (the fog top above the ground) and velocity u, driven by the large-scale push and slowed by friction, with the inversion acting like a lid. This is the textbook model for marine-layer flow through gaps (it reproduces flow speeding through the Golden Gate, spilling over low gaps, and banking up against ridges).

What it gives us:
- **A real fog top**, varying in space: deep against the coast hills, thinner where it squeezes through the Gate, rising and falling with the push. This replaces the single guessed top.
- **The flow that carries fog** at the layer scale, always consistent with the terrain.
- A physical way to say when fog **cannot** cross a ridge: where the ground rises above h.

Inputs: inversion height and strength (HRRR native levels, Oakland sounding twice a day, Bodega Bay RASS if it is reporting), the offshore wind (HRRR, buoys 46026/46237/46013/46012), and the pressure push (coast minus inland pressure, from stations). Corrected every scan by the observed fog edge (section 6).

Cost: 35,000 cells, 2D, an explicit step every about 10 s of model time; one 10-min Action run steps 60-120 times: well under a second in numpy (estimate). It can also run in the browser on the GPU later.

The existing `fogflow.js` mass-consistent wind is a cut-down version of the same idea (fixed top, no layer depth). L1 generalises it.

### 4.2 L2: fog water and temperature in 3D (fixes the column, the floor and the top)

Each active voxel carries two numbers instead of one fog value:
- **total water** q_t (vapour plus droplets), and
- **liquid-water temperature** θ_l (temperature corrected for condensation).

Fog water is then simply whatever exceeds saturation: q_l = max(0, q_t - q_sat(T, p)). This is the standard "warm cloud" scheme in weather models, reduced to its essentials.

What falls out of it for free:
- **Cloud floor:** where the air near the ground is warmer or drier, q_l is zero below some height, so fog lifts into stratus. The base height comes from the same rule as guide 08 (about 125 m per K of dew-point spread), but now in 3D and changing in time.
- **Fog top:** above the inversion the air is warm and dry, so q_l drops to zero within one or two voxels. The top is where L1 says it is, sharpened by the temperature jump.
- **Holes and thin spots:** wherever the air warms or mixes with dry air.
- **Drizzle and deposition**, already in guides 11 and 05, use q_l directly.

Each 5-min step: carry q_t and θ_l along the wind (L1 flow for the layer scale; inside it, the blended XLB 3D field scaled to the local L1 speed for the vertical and terrain structure); add sources (cooling at the top at night, radiation by day using guide 06 sun and GOES DSR, surface heat from section 4.3); then correct to observations (section 6).

Size and cost (estimate): 1-2 M active voxels x 2 bytes x 2 fields = 4-8 MB state; a semi-Lagrangian step in numpy is about 0.2-0.5 s; 30 steps per run (one per 20 s of the 10-min gap) is about 10-15 s. It fits in the Action. If not, numba or a coarser 80 m inner grid are fallbacks.

### 4.3 Urban heat: why fog thins over streets

Numbers that make this matter: at 13 °C, saturated air holds about 9.4 g of vapour per kg; warming it 1 K raises that by about 0.6 g/kg. Typical fog holds 0.1-0.3 g/kg of droplets. So **warming the air near the ground by only 0.2-0.5 K evaporates the fog there**, unless the wind keeps bringing fresh fog faster than the heat removes it.

That gives exactly the behaviour you described:
- Thin or patchy fog over warm streets, with the base lifted a few tens of metres (the Mission, SoMa, the flats).
- Fog down to the ground where the layer is thick and the push is strong (the Sunset, Richmond, Twin Peaks windward side), and at night when the pavement has cooled.
- When the city is fully covered under a deep layer, little sun gets through, the pavement cools toward air temperature, and fog reaches the ground almost everywhere.

Model: surface heat flux H(x, t) per 40 m cell from the existing pavement energy balance (guide 07, now per cell instead of per street point), with **imperviousness and building cover** as the static map (NLCD imperviousness at 30 m, public domain; DataSF footprints already in the repo). The heat warms the lowest layers with a height scale of about 10-40 m that shrinks with stronger wind. A static cross-check of where the city runs warm: Landsat thermal (100 m, resampled to 30 m, free) and ECOSTRESS (70 m, irregular passes, free) land-surface temperature on clear days.

Honest limit: no direct measurement of near-ground fog thinning exists in SF. Validation is indirect: station T/Td, webcam views of streetlights and building bases, and how the satellite edge recedes on warm afternoons.

### 4.4 L3: street-scale wind

What exists: 20 m steady LES fields per direction from XLB, run on your RTX 4080 at about 3,550 MLUPS (PROGRESS.md: the full domain took 6-7 min per direction).

New offline runs (PC GPU, once; then reused):
- **Nested 5 m tiles**, 2 x 2 km each with a 400 m margin: 560 x 560 x 60 cells is about 19 M cells, about 3 GB on the GPU and about 30-40 min per direction (estimate from the benchmark). Boundary values come from the 20 m field.
- Buildings from the DataSF footprints with real per-building heights (not cell averages), so streets and alleys exist in the geometry.
- **Directions:** 6 to start (the prevailing 247.5-315 sector plus 225 and 337.5), the rest later. 4 priority tiles x 6 directions is about 15 h of GPU time (estimate).
- **Unsteady snapshots:** save, for the prevailing directions, 30-60 instantaneous snapshots at 20 m as well as the mean. Played in a loop and scaled to the live speed, they give the gusts and swirling that the steady mean lacks, at no runtime physics cost.

How L3 is used: it does not run as a separate simulation live. It provides, per 5-10 m brick, (a) the local flow that moves fog detail, (b) a **sheltering factor** (how much fresh fog air a pocket receives versus the open street), and (c) the street heat from 4.3 at full resolution. The browser combines the L2 fog water with these to show fog pouring down a street, pooling behind a block, or thinning over a warm intersection.

Limit to state plainly: XLB as set up has no buoyancy, so the fields are neutral. Under the inversion the real flow is stably layered. The L1 model carries the layer-scale stability; the 3D fields provide terrain and building structure inside it. A stability-aware rerun is possible later but is a research step, not a plan item.

---

## 5. Data sources

Licence column: "PD" is US government public domain. Everything marked PD is free and can be redistributed with credit.

### 5.1 Satellites

| Source | What it gives | Cadence and resolution | Feasibility | Licence |
|---|---|---|---|---|
| **GOES-18 ABI (GOES-West), CONUS sector** | Visible band 2 (0.5 km, day), bands 7 and 13 (2 km, night fog signal 7 minus 13), cloud mask and cloud-top height products already used | **Every 5 min** (probed 2026-11-14). Full disk every 10 min. | Already ingested hourly; the 5-min crop store is planned (goes-crop-format.md). Band 2 is about 60 MB per CONUS scan, so read only the SF chunks with range requests (as `live/dsr.py` already does). | PD, NOAA open data on AWS |
| GOES-18 mesoscale sectors | Same bands every 1 min (or 30 s) | Only when NOAA points a sector at California, which is not ours to choose | Opportunistic: when present, use it; never depend on it | PD |
| GOES-19 (GOES-East) | Same, from the east | Views SF at a very low angle | Not worth it | PD |
| VIIRS (S-NPP, NOAA-20, NOAA-21) | Fog extent at 375 m, night fog products | 2-4 passes a day | Calibration of edge detail, not live | PD |
| Sentinel-2 / Landsat | 10-30 m daytime images, thermal (Landsat) | Every few days, late morning | Static: fog-edge climatology on hills, warm-surface map for 4.3 | Copernicus free licence / PD |

GOES views SF at about 46° from vertical, so a 400 m fog top appears about 0.4 km northeast of its true place (parallax, goes-anchor-plan 3.2). The city is only about 5-8 IR pixels across, so the satellite sets **where the bank is at km scale**; the lower levels set the detail.

A cheap extra signal: on the visible band, **the fog edge on a hillside sits at the fog-top elevation**. Matching the observed edge on Marin Headlands, San Bruno Mountain, Montara and Twin Peaks against the DEM gives a measured top height several times per hour by day.

### 5.2 Models and profiles

| Source | What it gives | Cadence | Feasibility | Licence |
|---|---|---|---|---|
| **HRRR** (3 km) | Wind, temperature and humidity on native levels (about 20-40 m apart near the ground), cloud base, visibility, boundary-layer height | New run every hour, output every 15 min to 18 h | On AWS (noaa-hrrr-bdp-pds) with index files for byte-range reads of a few fields over our box (the Herbie tool does this). | PD |
| RRFS (3 km, HRRR's successor) | Same, 15-min output | Hourly. Was pre-production in Aug 2026 with production scheduled for Oct 2026 (verify); hosting moved off AWS into NOAA servers | Already used through Open-Meteo; switch to direct native levels once hosting is settled | PD (Open-Meteo: CC BY 4.0) |
| RTMA-RU (2.5 km analysis) | Observed-and-model blend of surface wind, T, Td, visibility, ceiling | Every 15 min | Good for the L0/L1 boundary and for checking (verify fields on AWS) | PD |
| Oakland sounding | Inversion height and strength | 2 a day | Already used | PD |
| Bodega Bay profiler (NOAA PSL, 449 MHz wind profiler plus RASS temperature) | Wind and temperature profile, marine-layer depth upwind | Hourly when working | PSL's outage log says the profiler "is not transmitting" (dates inconsistent). Check the real-time page before building on it. | PD |
| Aircraft profiles (AMDAR/ACARS at SFO/OAK) | Dozens of temperature profiles a day through the inversion | Frequent | Restricted for private MADIS users (madis review). Leave out unless access changes. | Restricted |

### 5.3 Ground truth in the city

| Source | What it gives | Feasibility | Licence |
|---|---|---|---|
| METAR/SPECI, ASOS (KSFO, KOAK, KHAF, KSQL) | Ceiling (= cloud floor) and visibility, every hour and on change; 1-min ASOS via IEM | Already used | PD |
| NWS stations, CO-OPS 9414290 and other bay stations | Wind, T, Td at the shore | Already used | PD |
| NOAA PORTS (SF Bay) visibility sensors | Fog visibility over the water at several bay sites (verify which sites report) | Same CO-OPS API as 9414290 | PD |
| NDBC buoys 46026, 46237, 46013, 46012 | Upwind wind, air and **sea temperature** (cold upwelling makes fog) | Easy, keyless | PD |
| MADIS mesonet (CWOP, RAWS, PG&E) | Block-scale T, Td, wind | Pending your account; only open providers public | Mixed (madis review) |
| CWOP direct over APRS-IS | Same citizen stations, no account | New parser | Terms unverified |
| **Webcams** | Is a landmark visible or hidden; fog top and base heights; at night, streetlight halos | See below | Varies; see below |

**Webcams, how to use them.**

Stitching camera pictures into the model does not work. Each camera instead becomes a set of **line-of-sight sensors**:
1. For each camera, mark once by hand a few **landmarks at known 3D positions** (from the DEM and building data): Sutro Tower (top about 550 m above sea level), the Golden Gate towers (227 m above the water), Salesforce Tower (326 m), hilltops, a ridge line, buildings at several distances, and streetlights for night.
2. Each frame, compare each landmark's contrast with a clear-day reference. The fading follows a known law (Koschmieder), so it gives the **fog thickness along that line**, from "clear" to "fully hidden".
3. Landmarks at different heights give the **top** ("Sutro's top is out, its base is hidden": the top is between those heights). Landmarks at different distances give **density along the street**. Lit buildings and streetlights work at night, when satellites are weakest.
4. Only these numbers are kept. **No images are stored or republished.**

Processing is plain image arithmetic (Pillow/numpy) on about 20-50 cameras, a few seconds per run (estimate).

Candidates, all unverified for terms and pointing:

| Camera source | Notes |
|---|---|
| Caltrans CCTV (CWWP2) | Free JSON/XML/CSV lists of cameras by district with still images. "There is no charge for the use of this data." Caltrans site content is public domain, but they warn some content may be copyrighted. Bay Bridge, US-101, I-280 and Golden Gate approach views. **Best first source.** Ask the Caltrans traveler-info contact before any public display. |
| ALERTCalifornia (UC San Diego) | Ridge-top cameras looking over the region (Mt Tam and others); ideal for the top height from above. Terms unverified. |
| Golden Gate Bridge district, Exploratorium, SF Port, university and hotel cams | Classic fog views. Each has its own terms. |
| Windy webcams API, EarthCam | Aggregators. Keys and display terms apply. |

The rule I recommend (decision D3): derived numbers only, kept on the server; each camera's terms checked and recorded before it is added; a camera that forbids automated access is skipped.

---

## 6. Anchoring to reality at fixed steps

### 6.1 The cycle

The Action runs every 10 min (cron `*/10` in collect.yml; GitHub can start runs late). Each run:
1. **Fetch** everything new since the last run: 1-3 GOES scans, METARs, stations, buoys, HRRR (when a new hour is out), webcam frames.
2. **Forecast step** for each 5-min slot: carry L0 by HRRR wind; step the L1 marine-layer model; carry the L2 water and temperature (section 4).
3. **Anchor (analysis) at each 5-min mark**: pull the state toward the observations valid at that time (6.2).
4. **Replay smoothing** for the past few hours: a backward pass so the replay fits observations before and after each moment (already planned in goes-anchor-plan 3.4).
5. **Forecast tail** past the newest anchor: model only, fading to the HRRR/RRFS forecast over 1-3 h, labelled "forecast".
6. **Publish** 5-min frames for the observed window, hourly after that (tiered, per the replay review).

### 6.2 How each observation corrects the state

One method throughout: **optimal interpolation**. The correction is weighted by how reliable each observation is versus the model, and spreads out over a distance that suits each observation type. Spreading is **along the wind and blocked by terrain above the fog top**, so a ceiling at KSFO does not change fog on the far side of San Bruno Mountain.

| Observation | What it measures, in model terms | Corrects | Spread | Trust |
|---|---|---|---|---|
| GOES cloud cover (5 min) | Is there low cloud in this 2 km column, after parallax | L0 cover; L1 layer extent; L2 column water | 2-5 km | High by day, lower at night, near zero at dawn and dusk and under high cloud (goes-crop-format rule) |
| GOES visible edge on hillsides | Fog-top elevation | L1 depth | 5-10 km along the coast | High by day |
| HRRR native profile | Inversion height and strength | L1 depth and lid strength; L2 temperature above the top | Its 3 km grid | Medium (model) |
| Sounding / RASS | Inversion height | L1 depth | 20-50 km | High, but only 2 a day |
| METAR ceiling | Cloud floor at the airport | L2 near-ground temperature and humidity, so the base matches | 3-8 km | High |
| METAR / PORTS visibility | Near-ground fog water | L2 lowest layers | 2-5 km | High |
| Station T, Td | Air temperature and humidity | L2 lowest layers (already fitted per hour in anchors.py) | 1-3 km | Medium (siting) |
| Station wind | Local flow | Choice and blend of XLB directions and speed per region (already done), L1 flow | Region | Medium |
| Webcam line of sight | Fog amount along a known line | L2 voxels along that line | Along the line only | Medium; set per camera after a few weeks |

Starting values for weights come from the existing plans (for example `Kmax` about 0.5 for GOES in goes-anchor-plan). All get tuned with the validation harness (section 10). Later, an ensemble version (about 16 slightly different runs whose spread sets the weights) is possible in L0/L1 because they are 2D and cheap; L2 stays with optimal interpolation.

### 6.3 Between anchors, in the browser

The browser receives the 5-min L2 state (or a compact form of it, section 8.3) and the flow fields. Between frames it:
- interpolates in time with a smooth curve (no pulses every 5 min; goes-anchor-plan section 4),
- moves the fine detail at the real local wind speed, with L3 flow near the camera, and the gust snapshots for unsteadiness,
- never moves the large-scale fog away from where the last anchor put it; at the next frame it eases to the new state over a few seconds.

---

## 7. Where the compute goes

| Work | Where | Cost (estimate) | Notes |
|---|---|---|---|
| XLB 5 m street tiles, gust snapshots, re-runs of 20 m fields | Your PC (RTX 4080), offline, once | about 15 h GPU for the first 4 tiles x 6 directions | Results published as static assets |
| Static maps: imperviousness, sheltering, sky view, warm-surface map | PC CPU, once | minutes | |
| L0 + L1 + L2 cycle, analysis, frames | GitHub Action (2 vCPU, 7 GB, 10-min timeout) | about 1-3 min per run on top of today | Must keep a fallback: if the new path fails, publish today's tiles |
| Webcam processing | Action | seconds | Only numbers kept |
| Time interpolation, detail motion, L3 near the camera, rendering | Browser | see section 8 | |

If the Action budget proves too tight, the options in order: numba; 80 m instead of 40 m in L2; move the cycle to a small cloud job (Cloudflare already hosts the bus proxy, though Workers are not suited to numpy); or run it on your PC as a scheduled task (only works while the PC is on). Decision D2.

---

## 8. Rendering

### 8.1 What carries over from the earlier survey

The survey (fog/cloud_tech_survey.md) stands. Its top ideas are exactly what this needs:
- 1-in-16 low-resolution tracing with temporal reprojection,
- empty-space skipping (corrected as in grid-artifact-diagnosis.md: never skip past where the ray reaches the fog layer),
- flow-mapped detail noise on the real wind,
- baked sun transmittance,
- an analytic far field.

### 8.2 New pieces

1. **Brick pool with a lookup table (the sparse voxels).** Active 8³ bricks are packed into one 3D texture (the pool). A small 3D lookup texture says which pool slot holds each brick, or "empty", or "solid fog, density d". A ray reads the lookup (1 fetch), skips empty bricks whole, and samples inside occupied ones. This is constant time with no tree walk, so it suits WebGL2 and phones. Same structure for L2 (40 m) and L3 (5 m, only near the camera). This is the "IOI Smolder" style from the survey, without compute shaders.
2. **Cloud floor and top as real surfaces.** Density ramps sharply at the base and top with eroding noise (the Nubis approach), so fog has a visible underside you can stand under, ragged edges, and a flat, billowy top seen from Twin Peaks.
3. **Macro far field.** L0/L1 columns drawn analytically: a layer with base, top and thickness integrated in closed form along each ray, with noise shaping the top. Banks 30-60 km away look like real cloud fronts on the horizon at almost no cost. Blend to the voxel march near the city.
4. **Night lights in the fog.** City lights scattering in fog are most of what SF fog looks like at night. A low-resolution "light emission" texture (from streets and building density) is added as in-scatter inside the fog. It is cheap and has a big visual effect, so it belongs in the plan.
5. **Lighting:** sun transmittance baked per frame into the bricks (2 channels), plus 1-2 multiple-scattering octaves (survey idea 4). Fog seen from above at sunrise needs the bright top and dark underside this gives.
6. **Time:** two 5-min states blended per ray, or rebaked progressively a few brick slabs per frame (survey "per-hour cache" note).

### 8.3 Phone and browser limits

| Limit | Value | Consequence |
|---|---|---|
| WebGL2 has no compute shaders | | Physics in the browser is limited to fragment-shader passes; keep real physics in the Action. |
| WebGPU | Shipped in Safari 26 on iOS (2025), Chrome on Android and desktop (verify on your phone) | Optional faster path later: in-browser L1 stepping, compute-based brick traversal. Keep WebGL2 as the base (decision D6). |
| 3D texture size | iOS supports 2048 per side in WebGL2 (verify on device) | Brick pool of 256³ R8 = 16 MB; RG8 32 MB. |
| GPU memory on iPhone Safari | Pages get killed above a few hundred MB (estimate) | Budget: pool 32 MB, lookup 1 MB, two time states, history buffers; under about 120 MB total for fog. |
| Float textures | Filtering of 32-bit floats is only 53 % on iOS | Store in 8- and 16-bit formats. |
| Download per 5-min frame | | L2 state compressed to about 100-300 KB per frame (only active bricks, 8-bit, gzip); a 6 h observed window is about 10-20 MB, in line with the replay review's budget. |
| Heat and battery | | Render on demand (Plan A stage 2), quarter-resolution tracing on phones. |

Targets (from Plan A): overview at least 45 fps on a mid Android; p95 under 33 ms; no drop over 20 % after 5 minutes.

---

## 9. Data volumes (estimate)

| Item | Size | Hosting |
|---|---|---|
| L0/L1 frames (cover, base, top, depth, flow) | about 20-60 KB per frame | Pages |
| L2 5-min state | about 100-300 KB per frame, 72 frames in a 6 h window | Pages, rolling |
| L3 static tiles (2 x 2 km, 10 m, flow and sheltering, 6 directions, 0-60 m) | about 2-6 MB per tile compressed | Pages for 4-10 tiles; beyond that Pages' 1 GB cap and bandwidth become a concern (decision D4) |
| Gust snapshots at 20 m, sub-sampled | about 20-50 MB total | Release asset, streamed |
| Extended terrain tiles | about 20-40 MB | Pages |
| Crops, webcam numbers, run archive | a few MB per day | data branch / release packs (validation plan) |

---

## 10. Roadmap and what each phase must prove

Durations are estimates for one person working with Claude. Everything stays behind `?beta=1` until you approve promotion.

| Phase | Work | Size | Validate (gate) |
|---|---|---|---|
| **P0 Quick fixes** | Detail moves at the real local wind (`advect` 0.1 to about 1.0, time-correct). Ship the guide 08 cloud base to the viewer and leave the bake empty below it, which gives a first cloud floor. Fog top from HRRR native levels instead of pressure levels. Grid-artifact fix F2 if not merged yet. | 3-5 days | Fog visibly drifts at about the station wind speed. Ceilings at KSFO/KOAK match the rendered floor within about 100 m on 5 test nights. Top no longer stuck at 62 m or 900 m. |
| **P1 Wide view** | 5-min GOES crops (goes-anchor-plan stages 0-1, crop store as agreed) widened to the L0 box. Visible-band edge-on-hill top heights. L0 analysis and frames. Tiled terrain to 60 km. Analytic far-field render. | 2-3 weeks | Leave-one-scan-out: L0 predicts a hidden scan better than holding the last one. Your eye check against the GOES visible loop on 3 days. Phone frame time within target. |
| **P2 Marine layer (L1)** | Reduced-gravity layer model, its inputs (HRRR, buoys, sounding, pressure push), anchoring to the GOES edge and hill tops. Drives the fog top and the flow into L2's place in today's pipeline. | 2-3 weeks | Fog-top error versus soundings, visible hill edges and (later) webcams is lower than today's top. Arrival time at the coast and KSFO beats the current 1.3 x wind lag (validation plan section 4). |
| **P3 City 3D state (L2)** | Water and temperature voxels in sparse bricks, urban heat source, full analysis cycle (section 6), 5-min frames, brick-pool renderer. | 3-5 weeks | Ceiling and visibility leave-one-station-out beat P2 and today. Satellite skill (CSI) at least as good as today. Afternoon receding edge earlier over warm districts (check against GOES visible). No pulses at 5-min frame changes on a real phone. |
| **P4 Webcams** | Camera list with terms recorded, landmark marking tool, contrast measure, first as validation only, then as anchors. | 2 weeks plus about 1 h per camera to mark | After 2-3 weeks of data: landmark visibility predicted by the model versus observed, per camera; then the same with webcams assimilated (leave-one-camera-out). |
| **P5 Street scale (L3)** | 5 m XLB tiles for the priority neighbourhoods, gust snapshots, sheltering and heat maps, streamed L3 bricks in the browser. | 3-4 weeks | Station winds inside the tiles (few exist) closer than with 20 m fields. Visual check at street level with you. Frame budget at street level on a phone. This phase has the least ground truth; it will be judged mostly by eye. |
| **P6 Render polish** | Night lights in fog, multiple scattering, gust motion, WebGPU path if D6 says so. | 2-3 weeks | Side-by-side with webcam frames on 3 nights (private, not published). |
| **P7 Promote** | Public after your approval, via the deathfog-ship skill | n/a | Your call |

How this relates to the earlier plans:
- goes-anchor-plan becomes P1 plus the anchoring part of P3, with the same crop format.
- validation-plan is the scoring harness for every gate above. Its V0/V1 should start in parallel with P0, because summer fog nights can be hindcast now.
- Render Plans A and B continue independently. They free the frame budget P3-P5 need.

## 11. Risks

- **The satellite sees tops, not the ground.** Stratus over the hills looks like fog. The L2 base and the ceilings decide what touches the street, not the satellite.
- **Night and dawn are weak.** The visible band is gone at night, 7 minus 13 is noisier, and cameras lose landmarks. Streetlight halos help; otherwise the model carries more weight and the screen should say so.
- **Overfitting with few nights.** Mid-November gives few advective fog nights; most tuning has to be hindcast on last summer (validation plan).
- **Action limits.** The cycle must stay a few minutes and fail safe, falling back to today's tiles.
- **Webcam terms** can change or forbid automated use. Keep the system working with zero cameras.
- **Street scale is the least verifiable layer.** It should be presented as "simulated detail", not measured.
- **Scope.** This is about 4-5 months of phased work in total (estimate). Each phase is useful on its own, so stopping after any gate still leaves a better model.

---

## 12. Decisions for George

| # | Question | My recommendation |
|---|---|---|
| D1 | Macro area: about 120 x 100 km (sees fronts 1-3 h out), or stay near your 500 km² minimum? | 120 x 100 km as 2.5D layers. It costs little because it is 2D. |
| D2 | Compute: stay on free GitHub Actions, or allow paid cloud compute or your PC as a scheduled server? | Stay on Actions; design for it; revisit only if P3 does not fit. |
| D3 | Webcams: use derived numbers only (no images stored or shown), check each camera's terms first, and contact Caltrans before any public display? | Yes to all three. |
| D4 | Street-scale data hosting: GitHub Pages only (limits L3 to about 4-10 neighbourhoods), or Cloudflare R2 for more? | Pages for P5's first tiles; decide on R2 when we know tile sizes. |
| D5 | Street-scale priority areas | Potrero and Golden Gate Heights (the existing zones), then the drawn routes, then the Sunset and Richmond edge where fog first hits. |
| D6 | WebGL2 only, or add a WebGPU fast path when available? | WebGL2 base, WebGPU optional from P6. |
| D7 | Build the new pipeline alongside today's (beta) and switch when it beats it, or replace in place? | Alongside, behind `?beta=1`, with the existing compare reports. |
| D8 | Does this plan replace the next steps of goes-anchor-plan (they become P1 and part of P3)? | Yes, so there is one plan, not two. |

## Sources consulted today
- GOES-18 schedules and products: https://www.ssec.wisc.edu/datacenter/goes-18-schedules ; NOAA open data on AWS: https://registry.opendata.aws/noaa-hrrr-pds ; HRRR background and sub-hourly output: https://herbie.readthedocs.io/en/stable/user_guide/background/model-info/hrrr.html
- RRFS status (Aug 2026 post): https://luckgrib.com/blog/2026/08/25/rrfs.html ; NWS RRFS webinar: https://www.weather.gov/media/wrn/calendar/RRFS_webinar_20260625.pdf
- Caltrans CWWP2 and CCTV data: https://dot.ca.gov/programs/traffic-operations/traveler-information/cwwp ; https://cwwp2.dot.ca.gov/documentation/cctv/cctv.htm ; conditions of use: https://dot.ca.gov/conditions-of-use
- NOAA PSL Bodega Bay site: https://psl.noaa.gov/data/obs/sites/view_site_details.php?siteID=bby
- CIMSS on SF Bay fog and stratus dissipation: https://cimss.ssec.wisc.edu/satellite-blog/archives/29685
- Repo files: microclimate/wind.py, windrot.py, fog.py, fogtop.py, dirlib.py, live/anchors.py, live/forcing.py, live/east.py, live/dsr.py, site/v3/fog2.js, fogflow.js, PROGRESS.md, docs/model_guides/07 and 08, .github/workflows/collect.yml (all on v3-dev).
