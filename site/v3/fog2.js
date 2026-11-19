/* Volumetric fog (beta, ?beta=1). Two techniques behind one interface, picked by params.mode:
   - 'march': procedural ray march through a fog slab (what beta shows today).
   - 'voxel': the fog is baked into 3D textures over the whole fog grid (density in R, light reaching each voxel from above in G),
     one volume per data hour, and the screen pass samples the two hours around the shown time and blends them. The baked noise covers
     the whole grid without tiling and self-shadowing is free.
   Voxel speed-ups (each has a lab switch):
     cache  - a volume is baked once per data hour (two layer passes on the GPU) and reused while the time moves inside that hour;
     skip   - a coarse occupancy grid (320 m cells, from the data) lets rays jump over fog-free columns;
     motion - while the camera moves, the fog pass runs at half resolution with fewer steps;
     accum  - when the camera and time stand still, a few jittered full-quality passes are averaged, then the fog pass stops
              entirely and the averaged image is reused (cost close to zero, and no dither grain).
   Same public interface as fog.js (build, clear, ready, setVisible, setDensity, setFrame, update) plus renderFrame() which app.js calls instead of
   renderer.render, setParams()/params for the fog menu (foglab.js), setWeather() for the fog top series, bench() for the fog benchmark.
   Pipeline per frame: scene -> render target with depth texture; fog pass at 50-100% resolution reading that depth; composite to the canvas.
   A frame-time governor steps down tiers 3 -> 2 -> 1 -> 0; tier 0 is the old sheets (fog.js). */
import { createFog as createSheets } from './fog.js';
import { solveWind } from './fogflow.js';
import { colMax, volumeHeight, usable, createLru, emptyEntry } from './fog3dview.js';
import { parseIndex, buildLine, bracket, strideFor, kindAt, swapIsInvisible, isNewer } from './fogtravel.js';

const PX_REF = 16; // sprite value that maps to full density after the sqrt stretch (same as fog.js)
const OCC_CELLS = 4; // occupancy cell = 4 x 4 fog cells (320 m)
const MAX_RAY_DIST_M = 40000.0;          // maximum ray-march distance (40 km) and 16-bit fog depth normalization scale
const BASE_EXTINCTION_PER_M = 0.0105 * 2.2 * 1.5; // volumetric extinction coefficient sigma_t (0.03465 m^-1 before vertical exaggeration uEX)
const SHADOW_EXTINCTION_RATIO = 0.55;    // shadow uses lower extinction because forward scattering lets more sun through
const FLOW_CYCLE_SEC = 120.0;            // two-phase Vlachos flow-map reset period (s)
const DETAIL_NOISE_TILE_M = 230.0;       // 3D detail noise spatial period (m)
const GROUND_BILLOW_BIAS = 0.12;         // near-ground density boost in 2D tile mode only (0 in 3D mode so it never fights the 3D cloud floor)
const MAX_VOLUME_SHIFT_FRAC = 0.35;      // maximum UV advection shift (fraction of city grid) during hourly blends to avoid pulling mirrored copies across the city
const MOTION_LINGER_MS = 150;            // ms after camera motion stops before still-frame accumulation starts
const STILL_REFRESH_MS = 3000;           // ms interval for periodic refresh when still-frame accumulation is complete
const TIERS = [null, // 0 = old sheets
  { scale: 0.5, steps: 5, vsteps: 10, oct: 1, ms: 0 },
  { scale: 0.5, steps: 10, vsteps: 18, oct: 2, ms: 0 },
  { scale: 1.0, steps: 12, vsteps: 26, oct: 3, ms: 4 }];

// @sec: settings & defaults
/* Settings. DEFAULTS is what beta shows (George's chosen look, 2026-11-14); foglab.js offers presets and edits them live. */
export const DEFAULTS = {
  mode: 'voxel',   // 'march' | 'voxel' | 'sheets'
  follow: 0.3,       // fog top: 0 = flat inversion at a fixed altitude (hills poke out), 1 = constant depth above the ground
  topSrc: 'data',  // 'data' = hourly fog_top_m, 'fixed' = topFixed
  topFixed: 200,   // m
  topMin: 90, topMax: 900, // m; fog_top_m is often a 62 m floor or a 900 m cap value, not a real top
  smooth: 3.5,       // h; 0 = the rounded hour (old, jumps at the half hour), >0 = gaussian blend over neighbouring hours
  soft: 1,      // top and edge softness 0..1
  bump: 0.25,      // voxel: how lumpy the top surface is 0..1
  detail: 0.9,       // fine noise strength 0..1
  warp: 1.25,         // domain warp 0..1.5 (breaks straight lines into billows)
  advect: 1.0,       // voxel: noise drift with the mean wind, as a fraction of the wind speed (moves with simulation time, not wall time). 1 = the real wind speed (George, 2026-11-14: realism wins; was 0.1)
  dither: 1,       // ray start jitter 0..1 (0 = banding, 1 = fine grain the blur removes)
  blur: 1,         // depth-aware blur of the fog buffer
  steps: 0,        // 0 = tier default
  res: 0,          // fog buffer scale; 0 = tier default
  vres: 0,         // voxel: horizontal voxels per fog cell (1 = 80 m, 2 = 40 m); 0 = auto (phone 1, computer 2)
  vlayers: 0,      // voxel: vertical layers; 0 = auto (phone 16, computer 24)
  shadow: 0.8,       // voxel: self-shadow strength
  sun: 0.5,          // sunlight multiplier
  night: 1,        // night ambient floor multiplier
  auto: false,      // frame-time governor
  skip: 1,         // voxel: empty-space skipping
  accum: 6,        // voxel: still-frame refinement passes (0 = off: the fog is drawn every frame)
  motion: 1,       // voxel: half resolution and fewer steps while the camera moves
  motionSteps: 0.6, // voxel: step multiplier while moving
  flow: 1,         // voxel: fine detail drifts along a terrain-aware wind field (fogflow.js), solved per data hour
  flowSpeed: 1.0, // voxel: detail drift as a fraction of the real wind speed, in simulation time (1 = real speed; was 0.042)
  eddy: 0.2,       // voxel: curl-noise eddies added to the wind field (fraction of the wind speed)
  taa: 1,          // voxel: while the camera moves, trace 1 pixel in 16 per frame and reproject the rest
  edgeFade: 3000,  // voxel: how far (m) past the edge of the data the fog carries on before it has faded out. It was 8000 with a mirrored copy of the volume, which drew straight seams and repeated panels far out at sea; shorter and with a noisy edge it reads as a cloud bank ending (the macro view of guide 09 is the real answer beyond the data)
  macro: 1,        // beta: the wide-area view (macro.js, guide 09): wide terrain, the satellite cloud bank out to 60 km, longer camera range; the voxel fog stays the truth inside the city grid. Live data only; the camera range and the wide data load only when the camera is pulled back
  sky: 0,          // beta (guide 13): sky layers above the fog (macro.js keeps upper satellite cloud decks when top > fogTop + 100..250 m). Off by default
  travel: 1,       // voxel: 1 = inside the 5-minute window of fog5/index.json (guide 08) the fog is blended between 5-minute frames; outside it the hourly data as before. On in beta (George 2026-11-16); when the build has no fog5/ folder it changes nothing. Live data only
  data3d: 1,       // voxel: 1 = the fog takes its shape from the live 3D fog files (index.json fog3d: a cloud floor, a data top, holes behind hills; an hour without a file has NO fog); 0 = the 2D tile with a guessed top. Live data only; replay nights have no 3D files and keep the 2D tile (George 2026-11-16: realism, no fog columns from sky to ground)
};

// @sec: shaders (ray setup, march, voxel bake, TAA, composite)
const VERT = `varying vec2 vUv; void main(){ vUv = position.xy * .5 + .5; gl_Position = vec4(position.xy, 0., 1.); }`;

const TILE = `
  float tile(float k, vec2 q){ // q = (x 0..1, s 0..1 from north); same atlas layout as fog.js
    k = clamp(k, 0., uNT - 1.); float r = floor(k / uTiles.x), c = k - r * uTiles.x;
    vec2 tp = clamp(vec2(q.x * uTiles.z, q.y * uTiles.w), vec2(.5), uTiles.zw - .5);
    return texture2D(uTex, (vec2(c * uTiles.z, r * uTiles.w) + tp) * uTexel).r;
  }`;

/* shared ray setup: view ray, scene depth, slab entry/exit between y = 0 and uYmax */
const RAY = `
    vec2 uvR = uSubOn > .5 ? (floor(gl_FragCoord.xy) * 4. + uSub + .5) / uFull : vUv; // 1-in-16 mode: this low-res pixel traces one sub-pixel of its 4x4 block
    float dpx = texture2D(uDepth, uvR).r;
    vec4 v = uInvProj * vec4(uvR * 2. - 1., 1., 1.); vec3 dv = normalize(v.xyz / v.w);
    vec3 rd = normalize(uCamRot * dv), ro = uCam;
    float tScene = 1e9;
    if (dpx < .99999) { float vz = (uNear * uFar) / ((uFar - uNear) * dpx - uFar); tScene = -vz / -dv.z; }
    float t0, t1;
    if (abs(rd.y) < 1e-4) { if (ro.y < 0. || ro.y > uYmax) { gl_FragColor = vec4(0.); return; } t0 = 0.; t1 = 1e9; }
    else { float a = (0. - ro.y) / rd.y, b = (uYmax - ro.y) / rd.y; t0 = max(min(a, b), 0.); t1 = max(a, b); }
    t1 = min(min(t1, tScene), t0 + ${MAX_RAY_DIST_M.toFixed(1)});
    if (t1 <= t0) { gl_FragColor = vec4(0.); return; }
    float N = float(uSteps);
    float jit = mix(.5, fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(.06711056, .00583715))) + uJit), uDither);
    float T = 1.; vec3 C = vec3(0.);
    float cs = dot(rd, uSun), ph = .3 + 1.7 * hg(cs, .6) * 12.566 * .35;
    float cm = dot(rd, uMoon), phm = .3 + 1.2 * hg(cm, .55) * 12.566 * .3;`;

const COMMON_U = `
  precision highp float; varying vec2 vUv;
  precision highp sampler3D;
  uniform sampler2D uDepth, uTex, uHt; uniform sampler3D uNoise;
  uniform mat4 uInvProj; uniform mat3 uCamRot; uniform vec3 uCam;
  uniform float uNear, uFar, uTime, uDens, uH, uYmax, uHtMax, uFrame, uNT, uJit, uSubOn; uniform vec2 uSub, uFull;
  uniform vec4 uTiles; uniform vec2 uTexel, uGo, uGs;
  uniform vec3 uSun, uSunCol, uMoon, uAmb, uMoonCol, uOff;
  uniform float uSunI, uCity, uEX, uDbg, uFollow, uSoft, uDetail, uWarp, uDither;
  uniform int uSteps, uOct;
  float hg(float c, float g){ return (1. - g * g) / pow(1. + g * g - 2. * g * c, 1.5) / 12.566; }`;

