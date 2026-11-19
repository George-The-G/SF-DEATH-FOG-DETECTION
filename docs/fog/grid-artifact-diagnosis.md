# Grid pattern in the default beta fog: diagnosis and fix plan

Plan only. No product code was changed and nothing was deployed. Experiments ran in a throwaway worktree of `origin/v3-dev` (0db763f), now deleted.

## Cause (confirmed)

The "column-top skip" in the voxel ray march (`site/v3/fog2.js`, `VOX_FS`, added in commit e911250 "Beta fog: ... column-top skip") jumps a ray to the far edge of a 320 m occupancy column whenever the current sample sits above that column's fog layer:

```glsl
if (oc.x < .5 || p.y > oc.y * uHtMax * uFollow + max(uVHa, uVHb)) {   // empty column, OR above its fog layer
  ... tE = t + (distance to the column's horizontal exit) + .5;
  ... fi = max(fi, floor(sqrt((tE - t0) / (t1 - t0)) * N - jit)); continue;
}
```

Skipping an empty column is safe. Skipping "above the fog layer" is not: the test only asks whether the ray is above the top at the current sample, not whether it stays above until the column exit. A ray that is above the top here but descends into the fog before leaving the column is jumped past the fog. The steeper the view and the lower the fog top, the more rays do this:

- Straight down: the horizontal exit distance is huge (the ray has almost no horizontal travel), so the loop breaks and the pixel gets no fog.
- High oblique (the whole-city view): a ray drops more than the fog depth (about 300 m) in the 320 m it takes to cross a column, so it skips the fog in the middle of the column.
- The only pixels that keep fog are rays whose samples land close to a column boundary, where the jump is short. That leaves thin light lines on the 320 m boundaries and dark squares in between. In perspective the squares shrink with distance, which is the "finer toward the top-left" look in the screenshot.

Why it looks like a mystery setting: it does not depend on any setting. Every Performance or fog option I toggled in the fog menu (`skip`, `motion`, `taa`, `accum`, `detail`, `warp`, `flow`, `advect`, `bump`, `dither`, `blur`, `eddy`, `shadow`, `vres`, `vlayers`) left the pattern in place. `skip: 0` does not help because it only stops reading the occupancy texture; the height test still runs with a column top of `uHtMax * follow + layer height`. The pattern is also unrelated to the 1-in-16 reuse, the still-view reuse, the Performance defaults from PRs 27, 29 and 32, the wind drift, or the cloud-base model. Noise forced to a constant and a bake that writes only sprite coverage both still showed the lattice, so it is not in the bake or the data either.

## Where it shows

- Strongest where fog data covers the grid and the view is steep: top-down, and the default whole-city view from high up. Low, near-horizontal views are mostly fine.
- Phone and desktop both show it (phone emulation uses tier 2 with 1 voxel per 80 m cell; desktop tier 3 with 2). The lattice spacing is the same on both, about 320 m, so it follows the occupancy column, not the voxel size.
- It is on beta (`?beta=1`) in v3-dev. e911250 is not in `origin/main`, so the public page is not affected as long as public deploys from main (worth confirming before the next promote).
- Separate, smaller thing seen in the same shots: straight-edged blocky fog shapes beyond the north and south edge of the fog grid (the mirrored copy of the data past the edge). That is the existing "fog over the ocean past the grid edge" behaviour, not the lattice. I would leave it for now.

## Reproduce headless (done on the PC)

