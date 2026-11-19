# SF Death Fog: real-time cloud and fog techniques survey

*November 2026, for the WebGL2 voxel fog renderer (hourly 428 x 406 x 24 bake + fullscreen ray march). Numbers are quoted from the sources. "(not verified)" marks claims with no primary source I could open. Gains for our case are estimates, not measurements.*

## Summary: the five most valuable ideas, ranked by gain / effort

**1. Interleaved low-res tracing + temporal reconstruction (TAAU).**
- **What:** each frame, ray-march 1 pixel of every 4x4 (or 2x2) block, reproject the previous result with the camera matrices, clip it to the current neighbourhood, and upsample with depth. Variants ship in HZD, Unity HDRP, takram three-clouds and weBIGeo; UE5 traces clouds at quarter res by default.
- **Why it fits:** within an hour our fog is static or drifts a few m/s, and the camera motion is known exactly. Reprojection is therefore near-exact, an easier case than fast-moving game clouds.
- **Gain:** 16x (4x4) or 4x (2x2) fewer rays per frame. HZD went from ~20 ms to ~2 ms on PS4. three-clouds (three.js) went from 31 to 92-95 FPS at 4K on an M3 Max.
- **Sketch:** three passes.
  - **(A) Trace** at W/4 x H/4 into RGBA16F (needs EXT_color_buffer_float: 100% of iOS browsers per web3dsurvey). March sub-pixel `bayer4x4[frame % 16]`. Output premultiplied in-scatter + transmittance, plus fog depth (transmittance-weighted mean distance) in R16F.
  - **(B) Resolve** at half res on phones, full on desktop, with ping-pong RGBA16F history. Compute `prevUV = prevViewProj * vec4(camPos + dir * fogDepth, 1)`. Use the fresh sample where traced. Otherwise use history clipped to mean ± 1σ of the 3x3 low-res neighbours (variance clipping, Salvi). If off-screen or scene depth mismatches, fall back to a joint-bilateral upsample of (A).
  - **(C) Composite.** This replaces the 3x3 blur.
- **Risks:**
  - Ghosting at building and ridge silhouettes and through sparse fog (reported by takram and weBIGeo).
  - About 16 frames to converge after hour changes.
  - Moving detail (idea 3) needs a lower history weight where flow exceeds ~1 px/frame.

**2. Spend steps only where fog is: slab clipping + hierarchical skipping + two-state marching.**
- **What:** the fog is a 2.5D layer, so most of each ray is provably empty. Once per hour, build the skip structure from the baked volume, not from the unreliable fog-top field. Use either a 2D max-height texture with a max-mip pyramid (Tevs et al. 2008: 0.17 ms to build at 256² on a 2008 GPU) or a conservative coarse distance field from jump flooding.
- **March:**
  - Clip the ray to [ground, max top].
  - Leap empty tiles with a 2D DDA.
  - Re-spread the 18/26-step budget over the occupied interval.
  - Take coarse steps on a lower mip until density > 0, then step back once and refine (HZD; weBIGeo uses 4Δt at mip+3).
- **Gain:** Nubis³'s SDF + adaptive steps cut ray-march time from 6.1 to 2.2 ms (ground view) and from about 8 to 4 ms (in air). Ours scales with the clear fraction of each hour: large on patchy hours, small under full cover, where early exit already helps. Shorter intervals also reduce banding.
- **Risk:** a non-conservative structure erases thin wisps. Nubis's rule: the stored distance must never exceed the true one.