/* ---- technique 1: procedural ray march (beta today) */
const FOG_FS = COMMON_U + TILE + `
  void main(){` + RAY + `
    float fa = floor(uFrame), ft = uFrame - fa;
    for (int i = 0; i < 48; i++) {
      if (i >= uSteps) break;
      float u0 = (float(i) + jit) / N, u1 = (float(i) + 1. + jit) / N;
      float ta = t0 + (t1 - t0) * u0 * u0, tb = t0 + (t1 - t0) * u1 * u1, dt = tb - ta, t = .5 * (ta + tb);
      vec3 p = ro + rd * t;
      vec2 g = (p.xz - uGo) / uGs, qg = clamp(g, 0., 1.);
      float inside = 1. - smoothstep(0., 8000., length((g - qg) * uGs)); // fog keeps going over the ocean beyond the grid edge, fading over 8 km
      float gh = texture2D(uHt, qg).r * uHtMax;
      if (p.y < gh) continue;
      float hh = (p.y - gh * uFollow) / uH;
      if (hh < 0. || hh > 1.) continue;
      float px = mix(tile(fa, qg), tile(fa + 1., qg), ft) * 255.;
      float d = clamp(sqrt(px / ${PX_REF.toFixed(1)}) * uDens, 0., 1.) * inside;
      d = max(d, uDbg * inside);
      if (d < .003) continue;
      float prof = pow(1. - hh, 1.15) * smoothstep(0., .03, hh + .02);
      vec3 wp = vec3(p.x, p.y / uEX, p.z);
      // baked tileable 3D noise (R low fbm, G detail fbm, B warp). Detail fades out where the step is wider than the detail
      vec3 drift = vec3(uTime * .003, 0., uTime * .001);
      vec3 wq = wp;
      if (uOct > 1) wq.xz += (texture(uNoise, wp * vec3(1. / 3500., 1. / 1200., 1. / 3500.)).b - .5) * 500. * uWarp;
      float nL = texture(uNoise, wq * vec3(1. / 2200., 1. / 700., 1. / 2200.) + drift).r;
      float n = nL;
      if (uOct > 1) { float wD = 1. - smoothstep(120., 500., dt); n = mix(nL, .55 * nL + .45 * texture(uNoise, wq * vec3(1. / 520., 1. / 200., 1. / 520.) + 3.1).g, wD * .8 * uDetail); }
      if (uOct > 2) { float wF = 1. - smoothstep(40., 160., dt); n = mix(n, n * .7 + .3 * texture(uNoise, wq / 150. + 8.2).g, wF * .6 * uDetail); }
      float shape = smoothstep(.5 - uSoft, .5 + uSoft, n + .3 * d);
      float dens = d * prof * (.5 + .8 * shape);
      float sig = dens * ${BASE_EXTINCTION_PER_M.toFixed(5)} / uEX;
      float lt = exp(-d * .0105 * 2.2 / uEX * .5 * (uH * (1. - hh)) / max(uSun.y, .18) * .6 * 1.5);
      vec3 L = uAmb * (.55 + .45 * (1. - hh)) + uSunCol * lt * ph * uSunI;
      L += uMoonCol * phm * max(uMoon.y, 0.);
      L += vec3(1., .55, .22) * uCity * .02 * (1. - hh) * (.4 + .6 * d);
      float a = 1. - exp(-sig * dt);
      C += T * a * L; T *= 1. - a;
      if (T < .02) break;
    }
    gl_FragColor = vec4(C, 1. - T);
  }`;

/* ---- technique 2: voxel volume. Bake A: density per voxel for ONE data hour (uFrame is an integer here).
   Volume layer v (0..1) sits at y = ground * uFollow + v * uVH (scene units). */
const BAKE_A = `
  precision highp float; precision highp sampler3D; varying vec2 vUv;
  uniform sampler2D uTex, uHt; uniform sampler3D uNoise;
  uniform vec4 uTiles; uniform vec2 uTexel, uGo, uGs; uniform vec3 uOff;
  uniform float uNT, uFrame, uHtMax, uFollow, uVH, uTop, uDens, uSoft, uBump, uWarp, uEX, uLayer, uD, uDbg;
  uniform sampler2D uTex3; uniform float uUse3, uDz, uNz; uniform vec2 uAt, uT3;` + TILE + `
  float lay3(float k, vec2 q){ // one level of the 3D atlas; q as in tile(): x 0..1, s 0..1 from north
    float c = mod(k, uAt.x), r = floor(k / uAt.x);
    vec2 tp = clamp(q * uT3, vec2(.5), uT3 - .5); // stay half a texel inside the tile
    return texture2D(uTex3, (vec2(c * uT3.x, r * uT3.y) + tp) / (uAt * uT3)).r;
  }
  float data3(vec2 q, float zM){ // zM = metres above sea level, levels of uDz m (centre at (k + .5) * uDz)
    float f = clamp(zM / uDz - .5, 0., uNz - 1.), k0 = floor(f), k1 = min(k0 + 1., uNz - 1.);
    return mix(lay3(k0, q), lay3(k1, q), f - k0) * 255.;
  }
  mat2 rot(float a){ float c = cos(a), s = sin(a); return mat2(c, -s, s, c); }
  void main(){
    float v = (uLayer + .5) / uD;
    vec2 q = vUv;
    float gh = texture2D(uHt, q).r * uHtMax;
    float base = gh * uFollow, y = base + v * uVH;
    if (y < gh) { gl_FragColor = vec4(0.); return; }
    float px = uUse3 > .5 ? data3(q, y / uEX) : tile(uFrame, q) * 255.;
    float cov = max(clamp(sqrt(px / ${PX_REF.toFixed(1)}) * uDens, 0., 1.), uDbg);
    if (cov < .004) { gl_FragColor = vec4(0.); return; }
    // world metres; non-commensurate tile sizes and rotations so the 64^3 noise never repeats visibly over the grid
    vec3 w = vec3(uGo.x + q.x * uGs.x, y / uEX, uGo.y + q.y * uGs.y) - uOff;
    vec3 wq = w; wq.xz += (texture(uNoise, w * vec3(1. / 4100., 1. / 1500., 1. / 4100.)).b - .5) * 1100. * uWarp;
    vec3 w2 = wq; w2.xz = rot(.65) * w2.xz; vec3 w3 = wq; w3.xz = rot(1.24) * w3.xz;
    float n = .5 * texture(uNoise, wq * vec3(1. / 4600., 1. / 1500., 1. / 4600.)).r
            + .3 * texture(uNoise, w2 * vec3(1. / 1700., 1. / 600., 1. / 1700.) + .37).r
            + .2 * texture(uNoise, w3 * vec3(1. / 3100., 1. / 1100., 1. / 3100.) + .71).g;
    // lumpy top surface; uTop is the mean top above the base
    float tn = texture(uNoise, vec3(wq.x / 2600., .31, wq.z / 2600.)).r * .6 + texture(uNoise, vec3(w2.x / 900., .77, w2.z / 900.)).g * .4;
    float topL = uUse3 > .5 ? uVH : uTop * (1. + uBump * (tn - .5) * 1.6); // 3D data: hh runs 0..1 over the volume height, the data decides where the top is
    float hh = (y - base) / topL;
    float prof = uUse3 > .5 ? 1. : (1. - smoothstep(1. - .5 * uSoft - .05, 1., hh)) * (1. - .3 * clamp(hh, 0., 1.));
    // coverage remap: thick fog fills the slab; thin fog breaks into separate billows instead of a uniform haze (no ground bias in 3D mode so the 3D cloud floor is preserved)
    float thr = 1. - cov, e = .08 + .3 * uSoft, gBias = uUse3 > .5 ? 0. : (1. - hh) * ${GROUND_BILLOW_BIAS.toFixed(2)};
    float s = smoothstep(thr - e, thr + e, n + gBias);
    float dens = s * prof * (.35 + .65 * cov) * (.8 + .5 * (n - .5));
    gl_FragColor = vec4(clamp(dens, 0., 1.), 0., 0., 1.);
  }`;

/* Bake B: density plus the transmittance from the voxel straight up to the top of the volume (sun slant applied at render) */
const BAKE_B = `
  precision highp float; precision highp sampler3D; varying vec2 vUv;
  uniform sampler3D uA; uniform float uLayer, uD, uVH, uK;
  void main(){
    float v = (uLayer + .5) / uD, tau = 0.;
    for (int k = 0; k < 10; k++) tau += texture(uA, vec3(vUv, v + (1. - v) * (float(k) + .5) / 10.)).r;
    tau *= (1. - v) * uVH * uK / 10.;
    gl_FragColor = vec4(texture(uA, vec3(vUv, v)).r, exp(-tau), 0., 1.);
  }`;

/* Voxel render: two hour volumes A and B blended by uMixT; with advection the two are shifted along the wind so the noise drifts
   instead of cross-fading. Rays jump over columns the coarse occupancy grid marks empty. */
const VOX_FS = COMMON_U + `
  uniform sampler3D uVolA, uVolB; uniform sampler2D uOcc; uniform vec2 uOccN, uDrift; uniform float uEdge;
  uniform float uVHa, uVHb, uMixT, uK, uShadow, uSkip, uFlowOn, uFlowT, uWindScale, uDriftS;
  uniform sampler2D uWind;
#ifdef MRT
  layout(location = 1) out highp vec4 pc_depth;
#endif
  float detailNoise(vec3 q, vec2 qg){ // fine noise; with the wind field it drifts along the flow (two-phase flow noise, Neyret 2003 / Vlachos 2010)
    if (uFlowOn < .5) return texture(uNoise, (q + uOff) / ${DETAIL_NOISE_TILE_M.toFixed(1)}).g;
    vec2 wv = (texture(uWind, qg).rg * 255. - 128.) * uWindScale; vec3 vel = vec3(wv.x, 0., -wv.y);
    const float PER = ${FLOW_CYCLE_SEC.toFixed(1)};
    float c0 = floor(uFlowT / PER), c1 = floor(uFlowT / PER + .5), f0 = fract(uFlowT / PER), f1 = fract(uFlowT / PER + .5), w0 = 1. - abs(2. * f0 - 1.), w1 = 1. - w0;
    float n0 = texture(uNoise, (q - vel * f0 * PER) / ${DETAIL_NOISE_TILE_M.toFixed(1)} + fract(vec3(.7548776, .5698403, .4142136) * c0)).g;
    float n1 = texture(uNoise, (q - vel * f1 * PER) / ${DETAIL_NOISE_TILE_M.toFixed(1)} + fract(vec3(.7548776, .5698403, .4142136) * (c1 + 37.))).g;
    return .5 + (w0 * (n0 - .5) + w1 * (n1 - .5)) * inversesqrt(w0 * w0 + w1 * w1);
  }
  void main(){
#ifdef MRT
    pc_depth = vec4(1., 0., 0., 1.);
#endif
` + RAY + `
    float sy = max(uSun.y, .15);
    vec2 rxz = vec2(abs(rd.x) < 1e-6 ? 1e-6 : rd.x, abs(rd.z) < 1e-6 ? 1e-6 : rd.z);
    vec2 dA0 = -uDrift * uMixT, dB0 = uDrift * (1. - uMixT);
    float fi = 0., WD = 0.;
    for (int it = 0; it < 96; it++) {
      if (fi >= N) break;
      float u0 = (fi + jit) / N, u1 = (fi + 1. + jit) / N;
      float ta = t0 + (t1 - t0) * u0 * u0, tb = t0 + (t1 - t0) * u1 * u1, dt = tb - ta, t = .5 * (ta + tb);
      fi += 1.;
      vec3 p = ro + rd * t;
      vec2 g = (p.xz - uGo) / uGs, qg = clamp(g, 0., 1.);
      float og = length((g - qg) * uGs);
      if (og > uEdge * 1.45) continue; // well past the noisy edge fade: zero density, never sample mirrored edge texels
      vec2 cell = min(floor(g * uOccN), ceil(uOccN) - 1.);
      vec2 oc = uSkip > 0. && og == 0. ? texelFetch(uOcc, ivec2(cell), 0).rg : vec2(1.);
      float colTop = oc.y * uHtMax * uFollow + max(uVHa, uVHb); bool emp = oc.x < .5; // a column with no fog, or a ray above its fog layer
      if (emp || p.y > colTop) { // empty coarse column, or above its fog layer: jump to where the ray leaves the column
        vec2 bmin = uGo + cell / uOccN * uGs, bmax = bmin + uGs / uOccN;
        vec2 tt = (mix(bmin, bmax, step(0., rxz)) - p.xz) / rxz;
        float tE = t + max(min(tt.x, tt.y), 0.) + .5;
        if (!emp && rd.y < -1e-5) tE = min(tE, t + (p.y - colTop) / -rd.y); // above the layer only: never jump past where the ray reaches the fog top (a descending ray would skip fog in the column, giving a 320 m grid)
        if (tE >= t1) break;
        fi = max(fi, floor(sqrt((tE - t0) / (t1 - t0)) * N - jit));
        continue;
      }
      float gh = texture2D(uHt, qg).r * uHtMax;
      if (p.y < gh) continue;
      float base = gh * uFollow, va = (p.y - base) / uVHa, vb = (p.y - base) / uVHb;
      if (va < 0. || min(va, vb) > 1.) continue;
      float inside = 1. - smoothstep(0., uEdge, og * (.55 + .9 * texture(uNoise, vec3(p.x / 2300., .37, p.z / 2300.)).r)); // noisy edge: no straight line where the data ends
      if (inside < .002) continue;
      vec2 dA = clamp(dA0, -${MAX_VOLUME_SHIFT_FRAC.toFixed(2)}, ${MAX_VOLUME_SHIFT_FRAC.toFixed(2)}), dB = clamp(dB0, -${MAX_VOLUME_SHIFT_FRAC.toFixed(2)}, ${MAX_VOLUME_SHIFT_FRAC.toFixed(2)});
      if (uFlowOn > .5 && uDriftS > 0.) { vec2 wv = clamp((texture(uWind, qg).rg * 255. - 128.) * uWindScale * uDriftS / uGs, -${MAX_VOLUME_SHIFT_FRAC.toFixed(2)}, ${MAX_VOLUME_SHIFT_FRAC.toFixed(2)}); wv.y = -wv.y; dA = -wv * uMixT; dB = wv * (1. - uMixT); } // larger shapes follow the terrain-aware wind without wrapping mirrored copies across the domain
      vec2 uvA = clamp(qg + dA, vec2(.003), vec2(.997)), uvB = clamp(qg + dB, vec2(.003), vec2(.997));
      if (og > 0.) {
        vec2 wob = (texture(uNoise, vec3(p.x / 2400., .5, p.z / 2400.)).rb - .5) * .06;
        uvA = clamp(uvA + wob, vec2(.005), vec2(.995));
        uvB = clamp(uvB + wob, vec2(.005), vec2(.995));
      }
      vec2 s = va <= 1. ? texture(uVolA, vec3(uvA, va)).rg : vec2(0., 1.);
      if (uMixT > .001) s = mix(s, vb <= 1. ? texture(uVolB, vec3(uvB, vb)).rg : vec2(0., 1.), uMixT);
      float d = s.r * inside;
      if (d < .002) continue;
      // fine erosion close to the camera only (fades out where steps get long, so it cannot alias into streaks)
      float wD = (1. - smoothstep(50., 300., dt)) * uDetail;
      if (wD > .01) { float e = detailNoise(vec3(p.x, p.y / uEX * 2.5, p.z), qg); d *= mix(1., .35 + 1.3 * e, wD * .7); }
      float tr = mix(1., s.g, uShadow);
      float sh = pow(tr, 1. / sy);
      vec3 L = uAmb * (.4 + .6 * sqrt(tr)) + uSunCol * (sh + .2 * sqrt(sh)) * ph * uSunI;
      L += uMoonCol * phm * max(uMoon.y, 0.) * sqrt(tr);
      L += vec3(1., .55, .22) * uCity * .02 * (1. - va) * (.4 + .6 * d);
      float a = 1. - exp(-d * uK * dt);
      C += T * a * L; WD += T * a * t; T *= 1. - a;
      if (T < .02) break;
    }
    gl_FragColor = vec4(C, 1. - T);
#ifdef MRT
    { float fa = 1. - T, td = fa > .02 ? clamp(WD / fa / ${MAX_RAY_DIST_M.toFixed(1)}, 0., .9) : 1., sc = td * 255.; pc_depth = vec4(floor(sc) / 255., fract(sc), 0., 1.); } // opacity-weighted fog depth, packed in 16 bits
#endif
  }`;

