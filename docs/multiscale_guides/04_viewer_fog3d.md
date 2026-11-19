# Guide 04: the viewer bakes its fog volume from the 3D files

Read `00_README.md` first. Needs guide 03 live in beta. Touches `site/v3/fog2.js` (one agent at a time), a little of `app.js` and `foglab.js`. Beta only.

## Goal
In beta, the voxel fog takes its shape from `fog3d_<fff>.png` instead of "2D tile x guessed top". Result: a visible cloud floor where the data has one, a top that follows the data, holes behind hills. Public page unchanged.

## Check first (read the code, then confirm or report)
- `fog2.js` `BAKE_A`: a volume layer `v` sits at `y = gh * uFollow + v * uVH` in scene units; scene height = metres x `EX` (see `w = vec3(..., y / uEX, ...)`). Coverage is `cov = clamp(sqrt(px / 16) * uDens, 0, 1)` from the 2D sprite; `prof` shapes the top from `uTop`.
- `bakeHour(renderer, e, k)` sets `uTop`, `uVH` from `topForHour(k)`; `getVol` keeps at most 3 hour volumes.
- `updateOcc` builds the 320 m occupancy grid from the 2D sprite (`spritePx`), and the march uses `oc.y * uHtMax * uFollow + max(uVHa, uVHb)` as the column top. The column-top skip must already be the fixed version (F2 in `fog/grid-artifact-diagnosis.md`). If it is not merged, stop and report: do not build on the broken skip.
- How `app.js` passes the night index to the fog (`fog.build(url, fogGrid)`, `fog.setWeather`), for live and for replay nights. Replay nights have no 3D files and must keep working.

## Steps

1. **Lab parameter** `data3d` in `DEFAULTS`: `0` = today's bake, `1` = 3D data. Start with default `0`; add the switch to `foglab.js`. George decides when it becomes `1` (see Gate).
2. **Plumbing.** `app.js` passes `idx.fog3d` (or null) and the base URL to the fog through a new method `fog.setData3d(info, baseUrl)`. No other `app.js` change.
3. **Loader with a small cache** (in `fog2.js`): `get3d(k)` returns `{tex, colMax, topLayer}` for hour `k` or null.
   - Loads the PNG with an `Image`, uploads it as a 2D texture: `RedFormat` / `UnsignedByteType` if the three.js version in use supports it for images, else luminance; `LinearFilter`, `ClampToEdgeWrapping`, no mipmaps, `flipY = false`, `colorSpace` none. Verify with a known pixel that no gamma or premultiply changed the values.
   - Reads the pixels once through a canvas (as `readSprite` does) to compute `colMax[203 * 214]` = max over the 24 levels per column. If the canvas read fails, treat the hour as "no 3D data".
   - Keeps 3 entries (the two hours in use plus one ahead); disposes textures on eviction; starts loading hour `k + 1` when `k` is in use.
   - While an hour is still loading, keep showing the previous volume; never mix a 3D-baked hour with a 2D-baked hour in one blend. If either of the two hours has no 3D file, bake both from 2D for that pair.
4. **Bake.** Add to `BAKE_A`: `uniform sampler2D uTex3; uniform float uUse3, uDz, uNz; uniform vec2 uAt;` (`uAt` = atlas columns, rows).
   ```glsl
   float lay3(float k, vec2 q){                      // q as in tile(): x 0..1, s 0..1 from north
     float c = mod(k, uAt.x), r = floor(k / uAt.x);
     vec2 tp = clamp(q * vec2(214., 203.), vec2(.5), vec2(213.5, 202.5));   // stay half a texel inside the tile
     return texture2D(uTex3, (vec2(c * 214., r * 203.) + tp) / (uAt * vec2(214., 203.))).r;
   }
   float data3(vec2 q, float zM){                    // zM = metres above sea level
     float f = clamp(zM / uDz - .5, 0., uNz - 1.), k0 = floor(f), k1 = min(k0 + 1., uNz - 1.);
     return mix(lay3(k0, q), lay3(k1, q), f - k0) * 255.;
   }
   ```
   Take the tile size from uniforms rather than literals if the grid may change. In `main`, when `uUse3 > .5`: `px = data3(q, y / uEX)`, `cov` from the same sqrt mapping, and replace `prof` by `1.` and `topL` by the volume height (so `hh` still runs 0..1 for the small density gradient). Keep the noise erosion (`thr`, `s`) unchanged. `bump` has no effect in this mode.
5. **Volume height.** With 3D data, `vh = (max(topLayer of the hour, 3) + 2) * dz * EX`, capped at `(nz + 1) * dz * EX`. Set `uVH` and `e.vh` from it. `uTop` is unused. Both hours of a blend use their own `vh` as today.
6. **Occupancy.** When 3D data is active, build the occupancy texture from `colMax` of the two hours instead of `spritePx`: a 320 m cell is occupied if any of its columns has `colMax >= 1`. Keep the existing dilation. The column-top value stays as is (it only needs to be an upper bound: check that `oc.y * uHtMax * uFollow + max(uVHa, uVHb)` is at least the highest occupied voxel; if `uFollow < 1` it is).
7. **Sheets fallback (tier 0) and public** keep using the 2D sprite. No change.
8. **Regression check tool:** extend the headless tools with the lattice test proposed in the grid-artifact note (top-down shot, autocorrelation of fog opacity at the 320 m period must be flat). Run it for `data3d` 0 and 1.

## Verify
- Playwright, software GL is fine for the look: `?beta=1&lab=1`, phone 390 x 844 dsf 2 and desktop 1440 x 900, on a foggy live frame. Shots with `data3d` 0 and 1: default camera, top-down, and a low camera under the deck near Twin Peaks (`[7400, 300, -5200]` looking west). Save them on the PC and attach paths.
- Console: no errors, no warnings about texture formats.
- Replay nights (no 3D data) look exactly as before (pixel diff 0 with `data3d` 1 against `data3d` 0 on a replay night).
- Public page: unchanged (pixel diff against a build of `origin/v3-dev`).
- Bake time: `bench()` in the lab with `data3d` 1 against 0 (the 3D path adds 2 fetches per voxel; expect a similar or lower bake cost because the top noise is skipped). Real timings only on a real GPU; state which you used.
- JS tests: add `tests_js/test_fog3d.mjs` for the pure helpers (atlas offsets, `colMax`, `vh` rule, cache eviction).

## Gate
George looks at the three shot pairs on his phone and a computer and says whether `data3d` becomes the beta default. Until then it stays a lab switch. Report what looks worse as well as what looks better (for example a blocky floor at 25 m levels, or fog climbing lee slopes).

## Pitfalls
- `texture2D` in `BAKE_A` is GLSL ES 1.00 style with the three.js prefix; match the surrounding code, do not mix `texture` and `texture2D` for the same sampler type.
- Linear filtering across tile borders bleeds one level into another. The half-texel clamp prevents it; test with a volume that has a single bright level.
- PNG decode can apply colour management. Compare a decoded value with the file's raw byte in a test page before trusting the data.
- The time blend, drift (`uDrift`) and flow wind keep working on the baked volumes; do not change them in this guide.
- Phone memory: three extra 1284 x 812 single-channel textures are about 3 MB. Fine.
