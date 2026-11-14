/* Volumetric fog (beta, ?beta=1). Two techniques behind one interface, picked by params.mode:
   - 'march': procedural ray march through a fog slab (what beta shows today).
   - 'voxel': the fog is baked into a 3D texture over the whole fog grid (density in R, light reaching each voxel from above in G),
     then the screen pass samples that volume. The baked noise covers the whole grid without tiling, self-shadowing is free, and the
     volume is rebaked only when time, density or a setting changes (a few ms on the GPU), so a paused view never moves.
   Same public interface as fog.js (build, clear, ready, setVisible, setDensity, setFrame, update) plus renderFrame() which app.js calls instead of
   renderer.render, setParams()/params for the fog lab (?beta=1&lab=1, foglab.js), and setWeather() for the fog top series.
   Pipeline per frame: scene -> render target with depth texture; fog pass at 50-100% resolution reading that depth; composite to the canvas.
   A frame-time governor steps down tiers 3 -> 2 -> 1 -> 0; tier 0 is the old sheets (fog.js). */
import { createFog as createSheets } from './fog.js';

const PX_REF = 16; // sprite value that maps to full density after the sqrt stretch (same as fog.js)
const TIERS = [null, // 0 = old sheets
  { scale: 0.5, steps: 5, vsteps: 10, oct: 1, ms: 0 },
  { scale: 0.5, steps: 10, vsteps: 18, oct: 2, ms: 0 },
  { scale: 1.0, steps: 12, vsteps: 26, oct: 3, ms: 4 }];

/* Settings. DEFAULTS is what beta shows (unchanged from the last beta build); foglab.js offers presets and edits them live. */
export const DEFAULTS = {
  mode: 'march',   // 'march' | 'voxel' | 'sheets'
  follow: 1,       // fog top: 0 = flat inversion at a fixed altitude (hills poke out), 1 = constant depth above the ground
  topSrc: 'data',  // 'data' = hourly fog_top_m, 'fixed' = topFixed
  topFixed: 200,   // m
  topMin: 80, topMax: 300, // m; fog_top_m is often a 62 m floor or a 900 m cap value, not a real top
  smooth: 0,       // h; 0 = the rounded hour (old, jumps at the half hour), >0 = gaussian blend over neighbouring hours
  soft: 0.35,      // top and edge softness 0..1
  bump: 0.25,      // voxel: how lumpy the top surface is 0..1
  detail: 1,       // fine noise strength 0..1
  warp: 1,         // domain warp 0..1.5 (breaks straight lines into billows)
  advect: 0,       // voxel: noise drift with the mean wind, as a fraction of the wind speed (moves with simulation time, not wall time)
  dither: 1,       // ray start jitter 0..1 (0 = banding, 1 = fine grain the blur removes)
  blur: 1,         // depth-aware blur of the fog buffer
  steps: 0,        // 0 = tier default
  res: 0,          // fog buffer scale; 0 = tier default
  vres: 2,         // voxel: horizontal voxels per fog cell (1 = 80 m, 2 = 40 m)
  vlayers: 24,     // voxel: vertical layers
  shadow: 1,       // voxel: self-shadow strength
  sun: 1,          // sunlight multiplier
  night: 1,        // night ambient floor multiplier
  auto: true,      // frame-time governor
};

const VERT = `varying vec2 vUv; void main(){ vUv = position.xy * .5 + .5; gl_Position = vec4(position.xy, 0., 1.); }`;

const TILE = `
  float tile(float k, vec2 q){ // q = (x 0..1, s 0..1 from north); same atlas layout as fog.js
    k = clamp(k, 0., uNT - 1.); float r = floor(k / uTiles.x), c = k - r * uTiles.x;
    vec2 tp = clamp(vec2(q.x * uTiles.z, q.y * uTiles.w), vec2(.5), uTiles.zw - .5);
    return texture2D(uTex, (vec2(c * uTiles.z, r * uTiles.w) + tp) * uTexel).r;
  }`;