**3. Wind-driven motion: flow-mapped detail noise on the pipeline's own wind fields.**
- **What:** keep the hourly bake for the envelope and lighting, and move only high-frequency detail at render time. Sample a tiling 64³-128³ RGBA8 noise at `p - v(p)*phase*T` for two phases half a cycle apart, crossfaded (Vlachos 2010; Neyret 2003 instead regenerates layers based on accumulated local deformation).
- **Where v comes from:** the precomputed CFD-like per-direction fields, exported coarse (e.g. 107 x 101 x 2-4 heights, RG8/RG16F) and scaled by hourly speed, plus curl noise (Bridson 2007) baked in once per hour.
- **Why it fits:** wisps stream around Twin Peaks and speed up through the Golden Gate while the bank stays where the data puts it.
- **Don't warp the density field.** It is C(x)·S(t - L(x)), so steady banks are stationary. For moving fronts, evaluate C·S(t - L) at fractional time (Q2).
- **Cost:** +2 noise fetches per in-fog step near the camera (LOD'd out at distance) and 1 velocity fetch per ray. Affordable after idea 1.
- **Risks:** pulsing at phase reset (hide it with a noise offset, as Vlachos does). Distortion beyond about a third of the feature size looks wrong. Time-lapse playback multiplies speeds.

**4. Richer baked lighting (realism at ~zero per-pixel cost).**
- **What:**
  - Add a channel for transmittance toward the hour's sun, marched in the bake and amortized over frames. Nubis³'s summed-density light grid costs 0.1-0.2 ms over 8 frames and cut render time ~40% versus live light samples.
  - Keep G (to the top) as ambient visibility.
  - Shade with 1-2 multiple-scattering octaves (UE recommends 1 for games) and Hillaire's energy-conserving step integral `S*(1-exp(-σt*Δ))/σt`, so brightness stays stable across step counts and modes.
- **Bonus:** the ground-level sun channel doubles as a fog-shadow mask on terrain (cf. UE beer shadow maps, weBIGeo terrain shadows).
- **Risk:** at low sun the bake marches are long, so the bake costs more on phones.

**5. Distance LOD: analytic 2.5D far field + mip sampling.**
- **What:** beyond a few km, 40 m voxels are sub-pixel and jittered samples mostly add noise. Bake one 2D RGBA16F texture per hour holding per-column optical depth, base and top. Integrate far pixels analytically column by column (2D DDA) and blend to the voxel march in a band. Sample 3D-texture mips by step length.
- **Precedents:** UE height fog and Local Fog Volumes use analytic integrals and run on low-end hardware. Nubis³ uses split resolution by distance (~50% saved) and noise mips (~15%).
- **Gain:** unmeasured; proportional to the share of far pixels.
- **Risk:** a visible seam at the transition.

### Evaluation of the planned speed-ups
- **Accumulate when still:** correct, but it is idea 1's trivial case. Frame time matters most while moving. If detail noise moves, keep a 1-in-16 refresh instead of freezing.
- **Half res + fewer steps while moving:** step count dominates cost (Toft et al.: 128 to 8 steps took 128 ms to 2.3 ms). But fewer steps without temporal accumulation means banding, and resolution switches pop. Prefer constant low-res tracing + reconstruction, with dynamic resolution only as a safety valve.
- **Per-hour cache + crossfade:** good, but sampling two volumes doubles fetches per step. Bake the blended volume progressively instead, a few layers per frame. An RG8 volume is 4.17 M voxels x 2 B ≈ 8.3 MB, so cap cached hours on iOS.
- **Coarse 2D occupancy:** right direction. Store max top height, not a bit; add mips; derive it from the baked volume; keep it conservative.

## Q1. State of the art, 2022-2026

**Guerrilla (Nubis)**
- **HZD (2015):** 2D weather map + height profiles + 3D noise. About 2 ms on PS4 by tracing 1 pixel per 4x4 block per frame ("10x faster or more" than the ~20 ms before). Cheap samples search for cloud, then the march steps back to full samples (64-128 steps). About 6 cone light samples, one of them far away.
- **Nubis Evolved (SIGGRAPH 2022):**
  - **The PDF failed to load** from both the CloudFront link and the Advances mirror. Only the abstract was available: fly-through PS5 clouds, aimed at 1080p without temporal upscaling, plus fast-moving superstorms and internal lightning.
  - Nubis³ recaps the earlier height-profile method as amortised over 16 frames (20 ms to 2 ms) but struggling with fast camera motion near clouds.
- **Nubis³ (Advances 2023):**
  - **Data:** dense voxels (4 x 4 km x 500 m, 8 m, BC4).
  - **Ray-march acceleration:** a separate 512x512x64 SDF, BC1-compressed and never over-estimated. Step = max(sdf, max(1 m, √dist·0.08)) + jitter. Density is sampled only where the SDF < 0.
  - **Up-rez:** to 0.5 m precision with a 128³ 4-channel noise.
  - **Lighting:** 2 live light samples, the rest from a 256x256x32 summed-density grid.
  - **Resolution and cost:** 960x540, with 480x270 under 200 m. The ray march costs 2.2-4 ms on PS5.
  - **Wind:** a single global offset. Fluid sims are offline modeling only.
  - **Relevance:** the closest production analogue to our renderer.

**Hillaire / Frostbite / Unreal**
- **Frostbite volumetrics (2015):**
  - Froxels of 8x8 px x 64 slices, RGBA16F.
  - Temporal blend of 5% new / 95% history with Halton jitter.
  - PS4 at 900p: 2.95 ms (8x8 tiles) vs 0.83 ms (16x16).
  - The 2016 sky/clouds PDF **did not load**.
- **SIGGRAPH 2020 (UE):**
  - Beer shadow map in R11G11B10F: front depth, mean extinction, max optical depth.
  - Multiple-scattering octaves.
  - Summarises RDR2 as "trace at quarter resolution with TAA upsampling". The RDR2 deck (232 MB) was **not opened**.
  - States that clouds were "not reasonably achievable on mobile. For now."
- **UE5:**
  - `r.VolumetricRenderTarget.Mode` 0 (default) traces at quarter res and reconstructs at half.
  - No volumetric clouds on the mobile renderer.
  - Volumetric fog uses a frustum grid (cvar defaults: 16 px cells, 64 slices). About 1 ms on PS4 at High.
  - Local Fog Volumes are analytic.
  - Sparse Volume Textures are experimental and not on mobile.
- **Unity HDRP:** quarter res + temporal accumulation. About 2 ms on a base PS4, under 0.5 ms on a high-end GPU. Separate shape and erosion wind.

**Other talks**
- **Wronski (Ubisoft, AC4, 2014):** 160x90x64/128 froxels, ~1.1 ms total.
- **El Mansouri (Ubisoft, Rainbow Six Siege, GDC 2016):** checkerboard rendering with a 1.4 ms resolve and about 8-10 ms net gain.
- **IOI "Smolder" (Advances 2026 course site):** sparse 4³ bricks with an occupancy-bitmask DDA, 4-bit density, and an adaptive lighting rate (~50% saved in heavy smoke). Simulations are prebaked.
- **Borderlands 4 clouds (GDC 2026):** abstract only.

**Web**
- **takram three-clouds (three.js, GLSL):** a TAAU resolve marches 1/16 of texels. STBN jitter; 3 beer-shadow-map cascades. iPhone 13, Low preset: 36-53 FPS (author's v0.0.1 numbers). Ghosting through sparse clouds.
- **weBIGeo Clouds (TU Wien, March 2026):**
  - **Data:** WebGPU; ICON-D2 2.2 km data in BC4+zstd tiles.
  - **Rendering:** half-res + TAAU (3x3 variance clip) and a two-state march.
  - **Cost:** 2.25 ms worst case on an RTX 5070 Ti.
  - **Not covered:** no timestep blending or wind (future work); mobile untested. The thesis lists coarse vertical resolution as unable to resolve sharp fog-layer boundaries.

**Research**
- **Neural shading:** MRPNN (SIGGRAPH 2023) is real-time on an RTX 2080 for static clouds. Olajos et al. (HPG 2026) report MLPs at 0.3-10 ms; hardware unstated. Neural six-way lightmaps (2026) target smoke.
- **Kernel primitives:** "Don't Splat your Gaussians" (TOG) and Gabor Fields (2026) target path tracing, not real-time.
- **Shared caches:** Weinrauch et al. (HPG 2023) cache clouds across viewers.
- **Verdict:** none of this is WebGL-phone-ready.

## Q2. Making fog move with wind fields

**Key point.** Our field is F(x,t) = C_dir(x)·S(t - L(x)), with lag L ≈ distance along the flow / U. The pattern is stationary; only the signal propagates. Two consequences:
- **Moving the field is wrong.** Advecting or optical-flow-warping the density would drag steady banks downstream. Visible motion should come from detail moving through a stationary envelope.
- **Crossfade is nearly exact for hourly S.** With hourly, linearly interpolated S, the model's own front is a ramp about U x 1 h wide (≈18 km at 5 m/s, our whole domain), so per-pixel crossfading already matches it to first order. Sharp, sweeping fronts need sub-hourly S evaluated in-shader as C·S(t - L). **That needs a pipeline export of L and S; check whether sub-hourly S exists.**

| Technique | WebGL2 cost | Verdict |
|---|---|---|
| Uniform wind scroll (HZD, Nubis³, Unity, three-clouds) | ~free | Baseline; ignores terrain |
| Two-phase flow map (Vlachos 2010); advected textures (Neyret 2003) | 2 noise fetches + v. In Portal 2 it cost 2 extra texture fetches and 21 extra ALU instructions over plain scrolling | **Best fit** |
| Curl noise (Bridson 2007); ramping the potential near solids keeps flow tangent | ~free if baked into v | **Use** for swirls. Ding & Batty 2023 fix boundary discontinuities, but mainly in 2D, with an offline MATLAB prototype |
| Mass-consistent / potential flow (WindNinja: "minimize change in initial wind field while conserving mass"; Harris pressure projection) | 40-80 Jacobi passes on 214x203, once per hour | **Fallback** if CFD fields can't ship. Block cells where terrain is above the fog top |
| Semi-Lagrangian advection (Stam 1999: stable but "too much numerical dissipation"); MacCormack (Selle 2008: 2nd order, ~2x cost) | 1-2 fetches per texel per frame | Fine for 2D fields; diffusive and costly for the 3D volume; wrong for steady banks |
| Optical-flow interpolation (pysteps; Vandal & Nemani) | Motion estimation | Unnecessary, since we know v; pysteps can't model growth or decay |
| Wavelet turbulence (Kim 2008) | Offline (~30 s/frame at 400³) | Borrow only: scale noise by local speed or shear |
| Full fluid solve (GPU Gems ch. 38) | Feasible in 2D | Not data-faithful; not needed |

**How games do it.** Most scroll noise along one wind vector: HZD adds curl-noise distortion, Nubis³ uses a global offset, Unity separates shape and erosion wind. Ghost of Tsushima sums "vorticles" for gusts and vortices, and its particles sample the heightmap ahead of themselves to rise over terrain. I found no shipped title that advects clouds through a full 3D wind field. Microsoft Flight Simulator is **not verified**.

**Recipe:**
1. Ship coarse per-direction wind. If possible also ship a coarse 3D tracer, which would replace the 62 m / 900 m fog-top heuristic.
2. Bake v plus curl noise hourly.
3. Move detail with a two-phase flow map.
4. Move fronts with lag-aware time evaluation.

## Q3. Performance techniques, ranked for our case

1. **Interleaved tracing + temporal reconstruction:** up to 16x fewer rays. HZD ~10x; three-clouds ~3x end-to-end FPS; Toft et al. half to quarter res took 7.5 ms to 2.4 ms. On mobile, Arm's fog runs at w/4 x h/4 with a 1/16 history blend.
2. **Lower internal resolution + depth-aware upsample:** goes with #1. Toft et al.: full to half res took 297.7 ms to 128 ms. A UE5 mobile cloud demo (blog) reached 60 FPS on an Adreno 540 only after rendering at 320x180.
3. **Empty-space skipping + slab clipping:** 2-2.8x in Nubis³; scene-dependent for us.
4. **Distance LOD (analytic far field, mips):** Nubis³ reports ~15% from mips and ~50% from split resolution.
5. **Light caching:** about -40% in Nubis³. We already have it; extend it to the sun direction.
6. **Early termination:** in place (98%). weBIGeo and three-clouds stop at 99%.
7. **Froxels:** fixed cost, independent of screen resolution (Wronski ~1.1 ms; Frostbite 0.83-2.95 ms). Feasible via `framebufferTextureLayer`, but it needs one pass per slice and blurs edges. Low-end fallback only.
8. **Fewer steps while moving:** large raw saving, but it trades for banding. #1 subsumes it.

**WebGL2 notes:**
- **Jitter cost:** Toft et al. measured per-pixel jitter raising cost from 2.3 to 7.5 ms through cache misses. Profile IGN against a per-frame offset.
- **Float filtering:** OES_texture_float_linear is only 53% on iOS. Keep filtered data in RGBA8/RGBA16F.
- **Noise:** STBN (Wolfe et al. 2022) converges faster under temporal filtering.

## Sources
- Nubis³ PDF (loaded): https://d3d3g8mu99pzk9.cloudfront.net/AndrewSchneider/Nubis%20Cubed.pdf ; page: https://www.guerrilla-games.com/read/nubis-cubed
- Nubis Evolved PDF (FAILED): https://d3d3g8mu99pzk9.cloudfront.net/AndrewSchneider/NubisEvolved/NubisEvolved-NoVideos.pdf ; mirror (FAILED): https://advances.realtimerendering.com/s2022/SIGGRAPH2022-Advances-NubisEvolved-NoVideos.pdf ; abstract: https://www.guerrilla-games.com/read/nubis-evolved
- HFW superstorms, GDC 2022: https://gdcvault.com/play/1027688/The-Real-Time-Volumetric-Superstorms
- HZD 2015: https://www.guerrilla-games.com/read/the-real-time-volumetric-cloudscapes-of-horizon-zero-dawn ; https://d3d3g8mu99pzk9.cloudfront.net/AndrewSchneider/The-Real-time-Volumetric-Cloudscapes-of-Horizon-Zero-Dawn.pdf
- Burning Shores blog: https://blog.playstation.com/?p=377555
- Frostbite 2015: https://www.slideshare.net/DICEStudio/physically-based-and-unified-volumetric-rendering-in-frostbite ; 2016 page: https://www.ea.com/frostbite/news/physically-based-sky-atmosphere-and-cloud-rendering ; 2016 PDF (FAILED): https://media.contentapi.ea.com/content/dam/eacom/frostbite/files/s2016_pbs_frostbite_sky_clouds.pdf
- Hillaire 2020: https://blog.selfshadow.com/publications/s2020-shading-course/hillaire/s2020_pbs_hillaire_slides.pdf
- UE: https://dev.epicgames.com/documentation/unreal-engine/volumetric-cloud-component-in-unreal-engine?lang=en-US ; https://indxzero.github.io/ue544cvarwiki/articles/r.volumetricrendertarget.mode ; https://dev.epicgames.com/documentation/unreal-engine/volumetric-fog-in-unreal-engine?lang=en-US ; https://indxzero.github.io/ue544cvarwiki/articles/r.volumetricfog.gridpixelsize ; https://indxzero.github.io/ue544cvarwiki/articles/r.volumetricfog.gridsizez ; https://dev.epicgames.com/documentation/unreal-engine/local-fog-volumes-in-unreal-engine?lang=en-US ; https://dev.epicgames.com/documentation/unreal-engine/sparse-volume-textures-in-unreal-engine?lang=en-US
- Unity: https://blog.unity.com/es/technology/experience-true-volumetric-clouds-with-hdrp-unity-20212 ; https://docs.unity3d.com/Packages/com.unity.render-pipelines.high-definition@17.0/manual/volumetric-clouds-volume-override-reference.html
- Wronski 2014: https://www.advances.realtimerendering.com/s2014/wronski/bwronski_volumetric_fog_siggraph2014.pdf
- El Mansouri 2016: https://ia601504.us.archive.org/8/items/GDC2016Mansouri/GDC2016-Mansouri.pdf
- RDR2 abstract: https://advances.realtimerendering.com/s2019/index.htm
- Smolder 2026: https://advances.realtimerendering.com/s2026/content/AlexanderMueller_Smolder_Siggraph26.pdf
- Borderlands 4: https://schedule.gdconf.com/session/demystifying-the-clouds-of-borderlands-4-adapting-volumetric-cloud-technologies-to-a-ue5-open-world-game/915656
- Ghost of Tsushima: https://gamedeveloper.com/design/using-vorticles-to-simulate-wind-in-i-ghost-of-tsushima-i- ; https://gdcvault.com/play/1027350/Blowing-from-the-West-Simulating
- takram: https://cdn.jsdelivr.net/npm/@takram/three-clouds@0.7.4/README.md ; https://www.npmjs.com/package/@takram/three-clouds
- weBIGeo: https://www.cg.tuwien.ac.at/research/publications/2026/muth-2026-clouds/muth-2026-clouds-thesis.pdf ; https://www.webindex.page/project/webigeo-clouds
- Papers: https://sites.cs.ucsb.edu/~lingqi/publications/paper_mrpnn.pdf ; https://www.highperformancegraphics.org/2026/_assets/slides/environmental_volumetric_neural_shading_of_clouds_for_real-time_rendering.pdf ; https://www.alphaxiv.org/abs/2604.03748.md ; https://arxiv.org/abs/2405.15425v2 ; https://www.emergentmind.com/papers/2602.05081 (third-party summary) ; https://diglib7.eg.org/bitstream/handle/10.2312/hpg20231138/077-088.pdf ; https://arxiv.org/pdf/1609.05344
- Flow and wind: https://cgweb.informatik.uni-freiburg.de/intern/seminar/gridFluids_StableFluids.pdf ; https://developer.nvidia.com/gpugems/GPUGems/gpugems_ch38.html ; https://yingjie.math.gatech.edu/publications/SFKLR.pdf ; https://diglib.eg.org/items/c4bf80cc-23f0-4107-958d-e03c0b9c8ac4/full (abstract only) ; https://www.advances.realtimerendering.com/s2010/Vlachos-Waterflow(SIGGRAPH%202010%20Advanced%20RealTime%20Rendering%20Course).pdf ; https://www.dneg.com/wp-content/uploads/2022/11/2007.pdf ; https://cs.uwaterloo.ca/~c2batty/papers/Ding2023/Differentiable_Curl_Noise.pdf ; https://www.cs.cornell.edu/~tedkim/WTURB ; https://ninjastorm.firelab.org/windninja/howdoes/solvers.html ; https://acp.copernicus.org/articles/16/5229/2016/acp-16-5229-2016.html ; https://gmd.copernicus.org/articles/12/4185/2019/gmd-12-4185-2019.html ; https://arxiv.org/abs/1907.12013v1
- Performance and WebGL: https://pure.mpg.de/pubman/item/item_1325622_5/component/file_3590464/i3d08.pdf ; https://www.comp.nus.edu.sg/~tants/jfa.html ; https://developer.download.nvidia.com/gameworks/events/GDC2016/msalvi_temporal_supersampling.pdf ; https://research.nvidia.com/publication/2022-07_spatiotemporal-blue-noise-masks ; https://www.johanneskopf.de/publications/jbu (not opened) ; https://developer.arm.com/community/arm-community-blogs/b/mobile-graphics-and-gaming-blog/posts/clustered-volumetric-fog ; https://80.lv/articles/creating-volumetric-clouds-for-mobile-in-unreal-engine-5 ; https://web3dsurvey.com/webgl2/extensions/EXT_color_buffer_float ; https://web3dsurvey.com/webgl2/extensions/OES_texture_float_linear ; https://developer.mozilla.org/en-US/docs/Web/API/WebGL2RenderingContext/framebufferTextureLayer (not opened)
