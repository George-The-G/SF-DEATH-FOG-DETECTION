# Task 007 Plan — Macro Cloud Shader LOD, Early Discard & Async PNG Decode

## Objective
Reduce GPU fragment shader work and eliminate main-thread PNG decode stalls in [macro.js](file:///C:/Users/George/source/repos/SF-DEATH-FOG-DETECTION/_worktrees/wt_007-macro-shader-lod-perf/site/v3/macro.js) while preserving visual parity when the camera is still.

## Design

### 1. Early Fragment Discard & Per-Sample Culling (`FRAG`)
- **Whole-ray city-interior discard:** After computing the analytic ray-slab segment `[a, b]` and endpoints `pA`, `pB`, when `uSky < 0.5` (no upper sky decks over the city), compute `cityFade = max(seam(pA), seam(pB))`. Because the inset city interior (`seam < 0.002`) is a convex axis-aligned rectangle in XZ, `cityFade < 0.002` guarantees every sample along `[a, b]` lies inside the city interior where `fog2.js` owns cloud rendering, so the fragment executes `discard;` before the 8-step loop.
- **Whole-ray GOES-box discard:** Project `pA`, `pB` via `xzToUv()` to `uvA`, `uvB`. Because `xzToUv` is affine and `[0, 1]^2` is convex, if both endpoints lie on the same side outside `(0, 1)` along either axis, `discard;` immediately before the loop.
- **Initial reference-plane guard:** Skip the initial `c0` satellite-top lookup when `uv0` is outside `(0, 1)` or (`uSky < 0.5 && seam(p0) < 0.002`).
- **Per-sample early skips:** Inside the `N = 8` march loop, evaluate `seam(p.xz)` and `xzToUv(p.xz)` *before* `covUv(uv)` and 3-octave `vn()` procedural noise. Skip (`continue;`) samples inside the city interior (when `uSky < 0.5`), outside `(0, 1)` UV bounds, or with `c.r <= 0.08` (clear sky where `smoothstep(0.08, 0.72, c.r) == 0`).

### 2. Adaptive Filter Tap Count (`uMoving`)
- Add `uniform float uMoving` (`1.0` moving, `0.0` still) to `FRAG` and `U`.
- In `samp(sampler2D t, vec2 uv)`:
  - When `uMoving > 0.5`, use a 4-tap bilinear diagonal filter (`±0.75 * uTexel`, >55% fewer texture fetches than 9-tap, matching footprint with hardware `LinearFilter` to avoid pop on stop).
  - When `uMoving <= 0.5`, use the full 9-tap B2-spline/Gaussian filter (`±1.45 * uTexel`).
- In `update(dist)`, compare `dist`, `camera.position`, and `camera.getWorldDirection()` against the previous frame. When motion is detected, set `uMoving = 1` and arm a `140 ms` settle timer that resets `uMoving = 0` and calls `invalidate()` so demand-driven renders produce a crisp 9-tap resting frame.

### 3. Off-Main-Thread Async PNG Decode (`createImageBitmap`)
- In `frameTex(file)`, when `typeof createImageBitmap === 'function'`, load macro PNGs via `fetch(url) -> blob -> createImageBitmap(blob, { premultiplyAlpha: 'none', colorSpaceConversion: 'none' })` (with a plain `createImageBitmap(blob)` fallback) and upload directly to `new THREE.Texture(bmp)` (`flipY = false`, `LinearFilter`, `ClampToEdgeWrapping`, `generateMipmaps = false`) without any 2D canvas `drawImage` / `getImageData` readback.
- Fall back to `new Image()` + scratch canvas `DataTexture` when `createImageBitmap` is unavailable.
- Track a `srcGen` generation counter to close/discard stale `ImageBitmap`s if `setSource()` changes mid-fetch, and close `bmp.close()` alongside `tex.dispose()` in `disposeEntry()` on LRU/capacity eviction and `setSource()`.

## Verification
- Syntax/module check and `node --test tests_js/test_macro.mjs` inside the worktree.
- Confirm only `site/v3/macro.js` and `tasks/007-macro-shader-lod-perf/PLAN.md` are modified.
