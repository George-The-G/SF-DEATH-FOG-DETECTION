# Autodesk / NVIDIA "Warp speed CFD on GH200" vs our wind sim

Source: NVIDIA developer blog (published 2025-09-16, updated 2026-02-27). Read in full. It is marketing-level: relative numbers only, with absolute MLUPS in images I could not read. Our side is from `sim/wind-sim-settings.md` and `sim/guides/12_street_scale_spike.md`. `docs/WIND_SOLVER_CONFIG.md` is not on the default branch of the repo (404), so it is not reflected here.

## Bottom line
The article describes the same solver we already run: XLB, Warp backend. It is not a different technique to adopt. What it adds is a scale story (out-of-core, multi-node) that we do not need at a 27 M-cell city. Our open problems (boundary treatment, resolution at street scale) are not addressed by the article at all. Recommendation: adopt nothing from it now; spend effort on our own boundary and refinement work.

## What the article says
- XLB, open-source LBM library from Autodesk Research, Python-native, differentiable. Two backends: JAX and NVIDIA Warp.
- Warp vs JAX on one A100, lid-driven cavity: about 8x faster, 2 to 3x less memory.
- Warp vs FluidX3D (OpenCL/C++) on one GH200, 512^3 cavity: about 95% of its throughput.
- GH200 scale-out: domain lives mostly in CPU memory and is streamed to the GPU in tiles over NVLink-C2C (900 GB/s). 8 nodes reach about 50 billion cells, near-linear scaling, about 8x one node.
- A New York City LES video, no validation. No boundary-condition details, no refinement, no run times, no accuracy checks. Code: github.com/Autodesk/XLB.

## Side by side

| Aspect | Article | Us |
|---|---|---|
| Method | LBM, XLB | LBM, XLB (same) |
| Backend | Warp (headline), JAX | Warp |
| Hardware | A100; GH200 nodes, up to 8 | One RTX 4080 SUPER, about 3.55 G cell updates/s |
| Domain size | 512^3 single node; about 50 G cells on 8 nodes | About 27 M cells (20 m city), 6 to 7 min per wind direction; 18.8 M-cell 5 m tile needs about 3 GB |
| Resolution handling | Not described; uniform grid | Uniform 20 m; 5 m street scale is a separate spike with one-way nesting as "later refinement" |
| Boundaries | Not described | Bounce-back walls, free-slip sea, fixed-pressure outlet, log-law inflow planned, z0 map as end state |
| Validation | None shown | Cube test, empty-sea profile, buoy 46026 and SF stations |
| Turbulence | LES shown for NYC | Not stated in what I read; low-Reynolds behaviour (cube wake about 4 H vs about 1.6 H published) |

## What is relevant to us
1. **Performance is already near the ceiling.** Our 3.55 G updates/s on a consumer card is consistent with a well-optimised Warp LBM, and a 95%-of-FluidX3D result suggests the kernel is not leaving much on the table. Upgrading hardware would be a linear speedup, not a method change.
2. **Out-of-core and multi-node are not useful.** Our largest case (18.8 M to 27 M cells) fits in about 3 to 4 GB. Even a 2 m city-wide grid (about 27 G cells) is a different project. Revisit only if we decide on city-wide sub-10 m fields.
3. **The JAX vs Warp gap confirms our backend choice.** Stay on Warp. Note the stale-kernel-cache bug we hit is a Warp-specific risk; the private cache folder fix is the right mitigation.
4. **Nothing on our real accuracy gaps.** Wall treatment over sea, inflow profile, roughness and low-Re wake length are all outside the article. The relevant upstream work would be in the XLB repo itself (see below), not this post.

## Possible follow-ups (unverified, would need checking against the repo)
- Check the current XLB master for a multi-resolution / grid-refinement mode and for wall-model or rough-wall boundary conditions. I believe XLB has been adding both but could not confirm from the article, and I did not read the repo. If a refinement mode exists, it could replace the "one-way nesting" plan for the 5 m street spike and let us refine only near buildings at the Gate, Potrero and similar.
- Check whether XLB offers an LES/Smagorinsky collision option we do not already use. That would address the low-Re wake issue better than anything in the article.
- A newer XLB release may ship Warp performance fixes; pin and re-run the 3 s cube regression plus one 270 degree city run to compare timing before adopting.

## Recommendation
- Do not change method, hardware or backend because of this article.
- Do not touch the running 20 m recompute.
- After the recompute, spend a half day reading the XLB repo (releases, boundary conditions, multi-res, LES options) and report back. Only then decide whether the street-scale spike should use native refinement.
- Treat the article's numbers as marketing until absolute MLUPS are checked (Figures 1, 2 and 4 in the post).
