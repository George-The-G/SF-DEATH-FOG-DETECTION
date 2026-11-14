/* Volumetric fog (beta, ?beta=1&fog=vol): one ray march through a terrain-following fog slab instead of six noisy sheets.
   Same public interface as fog.js (build, clear, ready, setVisible, setDensity, setFrame, update) plus renderFrame() which app.js calls instead of renderer.render.
   Pipeline per frame: scene -> render target with depth texture; fog march at 100% or 50% resolution reading that depth (fog stops at terrain and buildings);
   composite to the canvas. A frame-time governor steps down tiers 3 -> 2 -> 1 -> 0; tier 0 is the old sheets (fog.js), so a weak device falls back to today's look.
   Plan: Claude doc "Realistic cloud and sky rendering plan", Stage 2. Density comes from the same hourly sprite as fog.js; height from the hourly fog top (clamped); ground height from terrainH. */
import { createFog as createSheets } from './fog.js';

const PX_REF = 16;          // sprite value that maps to full density after the sqrt stretch (same as fog.js)
const TOP_MIN_M = 80, TOP_MAX_M = 300; // visual slab thickness limits (m). fog_top_m is often a 62 m floor or 900 m cap value, not a real top
const TIERS = [null,        // 0 = old sheets
  { scale: 0.5, steps: 5, oct: 1, ms: 0 },
  { scale: 0.5, steps: 10, oct: 2, ms: 0 },
  { scale: 1.0, steps: 12, oct: 3, ms: 4 }];

const VERT = `varying vec2 vUv; void main(){ vUv = position.xy * .5 + .5; gl_Position = vec4(position.xy, 0., 1.); }`;

