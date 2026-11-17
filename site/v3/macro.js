/* The macro view (guide 09 part B, beta; lab switch `macro` of the fog lab): terrain and the satellite's cloud bank out to about 60 km.
   Only the 3D scene changes: a wide terrain mesh (data/terrain_wide.bin, 400 m cells, one draw call), a cloud slab drawn analytically from the macro frames
   (live_beta/macro/index.json: R = probability, G = confidence, B = top / 8 m, every 5 minutes) and a longer camera range. Near the city the voxel fog is the truth:
   the macro layer fades out over the outer 2 km of the city fog grid, and the wide terrain sinks 30 m under the detailed terrain.
   The wide terrain loads as soon as the switch is on and is always drawn; the cloud frames are fetched once the camera is pulled back (> 6 km from its target). Pure parts: macrocalc.js. */
import { parseWide, wideHeight, inHole, resampleMacro, macroBracket } from './macrocalc.js';

const BASE_THICK_M = 250;      // the base is a guess: top - 250 m (said so in the guide)
const FADE_M = 2000;           // the macro layer fades out over the outer 2 km of the city grid
const FAR_LIMIT = 150000, FAR_PLANE = 300000;

const VERT = `varying vec3 vW; void main(){ vec4 w = modelMatrix * vec4(position, 1.); vW = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }`;
const FRAG = `
  precision highp float;
  varying vec3 vW; uniform mat4 projectionMatrix;
  uniform sampler2D uA, uB; uniform float uMix, uHasB, uOn, uEX, uTopDef, uK, uNoiseR, uTime, uSky;
  uniform vec4 uBox;            // wide grid in scene metres: x0, z0 (north edge), width, height
  uniform vec4 uCity;           // city grid rectangle: xmin, zmin, xmax, zmax
  uniform vec3 uAmb, uSun, uSunCol; uniform float uSunI;
  float h21(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
  float vn(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3. - 2. * f); return mix(mix(h21(i), h21(i + vec2(1,0)), f.x), mix(h21(i + vec2(0,1)), h21(i + vec2(1,1)), f.x), f.y); }
  vec4 cov(vec2 xz){
    vec2 uv = (xz - uBox.xy) / uBox.zw; if (uv.x < 0. || uv.y < 0. || uv.x > 1. || uv.y > 1.) return vec4(0.);
    vec4 a = texture2D(uA, uv); return uHasB > .5 ? mix(a, texture2D(uB, uv), uMix) : a;
  }
  float seam(vec2 xz){ float d = min(min(xz.x - uCity.x, uCity.z - xz.x), min(xz.y - uCity.y, uCity.w - xz.y)); if (d <= 0.) return 1.; float t = clamp(d / ${FADE_M}., 0., 1.); return 1. - t * t * (3. - 2. * t); }
  void main(){
    vec3 ro = cameraPosition, rd = normalize(vW - cameraPosition);
    // top of the layer where the ray crosses the middle of the default layer (the satellite top where it has one, else the fused fog top)
    float tm = uTopDef * .85 * uEX, t = rd.y > 1e-4 ? (tm - ro.y) / rd.y : -1.;
    float top = uTopDef * uEX;
    if (t > 0. && t < 3.0e5) { vec4 c0 = cov(ro.xz + rd.xz * t); if (c0.b > 0.) top = c0.b * 8. * uEX; }
    float base = max(top - ${BASE_THICK_M}. * uEX, 0.);
    // analytic slab
    float a, b;
    if (abs(rd.y) < 1e-5) { if (ro.y < base || ro.y > top) discard; a = 0.; b = 3.0e5; }
    else { a = (base - ro.y) / rd.y; b = (top - ro.y) / rd.y; if (a > b) { float s = a; a = b; b = s; } if (b < 0.) discard; a = max(a, 0.); b = min(b, 2.6e5); }
    if (b <= a) discard;
    const int N = 8; float seg = (b - a) / float(N), od = 0., sumd = 0., sumh = 0.;
    for (int i = 0; i < N; i++) {
      float ts = a + (float(i) + .5) * seg; vec3 p = ro + rd * ts; vec4 c = cov(p.xz);
      float near = uNoiseR * (1. - smoothstep(10000., 15000., ts));  // detail only close to the camera: far away the smooth satellite field stands alone
      // fine noise at three scales, so the bank has billows and ragged edges instead of 1 km blocks
      float nz = vn(p.xz / 1200.) * .5 + vn(p.xz / 400.) * .3 + vn(p.xz / 130. + p.y / 90.) * .2;
      float dens = c.r * mix(.35, 1., c.g);                       // low confidence: less opaque, never invented
      dens = clamp((dens - (1. - nz) * .35 * near) * (1. + .6 * near), 0., 1.) * mix(1., .6 + .8 * nz, near * .5);   // erode the edges with the noise
      float hh = clamp((p.y - base) / max(top - base, 1.), 0., 1.);
      // sky layers (guide 13, lab switch sky): inside the city grid keep cloud the satellite puts well above the fog top (a deck above the fog, not the fog itself, which the voxels draw); cirrus-high tops fade out
      float sm = seam(p.xz);
      if (uSky > .5) { float tm8 = c.b * 2040.; float up = smoothstep(0., 1., (tm8 - uTopDef - 100.) / 150.) * (1. - smoothstep(3000., 4000., tm8)); sm = max(sm, up); }
      float w = dens * sm; od += w * seg; sumd += w; sumh += w * hh;
    }
    od *= uK; float hmean = sumd > 0. ? sumh / sumd : .5;
    float al = (1. - exp(-od)) * uOn; if (al < .004) discard;
    float ph = .75 + .25 * pow(max(dot(rd, uSun), 0.), 4.);
    vec3 L = (uAmb * (.7 + .3 * clamp(rd.y + .5, 0., 1.)) + uSunCol * uSunI * .55 * ph) * (.78 + .3 * hmean);   // brighter towards the top of the layer, greyer in the thick base
    gl_FragColor = linearToOutputTexel(vec4(L, al));
    vec4 cp = projectionMatrix * viewMatrix * vec4(ro + rd * a, 1.); gl_FragDepth = clamp(cp.z / cp.w * .5 + .5, 0., 1.); // depth of the nearest point of the layer, so terrain and buildings in front hide it
  }`;

