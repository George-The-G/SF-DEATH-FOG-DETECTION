# Viewer performance options (beta only)

Settings > Performance on `?beta=1`. The public page keeps today's code path (no performance code runs there).

| Option | Default | What it does |
|---|---|---|
| Simpler far buildings | on | Tiles whose nearest point is further than `16 m * bufferHeight / (2 tan(fov/2) * 4)` draw roofs only (about 5.6 km on a phone). Hysteresis 0.9 / 1.1. Near and far meshes share buffers. Polygon buildings are split per km tile; far tiles drop their walls. |
| Lower resolution while moving | off; on for weak devices | Pixel ratio x0.7 while the camera moves (and 220 ms after). Weak = touch device with memory <= 3 GB, cores <= 4, or an older Mali/Adreno/PowerVR GPU. Any device turns it on by itself if the median moving frame stays above 30 ms over 90 frames (unless you chose). |
| Smooth edges | on; off for weak devices | Own 4x multisampled target, blitted with the sRGB conversion. With fog on, the fog pass's own target is used (`fog.setMsaa`). The canvas itself no longer asks for antialiasing on beta. |

Also on beta: **render on demand**. A frame is drawn only when the camera, an input, a download or an animation changed something (fog still refining, playing, fly-along, live buses). Idle full redraw every 4 s as a safety net. `?ondemand=0` switches it off.

URL overrides: `?lod=0|1 &dynres=0|1 &aa=0|1 &ondemand=0`. Saved choices: localStorage `df3.perf`.

## Measuring
`node tools/perfbench.cjs "beta=1&lod=0"` vs `lod=1` (needs Playwright; set `THREE_DIR` to an unpacked three@0.160.0 if the CDN is blocked). Counts only: headless software GL gives no usable frame times. Baseline in `baseline.json`. Real-device frame times (phone and desktop GPU) are still to be taken by hand.

## Known limits
- Live bus markers keep drawing every frame (they move). 
- `fog2.js` got three small hooks: `busy()`, `setMsaa(n)`, and an optional `plain` draw callback in `renderFrame`. Fog owner should review.
