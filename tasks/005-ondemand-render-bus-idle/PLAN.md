# Task 005 Technical Plan: Render-on-Demand Bus Idle & Route Corridor Visibility

## Summary
Eliminates unnecessary idle GPU/CPU work in `site/v3/app.js` and `site/v3/buses.js`, and fixes premature bus cutoff along longer routes by filtering vehicles against the full route polyline corridor instead of a circle around the bottom stop.

## Changes

### 1. Full-Route Bus Corridor Filtering (`site/v3/buses.js`)
- Added `inRouteCorridor(o, p)`: checks `o.path.proj(p.x, p.z).d <= (o.d.radius_m || 1000) || Math.hypot(p.x - o.sx, p.z - o.sz) <= (o.d.radius_m || 1000)`.
- Replaced the bottom-stop circle filter (`Math.hypot(p.x - o.sx, p.z - o.sz) > (o.d.radius_m || 1000)`) in both `ingest()` (live/replay incoming feed) and the replay history/mock loader (`if (RP) ...`).
- Verified `/api/history` bbox query already spans the full `o.R.poly` bounding box plus `radius_m` margin, and no other distance-based fade/cutoff clips buses along the route.

### 2. Render-on-Demand Bus Activity Check (`site/v3/buses.js`, `site/v3/app.js`)
- Tracked `simPaused = !!(rpOn && !sim.speed)` in `update(sim)` so paused replay frames do not treat historical instantaneous `st.speed` as active motion.
- Exposed `isMoving()` on the `createBuses()` API:
  - Returns `true` when driving (`S.drive`), actively camera-following a selected bus (`S.b && S.cam`), or when any visible bus mesh is blending back (`b.blend`), in live wall-clock mode (`!RP`), or actively moving during unpaused replay (`!simPaused && b.st && b.st.speed > 0.05`).
  - Returns `false` when paused/idle with no moving/following/driving buses.
- Updated `keepDrawing` in `site/v3/app.js` from `|| BUSES` to `|| (BUSES && (BUSES.isMoving ? BUSES.isMoving() : true))`.

### 3. Per-Frame Allocation & Layout Poll Cleanup (`site/v3/app.js`)
- Hoisted `const _aaSz = new THREE.Vector2();` at module scope and reused it in `aaRender()` instead of allocating `new THREE.Vector2()` every frame.
- Replaced `setInterval(placeGear, 300)` with a `ResizeObserver` on `$('dock')`, a `MutationObserver` on `document.body` (`childList: true`, disconnecting once `#fogbtn` mounts), `resize` listener, and an initial `placeGear()` call.
- Removed the per-frame `ROUTES.forEach((r) => { r.hi.visible = false; r.dim.visible = false; })` loop from `frame()` when `useRib` is true, and updated `setMode()`, `clearRoute()`, and `selectRoute()` to gate route tube visibility on `!useRib`.

## Verification
- Run `node --test tests_js/test_bus.mjs tests_js/test_bustimeline.mjs tests_js/test_buslog.mjs` inside the worktree.
- Verify syntax of `site/v3/app.js` and `site/v3/buses.js` with `node --check`.
- Confirm `git status` shows edits strictly within `owned_paths` + `tasks/005-ondemand-render-bus-idle/PLAN.md`.