/* running average for still-frame refinement */
/* 1-in-16 temporal resolve: the pixel traced this frame takes the new value; the other 15 reproject last frame's fog with the camera
   matrices (at the scene depth, or part-way into the fog layer for sky pixels) and are clamped to the new low-res neighbourhood to limit ghosting */
const TAA_FS = `precision highp float; varying vec2 vUv;
  layout(location = 1) out highp vec4 pc_depth;
  uniform sampler2D uSmall, uSmallD, uHist, uHistD, uDepthR; uniform mat4 uInvProjR, uPrevVP; uniform mat3 uCamRotR; uniform vec3 uCamR;
  uniform float uNearR, uFarR, uYmaxR, uHasD; uniform vec2 uSubR, uFullR, uSmallN;
  // Catmull-Rom history read (9 bilinear taps -> 5): plain bilinear softens the fog a little more every frame it is reused
  vec4 histCR(vec2 uv){
    vec2 sz = uFullR, pos = uv * sz, c = floor(pos - .5) + .5, f = pos - c;
    vec2 w0 = f * (-.5 + f * (1. - .5 * f)), w1 = 1. + f * f * (-2.5 + 1.5 * f), w2 = f * (.5 + f * (2. - 1.5 * f)), w3 = f * f * (-.5 + .5 * f);
    vec2 wm = w1 + w2, o = w2 / wm, p0 = (c - 1.) / sz, p3 = (c + 2.) / sz, pm = (c + o) / sz;
    vec4 r = texture2D(uHist, vec2(pm.x, p0.y)) * wm.x * w0.y + texture2D(uHist, vec2(p0.x, pm.y)) * w0.x * wm.y + texture2D(uHist, pm) * wm.x * wm.y
           + texture2D(uHist, vec2(p3.x, pm.y)) * w3.x * wm.y + texture2D(uHist, vec2(pm.x, p3.y)) * wm.x * w3.y;
    return max(r, 0.);
  }
  float unpackD(vec4 d){ return d.r + d.g / 255.; }
  void main(){
    vec2 px = floor(vUv * uFullR), blk = floor(px / 4.), inb = px - blk * 4.;
    vec2 suv = (blk + .5) / uSmallN;
    vec4 cur = texture2D(uSmall, suv);
    if (inb == uSubR) { gl_FragColor = cur; pc_depth = texture2D(uSmallD, suv); return; }
    // neighbourhood statistics from bilinear taps around this pixel's own position (no 4x4 block grid in the clamp box)
    vec4 m1 = vec4(0.), m2 = vec4(0.), mn = vec4(1e3), mx = vec4(-1e3);
    for (int j = -1; j <= 1; j++) for (int i = -1; i <= 1; i++) { vec4 q = texture2D(uSmall, vUv + vec2(float(i), float(j)) * .75 / uSmallN); m1 += q; m2 += q * q; mn = min(mn, q); mx = max(mx, q); }
    m1 /= 9.; vec4 sig = sqrt(max(m2 / 9. - m1 * m1, 0.)) * 1.5 + .02;
    vec4 lo = max(mn, m1 - sig) - .015, hi = min(mx, m1 + sig) + .015; // variance clip, never wider than the plain min/max box
    float dpx = texture2D(uDepthR, vUv).r;
    vec4 v = uInvProjR * vec4(vUv * 2. - 1., 1., 1.); vec3 dv = normalize(v.xyz / v.w), rd = normalize(uCamRotR * dv);
    float t = 1e9, tf = -1.;
    if (uHasD > .5) { float dd = unpackD(texture2D(uHistD, vUv)); if (dd < .98) tf = dd * ${MAX_RAY_DIST_M.toFixed(1)}; } // last frame's fog depth at this pixel
    if (dpx < .99999) { float vz = (uNearR * uFarR) / ((uFarR - uNearR) * dpx - uFarR); t = -vz / -dv.z; }
    if (tf > 0.) t = min(t, tf);
    else if (abs(rd.y) > 1e-4) { float a = -uCamR.y / rd.y, b = (uYmaxR - uCamR.y) / rd.y, t0 = max(min(a, b), 0.), t1 = min(max(a, b), t0 + ${MAX_RAY_DIST_M.toFixed(1)}); if (t1 > t0) t = min(t, t0 + .35 * (t1 - t0)); }
    t = min(t, ${MAX_RAY_DIST_M.toFixed(1)});
    vec4 c = uPrevVP * vec4(uCamR + rd * t, 1.);
    vec2 puv = c.xy / c.w * .5 + .5;
    if (c.w <= 0. || any(lessThan(puv, vec2(0.))) || any(greaterThan(puv, vec2(1.)))) { gl_FragColor = texture2D(uSmall, vUv); pc_depth = texture2D(uSmallD, vUv); return; }
    gl_FragColor = clamp(histCR(puv), lo, hi);
    pc_depth = uHasD > .5 ? texture2D(uHistD, puv) : vec4(1., 0., 0., 1.);
  }`;

const ACC_FS = `precision highp float; varying vec2 vUv; uniform sampler2D uPrev, uNew; uniform float uW;
  void main(){ gl_FragColor = mix(texture2D(uPrev, vUv), texture2D(uNew, vUv), uW); }`;

const COMP_FS = `
  precision highp float; varying vec2 vUv; uniform sampler2D uScene, uFog, uDepthC; uniform vec2 uFogTexel; uniform float uNearC, uFarC, uBlur;
  float lin(vec2 uv){ float d = texture2D(uDepthC, uv).r; return -(uNearC * uFarC) / ((uFarC - uNearC) * d - uFarC); }
  void main(){
    vec4 s = texture2D(uScene, vUv), f = texture2D(uFog, vUv);
    if (uBlur > 0.) { // depth-aware 3x3 blur of the fog buffer: smooths the dither grain without smearing fog across building edges
      float dc = lin(vUv); vec4 acc = f; float ws = 1.;
      for (int j = -1; j <= 1; j++) for (int i = -1; i <= 1; i++) {
        if (i == 0 && j == 0) continue;
        vec2 uv2 = vUv + vec2(float(i), float(j)) * uFogTexel * 1.25;
        float w = exp(-abs(lin(uv2) - dc) / (dc * .04 + 1.)) * (i == 0 || j == 0 ? .8 : .55) * uBlur;
        acc += texture2D(uFog, uv2) * w; ws += w;
      }
      f = acc / ws;
    }
    gl_FragColor = vec4(s.rgb * (1. - f.a) + f.rgb, 1.);
    gl_FragColor = linearToOutputTexel(gl_FragColor);
  }`;

/* tileable 3D value noise, 64^3 RGBA8: R = low frequency fbm, G = detail fbm, B = independent low-frequency field for the warp. Built once (~100 ms). */
function makeNoise3D(THREE) {
  const N = 64, data = new Uint8Array(N * N * N * 4);
  let seed = 1337; const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
  const lat = (f) => { const a = new Float32Array(f * f * f); for (let i = 0; i < a.length; i++) a[i] = rnd(); return a; };
  const sm = (t) => t * t * t * (t * (t * 6 - 15) + 10);
  function vnoise(a, f, x, y, z) { // periodic in [0,1)
    const px = x * f, py = y * f, pz = z * f, ix = Math.floor(px), iy = Math.floor(py), iz = Math.floor(pz), fx = sm(px - ix), fy = sm(py - iy), fz = sm(pz - iz);
    const g = (i, j, k) => a[(((k % f) + f) % f * f + ((j % f) + f) % f) * f + (((i % f) + f) % f)];
    const l = (a0, b0, t) => a0 + (b0 - a0) * t;
    return l(l(l(g(ix, iy, iz), g(ix + 1, iy, iz), fx), l(g(ix, iy + 1, iz), g(ix + 1, iy + 1, iz), fx), fy), l(l(g(ix, iy, iz + 1), g(ix + 1, iy, iz + 1), fx), l(g(ix, iy + 1, iz + 1), g(ix + 1, iy + 1, iz + 1), fx), fy), fz);
  }
  const layers = [[[2, 4, 8], [.55, .3, .15]], [[8, 16, 32], [.55, .3, .15]], [[2, 3, 6], [.6, .3, .1]]].map(([fs, ws]) => ({ fs, ws, l: fs.map(lat) }));
  for (let z = 0; z < N; z++) for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    const o = ((z * N + y) * N + x) * 4;
    layers.forEach((L, c) => { let v = 0; L.fs.forEach((f, k) => { v += L.ws[k] * vnoise(L.l[k], f, x / N, y / N, z / N); }); data[o + c] = Math.max(0, Math.min(255, Math.round((v - .5) * 2.2 * 255 + 128))); });
    data[o + 3] = 255;
  }
  const t = new THREE.Data3DTexture(data, N, N, N); t.format = THREE.RGBAFormat; t.type = THREE.UnsignedByteType;
  t.minFilter = t.magFilter = THREE.LinearFilter; t.wrapS = t.wrapT = t.wrapR = THREE.RepeatWrapping; t.generateMipmaps = false; t.needsUpdate = true;
  return t;
}

