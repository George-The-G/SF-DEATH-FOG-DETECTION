# Multi-scale fog: review of the plan, and the build guides

Written 2026-11-14 (PT) against `origin/v3-dev` at da494de. This folder replaces the roadmap in `../multiscale-plan.md` (the plan stays as background reading). Nothing here is built.

George's decisions so far: webcams are parked (no guide). D1: macro area as suggested (the existing GOES box, about 123 x 122 km). D2: free compute only: GitHub Actions, Cloudflare free tier, the local PC as backup. Extra fog tracer layers: yes, see the verdict below.

"Estimate" = reasoned, not measured. "(verify)" = not confirmed from a primary source.

---

## 1. Review of the first plan

**What holds up:** layered columns for the far field and voxels only in the city; anchoring every 5 minutes; satellite sets the km scale and the tracer sets the detail; the list of causes for each flaw; phases that each ship something.

**What I changed, and why:**

1. **It missed the cheapest fix for "fog is a column".** The viewer's fog tile is one 2D slice: the lowest inflow band (0-150 m) sampled at street level (`replay.city_tracer_80m`). The full 3D tracer fields (20 m cells, 40 levels, 4 inflow bands) already sit on the PC and are never shipped. Shipping them in 3D gives a real cloud floor, a real top and lee-side holes with no new physics. This is now the core of the work (guides 01-04).
2. **The 3D water-and-temperature state was too fragile.** 0.3 K decides fog or no fog, and our stations are good to about 1 K, so that state would be mostly noise. It is replaced by a diagnostic version: tracer supply x cloud-base rule x urban heat lift (guide 05). Same visible effects, testable, cheap.
3. **Cost estimates for the Action were optimistic.** Carrying 4 M voxels along the wind in numpy is seconds per step, not tenths. The tracer approach needs only a weighted sum per hour, which is milliseconds.
4. **The sparse brick pool is not needed for the city.** 214 x 203 x 24 voxels is 1 MB dense. Sparse bricks are kept only for street scale (guide 12).
5. **The marine-layer model is a research step, not a build step.** Wetting and drying over terrain and hydraulic jumps make it easy to get wrong. It becomes a time-boxed spike with a go/no-go (guide 11). Until then the fog top comes from fusing the measurements we already have (guide 06).
6. **HRRR is not a safe dependency.** `live/config.py` notes HRRR on Open-Meteo ends 2026-11-30, and RRFS is replacing HRRR. Guide 06 treats a native-level profile as an optional input that must be probed first.
7. **"Fog moves at 10 % of the wind" was half right.** `advect: 0.1` and `flowSpeed: 0.042` are look settings George chose on 2026-11-14 for hourly data. The real problem is that the fog pattern itself never travels. Guide 08 fixes that with 5-minute data and makes true wind speed the beta default (George, 2026-11-14: the old values were set by eye and can be replaced).
8. **The macro box already exists.** `goes.LowCloud` crops `[-123.3, 37.2, -121.9, 38.3]`. D1 needs no new ingest area, only use of the whole crop.
9. **Dropped for now:** webcams (your call), night city lights, WebGPU, ensemble weights.

**Verdict on extra tracer layers: yes, in two senses, and both are worth it.**
- *More inflow bands* (11 instead of 4). The tracer is linear, so fog with any top height is a weighted sum of bands. With 150-300 m bands the top can only be placed coarsely; with 40-60 m bands it is placed to about 50 m. Cost: GPU time on the PC, once (guide 01).
- *Vertical layers shipped to the viewer* (24 levels of 25 m). This is what turns the column into a 3D shape (guides 02-04).
- Honest limit: the wind fields are neutral (no inversion), so fog climbs over hills more easily than in reality. The cloud-base and top rules cap it, and guide 11 is the proper fix.

---

## 2. The guides