const FOG_FS = `
  precision highp float; varying vec2 vUv;
  precision highp sampler3D;
  uniform sampler2D uDepth, uTex, uHt; uniform sampler3D uNoise;
  uniform mat4 uInvProj; uniform mat3 uCamRot; uniform vec3 uCam;
  uniform float uNear, uFar, uTime, uDens, uH, uYmax, uHtMax, uFrame, uNT, uNorthUp, uJit;
  uniform vec4 uTiles; uniform vec2 uTexel, uGo, uGs;
  uniform vec3 uSun, uSunCol, uMoon, uAmb, uMoonCol;
  uniform float uSunI, uCity, uEX, uDbg;
  uniform int uSteps, uOct;
  float hash3(vec3 p){ p = fract(p * .3183099 + .1); p *= 17.; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
  float vn3(vec3 p){ vec3 i = floor(p), f = fract(p); f = f * f * (3. - 2. * f);
    return mix(mix(mix(hash3(i), hash3(i + vec3(1,0,0)), f.x), mix(hash3(i + vec3(0,1,0)), hash3(i + vec3(1,1,0)), f.x), f.y),
               mix(mix(hash3(i + vec3(0,0,1)), hash3(i + vec3(1,0,1)), f.x), mix(hash3(i + vec3(0,1,1)), hash3(i + vec3(1,1,1)), f.x), f.y), f.z); }
  float tile(float k, vec2 q){ // q = (x 0..1, s 0..1 from north); same atlas layout as fog.js
    k = clamp(k, 0., uNT - 1.); float r = floor(k / uTiles.x), c = k - r * uTiles.x;
    vec2 tp = clamp(vec2(q.x * uTiles.z, q.y * uTiles.w), vec2(.5), uTiles.zw - .5);
    return texture2D(uTex, (vec2(c * uTiles.z, r * uTiles.w) + tp) * uTexel).r;
  }
  float hg(float c, float g){ return (1. - g * g) / pow(1. + g * g - 2. * g * c, 1.5) / 12.566; }
  void main(){
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
    float fa = floor(uFrame), ft = uFrame - fa, N = float(uSteps);
    float jit = fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(.06711056, .00583715))) + uJit);
    float T = 1.; vec3 C = vec3(0.);
    float cs = dot(rd, uSun), ph = .3 + 1.7 * hg(cs, .6) * 12.566 * .35;
    float cm = dot(rd, uMoon), phm = .3 + 1.2 * hg(cm, .55) * 12.566 * .3;
    for (int i = 0; i < 16; i++) {
      if (i >= uSteps) break;
      float u0 = (float(i) + jit) / N, u1 = (float(i) + 1. + jit) / N;
      float ta = t0 + (t1 - t0) * u0 * u0, tb = t0 + (t1 - t0) * u1 * u1, dt = tb - ta, t = .5 * (ta + tb);
      vec3 p = ro + rd * t;
      vec2 g = (p.xz - uGo) / uGs, qg = clamp(g, 0., 1.);
      float inside = 1. - smoothstep(0., 8000., length((g - qg) * uGs)); // fog keeps going over the ocean beyond the grid edge, fading over 8 km (needs a wider grid from the pipeline)
      float gh = texture2D(uHt, qg).r * uHtMax;
      float hh = (p.y - gh) / uH;
      if (hh < 0. || hh > 1.) continue;
      float px = mix(tile(fa, qg), tile(fa + 1., qg), ft) * 255.;
      float d = clamp(sqrt(px / ${PX_REF.toFixed(1)}) * uDens, 0., 1.) * inside;
      d = max(d, uDbg * inside);
      if (d < .003) continue;
      float prof = pow(1. - hh, 1.15) * smoothstep(0., .03, hh + .02);
      vec3 wp = vec3(p.x, p.y / uEX, p.z);
      // baked tileable 3D noise (R low fbm, G detail fbm, B warp). Detail fades out where the step is wider than the detail, so long grazing rays stop aliasing into streaks
      vec3 drift = vec3(uTime * .003, 0., uTime * .001);
      vec3 wq = wp;
      if (uOct > 1) wq.xz += (texture(uNoise, wp * vec3(1. / 3500., 1. / 1200., 1. / 3500.)).b - .5) * 500.; // domain warp: breaks straight lines into billows
      float nL = texture(uNoise, wq * vec3(1. / 2200., 1. / 700., 1. / 2200.) + drift).r;
      float n = nL;
      if (uOct > 1) { float wD = 1. - smoothstep(120., 500., dt); n = mix(nL, .55 * nL + .45 * texture(uNoise, wq * vec3(1. / 520., 1. / 200., 1. / 520.) + 3.1).g, wD * .8); }
      if (uOct > 2) { float wF = 1. - smoothstep(40., 160., dt); n = mix(n, n * .7 + .3 * texture(uNoise, wq / 150. + 8.2).g, wF * .6); }
      float shape = smoothstep(.15, .85, n + .3 * d); // wide soft edge: thin fog no longer flickers across a hard threshold
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

export function createFogVol({ THREE, scene, EX, isTouch, getMeta, getTerrainH, toast, tier0 }) {
  const noiseTex = makeNoise3D(THREE);
  const sheets = createSheets({ THREE, scene, EX, isTouch, getMeta, getTerrainH, toast });
  const quadGeo = new THREE.BufferGeometry(); quadGeo.setAttribute('position', new THREE.Float32BufferAttribute([-1, -1, 0, 3, -1, 0, -1, 3, 0], 3));
  const ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const U = {
    uDepth: { value: null }, uTex: { value: null }, uHt: { value: null },
    uInvProj: { value: new THREE.Matrix4() }, uCamRot: { value: new THREE.Matrix3() }, uCam: { value: new THREE.Vector3() },
    uNear: { value: 8 }, uFar: { value: 90000 }, uTime: { value: 0 }, uDens: { value: 1 }, uH: { value: 300 * EX }, uYmax: { value: 900 }, uHtMax: { value: 1 },
    uFrame: { value: 0 }, uNT: { value: 1 }, uNorthUp: { value: 1 }, uJit: { value: 0 }, uTiles: { value: new THREE.Vector4() }, uTexel: { value: new THREE.Vector2() },
    uGo: { value: new THREE.Vector2() }, uGs: { value: new THREE.Vector2(1, 1) },
    uSun: { value: new THREE.Vector3(0, 1, 0) }, uSunCol: { value: new THREE.Vector3() }, uMoon: { value: new THREE.Vector3(0, -1, 0) }, uAmb: { value: new THREE.Vector3(.3, .36, .5) }, uMoonCol: { value: new THREE.Vector3() },
    uDbg: { value: new URLSearchParams(location.search).has('fogdbg') ? 0.6 : 0 }, uNoise: { value: null }, uSunI: { value: 0 }, uCity: { value: 0 }, uEX: { value: EX }, uSteps: { value: 8 }, uOct: { value: 2 },
  };
  U.uNoise.value = noiseTex;
  const fogMat = new THREE.ShaderMaterial({ uniforms: U, vertexShader: VERT, fragmentShader: FOG_FS, depthTest: false, depthWrite: false });
  const fogScene = new THREE.Scene(); const fogMesh = new THREE.Mesh(quadGeo, fogMat); fogMesh.frustumCulled = false; fogScene.add(fogMesh);
  const CU = { uScene: { value: null }, uFog: { value: null }, uDepthC: { value: null }, uFogTexel: { value: new THREE.Vector2() }, uNearC: { value: 8 }, uFarC: { value: 90000 }, uBlur: { value: 1 } };
  const compMat = new THREE.ShaderMaterial({ uniforms: CU, vertexShader: VERT, fragmentShader: COMP_FS, depthTest: false, depthWrite: false });
  const compScene = new THREE.Scene(); const compMesh = new THREE.Mesh(quadGeo, compMat); compMesh.frustumCulled = false; compScene.add(compMesh);

  let tier = isTouch ? 2 : 3, want = true, ready = false, last = null, dens = 1, frameAcc = 0, lastNow = 0, calm = 0, tex = null, htTex = null;
  const qs = new URLSearchParams(location.search); if (qs.has('tier')) tier = Math.max(0, Math.min(3, +qs.get('tier') || 0));
  const dts = []; let sceneRT = null, fogRT = null, rtW = 0, rtH = 0, rtMs = -1, rtScale = -1;
  const sz = new THREE.Vector2();

  function disposeRT() { if (sceneRT) { sceneRT.dispose(); sceneRT.depthTexture && sceneRT.depthTexture.dispose(); } if (fogRT) fogRT.dispose(); sceneRT = fogRT = null; }
  function ensureRT(renderer) {
    const P = TIERS[tier]; renderer.getDrawingBufferSize(sz);
    const w = Math.max(2, sz.x | 0), h = Math.max(2, sz.y | 0);
    if (sceneRT && w === rtW && h === rtH && P.ms === rtMs && P.scale === rtScale) return;
    disposeRT(); rtW = w; rtH = h; rtMs = P.ms; rtScale = P.scale;
    const dt = new THREE.DepthTexture(w, h); dt.format = THREE.DepthFormat; dt.type = THREE.UnsignedIntType;
    sceneRT = new THREE.WebGLRenderTarget(w, h, { samples: P.ms, depthTexture: dt, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, type: THREE.UnsignedByteType });
    fogRT = new THREE.WebGLRenderTarget(Math.max(2, Math.round(w * P.scale)), Math.max(2, Math.round(h * P.scale)), { minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, depthBuffer: false });
    U.uDepth.value = sceneRT.depthTexture; CU.uScene.value = sceneRT.texture; CU.uFog.value = fogRT.texture; CU.uDepthC.value = sceneRT.depthTexture; CU.uFogTexel.value.set(1 / fogRT.width, 1 / fogRT.height);
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
        U.uHt.value = htTex; U.uHtMax.value = mx; U.uTex.value = t;
        U.uTiles.value.set(g.cols, g.rows, g.w, g.h); U.uNT.value = g.cols * g.rows; U.uNorthUp.value = g.row0 === 'north' ? 1 : 0; U.uTexel.value.set(1 / t.image.width, 1 / t.image.height);
        U.uGo.value.set(ox, oz - hm); U.uGs.value.set(wm, hm); U.uYmax.value = mx + 320 * EX;
        ready = true; if (tier === 0) sheets.build(png, g); resolve(true);
      }, undefined, () => { toast('Fog layer unavailable'); resolve(false); });
    });
  }

  function renderFrame(renderer, scn, camera) {
    if (tier === 0 || !ready || !want) { renderer.render(scn, camera); return; }
    const P = TIERS[tier]; ensureRT(renderer);
    U.uInvProj.value.copy(camera.projectionMatrixInverse); U.uCamRot.value.setFromMatrix4(camera.matrixWorld); U.uCam.value.copy(camera.position);
    U.uNear.value = CU.uNearC.value = camera.near; U.uFar.value = CU.uFarC.value = camera.far; CU.uBlur.value = qs.get('blur') === '0' ? 0 : 1; U.uSteps.value = P.steps; U.uOct.value = P.oct; U.uJit.value = 0; // static dither: a per-frame jitter shimmered on phones even with the simulation paused
    const prev = renderer.getRenderTarget();
    renderer.setRenderTarget(sceneRT); renderer.render(scn, camera);
    renderer.setRenderTarget(fogRT); renderer.render(fogScene, ortho);
    renderer.setRenderTarget(prev); renderer.render(compScene, ortho);
  }

  return {
    build, renderFrame,
    clear() { ready = false; sheets.clear(); },
    get ready() { return ready || sheets.ready; },
    get tier() { return tier; },
    setTier,
    setVisible(v) { want = v; if (tier === 0) sheets.setVisible(v); },
    setDensity(v) { dens = v; U.uDens.value = v; sheets.setDensity(v); },
    setFrame(f) { U.uFrame.value = f; sheets.setFrame(f); },
    /* hourly weather for the shown time: fog top (m, clamped to a visual range) */
    setWeather(w) { const top = w && w.top != null ? Math.min(TOP_MAX_M, Math.max(TOP_MIN_M, w.top)) : 200; U.uH.value = top * EX; },
    /* light from sky.js (display-space colours); without it the fog uses a neutral blue-white */
    setLight(L) {
      if (!L) return;
      const lin = (a) => a.map((x) => Math.pow(Math.max(x, 0), 2.2));
      const hor = lin(L.hor), zen = lin(L.zen), fl = [.22, .28, .42]; // night floor close to the daytime fog colour so night fog stays visible
      U.uAmb.value.set(...hor.map((h, i) => Math.max(h * .65 + zen[i] * .35, fl[i]) * 1.3));
      U.uSun.value.set(...L.sunDir); U.uSunCol.value.set(...L.sunCol); U.uSunI.value = Math.max(L.sunDir[1], 0) > 0 ? 1.1 : 0;
      U.uMoon.value.set(...L.moonDir); U.uMoonCol.value.set(.35, .42, .6).multiplyScalar(.28 * L.illum);
      U.uCity.value = Math.min(1, Math.max(0, (-L.sunEl - 4) / 10));
    },
    update(camera, dist, tSec) {
      U.uTime.value = tSec; sheets.update(camera, dist, tSec);
      const now = performance.now(); if (lastNow) { dts.push(now - lastNow); if (dts.length > 60) dts.shift(); } lastNow = now;
      if (dts.length === 60 && tier > 0) { // governor: one notch down after a slow window, never back up in the same session
        const s = [...dts].sort((a, b) => a - b), med = s[30];
        if (med > 22 && ++calm >= 1) { calm = 0; dts.length = 0; setTier(tier - 1, med.toFixed(0) + ' ms median'); }
      }
    },
    info() { return 'fog tier ' + tier + (tier ? ` (${TIERS[tier].steps} steps, ${Math.round(TIERS[tier].scale * 100)}% res)` : ' (sheets)'); },
  };
}