export function createMacro({ THREE, scene, camera, controls, EX, getMeta, getFogGrid, getTerrainH, fog, invalidate }) {
  const st = { src: null, base: '', idx: null, idxAt: 0, fetching: false, wide: null, wideBuild: false, wideMesh: null, slab: null, on: false, edgeBackup: null, tMs: 0, lim: null, near: 8, light: null };
  const texCache = new Map(); // file -> { tex } | 'loading' | 'failed'
  const sample = new THREE.Vector3();
  const TARGET = () => (st.wide ? { x0: st.wide.x0, y0: st.wide.y0, cell: st.wide.cell, nx: st.wide.nx, ny: st.wide.ny } : null);

  const U = {
    uA: { value: null }, uB: { value: null }, uMix: { value: 0 }, uHasB: { value: 0 }, uOn: { value: 0 }, uEX: { value: EX }, uTopDef: { value: 200 }, uK: { value: 2.6 / (BASE_THICK_M * EX) }, uNoiseR: { value: 1 }, uTime: { value: 0 }, uSky: { value: 0 },
    uBox: { value: new THREE.Vector4() }, uCity: { value: new THREE.Vector4() }, uAmb: { value: new THREE.Vector3(.3, .36, .5) }, uSun: { value: new THREE.Vector3(0, 1, 0) }, uSunCol: { value: new THREE.Vector3() }, uSunI: { value: 0 },
  };

  async function loadWide() {
    if (st.wide || st.wideBuild) return; st.wideBuild = true;
    try {
      const r = await fetch('data/terrain_wide.bin'); if (!r.ok) throw new Error('http ' + r.status);
      st.wide = parseWide(await r.arrayBuffer()); buildWide(); buildSlab(); invalidate && invalidate();
    } catch (e) { console.warn('macro: wide terrain failed', e); st.wideBuild = false; st.failed = true; }
  }
  function buildWide() {
    const W = st.wide, meta = getMeta(), nv = W.nx * W.ny, pos = new Float32Array(nv * 3), col = new Float32Array(nv * 3), idx = new Uint32Array((W.nx - 1) * (W.ny - 1) * 6);
    const lo = new THREE.Color('#25405a'), hi = new THREE.Color('#5f7563'), sea = new THREE.Color('#10263f'), c = new THREE.Color();
    const rect = [0, -meta.ny * meta.cell_m, meta.nx * meta.cell_m, 0]; // the detailed terrain's footprint in scene metres
    for (let r = 0; r < W.ny; r++) for (let q = 0; q < W.nx; q++) {
      const i = r * W.nx + q, h = wideHeight(W, r, q), x = W.x0 + (q + .5) * W.cell - meta.x0, z = -(W.y0 - (r + .5) * W.cell - meta.y0);
      pos[i * 3] = x; pos[i * 3 + 1] = h * EX - (inHole(x, z, rect, W.cell) ? 30 * EX : 0); pos[i * 3 + 2] = z;
      if (h < 0.6) c.copy(sea); else c.copy(lo).lerp(hi, Math.min(1, h / 260));
      col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
    }
    let k = 0;
    for (let r = 0; r < W.ny - 1; r++) for (let q = 0; q < W.nx - 1; q++) { const i = r * W.nx + q; idx[k++] = i; idx[k++] = i + W.nx; idx[k++] = i + 1; idx[k++] = i + 1; idx[k++] = i + W.nx; idx[k++] = i + W.nx + 1; }
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3)); g.setAttribute('color', new THREE.BufferAttribute(col, 3)); g.setIndex(new THREE.BufferAttribute(idx, 1));
    g.computeVertexNormals(); g.computeBoundingSphere();
    const m = new THREE.Mesh(g, new THREE.MeshLambertMaterial({ vertexColors: true, flatShading: true }));
    m.renderOrder = -1; m.visible = false; scene.add(m); st.wideMesh = m;
  }
  function buildSlab() {
    const W = st.wide, meta = getMeta(), w = W.nx * W.cell, h = W.ny * W.cell, x0 = W.x0 - meta.x0, zN = -(W.y0 - meta.y0), yTop = 1500 * EX;
    U.uBox.value.set(x0, zN, w, h);
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, yTop, h), new THREE.ShaderMaterial({ uniforms: U, vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false, depthTest: true, side: THREE.BackSide }));
    m.position.set(x0 + w / 2, yTop / 2, zN + h / 2); m.renderOrder = 6; m.frustumCulled = false; m.visible = false; scene.add(m); st.slab = m;
  }

  /* ---- frames */
  async function fetchIndex() {
    if (!st.src || st.fetching) return; st.fetching = true;
    try { const r = await fetch(st.src, { cache: 'no-store' }); if (r.ok) { const j = await r.json(); if (j && j.schema === 1) { st.idx = j; st.box = j.box; st.w = j.w; st.h = j.h; invalidate && invalidate(); } } } catch (e) { /* no macro frames: no layer */ }
    st.idxAt = performance.now(); st.fetching = false;
  }
  function frameTex(file) {
    const e = texCache.get(file); if (e && e !== 'loading' && e !== 'failed') return e.tex;
    if (e || !st.wide) return null; texCache.set(file, 'loading');
    const img = new Image(); const T = TARGET();
    img.onload = () => {
      try {
        const c = document.createElement('canvas'); c.width = img.width; c.height = img.height; const cx = c.getContext('2d', { willReadFrequently: true }); cx.drawImage(img, 0, 0);
        const px = cx.getImageData(0, 0, img.width, img.height).data; // alpha is 255 everywhere, so the colour values stay exact
        const tex = new THREE.DataTexture(resampleMacro(px, img.width, img.height, st.box, T), T.nx, T.ny, THREE.RGBAFormat, THREE.UnsignedByteType);
        tex.minFilter = tex.magFilter = THREE.LinearFilter; tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping; tex.generateMipmaps = false; tex.flipY = false; tex.needsUpdate = true;
        texCache.set(file, { tex, used: performance.now() }); invalidate && invalidate();
        if (texCache.size > 5) for (const [k, v] of texCache) { if (v !== 'loading' && v !== 'failed' && k !== file && texCache.size > 5) { v.tex.dispose(); texCache.delete(k); } }
      } catch (err) { texCache.set(file, 'failed'); }
    };
    img.onerror = () => texCache.set(file, 'failed');
    img.src = st.base + file + '?v=' + encodeURIComponent((st.idx && st.idx.frames.find((f) => f.file === file) || {}).t || '');
    return null;
  }

  function setLight(L) { // the same sun and ambient terms as the voxel fog (fog2.js setLight)
    if (!L) return; st.light = L;
    const P = fog.params, lin = (a) => a.map((x) => Math.pow(Math.max(x, 0), 2.2)), hor = lin(L.hor), zen = lin(L.zen), fl = [.22, .28, .42].map((x) => x * P.night);
    U.uAmb.value.set(...hor.map((h, i) => Math.max(h * .65 + zen[i] * .35, fl[i]) * 1.3));
    U.uSun.value.set(...L.sunDir); U.uSunCol.value.set(...L.sunCol); U.uSunI.value = (Math.max(L.sunDir[1], 0) > 0 ? 1.1 : 0) * P.sun;
  }

  function limits(on) {
    if (on && !st.lim) { st.lim = { max: controls.maxDistance, far: camera.far, near: camera.near }; }
    if (on) { controls.maxDistance = FAR_LIMIT; if (camera.far !== FAR_PLANE) { camera.far = FAR_PLANE; camera.updateProjectionMatrix(); } }
    else if (st.lim) { controls.maxDistance = st.lim.max; camera.far = st.lim.far; camera.near = st.lim.near; camera.updateProjectionMatrix(); st.lim = null; }
  }
  function edgeFade(on) { // the macro layer replaces the mirrored copy of the voxel fog beyond the data edge
    if (on && st.edgeBackup === null) { st.edgeBackup = fog.params.edgeFade; fog.setParams({ edgeFade: 500 }); }
    else if (!on && st.edgeBackup !== null) { fog.setParams({ edgeFade: st.edgeBackup }); st.edgeBackup = null; }
  }

  /* called every frame before the fog pass; dist = camera to target */
  function update(dist) {
    const want = !!(fog.params && fog.params.macro) && !!st.src;
    if (!want) { if (st.on) { st.on = false; limits(false); edgeFade(false); if (st.wideMesh) st.wideMesh.visible = false; if (st.slab) st.slab.visible = false; } return; }
    if (!st.on) { st.on = true; limits(true); edgeFade(true); if (st.light) setLight(st.light); }
    const camH = camera.position.y / EX;
    // near plane rises with the camera height so the buildings keep their depth precision with the far plane at 300 km
    const nearWant = Math.max(8, Math.min(250, camH / 100)); if (Math.abs(nearWant - camera.near) > camera.near * .15) { camera.near = nearWant; camera.updateProjectionMatrix(); }
    // the wide terrain is always there (it loads as soon as the view is on, 0.2 MB, and stays drawn even when the camera is down in the city); the satellite frames are fetched once the camera is pulled back
    if (!st.wide) loadWide();
    if ((dist > 6000 || camH > 4000) && (!st.idx || performance.now() - st.idxAt > 300000)) fetchIndex();
    if (!st.wideMesh) return;
    st.wideMesh.visible = true;
    const br = st.idx ? macroBracket(st.idx, st.tMs) : null;
    let show = !!br;
    if (br) {
      const ta = frameTex(br.a.file), tb = br.b !== br.a ? frameTex(br.b.file) : null;
      if (!ta) show = false; else { U.uA.value = ta; U.uB.value = tb || ta; U.uHasB.value = tb ? 1 : 0; U.uMix.value = tb ? br.mix : 0; }
    }
    // no macro pixel on screen: camera low and looking down
    camera.getWorldDirection(sample); if (camH < 1200 && sample.y < -0.45) show = false;
    const g = getFogGrid(), m = getMeta();
    if (g && m) { const ox = g.x0 - m.x0, oz = -(g.y0 - m.y0), hm = g.h * g.cell_m; U.uCity.value.set(ox, oz - hm, ox + g.w * g.cell_m, oz); }
    U.uTopDef.value = Math.max(150, fog.topM || 200); U.uSky.value = fog.params && fog.params.sky ? 1 : 0; U.uOn.value = show ? 1 : 0; U.uTime.value = performance.now() / 1000;
    st.slab.visible = show;
  }

  return {
    update, setLight,
    /* the macro folder index (macro/index.json) of the live output, or null */
    setSource(url) { st.src = url || null; st.base = url ? url.replace(/[^/]*$/, '') : ''; st.idx = null; st.idxAt = 0; for (const v of texCache.values()) if (v && v.tex) v.tex.dispose(); texCache.clear(); },
    setTime(ms) { st.tMs = ms; },
    get active() { return st.on; },
    debug() { const br = st.idx ? macroBracket(st.idx, st.tMs) : null; return { tMs: st.tMs, t: new Date(st.tMs).toISOString(), br: br && [br.a.file, br.b.file, br.mix], tex: [...texCache.entries()].map(([k, v]) => [k, typeof v === 'string' ? v : 'ok']), src: st.src, uOn: U.uOn.value, vis: st.slab && st.slab.visible }; },
    info() { return st.on ? `macro: wide terrain ${st.wide ? 'loaded' : 'not yet'}, ${st.idx ? st.idx.frames.length + ' frames' : 'no frames'}, layer ${U.uOn.value ? 'on' : 'off'}` : 'macro off'; },
  };
}
