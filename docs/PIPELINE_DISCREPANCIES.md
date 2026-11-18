# Pipeline discrepancies: code and data vs guides, README and config

Found while documenting the pipeline (`site/v3/pipeline.html`). Code at v3-dev `d1b6af4`; data from the beta build of 2026-11-18 04:43 UTC (status.json `generated_iso`) and the offline libraries on the PC. Source of truth: running code and data. "Guide" = `sim/guides/*` (PC copy, not in this repo) and `docs/model_guides/*`.

Severity: **H** changes output or misleads tuning, **M** wrong doc or dead config, **L** stale wording.

## H1. v3-dev `wind.py` is not the solver that made the beta wind library
- Beta library (`*_walls.npz`, recomputed on the PC 16 Nov) was built from branch `wind-cube-check`: fresh Warp kernel cache, free-slip sea, fixed-pressure outlet (`docs/WIND_SOLVER_CONFIG.md` on that branch). v3-dev `microclimate/wind.py` has none of it, so re-running v3-dev code gives a different field.
- Cause of the old bug: a stale Warp kernel cache meant the old library ran with no walls. Public still uses the old assets.
- Evidence, 270°, speed ratio 2 cells above surface, p5/p50/p95: old 0.995 / 1.007 / 1.018 (flat), walls 0.057 / 0.377 / 1.036. Run: 40,000 steps, 435 s, max ratio 1.38.
- Guide text "guide 01 needs no new wind runs" (00_README, PC copy) is out of date; the library was fully recomputed.
- Fix: merge `wind-cube-check` solver changes into v3-dev, or record the exact commit used for the assets in `refresh.sh`.

## H2. The 5 m street correction is stale on beta (effectively off)
- README and `docs/model_guides/00_README.md:35` say the 297-patch correction is applied. True for public: `rel_wind` median 0.498.
- Beta `live_static` has `rel_wind = 1` everywhere. `corr_bldg` (p5/p50/p95 0.03 / 0.33 / 1.005) was computed on the old no-walls 20 m field (branch `v3-refine`), and the beta 20 m field now already contains the walls, so applying it would likely double count (inference, not tested). Net: beta street wind comes from 20 m walls only.
- Wetness rule if applied: `rel_wind = clip(corr / 0.91, 0.25, 1)` (`REFINE_NORM 0.91`).
- Fix: re-run `refine.py` with the walls solver as the nesting parent, then repack `live_static`.

## H3. `TRACER_REF = 0.03` was tuned on the old tracer
- `wetness.py:38` comment: band 0 values at Potrero 0.0005 to 0.045. The beta point tracer at 270° (285,809 points) is p5 0.183 / p50 0.271 / p95 0.46, so nearly every point saturates (`sqrt(tr / 0.03)` capped at 1). Public: p5 0.001 / p50 0.12 / p95 0.544.
- Likely cause (inference): the beta tracer uses a different diffusivity (H4) plus the walls field. Effect: little street-to-street fog contrast on beta.
- Fix: retune `TRACER_REF` (and `FOG_EXP`) on the beta tracer.

## H4. Tracer diffusivity: config says 2.0, beta assets use 20 and 8
- `microclimate.toml [library] eddy_diffusivity_m2s = 2.0`; no vertical key. `fog.run_band(kd=2.0, kdv=None)` makes `kdv = kd`.
- Beta assets were made with `kd = 20` (sideways) and `kdv = 8` (vertical) from `spikes/derive_k20v8.py` and `spikes/patch_static.py`, passed as arguments, not from the toml.
- `fog.py:81` caps `dt` for large kd (`0.3·dx²/(6·max(kd,kdv))`). 270° bands: about 11,007 steps of 1 s, 17 s each.
- Fix: put `eddy_diffusivity_m2s = 20` and `eddy_diffusivity_v_m2s = 8` in the toml for beta; keep 2.0 for the public library.

## H5. fog5 2D tiles and hourly beta tiles disagree
- Hourly beta tile (`tile_from_3d`) = 3D level 0, which includes cloud base, lid and the 30 m guard. fog5 tile = `255 · A · trb` with no cloud floor (`fog5.py` docstring: prior is the hourly amp "without the GOES factor"). fog5 volumes do use base and lid.
- Seen: at 03 UTC (19:00 PT) the hourly beta tile is empty (mean 0; base about 594 m above a 221 m top) while the fog5 tile at 03:00 shows fog.
- Fix: apply `fogbase` to the fog5 tile, or hide the fog5 tile when the hourly tile is empty.

## M1. Switches missing from the guide's switch table
- `fog_lid`, `fog_base_2d`, `tile_from_3d` (added 16 Nov) exist in `C.BETA` (`live/config.py`) and in `status.json` (`fog3d.base` shows `tile_2d`, `tile_from_3d`) but not in the 00_README switch table. Current beta switches: fog3d, fog_base, urban_lift=False, fog_travel, top_fusion, fog_lid, fog_base_2d, tile_from_3d, goes5, wind_east.

## M2. Direction set differs between config and assets
- `microclimate.toml directions_deg = [202.5 … 360]` (8 values). Beta assets carry 180° and 202.5…360° (9 west) plus 22.5, 45, 67.5, 90, 112.5, 135° (6 east); 157.5° is a gap (`status.json wind_east`). East fields are street wind only; their tracer rows are zero.
- Only 270° has the 5 m correction (H2).

## M3. Sky layers: "nothing is drawn" is wrong
- `site/v3/fog2.js:61` comment and the `foglab.js:50` hint say sky layers draw nothing. `sky.js` is present and `macro.js` draws the satellite cloud deck and keeps it above the fog top. Default `sky: 0` (off) is still correct.

## M4. fog5 wind
- `status.json fog5.wind = "uniform (fogflow.js wind not used yet)"`, matching `travel.py:9`. Guide 08 plans a terrain-aware wind. Not a bug, an open guide item. Front speed = hour's wind × `TRAVEL_SPEED_FACTOR 1.3`.

## L1. Fog-top cap: 900 vs 1300
- `wetness.FOG_TOP_MAX_M = 900`. `forcing.py:345` and `:710` clamp the profile top to `FOG_TOP_MAX_M + 400 = 1300`, so a top between 900 and 1300 exists before the wetness clamp. Intended (a ceiling bound above the cap stays capped), but undocumented; docs that say "900 m max" are only true for the wetness input. The viewer clamps again (`topMax 900`).

## L2. Cloud-score headline is not skill
- Headline "78 % over 23 scorable hours" scores `fog_sheet`, which is nudged with the same satellite data it is scored against (cloud_score.json `definition.notes`: "consistency, not skill"). Independent RRFS, last 24 h: day CSI 0.182 (hit rate 0.48), night CSI 0.080 (hit rate 0.132). Any guide text calling the headline accuracy should say so.

## L3. Sounding gate rejects the inversion
- `top_fusion` rejected the Oakland sounding 14 of 14 times in the beta run (inversion base 2486 m, far above the fog, outside the 3 σ gate). Working as designed, but it means the sounding never constrains the top on this night; the top comes from model (78 used, 1 rejected) and GOES (7 used).

## Open questions for the owner
1. Which commit built the `*_walls.npz` assets, and should it be merged to v3-dev (H1)?
2. Is `rel_wind = 1` on beta intended until H2 is redone?
3. Should the beta `TRACER_REF` be retuned now (H3) or after H2?
