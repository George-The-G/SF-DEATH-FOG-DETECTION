# Plan A: viewer performance (building LOD, render on demand, dynamic resolution, antialiasing)

For: a Sonnet agent. Author: Claude (render thread), 2026-11-14. Approved by George in chat: items 1 to 4 below. Item 5 is explained only and NOT approved. Item 6 has its own plan (plan-B-building-merge.md).

Numbers marked "estimate" were not measured. Numbers marked "measured" were counted in a headless Chromium run of v3-dev on 2026-11-14 (software GL, so counts are reliable, timings are not).

## 0. Standing rules (follow the deathfog-agent-brief skill)

- Work in a fresh worktree from `origin/v3-dev`, own branch `perf-a-<stage>`. Never touch the dirty main checkout, `main`, or the deploy folder.
- Everything ships behind `?beta=1`. The public page must render byte-for-byte the same code path as today. Check this with Playwright on the public URL (no `?beta=1`).
- Do NOT edit `fog2.js`, `fog.js`, `fogflow.js`, `foglab.js` yourself. Another thread owns fog. Where this plan needs a fog hook (stages 2, 3, 4) write the smallest possible change as a separate commit, list it in the report, and say it needs the fog owner's review.
- Do not edit `ribbons.js` (public streets). `ribbons2.js` (beta streets) is fine to read, not needed here.
- Keep `buildingsMesh.userData.col` exactly as it is: `skate.js` and `buses.js` read it for building walls.
- Tests before merge: `python -m pytest -q tests tests_mc` and `node tests_js/*.mjs`. Five frozen-hash tests in test_physics/test_anchors are known to fail on Linux containers; on the Windows venv they must pass.
- No GPU jobs.

## 1. Where the time goes today (measured)

| View | Draw calls | Triangles |
|---|---|---|
| Whole-city default camera | 159 | 2.16 M |
| Same, buildings hidden | 8 | 0.32 M |
| Mid zoom | 118 | 1.86 M |
| Street level | 54 | 1.0 M |

Buildings: 163,375 instanced boxes x 10 triangles = 1.63 M, in 150 one-km tile meshes, plus 5,375 true polygons (~0.2 M triangles) in ONE mesh. Median building is 12 x 12 m, 7.9 m tall. At the default camera one pixel is about 19 m, so most building triangles are smaller than a pixel.

## Stage 0: measuring harness (do first, ~half a day)

Files: new `site/v3/tools/perfbench.cjs` (Node + Playwright), new `docs/perf/README.md`, results in `docs/perf/<date>-<stage>.json`.

1. Load `?beta=1&hud=1` (sky.js already has a frame-time HUD: median, p95, draws, triangles, GPU name).
2. Fixed camera list (scene coordinates, `[px,py,pz, tx,ty,tz]`):
   - over: `[3900, 8800, 7600, 8300, 0, -8000]` (default)
   - mid: `[6500, 2200, -4200, 6500, 0, -6200]`
   - hill: `[7400, 900, -5200, 7400, 0, -6400]`
   - street: `[5200, 350, -7300, 5200, 0, -8000]`
   - flyover: select the first route and call `__dbg.startFly()`, record 15 s.
   Set via `window.__dbg.camera` / `__dbg.controls` (already exposed).
3. For each: wait 3 s, record 120 frames of `requestAnimationFrame` deltas, `renderer.info.render` (via `__dbg.stats()`), drawing buffer size. Write JSON. Take a screenshot at deviceScaleFactor 1 for the image-diff checks in later stages.
4. Viewports: desktop 1440x900 and phone 390x844 dsf 2 (`devices['iPhone 13']`).
5. Real phones are George's step: document in `docs/perf/README.md` how to open `?beta=1&hud=1`, the four views, and where to type the HUD numbers. Include a 5-minute idle test (frame time at minute 1 vs minute 5) for heat throttling.

Acceptance: a baseline JSON for v3-dev committed before any other stage.

## Settings UI: new "Performance" section (beta only)