| # | Guide | Gives | Where it runs | Needs | Size |
|---|---|---|---|---|---|
| 01 | Finer inflow bands | 11 tracer bands per direction | PC GPU | nothing | S + GPU hours |
| 02 | 3D tracer asset | `live_tracer3d.npz` for the Action | PC CPU | 01 | S |
| 03 | Live 3D fog tiles | hourly 3D fog files behind switch `fog3d` | Action | 02 | M |
| 04 | Viewer 3D volume | fog with floor, top and holes | Browser | 03 | M |
| 05 | Cloud floor and urban heat | base lifts over warm streets | Action | 03 | M |
| 06 | Fog top fusion | one top with an error bar, not a guess | Action | nothing | M |
| 07 | 5-minute GOES and macro frames | wide-area cloud every 5 min | Action | nothing | M |
| 08 | Fog that travels | 5-min city fog carried by the wind, real-speed option | Action + browser | 03, 07 | L |
| 09 | Macro view | terrain and cloud banks out to 60 km | PC + browser | 07 | M-L |
| 10 | Compute and hosting | Cloudflare trigger and R2, PC backup | Cloudflare, PC | nothing | M |
| 11 | Marine-layer spike | go/no-go on a layer-depth model | PC CPU | 06, 07 | time-boxed |
| 12 | Street-scale spike | go/no-go on 5 m wind tiles | PC GPU | 04 | time-boxed |

**Order.** Three tracks can run at once: A = 01, 02, 03, 04, 05 (in order). B = 06 and 07 (independent of each other), then 08 when 03 and 07 are in. C = 10. Then 09, 11, 12. Only one agent at a time may use the GPU (01, 12) and only one at a time may edit `site/v3/fog2.js` (04, 08, 09).

---

## 3. Rules for every guide

These repeat the `deathfog-agent-brief` skill and `docs/model_guides/00_README.md`. Read both first.