/* fog top (m) at fractional hour h from the hourly series: clamp each hour, then a gaussian blend over +-smooth hours (continuous in h) */
export function topAt(series, h, P) {
  if (P.topSrc === 'fixed') return P.topFixed;
  if (!series || !series.length) return 200;
  const n = series.length, val = (i) => { const x = series[Math.max(0, Math.min(n - 1, i))]; return x == null ? 200 : Math.min(P.topMax, Math.max(P.topMin, x)); };
  if (!(P.smooth > 0)) return val(Math.round(h));
  const sg = P.smooth / 2, R = Math.ceil(P.smooth * 1.5); let ws = 0, acc = 0;
  for (let i = Math.floor(h) - R; i <= Math.ceil(h) + R; i++) { const w = Math.exp(-((i - h) ** 2) / (2 * sg * sg)); ws += w; acc += w * val(i); }
  return acc / ws;
}

// @sec: createFogVol controller
/// Create the volumetric fog controller (march + 3D voxel bake + TAA + occupancy skipping).
export function createFogVol({ THREE, scene, EX, isTouch, getMeta, getTerrainH, toast, tier0, params }) {
  const P = { ...DEFAULTS, ...(params || {}) };
  const noiseTex = makeNoise3D(THREE);
  const sheets = createSheets({ THREE, scene, EX, isTouch, getMeta, getTerrainH, toast });
  const quadGeo = new THREE.BufferGeometry(); quadGeo.setAttribute('position', new THREE.Float32BufferAttribute([-1, -1, 0, 3, -1, 0, -1, 3, 0], 3));
  const ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const V2 = () => new THREE.Vector2(), V3 = () => new THREE.Vector3();
  const U = {
    uDepth: { value: null }, uTex: { value: null }, uHt: { value: null },
    uInvProj: { value: new THREE.Matrix4() }, uCamRot: { value: new THREE.Matrix3() }, uCam: { value: V3() },
    uNear: { value: 8 }, uFar: { value: 90000 }, uTime: { value: 0 }, uDens: { value: 1 }, uH: { value: 300 * EX }, uYmax: { value: 900 }, uHtMax: { value: 1 },
    uFrame: { value: 0 }, uNT: { value: 1 }, uJit: { value: 0 }, uTiles: { value: new THREE.Vector4() }, uTexel: { value: V2() },
    uGo: { value: V2() }, uGs: { value: new THREE.Vector2(1, 1) }, uOff: { value: V3() },
    uSun: { value: new THREE.Vector3(0, 1, 0) }, uSunCol: { value: V3() }, uMoon: { value: new THREE.Vector3(0, -1, 0) }, uAmb: { value: new THREE.Vector3(.3, .36, .5) }, uMoonCol: { value: V3() },
    uSubOn: { value: 0 }, uSub: { value: V2() }, uFull: { value: V2() },
    uDbg: { value: new URLSearchParams(location.search).has('fogdbg') ? 0.6 : 0 }, uNoise: { value: noiseTex }, uSunI: { value: 0 }, uCity: { value: 0 }, uEX: { value: EX },
    uSteps: { value: 8 }, uOct: { value: 2 }, uFollow: { value: 1 }, uSoft: { value: .35 }, uDetail: { value: 1 }, uWarp: { value: 1 }, uDither: { value: 1 },
    uEdge: { value: 3000 }, uVolA: { value: null }, uVolB: { value: null }, uVHa: { value: 300 }, uVHb: { value: 300 }, uMixT: { value: 0 }, uDrift: { value: V2() },
    uWind: { value: null }, uWindScale: { value: 0.1 }, uDriftS: { value: 0 }, uFlowOn: { value: 0 }, uFlowT: { value: 0 },
    uOcc: { value: null }, uOccN: { value: new THREE.Vector2(1, 1) }, uSkip: { value: 1 }, uK: { value: .0231 }, uShadow: { value: 1 },
  };
  const quad = (fs, uni, defs) => { const m = new THREE.ShaderMaterial({ uniforms: uni, vertexShader: VERT, fragmentShader: fs, depthTest: false, depthWrite: false, defines: defs }); const s = new THREE.Scene(), me = new THREE.Mesh(quadGeo, m); me.frustumCulled = false; s.add(me); return s; };
  const marchScene = quad(FOG_FS, U), voxScene = quad(VOX_FS, U), voxMRTScene = quad(VOX_FS, U, { MRT: 1 });
  const CU = { uScene: { value: null }, uFog: { value: null }, uDepthC: { value: null }, uFogTexel: { value: V2() }, uNearC: { value: 8 }, uFarC: { value: 90000 }, uBlur: { value: 1 } };
  const compScene = quad(COMP_FS, CU);
  const AU = { uPrev: { value: null }, uNew: { value: null }, uW: { value: 1 } };
  const TU = { uSmall: { value: null }, uSmallD: { value: null }, uHist: { value: null }, uHistD: { value: null }, uHasD: { value: 0 }, uDepthR: { value: null }, uInvProjR: U.uInvProj, uPrevVP: { value: new THREE.Matrix4() }, uCamRotR: U.uCamRot, uCamR: U.uCam,
    uNearR: U.uNear, uFarR: U.uFar, uYmaxR: U.uYmax, uSubR: { value: V2() }, uFullR: { value: V2() }, uSmallN: { value: V2() } };
  const taaScene = quad(TAA_FS, TU);
  const curVP = new THREE.Matrix4(), lastVP = new THREE.Matrix4();
  const BAYER = [0, 10, 2, 8, 5, 15, 7, 13, 1, 11, 3, 9, 4, 14, 6, 12].map((k) => [k & 3, k >> 2]); // 4x4 visiting order, spread out
  const accScene = quad(ACC_FS, AU);
  // voxel bake passes (uniform objects shared with U where the value is the same for every hour)
  const BA = { uTex: { value: null }, uHt: U.uHt, uNoise: U.uNoise, uTiles: { value: new THREE.Vector4() }, uTexel: { value: V2() }, uGo: U.uGo, uGs: U.uGs, uOff: { value: V3() }, uNT: U.uNT, uFrame: { value: 0 }, uHtMax: U.uHtMax,
    uFollow: U.uFollow, uVH: { value: 300 }, uTop: { value: 200 }, uDens: U.uDens, uSoft: U.uSoft, uBump: { value: .25 }, uWarp: U.uWarp, uEX: U.uEX, uLayer: { value: 0 }, uD: { value: 24 }, uDbg: U.uDbg,
    uTex3: { value: null }, uUse3: { value: 0 }, uDz: { value: 25 }, uNz: { value: 24 }, uAt: { value: V2() }, uT3: { value: new THREE.Vector2(214, 203) } };
  const BB = { uA: { value: null }, uLayer: BA.uLayer, uD: BA.uD, uVH: BA.uVH, uK: { value: .0231 } };
  const bakeAScene = quad(BAKE_A, BA), bakeBScene = quad(BAKE_B, BB);

  let tier = isTouch ? 2 : 3, want = true, ready = false, last = null, lastNow = 0, tex = null, htTex = null, grid = null, lastL = null, wx = null, topM = 200;
  const qs = new URLSearchParams(location.search); if (qs.has('tier')) tier = Math.max(0, Math.min(3, +qs.get('tier') || 0));
  const baseTier = tier || 3;
  const dts = []; let sceneRT = null, fogRT = null, motionRT = null, smallRT = null, histRT = [null, null], histCur = 0, lastFogTex = null, lastFogDepth = null, nextFogDepth = null, taaN = 0, accRT = [null, null], rtW = 0, rtH = 0, rtMs = -1, rtScale = -1;
  const sz = new THREE.Vector2();

  // ---- voxel state: hour volume cache, occupancy, refinement
  const cache = []; // { k, rt, ver, vh, used }
  let scratch = null, volKey = '', bakeVer = 0, bakeMs = 0, bakes = 0, frameNo = 0;
  let spritePx = null, spriteW = 0, occTex = null, occKey = '', htU8 = null, windMax = 0;
  let accN = 0, accCur = 0, lastKey = '', lastMoveT = -1e9, lastFullT = 0, phase = 'off', lastR = null, lastScn = null, lastCam = null;
  const camEls = new Float64Array(32);
  let stepMin = 60, groundM = null, windTex = null, windKey = '';
  /* terrain-aware wind for data hour k (fogflow.solveWind, ~20 ms on the CPU, only when the quantized wind or top actually changes) */
  function updateWind(hk) {
    if (!P.flow || !grid || !groundM || !wx || !wx.windMs) { U.uFlowOn.value = 0; return; }
    const fi = Math.max(0, Math.min(wx.windMs.length - 1, hk * 60 / stepMin)), i0 = Math.floor(fi), i1 = Math.min(wx.windMs.length - 1, i0 + 1), u = fi - i0;
    const sp0 = wx.windMs[i0], sp1 = wx.windMs[i1], sp = sp0 != null && sp1 != null ? sp0 * (1 - u) + sp1 * u : (sp0 ?? sp1);
    const dir = wx.windDir[Math.round(fi)];
    const qTop = Math.round(topForHour(hk) / 50) * 50, qSp = sp != null ? Math.round(sp * 2) / 2 : null, qDir = dir != null ? Math.round(dir / 10) * 10 : null;
    const key = [qSp, qDir, qTop, P.eddy].join();
    if (key !== windKey && qSp != null && qDir != null) {
      windKey = key;
      const r = solveWind({ w: grid.w, h: grid.h, cellM: grid.cell_m, ground: groundM, topM: qTop, speed: qSp, dirFrom: qDir, eddy: P.eddy, seed: 1 });
      if (!windTex) { windTex = new THREE.DataTexture(new Uint8Array(grid.w * grid.h * 2), grid.w, grid.h, THREE.RGFormat, THREE.UnsignedByteType); windTex.minFilter = windTex.magFilter = THREE.LinearFilter; windTex.flipY = false; }
      windTex.image.data.set(r.rg); windTex.needsUpdate = true; U.uWind.value = windTex; U.uWindScale.value = r.scale; windMs = r.ms;
      let m = 0; for (let q = 0; q < r.rg.length; q++) { const a = Math.abs(r.rg[q] - 128); if (a > m) m = a; } windMax = m * r.scale * 1.42; occKey = '';
    }
    U.uFlowOn.value = windTex ? 1 : 0;
    U.uFlowT.value = U.uFrame.value * (grid.frame_min || 60) * 60 * P.flowSpeed; // simulation seconds, scaled
  }
  let windMs = 0;

  const scaleNow = () => (P.res > 0 ? P.res : TIERS[tier].scale);
  const vres = () => P.vres || (isTouch ? 1 : 2), vlayers = () => P.vlayers || (isTouch ? 16 : 24);
  const rtOpts = { minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, depthBuffer: false };
  const mrt = (w, h) => { const r = new THREE.WebGLMultipleRenderTargets(w, h, 2, rtOpts); r.texture[1].minFilter = r.texture[1].magFilter = THREE.NearestFilter; return r; }; // [0] colour + opacity, [1] packed fog depth (nearest: packed values must not blend)
  function disposeRT() {
    if (sceneRT) { sceneRT.dispose(); sceneRT.depthTexture && sceneRT.depthTexture.dispose(); }
    [fogRT, motionRT, accRT[0], accRT[1], smallRT, histRT[0], histRT[1]].forEach((r) => r && r.dispose());
    sceneRT = fogRT = motionRT = smallRT = null; accRT = [null, null]; histRT = [null, null]; accN = 0; lastFogTex = null; lastFogDepth = null;
  }
  let msOver = -1; // app.js (beta, Settings > Performance): -1 = tier default, otherwise the sample count wanted for the scene target
  const msNow = () => (msOver >= 0 ? msOver : TIERS[tier].ms);
  function ensureRT(renderer) {
    const T = TIERS[tier], sc = scaleNow(); renderer.getDrawingBufferSize(sz);
    const w = Math.max(2, sz.x | 0), h = Math.max(2, sz.y | 0);
    if (sceneRT && w === rtW && h === rtH && msNow() === rtMs && sc === rtScale) return;
    disposeRT(); rtW = w; rtH = h; rtMs = msNow(); rtScale = sc;
    const dt = new THREE.DepthTexture(w, h); dt.format = THREE.DepthFormat; dt.type = THREE.UnsignedIntType;
    sceneRT = new THREE.WebGLRenderTarget(w, h, { samples: msNow(), depthTexture: dt, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, type: THREE.UnsignedByteType });
    const fw = Math.max(2, Math.round(w * sc)), fh = Math.max(2, Math.round(h * sc));
    fogRT = new THREE.WebGLRenderTarget(fw, fh, rtOpts);
    motionRT = new THREE.WebGLRenderTarget(Math.max(2, fw >> 1), Math.max(2, fh >> 1), rtOpts);
    accRT = [new THREE.WebGLRenderTarget(fw, fh, rtOpts), new THREE.WebGLRenderTarget(fw, fh, rtOpts)];
    smallRT = mrt(Math.ceil(fw / 4), Math.ceil(fh / 4));
    histRT = [mrt(fw, fh), mrt(fw, fh)];
    U.uDepth.value = sceneRT.depthTexture; CU.uScene.value = sceneRT.texture; CU.uDepthC.value = sceneRT.depthTexture;
  }

  /* ---- hour volumes */
  function ensureVolStore() {
    if (!grid) return false;
    const w = grid.w * vres(), h = grid.h * vres(), d = vlayers(), key = w + 'x' + h + 'x' + d;
    if (scratch && key === volKey) return true;
    cache.forEach((e) => e.rt.dispose()); cache.length = 0; if (scratch) scratch.dispose();
    const o = { depthBuffer: false, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, type: THREE.UnsignedByteType, format: THREE.RedFormat };
    scratch = new THREE.WebGL3DRenderTarget(w, h, d, o); setupVolTex(scratch.texture, THREE.RedFormat);
    volKey = key; BA.uD.value = d; BB.uA.value = scratch.texture;
    return true;
  }
  function setupVolTex(t, f) { t.format = f; t.type = THREE.UnsignedByteType; t.minFilter = t.magFilter = THREE.LinearFilter; t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping; t.wrapR = THREE.ClampToEdgeWrapping; t.generateMipmaps = false; }
  function newVolRT() {
    const [w, h, d] = volKey.split('x').map(Number);
    const r = new THREE.WebGL3DRenderTarget(w, h, d, { depthBuffer: false, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, type: THREE.UnsignedByteType, format: THREE.RGFormat });
    setupVolTex(r.texture, THREE.RGFormat); return r;
  }
  const hoursOf = (k) => k * (grid ? grid.frame_min || 60 : 60) / 60; // data hour k -> hours since the start
  function topForHour(hk) { // hk = hours since the start of the fog data
    if (P.topSrc === 'fixed') return P.topFixed;
    if (wx && wx.series) return topAt(wx.series, hk * 60 / stepMin, P);
    if (wx && wx.top != null) return Math.min(P.topMax, Math.max(P.topMin, wx.top));
    return 200;
  }
  function windOffset(hours, out) { // metres the noise has drifted with the mean wind after `hours`
    if (wx && wx.wind && P.advect > 0) out.set(wx.wind[0], 0, wx.wind[1]).multiplyScalar(hours * 3600 * P.advect); else out.set(0, 0, 0);
    return out;
  }
  /* ---- 3D fog data (guide 04): index.json fog3d atlases, decoded on demand, 8 frames kept */
  let d3Canvas = null, d3Ctx = null;
  const d3 = { info: null, base: '', sfx: '', keep: [], pending: new Set(), missing: new Set(),
    zero: null, cache: createLru(8, (e) => { e.dead = true; if (e.tex && !e.empty) e.tex.dispose(); }) };
  const d3On = () => !!(P.data3d && d3.info && grid && usable(d3.info, grid));
  function d3Fail(id) { d3.pending.delete(id); d3.missing.add(id); }
  const d3Url = (kf) => (kf.src === 'f' ? tv.dir : d3.base) + kf.vol + (kf.src === 'f' ? '' : d3.sfx); // fog5 file names carry a content hash, no suffix needed
  function d3Load(kf) { // starts decoding key frame kf; uses one reusable scratch canvas and a 4x4 subsampled colMax scan so onload takes <1 ms on the CPU
    const I = d3.info; if (!kf || !I || d3.cache.has(kf.id) || d3.pending.has(kf.id) || d3.missing.has(kf.id)) return;
    if (kf.src === 'f' && kf.vol && !tvInfoOk()) { d3.missing.add(kf.id); return; }
    if (!kf.vol) {
      if (kf.src === 'f' && !kf.empty) { d3.missing.add(kf.id); return; }
      // no file for this hour or 5-minute frame = the data says no fog: an explicit empty entry (never the 2D fallback)
      if (!d3.zero) { d3.zero = new THREE.DataTexture(new Uint8Array([0]), 1, 1, THREE.RedFormat, THREE.UnsignedByteType); d3.zero.needsUpdate = true; }
      d3.cache.set(kf.id, emptyEntry(kf.id, I, d3.zero), d3.keep); return;
    }
    const id = kf.id, top = kf.top != null ? kf.top : I.nz - 1;
    d3.pending.add(id); const info = I, img = new Image();
    img.onload = () => {
      if (d3.info !== info) { d3.pending.delete(id); return; } // the data changed while loading
      try {
        const W = info.cols * info.w, H = info.rows * info.h; if (img.width !== W || img.height !== H) throw new Error('atlas size');
        if (!d3Canvas || d3Canvas.width !== W || d3Canvas.height !== H) {
          d3Canvas = document.createElement('canvas'); d3Canvas.width = W; d3Canvas.height = H;
          d3Ctx = d3Canvas.getContext('2d', { willReadFrequently: true });
        }
        d3Ctx.drawImage(img, 0, 0);
        const rgba = d3Ctx.getImageData(0, 0, W, H).data, px = new Uint8Array(W * H);
        for (let i = 0, j = 0; i < px.length; i++, j += 4) px[i] = rgba[j];
        const tex = new THREE.DataTexture(px, W, H, THREE.RedFormat, THREE.UnsignedByteType);
        tex.minFilter = tex.magFilter = THREE.LinearFilter; tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping; tex.generateMipmaps = false; tex.flipY = false; tex.needsUpdate = true;
        // Fast 320m cell-level colMax: only needs to know if any column in each 4x4 cell has fog (stride 2 scan is 4x faster and dilated by r>=1 cell in updateOcc)
        const w = info.w, h = info.h, nz = info.nz, cols = info.cols, cm = new Uint8Array(w * h);
        for (let k = 0; k <= top && k < nz; k++) {
          const ox = (k % cols) * w, oy = ((k / cols) | 0) * h;
          for (let y = 0; y < h; y += 2) {
            const src = (oy + y) * W + ox, dst = y * w;
            for (let x = 0; x < w; x += 2) { if (px[src + x] >= 1) cm[dst + x] = 255; }
          }
        }
        d3.cache.set(id, { k: id, tex, colMax: cm, topLayer: top }, d3.keep);
        d3.pending.delete(id);
      } catch (e) { d3Fail(id); }
    };
    img.onerror = () => d3Fail(id);
    img.src = d3Url(kf);
  }
  function d3State(kf) { // { e } loaded, { loading } coming, {} none for this key frame (no file, failed, or off)
    if (!d3.info) return {};
    const e = d3.cache.get(kf.id); if (e) return { e };
    d3Load(kf); return d3.pending.has(kf.id) ? { loading: true } : {};
  }
  /* ---- 5-minute fog frames (guide 08 part B, lab switch travel): fog5/index.json -> a key frame line (fogtravel.js); the hourly tile sheets of fog5 decode on demand, 3 kept */
  const tv = { src: null, dir: '', parsed: null, lines: new Map(), lineSig: '', fetching: false, poll: null, shown: null, bad: false, stride: 1, hps: 0, lastH: null, lastT: 0, lastChange: 0, pendingIdx: null,
    sheets: createLru(3, (e) => { e.dead = true; if (e.tex) e.tex.dispose(); }), pending: new Set(), failed: new Set() };
  const tvInfoOk = () => !!(tv.parsed && d3.info && ['nz', 'dz_m', 'cols', 'rows', 'w', 'h'].every((q) => tv.parsed.info[q] === d3.info[q]));
  const tvOn = () => !!(P.travel && tv.parsed && !tv.bad && grid && (grid.frame_min || 60) === 60);
  function tvLine() { // the key frames for the current stride (hourly frames outside the window, 5-minute frames inside it)
    const n = U.uNT.value, v3 = d3On(), sig = [tv.parsed.generated, tv.parsed.frames.length, n, tv.stride, d3.info ? 1 : 0, d3.base, v3 ? 1 : 0].join('|');
    if (sig !== tv.lineSig) { tv.lines.clear(); tv.lineSig = sig; }
    let L = tv.lines.get(tv.stride);
    if (!L) { L = buildLine({ n, files: d3.info ? d3.info.files : null, top_layer: d3.info ? d3.info.top_layer : null }, tv.parsed, tv.stride, v3); tv.lines.set(tv.stride, L); }
    return L;
  }
  function tvSheet(kf) { // the decoded tile sheet of a fog5 frame, or null while it loads
    const e = tv.sheets.get(kf.tile); if (e) return e;
    if (tv.pending.has(kf.tile) || tv.failed.has(kf.tile)) return null;
    tv.pending.add(kf.tile); const name = kf.tile, img = new Image(), pr = tv.parsed;
    img.onload = () => {
      tv.pending.delete(name); if (tv.parsed !== pr && !(tv.parsed && tv.parsed.frames.some((f) => f.tile === name))) return;
      try {
        const g = pr.info, W = img.width, H = img.height; if (W % g.w || H % g.h || W < g.w || H < g.h) throw new Error('sheet size');
        const c = document.createElement('canvas'); c.width = W; c.height = H;
        const cx = c.getContext('2d', { willReadFrequently: true }); cx.drawImage(img, 0, 0);
        const rgba = cx.getImageData(0, 0, W, H).data, px = new Uint8Array(W * H);
        for (let i = 0, j = 0; i < px.length; i++, j += 4) px[i] = rgba[j];
        const tex = new THREE.DataTexture(px, W, H, THREE.RedFormat, THREE.UnsignedByteType);
        tex.minFilter = tex.magFilter = THREE.LinearFilter; tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping; tex.generateMipmaps = false; tex.flipY = false; tex.needsUpdate = true;
        tv.sheets.set(name, { tex, px, W, H, cols: W / g.w, rows: H / g.h }, tv.keepSheets);
      } catch (e) { tv.failed.add(name); tv.bad = true; bakeVer++; } // a bad sheet: back to the hourly data for good (until a newer index)
    };
    img.onerror = () => { tv.pending.delete(name); tv.failed.add(name); tv.bad = true; bakeVer++; };
    img.src = tv.dir + name; return null;
  }
  function tvApply(parsed) { // make an index current
    tv.parsed = parsed; tv.lines.clear(); tv.lineSig = ''; tv.failed.clear(); tv.bad = false; tv.shown = null; bakeVer++; occKey = ''; lastVsig = null;
  }
  async function tvFetch() { // read fog5/index.json (fixed name: never cached); a newer one replaces the current one at a moment the frames on screen do not change
    if (!tv.src || tv.fetching) return; tv.fetching = true;
    try {
      const r = await fetch(tv.src.url, { cache: 'no-store' }); if (!r.ok) throw new Error('http ' + r.status);
      const parsed = parseIndex(await r.json(), tv.src.t0ms);
      if (!parsed) { if (!tv.parsed) tv.bad = true; return; }
      if (!isNewer(tv.parsed, parsed)) return;
      if (!tv.parsed) tvApply(parsed); else tv.pendingIdx = parsed; // swapped in prepVoxel once the shown frames would not change
    } catch (e) { if (!tv.parsed) tv.bad = true; } finally { tv.fetching = false; }
  }
  function tvSync() { // lab switch or source changed: fetch now, and every 5 minutes while on
    const want5 = !!(P.travel && tv.src);
    if (want5 && !tv.parsed && !tv.bad && !tv.fetching) tvFetch();
    if (want5 && !tv.poll) tv.poll = setInterval(tvFetch, 300000);
    if (!want5 && tv.poll) { clearInterval(tv.poll); tv.poll = null; }
  }
  const hourlyKf = (k) => ({ id: k, hk: hoursOf(k), src: 'h', k, tile: null, slot: -1, vol: d3.info && d3.info.files ? d3.info.files[k] || null : null, top: d3.info && d3.info.top_layer && d3.info.top_layer[k] != null ? d3.info.top_layer[k] : null, kind: 'observed' });
  let lastVsig = null;
  function bakeHour(renderer, e, kf, d3e) {
    const t0 = performance.now(); let top = topForHour(kf.hk) * EX, vh = top * (1 + P.bump * .8) + 4; const D = vlayers();
    if (d3e) {
      // Smooth the 3D volume ceiling with topForHour(kf.hk) so integer 25m steps in d3e.topLayer never rescale the 24 voxel layers abruptly between 5-min frames
      const d3Vh = volumeHeight(d3e.topLayer, d3.info, EX);
      vh = kf.src === 'f' ? Math.max(d3Vh, Math.min((d3.info.nz + 1) * d3.info.dz_m * EX, top + 50 * EX)) : d3Vh;
      top = vh; BA.uTex3.value = d3e.tex; BA.uUse3.value = 1;
    } else BA.uUse3.value = 0;
    const sh = kf.src === 'f' && !d3e ? tv.sheets.get(kf.tile) : null; // 3D data: the 2D tile is not read
    if (sh) { BA.uTex.value = sh.tex; BA.uTiles.value.set(sh.cols, sh.rows, grid.w, grid.h); BA.uTexel.value.set(1 / sh.W, 1 / sh.H); BA.uFrame.value = kf.slot; }
    else { BA.uTex.value = U.uTex.value; BA.uTiles.value.copy(U.uTiles.value); BA.uTexel.value.copy(U.uTexel.value); BA.uFrame.value = kf.k; }
    // For 5-minute travel frames, keep uOff anchored to the rounded hour so VOX_FS's terrain-aware wind drift (dA, dB) smoothly carries the billows without fighting a per-5-min night-average uOff shift
    BA.uTop.value = top; BA.uVH.value = vh; windOffset(kf.src === 'f' ? Math.round(kf.hk) : kf.hk, BA.uOff.value);
    for (let l = 0; l < D; l++) { BA.uLayer.value = l; renderer.setRenderTarget(scratch, l); renderer.render(bakeAScene, ortho); }
    for (let l = 0; l < D; l++) { BA.uLayer.value = l; renderer.setRenderTarget(e.rt, l); renderer.render(bakeBScene, ortho); }
    e.k = kf.id; e.hk = kf.hk; e.kf = kf; e.ver = bakeVer; e.vh = vh; e.stamp = ++bakes; e.src3 = d3e ? 1 : 0; e.d3 = d3e || null;
    bakeMs = performance.now() - t0; // CPU-side submit time (the GPU work is asynchronous); bench() measures it synchronously
  }
  function getVol(renderer, kf, budget, keep, d3e) {
    let e = cache.find((c) => c.k === kf.id);
    if (e && e.ver === bakeVer && e.src3 === (d3e ? 1 : 0)) { e.used = frameNo; return e; }
    if (budget.n <= 0) return e || null; // stale (or nothing): reuse what there is, bake next frame
    if (kf.src === 'f' && !d3e && !tvSheet(kf)) return e || null; // the 2D tile sheet of this 5-minute frame is still loading
    if (!e) {
      if (cache.length < 3) { e = { k: kf.id, rt: newVolRT(), ver: -1 }; cache.push(e); }
      else { e = cache.filter((c) => !keep.includes(c.k)).sort((a, b) => a.used - b.used)[0]; }
    }
    budget.n--; bakeHour(renderer, e, kf, d3e); e.used = frameNo; return e;
  }

  /* ---- coarse occupancy (320 m columns) from the sprite pixels, for hours kA and kB, dilated so drift and filtering never cut fog off */
  function readSprite(img) {
    try {
      const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
      const cx = c.getContext('2d', { willReadFrequently: true }); cx.drawImage(img, 0, 0);
      const d = cx.getImageData(0, 0, img.width, img.height).data, out = new Uint8Array(img.width * img.height);
      for (let i = 0, j = 0; i < out.length; i++, j += 4) out[i] = d[j];
      spritePx = out; spriteW = img.width;
    } catch (e) { spritePx = null; } // unreadable: no skipping
  }
  function updateOcc(kfA, kfB, d3A, d3B, span) { // span = hours between the two key frames (the drift between them)
    if (!grid) return;
    const kA = kfA.id, kB = kfB.id, ow = Math.ceil(grid.w / OCC_CELLS), oh = Math.ceil(grid.h / OCC_CELLS);
    const drift = P.advect > 0 && wx && wx.wind ? Math.hypot(wx.wind[0], wx.wind[1]) * 3600 * span * P.advect : 0;
    const fdrift = P.flow && U.uFlowOn.value ? windMax * 3600 * span * P.advect : 0;
    const r = 1 + Math.ceil(Math.max(drift, fdrift) / (OCC_CELLS * grid.cell_m));
    const sh = (kf) => (kf.src === 'f' ? tv.sheets.get(kf.tile) : null), shA = sh(kfA), shB = sh(kfB);
    const key = [kA, kB, ow, oh, r, U.uDbg.value > 0, !!spritePx, U.uDens.value > 0, d3A ? 1 : 0, !!shA, !!shB].join(',');
    if (key === occKey && occTex) return; occKey = key;
    const raw = new Uint8Array(ow * oh);
    if (d3A && U.uDbg.value <= 0) { // 3D data: a cell is occupied when any of its columns has fog at any level
      for (const cm of [d3A.colMax, d3B.colMax]) for (let y = 0; y < grid.h; y++) { const oy = Math.floor(y / OCC_CELLS) * ow; for (let x = 0; x < grid.w; x++) if (cm[y * grid.w + x] >= 1) raw[oy + Math.floor(x / OCC_CELLS)] = 255; }
    } else if (U.uDbg.value > 0) raw.fill(255);
    else for (const kf of [kfA, kfB]) {
      const fiv = kf.src === 'f', S = fiv ? sh(kf) : null, px = fiv ? (S && S.px) : spritePx, W = fiv ? (S && S.W) : spriteW, cols = fiv ? (S && S.cols) : grid.cols, k = fiv ? kf.slot : kf.k;
      if (!px) { raw.fill(255); continue; } // unreadable: no skipping
      const tr = Math.floor(k / cols), tc = k - tr * cols, x0 = tc * grid.w, y0 = tr * grid.h;
      for (let y = 0; y < grid.h; y++) {
        const row = (y0 + y) * W + x0, oy = Math.floor(y / OCC_CELLS) * ow;
        for (let x = 0; x < grid.w; x++) if (px[row + x] > 0) raw[oy + Math.floor(x / OCC_CELLS)] = 255;
      }
    }
    // G = highest ground in the (dilated) cell, so rays also skip the clear air above the fog layer of that column
    const gmax = new Uint8Array(ow * oh);
    if (htU8) for (let y = 0; y < grid.h; y++) for (let x = 0; x < grid.w; x++) { const o = Math.floor(y / OCC_CELLS) * ow + Math.floor(x / OCC_CELLS); if (htU8[y * grid.w + x] > gmax[o]) gmax[o] = htU8[y * grid.w + x]; }
    const occ = new Uint8Array(ow * oh * 2);
    for (let y = 0; y < oh; y++) for (let x = 0; x < ow; x++) {
      let m = 0, g = 0;
      for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
        const yy = y + dy, xx = x + dx; if (yy < 0 || yy >= oh || xx < 0 || xx >= ow) continue;
        if (raw[yy * ow + xx]) m = 255; if (gmax[yy * ow + xx] > g) g = gmax[yy * ow + xx];
      }
      occ[(y * ow + x) * 2] = m; occ[(y * ow + x) * 2 + 1] = htU8 ? Math.min(255, g + 1) : 255;
    }
    if (occTex && (occTex.image.width !== ow || occTex.image.height !== oh)) { occTex.dispose(); occTex = null; }
    if (!occTex) { occTex = new THREE.DataTexture(occ, ow, oh, THREE.RGFormat, THREE.UnsignedByteType); occTex.minFilter = occTex.magFilter = THREE.NearestFilter; occTex.flipY = false; }
    else occTex.image.data.set(occ);
    occTex.needsUpdate = true; U.uOcc.value = occTex;
    U.uOccN.value.set(grid.w / OCC_CELLS, grid.h / OCC_CELLS); // the grid measured in occupancy cells (the last cell may be partial)
  }

  /* everything that depends on settings, time or weather */
  function applyTop() {
    const h = wx ? wx.at : 0; topM = topAt(wx && wx.series, h, P);
    if (P.topSrc !== 'fixed' && wx && !wx.series && wx.top != null) topM = Math.min(P.topMax, Math.max(P.topMin, wx.top));
    const top = topM * EX, mx = U.uHtMax.value;
    U.uH.value = top;
    if (P.mode !== 'voxel') U.uYmax.value = mx * P.follow + top + 2;
    windOffset(U.uFrame.value * (grid ? grid.frame_min || 60 : 60) / 60, U.uOff.value);
  }
  let lastBakeSig = '';
  function applyParams() {
    U.uFollow.value = P.follow; U.uSoft.value = P.soft; U.uDetail.value = P.detail; U.uWarp.value = P.warp; U.uDither.value = P.dither;
    U.uEdge.value = P.edgeFade; BA.uBump.value = P.bump; U.uShadow.value = P.shadow; U.uSkip.value = P.skip ? 1 : 0;
    const K = BASE_EXTINCTION_PER_M / EX; U.uK.value = K; BB.uK.value = K * SHADOW_EXTINCTION_RATIO; // shadow uses a lower extinction: forward scattering lets more sun through
    if (P.mode === 'sheets') { if (tier !== 0) setTier(0, 'lab'); }
    else if (tier === 0) setTier(baseTier, 'lab');
    const bSig = [P.mode, P.follow, P.soft, P.bump, P.warp, P.advect, P.topSrc, P.topFixed, P.topMin, P.topMax, P.smooth, P.vres, P.vlayers, P.data3d, P.travel].join('|');
    if (bSig !== lastBakeSig) { lastBakeSig = bSig; bakeVer++; occKey = ''; }
    applyTop(); if (lastL) setLight(lastL); tvSync();
  }
  let lightSig = '';
  function setLight(L) {
    if (!L) return; lastL = L;
    const lin = (a) => a.map((x) => Math.pow(Math.max(x, 0), 2.2));
    const hor = lin(L.hor), zen = lin(L.zen), fl = [.22, .28, .42].map((x) => x * P.night); // night floor close to the daytime fog colour so night fog stays visible
    U.uAmb.value.set(...hor.map((h, i) => Math.max(h * .65 + zen[i] * .35, fl[i]) * 1.3));
    U.uSun.value.set(...L.sunDir); U.uSunCol.value.set(...L.sunCol); U.uSunI.value = (Math.max(L.sunDir[1], 0) > 0 ? 1.1 : 0) * P.sun;
    U.uMoon.value.set(...L.moonDir); U.uMoonCol.value.set(.35, .42, .6).multiplyScalar(.28 * L.illum * P.night);
    U.uCity.value = Math.min(1, Math.max(0, (-L.sunEl - 4) / 10));
    lightSig = [U.uAmb.value.toArray(), U.uSun.value.toArray(), U.uSunCol.value.toArray(), U.uSunI.value, U.uMoon.value.toArray(), U.uMoonCol.value.toArray(), U.uCity.value].join();
  }

  function setTier(n, why) {
    n = Math.max(0, Math.min(3, n)); if (n === tier) return; tier = n;
    if (n === 0) { disposeRT(); if (last && !sheets.ready) sheets.build(last.png, last.g).then(() => sheets.setVisible(want)); else sheets.setVisible(want); }
    else sheets.setVisible(false);
    if (tier0) tier0(n, why);
  }

  function build(png, g) {
    last = { png, g }; ready = false; dts.length = 0;
    return new Promise((resolve) => {
      new THREE.TextureLoader().load(png, (t) => {
        t.minFilter = t.magFilter = THREE.LinearFilter; t.generateMipmaps = false; t.flipY = false; t.colorSpace = THREE.NoColorSpace; t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
        if (tex) tex.dispose(); tex = t; readSprite(t.image);
        const meta = getMeta(), terrainH = getTerrainH(), wm = g.w * g.cell_m, hm = g.h * g.cell_m, ox = g.x0 - meta.x0, oz = -(g.y0 - meta.y0);
        // ground height texture (scene units, 1 texel per fog cell), 8 bit: height / uHtMax
        const w = g.w, h = g.h, raw = new Float32Array(w * h); let mx = 1;
        for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) { // row 0 = north (z = oz - hm), same as the sprite
          const x = ox + (c + .5) / w * wm, z = oz - hm + (r + .5) / h * hm, v = terrainH ? Math.max(terrainH.at(x, z), 0) : 0; raw[r * w + c] = v; if (v > mx) mx = v;
        }
        const u8 = new Uint8Array(w * h); for (let k = 0; k < u8.length; k++) u8[k] = Math.round(raw[k] / mx * 255);
        if (htTex) htTex.dispose(); htTex = new THREE.DataTexture(u8, w, h, THREE.RedFormat, THREE.UnsignedByteType); htTex.minFilter = htTex.magFilter = THREE.LinearFilter; htTex.needsUpdate = true; htTex.flipY = false;
        htU8 = u8; U.uHt.value = htTex; U.uHtMax.value = mx; U.uTex.value = t; grid = g;
        groundM = new Float32Array(raw.length); for (let k = 0; k < raw.length; k++) groundM[k] = raw[k] / EX; windKey = '';
        U.uTiles.value.set(g.cols, g.rows, g.w, g.h); U.uNT.value = g.cols * g.rows; U.uTexel.value.set(1 / t.image.width, 1 / t.image.height);
        U.uGo.value.set(ox, oz - hm); U.uGs.value.set(wm, hm);
        applyParams();
        ready = true; if (tier === 0) sheets.build(png, g); resolve(true);
      }, undefined, () => { toast('Fog layer unavailable'); resolve(false); });
    });
  }

  function camMoved(camera) {
    const a = camera.matrixWorld.elements, b = camera.projectionMatrix.elements; let moved = false;
    for (let i = 0; i < 16; i++) {
      if (Math.abs(camEls[i] - a[i]) > 1e-4 * (1 + Math.abs(a[i]))) moved = true; camEls[i] = a[i];
      if (Math.abs(camEls[16 + i] - b[i]) > 1e-6 * (1 + Math.abs(b[i]))) moved = true; camEls[16 + i] = b[i];
    }
    return moved;
  }
  function setCamUniforms(camera) {
    U.uInvProj.value.copy(camera.projectionMatrixInverse); U.uCamRot.value.setFromMatrix4(camera.matrixWorld); U.uCam.value.copy(camera.position);
    U.uNear.value = CU.uNearC.value = camera.near; U.uFar.value = CU.uFarC.value = camera.far;
  }
  function stepsFor(vox) { const T = TIERS[tier]; return Math.min(48, P.steps > 0 ? P.steps : (vox ? T.vsteps : T.steps)); }
  function fogPass(renderer, target, steps, jit) {
    U.uSteps.value = steps; U.uJit.value = jit;
    renderer.setRenderTarget(target); renderer.render(P.mode === 'voxel' ? (target.isWebGLMultipleRenderTargets ? voxMRTScene : voxScene) : marchScene, ortho);
  }
  /* pick and (if needed) bake the hour volumes for the shown time; sets the voxel uniforms; returns a state signature */
  function prepVoxel(renderer) {
    if (!ensureVolStore()) return null;
    frameNo++;
    const f = Math.max(0, Math.min(U.uNT.value - 1, U.uFrame.value)), now = performance.now();
    const trav = tvOn();
    let kfA, kfB, mixT, next;
    if (trav) { // 5-minute frames inside the fog5 window, hourly outside; one blend between two key frames always
      const hnow = hoursOf(f);
      if (tv.lastH !== null && hnow !== tv.lastH) { const dt = Math.max(0.001, (now - tv.lastT) / 1000); tv.hps = tv.hps * 0.7 + 0.3 * (Math.abs(hnow - tv.lastH) / dt); tv.lastChange = now; }
      if (now - tv.lastChange > 500) tv.hps = 0;
      tv.lastH = hnow; tv.lastT = now;
      const want = strideFor(tv.hps, tv.parsed.stepMin); // every 5-minute frame, or every third while playing faster than one frame per 0.5 s (with a little hysteresis)
      if (want !== tv.stride && (want === 3 ? strideFor(tv.hps * 1.2, tv.parsed.stepMin) === 3 : strideFor(tv.hps * 0.8, tv.parsed.stepMin) === 1)) tv.stride = want;
      if (tv.pendingIdx) { // a newer index: swap when the two frames on screen are the same in it (hashed names), so nothing visibly changes
        const old = tv.stride, np = tv.pendingIdx, L2 = buildLine({ n: U.uNT.value, files: d3.info ? d3.info.files : null, top_layer: d3.info ? d3.info.top_layer : null }, np, old, d3On());
        if (swapIsInvisible(tv.shown, L2, hnow)) { tv.pendingIdx = null; tvApply(np); }
      }
      const L = tvLine(), br = bracket(L, hnow, P.travelEase || 0);
      kfA = br.a; kfB = br.b; mixT = kfB !== kfA ? br.mix : 0; next = L[Math.min(L.length - 1, br.i + 2)];
      tv.shown = { a: kfA, b: kfB };
      tv.keepSheets = [kfA.tile, kfB.tile, next && next.tile];
      if (next && next.src === 'f' && !next.vol) tvSheet(next); // prefetch the next sheet
    } else {
      const kA = Math.floor(f), kB = Math.min(kA + 1, U.uNT.value - 1);
      mixT = kB > kA ? f - kA : 0; kfA = hourlyKf(kA); kfB = hourlyKf(kB); next = hourlyKf(kB + 1);
    }
    const budget = { n: cache.some((c) => c.ver === bakeVer) ? 1 : 2 }; // bake at most one volume per frame once something is on screen
    // 3D data (guide 04): both key frames of a blend come from 3D files or both from the 2D tile; while a file is still loading the previous volume stays up
    let d3A = null, d3B = null;
    if (d3On()) {
      d3.keep = [kfA.id, kfB.id];
      const sA = d3State(kfA), sB = mixT > .001 ? d3State(kfB) : sA; if (next && next.vol) d3Load(next);
      if (sA.e && sB.e) { d3A = sA.e; d3B = sB.e; }
      else if ((sA.loading || sB.loading) && lastVsig && cache.some((c) => c.ver === bakeVer)) return lastVsig;
    }
    const keep = [kfA.id, kfB.id];
    let eA = getVol(renderer, kfA, budget, keep, d3A), eB = mixT > .001 ? getVol(renderer, kfB, budget, keep, d3B) : null;
    if (!eA && eB) { eA = eB; mixT = 0; }
    if (!eA) return null;
    if (!eB) { eB = eA; if (eA.k !== kfB.id) mixT = 0; }
    if (d3A && eA.src3 !== eB.src3 && lastVsig) return lastVsig;   // never blend a 3D-baked hour with a 2D-baked one: keep the previous picture until the other hour is baked (next frame)
    U.uVolA.value = eA.rt.texture; U.uVolB.value = eB.rt.texture; U.uVHa.value = eA.vh; U.uVHb.value = eB.vh; U.uMixT.value = mixT;
    U.uYmax.value = U.uHtMax.value * P.follow + Math.max(eA.vh, eB.vh) + 2;
    const span = eB.hk > eA.hk ? eB.hk - eA.hk : (trav && eA.kf && eA.kf.src === 'f' ? tv.parsed.stepMin / 60 : (grid ? (grid.frame_min || 60) / 60 : 1)); // hours between the two volumes: the drift between them is the wind over that time
    const ds = grid ? 3600 * span * P.advect : 0; U.uDriftS.value = ds;
    if (P.advect > 0 && wx && wx.wind && grid) U.uDrift.value.set(wx.wind[0] * ds / U.uGs.value.x, wx.wind[1] * ds / U.uGs.value.y);
    else U.uDrift.value.set(0, 0);
    updateWind(eA.hk); updateOcc(eA.kf, eB.kf, d3A && d3B && eA.src3 && eB.src3 ? d3A : null, d3A && d3B && eA.src3 && eB.src3 ? d3B : null, span); // 3D occupancy only when both volumes are 3D
    return (lastVsig = `${U.uFlowOn.value}:${U.uFlowT.value.toFixed(2)}|${eA.k}:${eA.stamp}|${eB.k}:${eB.stamp}|${mixT.toFixed(4)}`);
  }

  /* guide 08 B.4: at the real wind speed the fine detail moves more than a pixel per frame once playback is fast, and reprojecting the 1-in-16 history would ghost it.
     More than 0.5 m of drift per rendered frame (60 per second) at the current playback rate: no history this frame (the half-resolution path has none). Real time x1 is 0.08 m. */
  function fastFlow() {
    if (!P.flow || !U.uFlowOn.value || !wx || !wx.windMs) return false;
    const hps = tv.hps || 0, sp = wx.windMs[Math.max(0, Math.min(wx.windMs.length - 1, Math.round(U.uFrame.value * (grid ? grid.frame_min || 60 : 60) / stepMin)))] || 0;
    return sp * P.flowSpeed * hps * 3600 / 60 > 0.5;
  }
  function renderFrame(renderer, scn, camera, plain) { // plain (optional): how app.js wants the scene drawn when the fog pass is not running (beta: through its own multisampled target)
    const draw = plain || (() => renderer.render(scn, camera));
    lastR = renderer; lastScn = scn; lastCam = camera;
    if (tier === 0 || !ready || !want) { phase = 'off'; draw(); return; }
    const vox = P.mode === 'voxel', prev = renderer.getRenderTarget(), now = performance.now();
    ensureRT(renderer);
    if (!tvOn()) { const hnow = hoursOf(U.uFrame.value); if (tv.lastH !== null && hnow !== tv.lastH) { const dt = Math.max(0.001, (now - tv.lastT) / 1000); tv.hps = tv.hps * 0.7 + 0.3 * (Math.abs(hnow - tv.lastH) / dt); tv.lastChange = now; } if (now - tv.lastChange > 500) tv.hps = 0; tv.lastH = hnow; tv.lastT = now; }
    const vsig = vox ? prepVoxel(renderer) : '';
    if (vox && vsig === null) { renderer.setRenderTarget(prev); draw(); phase = 'waiting for data'; return; }
    setCamUniforms(camera);
    renderer.setRenderTarget(sceneRT); renderer.render(scn, camera);
    if (!vox) { // procedural march: unchanged pipeline (its noise drifts with wall time, so every frame is new)
      fogPass(renderer, fogRT, stepsFor(false), 0); CU.uFog.value = fogRT.texture; CU.uBlur.value = qs.get('blur') === '0' ? 0 : P.blur;
      CU.uFogTexel.value.set(1 / fogRT.width, 1 / fogRT.height); phase = 'march';
    } else {
      if (camMoved(camera)) { lastMoveT = now; accN = 0; }
      const key = [vsig, U.uDens.value, lightSig, U.uDbg.value, rtW, rtH, rtScale, U.uOff.value.x, U.uOff.value.z, bakeVer].join('|');
      if (key !== lastKey) { lastKey = key; accN = 0; }
      if (now - lastFullT > STILL_REFRESH_MS && accN >= P.accum) accN = 0; // refresh now and then (catches scene changes the fog cannot see)
      const blur0 = qs.get('blur') === '0' ? 0 : P.blur;
      curVP.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
      if (P.motion && now - lastMoveT < MOTION_LINGER_MS && P.taa && lastFogTex && !fastFlow()) { // camera moving: trace 1 pixel in 16 at full quality, reproject the rest
        const [ox, oy] = BAYER[taaN++ & 15];
        U.uSubOn.value = 1; U.uSub.value.set(ox, oy); U.uFull.value.set(fogRT.width, fogRT.height);
        fogPass(renderer, smallRT, stepsFor(true), 0); U.uSubOn.value = 0;
        let dst = histRT[histCur ^ 1]; if (dst.texture[0] === lastFogTex) { histCur ^= 1; dst = histRT[histCur ^ 1]; }
        TU.uSmall.value = smallRT.texture[0]; TU.uSmallD.value = smallRT.texture[1]; TU.uHist.value = lastFogTex; TU.uHistD.value = lastFogDepth; TU.uHasD.value = lastFogDepth ? 1 : 0; TU.uDepthR.value = sceneRT.depthTexture; TU.uPrevVP.value.copy(lastVP);
        TU.uSubR.value.set(ox, oy); TU.uFullR.value.set(fogRT.width, fogRT.height); TU.uSmallN.value.set(smallRT.width, smallRT.height);
        renderer.setRenderTarget(dst); renderer.render(taaScene, ortho); histCur ^= 1;
        CU.uFog.value = dst.texture[0]; nextFogDepth = dst.texture[1]; CU.uFogTexel.value.set(1 / fogRT.width, 1 / fogRT.height); CU.uBlur.value = blur0 * .5; accN = 0; phase = 'moving (1 in 16)';
      } else if (P.motion && now - lastMoveT < MOTION_LINGER_MS) { // camera moving: half resolution, fewer steps, no refinement
        fogPass(renderer, motionRT, Math.max(6, Math.round(stepsFor(true) * P.motionSteps)), 0);
        CU.uFog.value = motionRT.texture; CU.uFogTexel.value.set(1 / motionRT.width, 1 / motionRT.height); CU.uBlur.value = blur0; accN = 0; phase = 'moving';
      } else if (P.accum > 0) {
        if (accN < P.accum) { // one more jittered full-quality pass, averaged in
          fogPass(renderer, fogRT, stepsFor(true), accN === 0 ? 0 : (accN * 0.6180339887) % 1);
          const dst = accRT[accCur ^ 1]; AU.uPrev.value = accRT[accCur].texture; AU.uNew.value = fogRT.texture; AU.uW.value = 1 / (accN + 1);
          renderer.setRenderTarget(dst); renderer.render(accScene, ortho); accCur ^= 1; accN++; lastFullT = now; phase = 'refining';
        } else phase = 'still (fog reused)';
        CU.uFog.value = accRT[accCur].texture; CU.uFogTexel.value.set(1 / fogRT.width, 1 / fogRT.height); CU.uBlur.value = blur0 * Math.max(0, 1 - (accN - 1) / 4);
      } else {
        fogPass(renderer, fogRT, stepsFor(true), 0); CU.uFog.value = fogRT.texture; CU.uFogTexel.value.set(1 / fogRT.width, 1 / fogRT.height); CU.uBlur.value = blur0; phase = 'every frame';
      }
    }
    if (vox) { lastFogTex = CU.uFog.value; lastFogDepth = nextFogDepth; nextFogDepth = null; lastVP.copy(curVP); }
    renderer.setRenderTarget(prev); renderer.render(compScene, ortho);
  }

  /* lab benchmark: median ms of the fog pass alone (GPU-synchronised with a 1-pixel read), plus a synchronised bake of one hour */
  function bench(n = 5, opts = {}) {
    const r = lastR; if (!r || !fogRT || tier === 0) return null;
    const gl = r.getContext(), px = new Uint8Array(4), vox = P.mode === 'voxel', prev = r.getRenderTarget();
    if (lastCam) setCamUniforms(lastCam);
    if (vox) prepVoxel(r);
    const taa = opts.motion && P.taa && vox, steps = opts.motion && !taa ? Math.max(6, Math.round(stepsFor(vox) * P.motionSteps)) : stepsFor(vox), target = taa ? smallRT : opts.motion ? motionRT : fogRT;
    if (taa) { U.uSubOn.value = 1; U.uSub.value.set(1, 2); U.uFull.value.set(fogRT.width, fogRT.height); }
    const times = [];
    fogPass(r, target, steps, 0); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px); // warm-up
    for (let i = 0; i < n; i++) { const t0 = performance.now(); fogPass(r, target, steps, 0); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px); times.push(performance.now() - t0); }
    let bake = null;
    if (vox && opts.bake !== false && cache.length) {
      const e = cache[0], t0 = performance.now(); bakeHour(r, e, e.kf, e.d3 && !e.d3.dead ? e.d3 : null); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px); bake = performance.now() - t0;
    }
    r.setRenderTarget(prev); accN = 0; U.uSubOn.value = 0;
    times.sort((a, b) => a - b);
    return { ms: times[times.length >> 1], min: times[0], steps, res: scaleNow() * (opts.motion ? .5 : 1), mode: P.mode, bakeMs: bake, size: [target.width, target.height], info: info() };
  }

  const stats = () => { if (!dts.length) return { med: 0, p95: 0 }; const s = [...dts].sort((a, b) => a - b); return { med: s[s.length >> 1], p95: s[Math.min(s.length - 1, Math.floor(s.length * .95))] }; };
  function info() {
    if (!tier) return 'fog tier 0 (sheets)';
    const vox = P.mode === 'voxel', st = stepsFor(vox);
    if (!vox) return `fog march tier ${tier} (${st} steps, ${Math.round(scaleNow() * 100)}% res) top ${Math.round(topM)} m`;
    const [w, h, d] = (volKey || '0x0x0').split('x');
    return `fog voxel${tvOn() ? ' + 5-min frames' : ''} tier ${tier} (${st} steps, ${Math.round(scaleNow() * 100)}% res, ${w}x${h}x${d} voxels) top ${Math.round(topM)} m\n${phase}${phase === 'refining' ? ' ' + accN + '/' + P.accum : ''}; ${bakes} hour bakes, last ${bakeMs.toFixed(1)} ms submit${U.uFlowOn.value ? `; wind field ${windMs.toFixed(0)} ms` : ''}`;
  }

  return {
    build, renderFrame, bench, info,
    /* hooks for render-on-demand and the Performance settings (app.js): busy = the fog still changes without any input */
    busy() { if (!want || !ready) return false; if (tier === 0) return true; return phase !== 'still (fog reused)'; },
    setMsaa(n) { msOver = n; },
    clear() { ready = false; sheets.clear(); },
    get ready() { return ready || sheets.ready; },
    get tier() { return tier; },
    setTier,
    setVisible(v) { want = v; if (tier === 0) sheets.setVisible(v); },
    setDensity(v) { if (v !== U.uDens.value) { U.uDens.value = v; bakeVer++; occKey = ''; } sheets.setDensity(v); },
    setFrame(f) { if (f !== U.uFrame.value) { U.uFrame.value = f; applyTop(); } sheets.setFrame(f); },
    /* weather for the shown time: at = fractional index into the weather series, series = fog_top_m, step_min = weather step,
       wind_ms/wind_dir = hourly arrays (dir = from, degrees). { top } alone keeps the old behaviour. */
    setWeather(w) {
      if (!w) return;
      if (w.step_min) stepMin = w.step_min;
      const nw = { windMs: w.wind_ms || null, windDir: w.wind_dir || null, top: w.top, at: w.at != null ? w.at : 0, series: w.series || null, windSrc: w.wind_ms || null, wind: wx && wx.windSrc && wx.windSrc === w.wind_ms ? wx.wind : null };
      if (!nw.wind && w.wind_ms && w.wind_dir) { // mean wind vector (towards) in scene axes: x east, z = -north
        let ex = 0, ez = 0, n = 0; w.wind_ms.forEach((s, i) => { const d = w.wind_dir[i]; if (s == null || d == null) return; const r = d * Math.PI / 180; ex += -Math.sin(r) * s; ez += Math.cos(r) * s; n++; });
        nw.wind = n ? [ex / n, ez / n] : null;
      }
      const seriesChanged = !wx || wx.series !== nw.series || wx.windSrc !== nw.windSrc;
      wx = nw; if (seriesChanged) bakeVer++; applyTop();
    },
    /* guide 04: the index.json "fog3d" entry (or null), the folder of the files and the cache-busting suffix; live nights only (replay nights call it with null) */
    setData3d(info, base, sfx) {
      d3.info = info || null; d3.base = base || ''; d3.sfx = sfx || ''; d3.pending.clear(); d3.missing.clear(); lastVsig = null;
      for (const k of d3.cache.keys()) { const e = d3.cache.get(k); e.dead = true; if (e.tex && !e.empty) e.tex.dispose(); }
      d3.cache = createLru(8, (e) => { e.dead = true; if (e.tex && !e.empty) e.tex.dispose(); });
      if (info) { BA.uDz.value = info.dz_m; BA.uNz.value = info.nz; BA.uAt.value.set(info.cols, info.rows); BA.uT3.value.set(info.w, info.h); }
      bakeVer++; occKey = '';
    },
    get data3d() { return d3On(); },
    /* guide 08 B: src = { url: fog5/index.json, t0ms: time of data hour 0 } or null; used when the lab switch travel is on (live nights, beta) */
    setTravel(src) {
      if (tv.poll) { clearInterval(tv.poll); tv.poll = null; }
      tv.src = src ? { url: src.url, t0ms: src.t0ms } : null; tv.dir = src ? src.url.replace(/[^/]*$/, '') : '';
      tv.parsed = null; tv.pendingIdx = null; tv.bad = false; tv.lines.clear(); tv.lineSig = ''; tv.shown = null; tv.failed.clear(); tv.pending.clear();
      for (const k of tv.sheets.keys()) { const e = tv.sheets.get(k); e.dead = true; if (e.tex) e.tex.dispose(); }
      tv.sheets = createLru(3, (e) => { e.dead = true; if (e.tex) e.tex.dispose(); });
      lastVsig = null; bakeVer++; occKey = ''; tvSync();
    },
    get travel() { return tvOn(); },
    /* the kind (observed | interpolated | forecast) of the 5-minute fog frame at app time f (data hours), or null outside the 5-minute window or with the switch off */
    travelKind(f) { if (!tvOn()) return null; return kindAt(tvLine(), hoursOf(f)); },
    setLight,
    get params() { return { ...P }; },
    setParams(patch) { Object.assign(P, patch || {}); applyParams(); },
    get topM() { return topM; },
    stats,
    update(camera, dist, tSec) {
      U.uTime.value = tSec; sheets.update(camera, dist, tSec);
      const now = performance.now(); if (lastNow) { dts.push(now - lastNow); if (dts.length > 60) dts.shift(); } lastNow = now;
      if (P.auto && dts.length === 60 && tier > 0) { // governor: one notch down after a slow window, never back up in the same session
        const med = stats().med;
        if (med > 22) { dts.length = 0; setTier(tier - 1, med.toFixed(0) + ' ms median'); }
      }
    },
  };
}