/* shared ray setup: view ray, scene depth, slab entry/exit between y = 0 and uYmax */
const RAY = `
    float dpx = texture2D(uDepth, vUv).r;
    vec4 v = uInvProj * vec4(vUv * 2. - 1., 1., 1.); vec3 dv = normalize(v.xyz / v.w);
    vec3 rd = normalize(uCamRot * dv), ro = uCam;
    float tScene = 1e9;
    if (dpx < .99999) { float vz = (uNear * uFar) / ((uFar - uNear) * dpx - uFar); tScene = -vz / -dv.z; }
    float t0, t1;
    if (abs(rd.y) < 1e-4) { if (ro.y < 0. || ro.y > uYmax) { gl_FragColor = vec4(0.); return; } t0 = 0.; t1 = 1e9; }
    else { float a = (0. - ro.y) / rd.y, b = (uYmax - ro.y) / rd.y; t0 = max(min(a, b), 0.); t1 = max(a, b); }
    t1 = min(min(t1, tScene), t0 + 40000.);
    if (t1 <= t0) { gl_FragColor = vec4(0.); return; }
    float N = float(uSteps);
    float jit = mix(.5, fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(.06711056, .00583715)))), uDither);
    float T = 1.; vec3 C = vec3(0.);
    float cs = dot(rd, uSun), ph = .3 + 1.7 * hg(cs, .6) * 12.566 * .35;
    float cm = dot(rd, uMoon), phm = .3 + 1.2 * hg(cm, .55) * 12.566 * .3;`;

