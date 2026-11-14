# Skate mode ("Take control")

A side feature of the 3-D viewer. It does not touch the wetness model, the fog, the sky or any data build.
Feature list, ideas and suggestions live in [FEATURES.md](FEATURES.md), not in the main docs.

## What it is
During a route flyover a **Take control** button appears (top right). Tapping it hands the skater to you:

* **Phone:** drag the on-screen stick. Up pushes, down brakes, left and right steer. A small "↺ Street" button puts you back on the nearest street.
* **Desktop:** W A S D or arrow keys (S, down arrow or space brakes), R for Back on street, Esc exits. A gamepad works too (left stick, A or right trigger pushes, B or left trigger brakes).
* You roll downhill by gravity. Wet streets make steering slippery (same wetness the street colours show). Buildings are walls. Water and very steep ground off a street block you.
* Free skate only for now: no goals, scores or timers.

It is on for everyone who sees the data-layers page (`?beta=1`, or the public page once the live output is promoted). The old page without the data layers does not show it.

## Where the code is (all under `site/v3`, which is gitignored: add new files with `git add -f`)
| File | What |
|---|---|
| `skate.js` | Everything: ground and street lookup, walls, simulation, chase camera, joystick and keys. No three.js import (app.js passes it in). Loaded with `import()` only when Take control is tapped. |
| `skate.css` | Button, joystick and the slim bottom bar while skating. Loaded only during a flyover. |
| `app.js` | A small hook: `takeControl()`, `skateStep()`, `skateReadout()`, the `flytake` button, `keepCameraAboveGround()`, `group.userData.col` (building data for walls). Search for `Take control` and `keepCameraAboveGround`. |
| `index.html` | The `#flytake` button. |
| `tools/skate_test.mjs` | Offline tests: `node site/v3/tools/skate_test.mjs` (real terrain, buildings, streets; no browser). |

## How it works (plain version)
* **Ground:** the terrain grid (a point every 60 m) blended onto each street's own height near a street.
* **Streets:** the street sample table already loaded for the colours, indexed in a 32 m grid. District streets have two lane lines 10 m apart, city streets one line, so "on the street" means within about 8 m of a sample line.
* **Walls:** building boxes and outlines from `buildings.bin`, indexed in a 32 m grid. A circle of 1.8 m is pushed out of them and slides along. Respawn and "wedged" cases go back to the last good spot or the nearest clear street.
* **Moving:** push, gravity times true grade (heights are stretched 1.5 times in the scene, so slopes are divided by that), drag, brake, steering that slows with speed, side slip that grows with wetness. Everything is tunable in `TUNE` at the top of `skate.js`.
* **Camera:** chase camera with a spring, never under the ground (it is raised until the line to the skater clears hills). `keepCameraAboveGround()` in app.js does the same for orbit, flights and the flyover for everyone (`?noclamp=1` turns it off).
* **Cost:** no data files. About 4 MB of lookup tables built on first tap (about 80 ms laptop). One step costs about 10 microseconds. The pixel ratio drops on slow frames.

## Rules for changes
* Keep skating code in `skate.js` / `skate.css`; keep the app.js hook small so it merges cleanly with fog, sky and bus work.
* Never let skating change wetness, fog or any shared data. It only reads them.
* Run `node site/v3/tools/skate_test.mjs` before shipping. Add a test for any new collision or movement rule.
* New ideas go in FEATURES.md, not in the main README, PROGRESS.md or plan files.