- Playwright (Python, from the repo `.venv`) with Edge, `--use-angle=swiftshader`, served from a worktree of `origin/v3-dev`, three@0.160.0 unpacked from `npm pack` and routed in for the CDN URL.
- URL `?beta=1&night=2026-10-01&lab=1`, iPhone user agent, 390x844 at dsf 2, `__dbg.tNow = 12.4` (20:04 Thu Oct 1), default camera (3900, 8800, 7600) looking at (8300, 0, -8000). Wait about 9 s for the still-frame reuse to settle.
- Top-down view for the clearest picture: camera (8300, 36000, -7999), target (8300, 0, -8000).
- Scripts: [gridfog_repro.py](../../spikes/grid_artifact/gridfog_repro.py) (single shot) and the multi-shot driver with a JSON plan are at `C:\Users\George\AppData\Local\Temp\gridfog_work\` on the PC (`repro2.py`, `plan*.json`). The driver sets `window.__fogLab.fog.setParams(...)` and `__dbg.tNow`, so it can toggle any setting between shots.
- Software GL: the look is valid, timings are not.

Images: [before_phone_2004.png](img/before_phone_2004.png), [before_topdown_desktop.png](img/before_topdown_desktop.png), [volume_lookup_debug.png](img/volume_lookup_debug.png) (raw volume sample: lattice is already in what the march reads), [after_F1_F2_topdown.png](img/after_F1_F2_topdown.png) (left F1, right F2), [after_F2_phone.png](img/after_F2_phone.png).

## Proposed fix

Option F2 (recommended): keep the height skip but never jump past the point where the ray reaches the column's fog top.

```glsl
float colTop = oc.y * uHtMax * uFollow + max(uVHa, uVHb);
bool emp = oc.x < .5;
if (emp || p.y > colTop) {
  vec2 bmin = ..., bmax = ...; vec2 tt = ...;
  float tE = t + max(min(tt.x, tt.y), 0.) + .5;
  if (!emp && rd.y < -1e-5) tE = min(tE, t + (p.y - colTop) / -rd.y);   // stop where the ray gets down to the fog layer
  if (tE >= t1) break;
  fi = max(fi, floor(sqrt((tE - t0) / (t1 - t0)) * N - jit));
  continue;
}
```

Empty columns still skip to the column exit (that is correct: no fog in them, with the drift dilation already applied). Above-layer columns skip only as far as the ray reaches the layer, so rays that never descend into the layer (looking up or level, or still above at the exit) skip the whole column as before.

Option F1 (fallback): drop the height test (`if (oc.x < .5)`). Simpler, same picture, but loses the saving for rays above the fog.

I tested both with a throwaway patch; the lattice is gone in the top-down and the phone whole-city shots, and the fog looks the same as the unskipped result.

Expected cost and quality:
- Quality: no change to the look. F2 should be pixel-identical to F1 apart from sample positions after a jump; the jump already floors to the previous step, so it never overshoots.
- Cost: F2 keeps almost all of the saving, because most rays are either above the layer all the way or enter it, and the ray only stops skipping at the layer top. Steep views lose some savings (that is the point: those rays do pass through fog). I could not measure frame time here (SwiftShader). F1 costs more for rays above the fog.
- Risk: low. The change is a few lines in one shader branch, behind the existing `skip` switch for the occupancy part. Public output is unaffected because e911250 is only in v3-dev.

## Verify

1. Headless (before and after on the same build): the two shots above at 20:04, plus 04:00 Fri and a mid-night frame, phone (390x844 dsf2) and desktop. Expect no regular lattice; compare the after shot against `skip=0` plus the height test removed (F1) as the reference: they should match closely.
2. Add a tiny regression check to the existing headless tools (top-down shot, assert the row and column autocorrelation of fog opacity at the 320 m period is flat). Cheap and it would have caught this.
3. On the iPhone: load `?beta=1&night=2026-10-01`, drag the slider to 20:04, whole-city view, and also tilt the camera to the top-down and then low angles. The grid and the stripes at the ocean edge should be gone. Check the Performance panel numbers (or the existing perf note) for frame time before and after in the whole-city view, since the skip saving is what the change must preserve.
4. Confirm the public page is unchanged (it does not include e911250).

## Notes for whoever builds it

- This touches `fog2.js` only. The other thread working on the bottom bar and LOD works in `app.js`, `beta_ui.js` and the building LOD, so there should be no overlap.
- Per the brief, build on a fresh branch from `origin/v3-dev`, run `python -m pytest -q tests tests_mc` and the JS tests, and verify `?beta=1` and the public page. No deploy until George approves.