- PC work goes through desktop-commander (PowerShell). Python: `C:\Users\George\source\repos\SF-DEATH-FOG-DETECTION\.venv\Scripts\python.exe`. Data: `C:\Users\George\deathfog-data\`.
- Fresh git worktree from `origin/v3-dev`, own branch `sim-<guide>-<short-name>`. Never the main checkout, never `main`, never force-push, never the deploy folder.
- **Every change sits behind a named switch that is off for public.** With the switch off, outputs are byte-identical. The frozen-hash tests (`tests_mc/test_physics.py` and friends) must pass on the Windows venv. Five of them are known to fail on Linux containers.
- Switch pattern for this work: copy how `WIND_EAST` is done in `microclimate/live/config.py` and `build.settings()`: a module constant (`False`), a key in `C.BETA` (`True` once the guide says so), never in `C.PROMOTED` without George's approval. Switch names are fixed in section 4.
- Viewer changes: beta only (`?beta=1`), public page verified unchanged with Playwright. A change of the default look needs before/after screenshots sent to George and his OK. New looks start as a fog lab option (`?beta=1&lab=1`).
- Tests before any merge: `python -m pytest -q tests tests_mc` and `node tests_js/*.mjs`. Each guide lists its own new tests.
- Live build budget: under 3 minutes and 3 GB on the runner. First-paint download must not grow: new viewer data is loaded on demand.
- New constants: `live/config.py` for data handling, top of `wetness.py` for model constants, each with a one-line comment and "first guess" where no measurement backs it.
- No merge into `v3-dev` and no deploy without George's go-ahead. Deploys go through the `deathfog-ship` skill.
- Report at the end of each guide: files changed, test counts, the numbers the guide asks for, screenshot paths, risks, anything cut. Say plainly what was not verified.
- If a "check first" step in a guide contradicts the guide, stop and report. Do not improvise around it.

---

## 4. Shared contracts (do not change without updating every guide)

**Tile grid** (`replay.FOG_W, FOG_H, FOG_CELL` = 214, 203, 80 m). Row 0 = north, column 0 = west. Cell centre: `x = 540100 + 80 * (c + 0.5)`, `y = 4171500 + 20 * 811 - 80 * (r + 0.5)` (EPSG:32610). The 20 m grid is 855 x 811 x 40; a 20 m array `a[nx, ny]` becomes a tile with `a.T[::-1]`, edge-padded to 856 x 812, then 4 x 4 block means (see `replay.city_tracer_80m`).

**Height levels for all 3D fog data:** `NZ = 24`, `DZ = 25.0` m, level k centre at `(k + 0.5) * 25` m above sea level (12.5 ... 587.5 m). Heights are above sea level, never above ground.

**Inflow bands (guide 01), effective edges in metres:** `[0, 40, 100, 140, 200, 260, 300, 360, 420, 500, 640, 800]`, 11 bands. All edges are multiples of 20 m, so they fall on cell faces. The old 4 bands (nominal 0-150, 150-300, 300-500, 500-800; effective 0-140, 140-300, 300-500, 500-800) are exactly bands 0-2, 3-5, 6-8 and 9-10 added up. That nesting is the main correctness test.

**Band weights for a fog layer between `lo` and `top` (m above sea level):** `a_b = clip((min(top, hi_b) - max(lo, lo_b)) / (hi_b - lo_b), 0, 1)`. `lo` is 0 until a later guide says otherwise.

**Directions with fog tracer:** the 9 west directions 180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5, 360. East directions get zero tracer (as `live/east.py` does).

**Switch names:**

| Switch | Guide | Changes |
|---|---|---|
| `fog3d` | 03 | writes 3D fog files; nothing else |
| `fog_base` | 05 | cloud floor applied to the 3D files |
| `urban_lift` | 05 | urban heat raises the floor (needs `fog_base`) |
| `top_fusion` | 06 | fog top series (this one also feeds street wetness) |
| `goes5` | 07 | 5-minute crop store and macro frames |
| `fog_travel` | 08 | 5-minute city fog from predict-correct (needs `goes5`) |

**File names in the live output folder** (all optional; the viewer must work when any is missing):
- `fog3d_<fff>.png`, one per hourly frame (guide 03); `index.json` key `fog3d`.
- `macro/index.json` and `macro/<yyyymmddThhmm>.png` (guide 07).
- `fog5/index.json` and its frames (guide 08).

---

## 5. How we will know it is better

One scorecard, used by every guide that changes the fog (the harness is `validate/validation-plan.md`; `live/cloud_score.py` already produces the satellite part):

1. Satellite match of the fog sheet (hit rate, false alarms, CSI) on the coast, upwind and bay boxes. Must not get worse.
2. Ceiling error at KSFO, KOAK, KHAF, leave-one-airport-out: the rendered floor against the reported ceiling.
3. Fog-top error against the Oakland soundings (leave-one-sounding-out).
4. Timing: arrival and clearing at the coast box and at the airports, signed.
5. By eye, with George: three chosen nights, beta against public, phone and desktop.

A guide passes when its own gate is met and 1-4 are no worse than before on the same nights. Report the numbers either way.

---

## 6. Still open for George

| # | Question | Default the guides assume |
|---|---|---|
| D4 | Street-scale and 5-minute data hosting | Cloudflare R2 free tier (guide 10); needs a card on file with Cloudflare even when free (verify) |
| D5 | Street-scale priority areas | Potrero and Golden Gate Heights |
| D6 | WebGPU | Not now |
| D7 | New pipeline alongside the old one in beta, switch when it scores better | Yes |
| D8 | `replay/goes-anchor-plan.md` is absorbed into guides 07 and 08 | Yes |
| D9 | Should street wetness also use the 11-band tracer later? It changes the public numbers. | Not in these guides; decide after guide 04 |
| D10 | Real-speed fog motion as the beta default (guide 08) | DECIDED 2026-11-14: yes. George picked the old speed values by eye; the goal is realism, so true wind speed wins. |

---

## 7. Do the wind runs need a 120 km mesh? No (decided 2026-11-14)

- At 20 m cells, 120 x 120 km x 800 m is about 1.4 billion cells, roughly 50 times today's 27.7 M and far beyond a 16 GB GPU.
- A coarse regional run (100 m cells) would fit, but the XLB setup has no inversion. At regional scale the inversion is what makes the coastal mountains block the marine layer, so a neutral run would give confident wrong answers.
- The regional scale is covered by data instead: satellite (guide 07), the weather model and stations, and the marine-layer experiment (guide 11).
- Guide 01 needs NO new wind runs. It reuses the existing wind fields and only runs the tracer.
- The wide terrain in guide 09 is a display mesh only.
- Possible later upgrade, to decide after guide 11: grow the 20 m wind domain modestly (about 25 x 25 km, about 62 M cells, near the GPU memory limit) to take in all of the Marin Headlands and San Bruno Mountain, with inflow taken from the marine-layer model. Do not start this before guide 11 reports.