- Built in `app.js` only when `BETA`. Append after `initBeta()` has run, because `beta_ui.js buildSettings()` moves the first `.sec` that contains `.tgs` into its "Map" group. Use your own classes (`perf-sec`, `perf-tgs`) styled like `.sec`/`.tgs` so beta_ui never grabs it. If `#bx-g-map` exists, insert the section after that group; otherwise append to `#sheet`.
- Markup (one heading, three checkboxes, one note line):
  ```html
  <div class="sec perf-sec" id="perfsec">
    <h4>Performance</h4>
    <div class="perf-tgs">
      <label><input type="checkbox" id="perf-lod" checked><span>Simpler far buildings</span></label>
      <label><input type="checkbox" id="perf-dynres" checked><span>Lower resolution while moving</span></label>
      <label><input type="checkbox" id="perf-aa" checked><span>Smooth edges</span></label>
    </div>
    <div class="note" id="perf-note"></div>
  </div>
  ```
- State: `localStorage['df3.perf'] = {"lod":true,"dynres":true,"aa":true}`, every read and write in try/catch, defaults when storage fails.
- URL overrides for testing: `?lod=0|1`, `?dynres=0|1`, `?aa=0|1`, `?ondemand=0`. URL wins over storage.
- The note line shows the current state in plain words, e.g. "Far buildings simplified beyond 5.6 km · resolution 100%".
- Mockup rule: show George a screenshot of the settings panel (phone and desktop) before merging stage 1.

## Stage 1: building distance LOD (checkbox "Simpler far buildings")

Where: `buildBuildings()` in `site/v3/app.js` (around line 183) and the frame loop (`frame()`, around line 842).

1. Far geometry: a unit roof quad (2 triangles, y = 1, normal up) as `roofGeo`.
2. For each tile `InstancedMesh im`, create `imFar = new THREE.InstancedMesh(roofGeo, matFar, n)` and share buffers: `imFar.instanceMatrix = im.instanceMatrix; imFar.instanceColor = im.instanceColor;` Copy `boundingSphere`, `matrixAutoUpdate = false`. Store `{ im, imFar, cx, cz }` with the tile centre.
3. `matFar`: same Lambert material, colour multiplied by a constant `FAR_TINT` (start 0.92, tune by image diff against full detail at the over view) so the average brightness does not pop when walls disappear.
4. Switching distance from screen size, not a fixed number: switch when a 16 m building is under 4 device pixels:
   `dFar = 16 * hPx / (2 * tan(fovRad / 2) * 4)` where `hPx = drawingBufferHeight`. Phone (1688 px, fov 62°) gives ~5.6 km; desktop (1575 px, fov 50°) ~6.8 km (estimates). Hysteresis: go far at `dFar * 1.1`, back near at `dFar * 0.9`.
