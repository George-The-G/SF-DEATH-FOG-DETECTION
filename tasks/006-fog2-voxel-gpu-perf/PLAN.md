# Plan — Task 006: Async 3D fog atlas decode, VOX_FS GPU loop & empty-skip optimization

## Goal
Reduce main-thread decode/upload stalls and per-sample GPU fragment cost in `site/v3/fog2.js` without changing visual output.

## Scope (`owned_paths`)
- `site/v3/fog2.js`

## Changes
1. **`VOX_FS` ray-march hot loop (`site/v3/fog2.js` L240–295):**
   - Hoist loop-invariant `invSy = 1. / max(uSun.y, .15)`, `occMax = ceil(uOccN) - 1.`, `cellSz = uGs / uOccN`, and clamped `dA0`/`dB0` outside the 96-step ray-march loop.
   - Guard the 3D `uNoise` edge-fade fetch on `og > 0.` (`inside = og > 0. ? ... : 1.`), eliminating a wasted 3D texture lookup on every interior city sample (~85% of all samples).
   - Skip sampling `uVolA` when `uMixT >= 0.999` (symmetric to skipping `uVolB` when `uMixT <= 0.001`).
2. **Off-main-thread `createImageBitmap` decode & direct GPU upload (`d3Load` / `d3Commit`):**
   - Decode `fog3d` / `fog5` volume atlases via `fetch(url) -> blob -> createImageBitmap(blob, { premultiplyAlpha: 'none', colorSpaceConversion: 'none' })` with `new Image()` fallback.
   - Upload the decoded `ImageBitmap` directly to `THREE.Texture` (`LinearFilter`, `ClampToEdgeWrapping`, `flipY = false`), removing the 1,042,608-pixel JS `Uint8Array` allocation and RGBA→R copy loop.
   - Compute coarse 320 m `colMax` from a half-resolution reusable scratch canvas (`W/2 × H/2`, 4× fewer pixels read back).
   - Close `ImageBitmap` instances cleanly on LRU eviction and stale-load discard (`bmp.close()`).
3. **Off-main-thread decode & reusable canvas in `tvSheet` / `tvCommit`:**
   - Decode `fog5` tile sheets via `createImageBitmap` off the main thread and reuse a single `tvCanvas`/`tvCtx` scratch canvas instead of allocating a new `<canvas>` per sheet.

## Verification
- `node --check site/v3/fog2.js`
- `tools/task.py verify 006` (schema, deliverables, scope, `pytest`, `node --test tests_js/*.mjs`, headless desktop + phone Playwright previews).
