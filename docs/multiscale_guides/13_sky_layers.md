# Sky layers: thin and multi-level clouds (plan, 2026-11-16)

Status: planned, to start after the twelve multi-scale guides are complete (George, 2026-11-16). Nothing built yet.

## Goal
Show clouds the way they really occur: several layers at different heights, including thin clouds seen by the satellite that never touch the ground, with the fog tracer staying the lowest layer. No fog columns from sky to ground where nothing says the air is saturated near the street.

## What we have today
- **Fog layer** (guides 01 to 06): 3D files `fog3d_<fff>.png` with a cloud floor from the cloud base, a lid at the fused fog top (switch `fog_lid`), and the walls-wind tracer for the shape.
- **Satellite** (guide 07): a 5-minute store of the GOES clear-sky mask and cloud-top height at 2 km, macro frames of probability, confidence and top.
- **Airport reports:** up to three layers each (base and coverage) at KSFO, KOAK, KHAF.
- **Weather model and Oakland sounding:** humidity at every height (RRFS pressure levels).

## Design
1. **Layer table per hour** (live build, new module `live/skylayers.py`): up to 3 layers, each with base, top, coverage (0..1), and a source tag.
   - Layer 0 = the fog layer (unchanged).
   - Upper layers from the model humidity profile: a layer exists where relative humidity stays at or above a saturation threshold (first guess 92 %); base and top are the edges of that run of levels.
   - Corrected by airport reports (base and coverage of the second and third layers) with the same leave-one-out idea as the ceiling offset, and by the satellite top (the highest layer's top is moved toward the satellite height where the satellite sees cloud with confidence).
2. **Thin clouds seen only by the satellite:** where the satellite shows cloud and the layers above do not explain it, add a slab with top = satellite height, base = top minus a thickness from the humidity run (default 150 m, first guess), never below the cloud base of the fog floor. Cirrus (very high, no humidity run) is a faint high sheet.
3. **Output:** per frame, one small PNG with a coverage map per upper layer on the tile grid plus the table in `index.json` (`sky_layers`). Files are optional and load on demand.
4. **Viewer** (beta, lab switch `sky`): each layer is an analytic slab like guide 09's macro layer (ray meets slab, a few coverage samples, lit with the same sun and ambient as the fog), faded where the voxel fog is the truth, never darkening streets under a clear gap.
5. **Switch:** `sky_layers`, off for public, beta after review. A mock for George before the beta look changes.

## Checks (honest gates)
- Layer table against airport reports: base error per layer at the three airports (leave one airport out), target under 150 m on the lowest upper layer.
- Satellite agreement: share of satellite cloud pixels that the layers cover, and the share of clear pixels that get cloud (false alarms), on the 5-minute store.
- Visual: three chosen nights, flat map versus fog only versus fog plus sky layers, phone and desktop.
- Cost: build under +15 s, files under 3 MB, viewer under +1 ms on a real GPU.

## Known limits
- The satellite sees only the highest top, so what lies under an upper deck comes from the model and the airport reports.
- Humidity-based layers inherit the model's humidity error; a 1 K or 5 % error moves a layer by tens to a hundred metres.
- No cloud microphysics: no rain shafts, no virga, no convective towers.

## Inputs to decide before starting
- Humidity threshold and default thickness (first guesses above).
- Whether airport layers are strong evidence (override the model) or weak (nudge).
- Whether thin cirrus should render at all in the first version.

## Input prep (started 2026-11-16, before the guide begins)
- Daily collector: `tools/collect_sky_inputs.py` writes `C:\Users\George\deathfog-data\sky_inputs\<yyyymmdd>\` with the raw METAR (all cloud layers, 72 h; the live fetch keeps only the lowest ceiling), a 7-day RRFS profile at the Golden Gate Park point with a wider ladder (12 levels, 1000 to 300 hPa; the live fetch stops at 850 hPa, about 1.5 km, so upper layers need more levels), the published 5-minute GOES store, and the newest OAK sounding. First run: 2026-11-16, all four parts saved. Run it once a day; five or more days give the guide its test nights.
- The live fetch (`PROFILE_VARS` in `live/config.py`) will need the same wider ladder when the guide starts.
- This guide is number 13 of the multi-scale series; start it after guides 01 to 12 and the open fixes (the flat-map floor decision, the refinement patch rerun, more nights for the ceiling gate). One agent at a time in `site/v3/fog2.js`, after guides 08B and 09B.

## Note from 2026-11-16 (George: save thin clouds for this guide)
Real thin clouds (thin stratus, patchy low decks with clear air below) do not register in the sim today: the sim draws fog only where its fog supply and tracer allow, and the satellite only scales fog the model already has. A cheap first milestone for this guide: in the OBSERVED hours only, where the satellite shows cloud with decent confidence and a low top, add a thin deck even if the model has no fog (top = satellite height, base from the airport report or the humidity run, never the ground). The forecast hours need the humidity-layer table below. The cloud floor and the fog lid (switches `fog_base`, `fog_lid`) cut partly saturated air, so check that thin decks are not removed by them.