const COMMON_U = `
  precision highp float; varying vec2 vUv;
  precision highp sampler3D;
  uniform sampler2D uDepth, uTex, uHt; uniform sampler3D uNoise;
  uniform mat4 uInvProj; uniform mat3 uCamRot; uniform vec3 uCam;
  uniform float uNear, uFar, uTime, uDens, uH, uYmax, uHtMax, uFrame, uNT;
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
      float sig = dens * .0105 / uEX * 2.2 * 1.5;
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

/* ---- technique 2: voxel volume. Bake A: density per voxel. Volume layer v (0..1) sits at y = ground * uFollow + v * uVH (scene units). */
const BAKE_A = `
  precision highp float; precision highp sampler3D; varying vec2 vUv;
  uniform sampler2D uTex, uHt; uniform sampler3D uNoise;
  uniform vec4 uTiles; uniform vec2 uTexel, uGo, uGs; uniform vec3 uOff;
  uniform float uNT, uFrame, uHtMax, uFollow, uVH, uTop, uDens, uSoft, uBump, uWarp, uEX, uLayer, uD, uDbg;` + TILE + `
  mat2 rot(float a){ float c = cos(a), s = sin(a); return mat2(c, -s, s, c); }
  void main(){
    float v = (uLayer + .5) / uD;
    vec2 q = vUv;
    float gh = texture2D(uHt, q).r * uHtMax;
    float base = gh * uFollow, y = base + v * uVH;
    if (y < gh) { gl_FragColor = vec4(0.); return; }
    float fa = floor(uFrame), ft = uFrame - fa;
    float px = mix(tile(fa, q), tile(fa + 1., q), ft) * 255.;
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
    float topL = uTop * (1. + uBump * (tn - .5) * 1.6);
    float hh = (y - base) / topL;
    float prof = (1. - smoothstep(1. - .5 * uSoft - .05, 1., hh)) * (1. - .3 * clamp(hh, 0., 1.));
    // coverage remap: thick fog fills the slab; thin fog breaks into separate billows instead of a uniform haze
    float thr = 1. - cov, e = .08 + .3 * uSoft;
    float s = smoothstep(thr - e, thr + e, n + (1. - hh) * .12);
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

const VOX_FS = COMMON_U + `
  uniform sampler3D uVol; uniform float uVH, uK, uShadow;
  void main(){` + RAY + `
    float sy = max(uSun.y, .15);
    for (int i = 0; i < 48; i++) {
      if (i >= uSteps) break;
      float u0 = (float(i) + jit) / N, u1 = (float(i) + 1. + jit) / N;
      float ta = t0 + (t1 - t0) * u0 * u0, tb = t0 + (t1 - t0) * u1 * u1, dt = tb - ta, t = .5 * (ta + tb);
      vec3 p = ro + rd * t;
      vec2 g = (p.xz - uGo) / uGs, qg = clamp(g, 0., 1.);
      float inside = 1. - smoothstep(0., 8000., length((g - qg) * uGs));
      float gh = texture2D(uHt, qg).r * uHtMax;
      if (p.y < gh) continue;
      float v = (p.y - gh * uFollow) / uVH;
      if (v < 0. || v > 1.) continue;
      vec2 gm = g; float og = length((g - qg) * uGs); // past the edge: wobble the mirror line so it does not show as a straight seam
      if (og > 0.) gm += (texture(uNoise, vec3(p.x / 1900., .5, p.z / 1900.)).rb - .5) * min(og, 1500.) / uGs * 1.4;
      vec2 s = texture(uVol, vec3(gm, v)).rg; // unclamped: the volume wraps mirrored past the grid edge, so fog over the ocean keeps its texture instead of stretching into streaks
      float d = s.r * inside;
      if (d < .002) continue;
      // fine erosion close to the camera only (fades out where steps get long, so it cannot alias into streaks)
      float wD = (1. - smoothstep(50., 300., dt)) * uDetail;
      if (wD > .01) { float e = texture(uNoise, (vec3(p.x, p.y / uEX * 2.5, p.z) + uOff) / 230.).g; d *= mix(1., .35 + 1.3 * e, wD * .7); }
      float tr = mix(1., s.g, uShadow);
      float sh = pow(tr, 1. / sy);
      vec3 L = uAmb * (.4 + .6 * sqrt(tr)) + uSunCol * (sh + .2 * sqrt(sh)) * ph * uSunI;
      L += uMoonCol * phm * max(uMoon.y, 0.) * sqrt(tr);
      L += vec3(1., .55, .22) * uCity * .02 * (1. - v) * (.4 + .6 * d);
      float a = 1. - exp(-d * uK * dt);
      C += T * a * L; T *= 1. - a;
      if (T < .02) break;
    }
    gl_FragColor = vec4(C, 1. - T);
  }`;

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
        float w = exp(-abs(lin(uv2) - dc) / (dc * .04 + 1.)) * (i == 0 || j == 0 ? .8 : .55);
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
    uFrame: { value: 0 }, uNT: { value: 1 }, uTiles: { value: new THREE.Vector4() }, uTexel: { value: V2() },
    uGo: { value: V2() }, uGs: { value: new THREE.Vector2(1, 1) }, uOff: { value: V3() },
    uSun: { value: new THREE.Vector3(0, 1, 0) }, uSunCol: { value: V3() }, uMoon: { value: new THREE.Vector3(0, -1, 0) }, uAmb: { value: new THREE.Vector3(.3, .36, .5) }, uMoonCol: { value: V3() },
    uDbg: { value: new URLSearchParams(location.search).has('fogdbg') ? 0.6 : 0 }, uNoise: { value: noiseTex }, uSunI: { value: 0 }, uCity: { value: 0 }, uEX: { value: EX },
    uSteps: { value: 8 }, uOct: { value: 2 }, uFollow: { value: 1 }, uSoft: { value: .35 }, uDetail: { value: 1 }, uWarp: { value: 1 }, uDither: { value: 1 },
    uVol: { value: null }, uVH: { value: 300 }, uK: { value: .0231 }, uShadow: { value: 1 },
  };
  const quad = (fs, uni) => { const m = new THREE.ShaderMaterial({ uniforms: uni, vertexShader: VERT, fragmentShader: fs, depthTest: false, depthWrite: false }); const s = new THREE.Scene(), me = new THREE.Mesh(quadGeo, m); me.frustumCulled = false; s.add(me); return s; };
  const marchScene = quad(FOG_FS, U), voxScene = quad(VOX_FS, U);
  const CU = { uScene: { value: null }, uFog: { value: null }, uDepthC: { value: null }, uFogTexel: { value: V2() }, uNearC: { value: 8 }, uFarC: { value: 90000 }, uBlur: { value: 1 } };
  const compScene = quad(COMP_FS, CU);
  // voxel bake passes (uniform objects shared with U where the value is the same)
  const BA = { uTex: U.uTex, uHt: U.uHt, uNoise: U.uNoise, uTiles: U.uTiles, uTexel: U.uTexel, uGo: U.uGo, uGs: U.uGs, uOff: U.uOff, uNT: U.uNT, uFrame: U.uFrame, uHtMax: U.uHtMax,
    uFollow: U.uFollow, uVH: U.uVH, uTop: { value: 200 }, uDens: U.uDens, uSoft: U.uSoft, uBump: { value: .25 }, uWarp: U.uWarp, uEX: U.uEX, uLayer: { value: 0 }, uD: { value: 24 }, uDbg: U.uDbg };
  const BB = { uA: { value: null }, uLayer: BA.uLayer, uD: BA.uD, uVH: U.uVH, uK: { value: .0231 } };
  const bakeAScene = quad(BAKE_A, BA), bakeBScene = quad(BAKE_B, BB);
  let volA = null, volB = null, volKey = '', dirty = true, lastBake = 0, bakeMs = 0;

  let tier = isTouch ? 2 : 3, want = true, ready = false, last = null, lastNow = 0, tex = null, htTex = null, grid = null, lastL = null, wx = null, topM = 200;
  const qs = new URLSearchParams(location.search); if (qs.has('tier')) tier = Math.max(0, Math.min(3, +qs.get('tier') || 0));
  const baseTier = tier || 3;
  const dts = []; let sceneRT = null, fogRT = null, rtW = 0, rtH = 0, rtMs = -1, rtScale = -1;
  const sz = new THREE.Vector2();

  const scaleNow = () => (P.res > 0 ? P.res : TIERS[tier].scale);
  function disposeRT() { if (sceneRT) { sceneRT.dispose(); sceneRT.depthTexture && sceneRT.depthTexture.dispose(); } if (fogRT) fogRT.dispose(); sceneRT = fogRT = null; }
  function ensureRT(renderer) {
    const T = TIERS[tier], sc = scaleNow(); renderer.getDrawingBufferSize(sz);
    const w = Math.max(2, sz.x | 0), h = Math.max(2, sz.y | 0);
    if (sceneRT && w === rtW && h === rtH && T.ms === rtMs && sc === rtScale) return;
    disposeRT(); rtW = w; rtH = h; rtMs = T.ms; rtScale = sc;
    const dt = new THREE.DepthTexture(w, h); dt.format = THREE.DepthFormat; dt.type = THREE.UnsignedIntType;
    sceneRT = new THREE.WebGLRenderTarget(w, h, { samples: T.ms, depthTexture: dt, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, type: THREE.UnsignedByteType });
    fogRT = new THREE.WebGLRenderTarget(Math.max(2, Math.round(w * sc)), Math.max(2, Math.round(h * sc)), { minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, depthBuffer: false });
    U.uDepth.value = sceneRT.depthTexture; CU.uScene.value = sceneRT.texture; CU.uFog.value = fogRT.texture; CU.uDepthC.value = sceneRT.depthTexture; CU.uFogTexel.value.set(1 / fogRT.width, 1 / fogRT.height);
  }

  function ensureVol() {
    if (!grid) return false;
    const w = grid.w * P.vres, h = grid.h * P.vres, d = P.vlayers, key = w + 'x' + h + 'x' + d;
    if (volA && key === volKey) return true;
    if (volA) { volA.dispose(); volB.dispose(); }
    const o = { depthBuffer: false, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, type: THREE.UnsignedByteType };
    volA = new THREE.WebGL3DRenderTarget(w, h, d, { ...o, format: THREE.RedFormat });
    volB = new THREE.WebGL3DRenderTarget(w, h, d, { ...o, format: THREE.RGFormat });
    [[volA, THREE.RedFormat], [volB, THREE.RGFormat]].forEach(([r, f]) => { const t = r.texture; t.format = f; t.type = THREE.UnsignedByteType; t.minFilter = t.magFilter = THREE.LinearFilter; t.wrapS = t.wrapT = THREE.MirroredRepeatWrapping; t.wrapR = THREE.ClampToEdgeWrapping; t.generateMipmaps = false; });
    volKey = key; BA.uD.value = d; BB.uA.value = volA.texture; U.uVol.value = volB.texture; dirty = true;
    return true;
  }
  function bake(renderer) {
    const t0 = performance.now();
    for (let l = 0; l < P.vlayers; l++) { BA.uLayer.value = l; renderer.setRenderTarget(volA, l); renderer.render(bakeAScene, ortho); }
    for (let l = 0; l < P.vlayers; l++) { BA.uLayer.value = l; renderer.setRenderTarget(volB, l); renderer.render(bakeBScene, ortho); }
    bakeMs = performance.now() - t0; dirty = false; lastBake = t0;
  }

  /* everything that depends on settings, time or weather */
  function applyTop() {
    const h = wx ? wx.at : 0; topM = topAt(wx && wx.series, h, P);
    if (P.topSrc !== 'fixed' && wx && !wx.series && wx.top != null) topM = Math.min(P.topMax, Math.max(P.topMin, wx.top));
    const top = topM * EX, mx = U.uHtMax.value;
    U.uH.value = top; BA.uTop.value = top; U.uVH.value = top * (1 + P.bump * .8) + 4;
    U.uYmax.value = mx * P.follow + (P.mode === 'voxel' ? U.uVH.value : top) + 2;
    // noise drift with the mean wind over the series, driven by simulation time so a paused view stays still
    if (wx && wx.wind && P.advect > 0) U.uOff.value.set(wx.wind[0], 0, wx.wind[1]).multiplyScalar(h * 3600 * P.advect); else U.uOff.value.set(0, 0, 0);
    dirty = true;
  }
  function applyParams() {
    U.uFollow.value = P.follow; U.uSoft.value = P.soft; U.uDetail.value = P.detail; U.uWarp.value = P.warp; U.uDither.value = P.dither;
    BA.uBump.value = P.bump; U.uShadow.value = P.shadow; CU.uBlur.value = qs.get('blur') === '0' ? 0 : P.blur;
    const K = .0105 / EX * 2.2 * 1.5; U.uK.value = K; BB.uK.value = K * .55; // shadow uses a lower extinction: forward scattering lets more sun through
    if (P.mode === 'sheets') { if (tier !== 0) setTier(0, 'lab'); }
    else if (tier === 0) setTier(baseTier, 'lab');
    applyTop(); if (lastL) setLight(lastL);
  }
  function setLight(L) {
    if (!L) return; lastL = L;
    const lin = (a) => a.map((x) => Math.pow(Math.max(x, 0), 2.2));
    const hor = lin(L.hor), zen = lin(L.zen), fl = [.22, .28, .42].map((x) => x * P.night); // night floor close to the daytime fog colour so night fog stays visible
    U.uAmb.value.set(...hor.map((h, i) => Math.max(h * .65 + zen[i] * .35, fl[i]) * 1.3));
    U.uSun.value.set(...L.sunDir); U.uSunCol.value.set(...L.sunCol); U.uSunI.value = (Math.max(L.sunDir[1], 0) > 0 ? 1.1 : 0) * P.sun;
    U.uMoon.value.set(...L.moonDir); U.uMoonCol.value.set(.35, .42, .6).multiplyScalar(.28 * L.illum * P.night);
    U.uCity.value = Math.min(1, Math.max(0, (-L.sunEl - 4) / 10));
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
        if (tex) tex.dispose(); tex = t;
        const meta = getMeta(), terrainH = getTerrainH(), wm = g.w * g.cell_m, hm = g.h * g.cell_m, ox = g.x0 - meta.x0, oz = -(g.y0 - meta.y0);
        // ground height texture (scene units, 1 texel per fog cell), 8 bit: height / uHtMax
        const w = g.w, h = g.h, raw = new Float32Array(w * h); let mx = 1;
        for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) { // row 0 = north (z = oz - hm), same as the sprite
          const x = ox + (c + .5) / w * wm, z = oz - hm + (r + .5) / h * hm, v = terrainH ? Math.max(terrainH.at(x, z), 0) : 0; raw[r * w + c] = v; if (v > mx) mx = v;
        }
        const u8 = new Uint8Array(w * h); for (let k = 0; k < u8.length; k++) u8[k] = Math.round(raw[k] / mx * 255);
        if (htTex) htTex.dispose(); htTex = new THREE.DataTexture(u8, w, h, THREE.RedFormat, THREE.UnsignedByteType); htTex.minFilter = htTex.magFilter = THREE.LinearFilter; htTex.needsUpdate = true; htTex.flipY = false;
        U.uHt.value = htTex; U.uHtMax.value = mx; U.uTex.value = t; grid = g;
        U.uTiles.value.set(g.cols, g.rows, g.w, g.h); U.uNT.value = g.cols * g.rows; U.uTexel.value.set(1 / t.image.width, 1 / t.image.height);
        U.uGo.value.set(ox, oz - hm); U.uGs.value.set(wm, hm);
        applyParams();
        ready = true; if (tier === 0) sheets.build(png, g); resolve(true);
      }, undefined, () => { toast('Fog layer unavailable'); resolve(false); });
    });
  }

  function renderFrame(renderer, scn, camera) {
    if (tier === 0 || !ready || !want) { renderer.render(scn, camera); return; }
    const T = TIERS[tier], vox = P.mode === 'voxel'; ensureRT(renderer);
    const prev = renderer.getRenderTarget();
    if (vox && ensureVol() && dirty && performance.now() - lastBake > 33) bake(renderer); // at most ~30 bakes a second while the time slider moves
    U.uInvProj.value.copy(camera.projectionMatrixInverse); U.uCamRot.value.setFromMatrix4(camera.matrixWorld); U.uCam.value.copy(camera.position);
    U.uNear.value = CU.uNearC.value = camera.near; U.uFar.value = CU.uFarC.value = camera.far;
    U.uSteps.value = Math.min(48, P.steps > 0 ? P.steps : (vox ? T.vsteps : T.steps)); U.uOct.value = T.oct;
    renderer.setRenderTarget(sceneRT); renderer.render(scn, camera);
    renderer.setRenderTarget(fogRT); renderer.render(vox ? voxScene : marchScene, ortho);
    renderer.setRenderTarget(prev); renderer.render(compScene, ortho);
  }

  const stats = () => { if (!dts.length) return { med: 0, p95: 0 }; const s = [...dts].sort((a, b) => a - b); return { med: s[s.length >> 1], p95: s[Math.min(s.length - 1, Math.floor(s.length * .95))] }; };

  return {
    build, renderFrame,
    clear() { ready = false; sheets.clear(); },
    get ready() { return ready || sheets.ready; },
    get tier() { return tier; },
    setTier,
    setVisible(v) { want = v; if (tier === 0) sheets.setVisible(v); },
    setDensity(v) { U.uDens.value = v; sheets.setDensity(v); dirty = true; },
    setFrame(f) { if (f !== U.uFrame.value) dirty = true; U.uFrame.value = f; sheets.setFrame(f); },
    /* weather for the shown time: at = fractional hour index into the hourly series, series = fog_top_m, wind_ms/wind_dir = hourly arrays (dir = from, degrees).
       { top } alone keeps the old behaviour. */
    setWeather(w) {
      if (!w) return;
      const nw = { top: w.top, at: w.at != null ? w.at : 0, series: w.series || null, windSrc: w.wind_ms || null, wind: wx && wx.windSrc && wx.windSrc === w.wind_ms ? wx.wind : null };
      if (!nw.wind && w.wind_ms && w.wind_dir) { // mean wind vector (towards) in scene axes: x east, z = -north
        let ex = 0, ez = 0, n = 0; w.wind_ms.forEach((s, i) => { const d = w.wind_dir[i]; if (s == null || d == null) return; const r = d * Math.PI / 180; ex += -Math.sin(r) * s; ez += Math.cos(r) * s; n++; });
        nw.wind = n ? [ex / n, ez / n] : null;
      }
      wx = nw; applyTop();
    },
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
    info() {
      if (!tier) return 'fog tier 0 (sheets)';
      const T = TIERS[tier], vox = P.mode === 'voxel', st = P.steps > 0 ? P.steps : (vox ? T.vsteps : T.steps);
      return `fog ${vox ? 'voxel' : 'march'} tier ${tier} (${st} steps, ${Math.round(scaleNow() * 100)}% res${vox ? `, bake ${bakeMs.toFixed(1)} ms` : ''}) top ${Math.round(topM)} m`;
    },
  };
}