5. Per frame (only when buildings are visible and the camera moved): for each of the 150 tiles compute distance camera to tile centre (2-D plus height), set `im.visible`/`imFar.visible`. That is 150 distance checks, negligible.
6. Polygon buildings: split the single polygon mesh into one mesh per km tile (same key as boxes), with walls and roof in separate index ranges or separate meshes; far tiles hide the walls. This also lets them frustum-cull when zoomed in.
7. Checkbox off = always near (today's look).

Acceptance:
- over view: triangles from 2.16 M to under 0.8 M (estimate ~0.5 M); draw calls unchanged or lower.
- Image diff over/mid view, LOD on vs off: mean absolute pixel difference under 2% and no visible seams in the screenshots; send both to George.
- Slow zoom from street to over: no visible popping (record a short video or 10 screenshots).
- Public page unchanged.

Risk: low. Roof-only tiles lose wall shading at distance; the tint constant compensates.

## Stage 2: render only when something changed (no checkbox, `?ondemand=0` turns it off)

Today `frame()` renders every frame forever, even when nothing moves. On phones this heats the device, which then throttles.

1. Add `let dirty = 2; const invalidate = (n = 2) => { dirty = Math.max(dirty, n); };` (2 frames covers damping start/stop.)
2. Keep `requestAnimationFrame(frame)` and `controls.update()` every frame (cheap). At the end, render only if `dirty > 0` (then `dirty--`) or something is animating.
3. Call `invalidate()` from: `controls.addEventListener('change')`, `resize()`, `applyTime()`, camera `flight`, `fly`, skate (`fly.control`), `playing`, every settings checkbox/slider, night switch, `selectNight`, live data arrivals (`liveRest`, `liveSetData`), route select/clear, tooltip-independent layer changes from `beta_ui.js` (pass `invalidate` in `betaCtx()` and call it from beta_ui where it changes the scene), `SKY.setTime`, label updates.
4. Continuous animators keep rendering while active:
   - buses (`BUSES` present and visible): keep rendering, but at most 30 fps (skip every other frame) unless the camera moves.
   - sheets fog (`fog.js`, public fog) drifts with time: render at 30 fps while visible.
   - volumetric fog (`fog2.js`): needs frames while it refines. Ask the fog owner for `fog.busy()` returning true while phase is refining, moving, or the wind-flow drift is on. Until it exists, treat volumetric fog as always busy (no saving, but correct).
5. Watchdog: render at least once every 2 s anyway, so a missed trigger heals itself.
6. HUD: show "idle" when frames are skipped.

Acceptance: with the camera still, fog off and no buses, Chrome Performance shows rAF ticks with zero draw calls; a checklist of 15 interactions (orbit, zoom, pan, damping coast, play, scrub, route pick, fly, skate, every toggle, night switch, resize, rotate phone, live data arrival, tooltip) all update on screen. Record the checklist results in the report.

Risk: medium. A missed trigger shows as a frozen picture; the watchdog limits it to 2 s.

## Stage 3: dynamic resolution (checkbox "Lower resolution while moving")

1. Base pixel ratio stays as today: `min(devicePixelRatio, isTouch ? 2 : 1.75)`.
2. While moving (dirty because of camera motion, flight, fly, skate or play), keep a rolling median of the last 20 frame times. If the median is over 20 ms, step the scale down: 1.0 -> 0.8 -> 0.65 (of base). If under 12 ms, step back up. At most one step per 500 ms.
3. When idle for 300 ms, restore scale 1.0 and render one full-quality frame.
4. Apply with `renderer.setPixelRatio(base * scale)`. That reallocates the canvas and the volumetric fog's render targets (fog2 `ensureRT` reads the drawing buffer size). The 500 ms limit keeps that rare. If the fog owner prefers, a fog hook that keeps its targets and draws into a sub-viewport is the better long-term fix; note it in the report, do not build it.
5. Note line shows the current scale.

Acceptance: with Chrome CPU throttling 4x and the phone viewport, the motion frame-time median improves (report numbers); the still image after 300 ms idle matches the full-resolution screenshot exactly.

Risk: low. Slight softness only while moving.

## Stage 4: antialiasing (checkbox "Smooth edges")

Today the canvas is created with `antialias: true`. With volumetric fog (beta default) the scene is drawn into the fog's own render target and the canvas only receives one full-screen copy, so the canvas multisampling is wasted memory. Phones on fog tier 2 get no antialiasing at all (`ms: 0`).

1. Beta only: create the renderer with `antialias: false`.
2. Antialiasing comes from a multisampled scene render target instead:
   - fog visible (fog2 draws): ask the fog owner for `fog.setSamples(n)`, n = 4 when "Smooth edges" is on, 0 when off, for every tier (today it is fixed per tier).
   - fog hidden or fog tier 0: app.js renders the scene into its own `WebGLRenderTarget({ samples: 4 })` and copies it to the canvas with a full-screen triangle (resolve happens in the target). Off = render straight to the canvas.
3. Toggle switches live (no reload needed).
4. Default: on. After real-phone numbers (stage 0 harness), George decides whether phones default to off.

Acceptance: desktop edges look the same as today (screenshot diff), phone with fog on now has smooth edges, GPU memory (Chrome about:gpu or `renderer.info.memory`) no longer includes a multisampled default framebuffer. Report frame time on vs off.

Risk: low to medium (touches the fog render path through a hook only).

## Item 5 (explained, not approved): compact building instances

Each instanced building today carries a 4x4 float matrix (64 bytes) plus a colour (12 bytes): ~12 MB of GPU data for 163k boxes, built on the main thread with 163k `Object3D.updateMatrix()` calls at load. A box only needs centre x/z, base, height, width, depth, angle and a tint: 16 bytes. The vertex shader rebuilds the box from those numbers. Gains (estimates): GPU data 12 MB -> ~2.6 MB, faster load on phones, less memory traffic per frame, and a spare 4 bits for a "hidden wall" mask that plan B needs. Cost: a custom shader (Lambert lighting via `onBeforeCompile`), and the LOD of stage 1 must share the new buffer. Do not build until George approves.

## How to finish each stage

- One PR per stage into `v3-dev`, each with before/after numbers from the harness and screenshots (phone 390x844 dsf 2 and desktop).
- Do not merge or deploy. George decides; deploy goes through the deathfog-ship skill.
- Report: files changed, test counts, harness numbers before/after, screenshot paths, fog hooks requested, risks, anything cut.
