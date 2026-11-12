import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

/* ------------------------------------------------------------------ config */
const SRC_DIRS = new URLSearchParams(location.search).get('src') === 'mock' ? ['replay_mock/'] : ['replay/', 'replay_mock/'];
const RAMP = [[0, '#3a8696'], [18, '#6fb09f'], [24, '#f4e04d'], [50, '#f2c230'], [65, '#ee8a2c'], [80, '#d9531e'], [90, '#b3202f'], [100, '#7a0f3b']];
const CATS = ['dry', 'damp', 'slick', 'wet', 'icy-fog'];
const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const isTouch = matchMedia('(pointer: coarse)').matches;

function hexRgb(h) { return [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255); }
function rampColor(v) {
  v = clamp(v, 0, 100);
  for (let i = 1; i < RAMP.length; i++) {
    if (v <= RAMP[i][0]) {
      const a = hexRgb(RAMP[i - 1][1]), b = hexRgb(RAMP[i][1]), t = (v - RAMP[i - 1][0]) / (RAMP[i][0] - RAMP[i - 1][0]);
      return a.map((x, k) => x + (b[k] - x) * t);
    }
  }
  return hexRgb(RAMP[RAMP.length - 1][1]);
}
const rgbCss = (c) => `rgb(${c.map((x) => Math.round(x * 255)).join(',')})`;
$('ramp').style.background = `linear-gradient(90deg, ${RAMP.map(([s, c]) => `${c} ${s}%`).join(',')})`;
const glslRamp = (() => {
  const col = (h) => `vec3(${hexRgb(h).map((x) => x.toFixed(4)).join(',')})`;
  let s = 'vec3 ramp(float v){ v=clamp(v,0.,100.);\n';
  for (let i = 1; i < RAMP.length; i++) {
    s += `if(v<=${RAMP[i][0].toFixed(1)}) return mix(${col(RAMP[i - 1][1])},${col(RAMP[i][1])},(v-${RAMP[i - 1][0].toFixed(1)})/${(RAMP[i][0] - RAMP[i - 1][0]).toFixed(1)});\n`;
  }
  return s + `return ${col(RAMP[RAMP.length - 1][1])};}\n`;
})();

/* ------------------------------------------------------------------ status UI */
const overlay = $('overlay');
function setStatus(msg, err) {
  $('status').textContent = msg;
  overlay.classList.toggle('err', !!err);
  $('retry').hidden = !err;
  if (err) overlay.classList.remove('hide');
}
$('retry').onclick = () => location.reload();
function toast(msg) { // non-fatal notice (reuses the badge)
  const b = $('badge'); b.textContent = msg; b.hidden = false;
}
window.addEventListener('error', (e) => { if (overlay.classList.contains('hide')) toast('Something went wrong: ' + (e.message || 'error')); });

function webglOk() {
  try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl')); } catch { return false; }
}
if (!webglOk()) { setStatus('This page needs WebGL, which your browser or device has disabled.', true); throw new Error('no webgl'); }

async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`); return r.json(); }
async function getBuf(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`); return r.arrayBuffer(); }
function withTimeout(p, ms, what) { return Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error(what + ' timed out')), ms))]); }

/* ------------------------------------------------------------------ scene */
const canvas = $('gl');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(devicePixelRatio || 1, isTouch ? 2 : 1.75));
const scene = new THREE.Scene();
scene.background = new THREE.Color('#070b14');
const camera = new THREE.PerspectiveCamera(50, 1, 8, 90000);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true; controls.dampingFactor = 0.08;
controls.maxPolarAngle = 1.48; controls.minDistance = 60; controls.maxDistance = 32000;
controls.screenSpacePanning = false;

scene.add(new THREE.HemisphereLight(0x8fa8d8, 0x2a3348, 1.6));
const moon = new THREE.DirectionalLight(0xc8d4ff, 2.2);
moon.position.set(-6000, 9000, 5000); scene.add(moon);
scene.add(new THREE.AmbientLight(0x202840, 0.8));

let meta = null, pts = null, terrainH = null, T = null;
const CITY = { target: new THREE.Vector3(8300, 0, -8000), pos: new THREE.Vector3(3900, 8800, 7600) };
camera.position.copy(CITY.pos); controls.target.copy(CITY.target);

function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h; camera.fov = w < h ? 62 : 50; camera.updateProjectionMatrix();
  const ch = $('controls').offsetHeight; document.documentElement.style.setProperty('--ctl-h', ch + 'px');
}
addEventListener('resize', resize); new ResizeObserver(resize).observe($('controls')); resize();

/* ------------------------------------------------------------------ terrain height lookup */
function buildHeightGrid(geom, step) {
  const p = geom.attributes.position;
  let maxI = 0, maxJ = 0;
  for (let k = 0; k < p.count; k++) { maxI = Math.max(maxI, Math.round(p.getX(k) / step)); maxJ = Math.max(maxJ, Math.round(-p.getZ(k) / step)); }
  const ni = maxI + 1, nj = maxJ + 1, h = new Float32Array(ni * nj).fill(-9999);
  for (let k = 0; k < p.count; k++) h[Math.round(p.getX(k) / step) * nj + Math.round(-p.getZ(k) / step)] = p.getY(k);
  return {
    ni, nj, step, h,
    at(x, z) {
      const fi = clamp(x / step, 0, ni - 1.001), fj = clamp(-z / step, 0, nj - 1.001), i = Math.floor(fi), j = Math.floor(fj), a = fi - i, b = fj - j;
      const g = (ii, jj) => Math.max(h[ii * nj + jj], -50);
      return (g(i, j) * (1 - a) + g(i + 1, j) * a) * (1 - b) + (g(i, j + 1) * (1 - a) + g(i + 1, j + 1) * a) * b;
    },
  };
}

/* ------------------------------------------------------------------ meshes */
const loader = new GLTFLoader();
const loadGLB = (url) => new Promise((res, rej) => loader.load(url, res, undefined, rej));
let terrainMesh = null, buildingsMesh = null;

async function loadMeshes() {
  try {
    const gl = await withTimeout(loadGLB('data/terrain.glb'), 60000, 'terrain');
    gl.scene.traverse((o) => {
      if (!o.isMesh) return;
      const g = o.geometry; if (!g.attributes.normal) g.computeVertexNormals();
      const p = g.attributes.position, col = new Float32Array(p.count * 3), lo = new THREE.Color('#25405a'), hi = new THREE.Color('#5f7563'), c = new THREE.Color();
      for (let k = 0; k < p.count; k++) { c.copy(lo).lerp(hi, clamp(p.getY(k) / 260, 0, 1)); if (p.getY(k) < 0.6) c.set('#10263f'); col.set([c.r, c.g, c.b], k * 3); }
      g.setAttribute('color', new THREE.BufferAttribute(col, 3));
      o.material = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide });
      terrainH = buildHeightGrid(g, meta.cell_m * 3);
    });
    terrainMesh = gl.scene; scene.add(terrainMesh);
  } catch (e) { console.warn(e); toast('Terrain failed to load'); }
  try {
    const gl = await withTimeout(loadGLB('data/buildings.glb'), 60000, 'buildings');
    gl.scene.traverse((o) => {
      if (!o.isMesh) return;
      if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
      o.material = new THREE.MeshLambertMaterial({ color: 0x6b7a96, emissive: 0x0b1224, side: THREE.DoubleSide });
    });
    buildingsMesh = gl.scene; scene.add(buildingsMesh);
  } catch (e) { console.warn(e); toast('Buildings failed to load'); }
}

/* ------------------------------------------------------------------ street points */
let wetAttr = null, pointsMesh = null, pointMat = null, elevY = null, wetNow = null;
function buildPoints(bin, info) {
  const N = info.n;
  const f = new Float32Array(bin, 0, N * 4), side = new Uint8Array(bin, N * 16, N), route = new Uint8Array(bin, N * 17, N), st = new Uint16Array(bin, N * 18, N);
  pts = { N, f, side, route, st, streets: info.streets };
  elevY = new Float32Array(N);
  const iPos = new Float32Array(N * 3), iBear = new Float32Array(N), iSide = new Float32Array(N), iRoute = new Float32Array(N);
  for (let k = 0; k < N; k++) {
    const x = f[k * 4], z = f[k * 4 + 2], y = Math.max(f[k * 4 + 1], terrainH ? terrainH.at(x, z) : 0) + 1.5;
    elevY[k] = y; iPos.set([x, y, z], k * 3); iBear[k] = f[k * 4 + 3]; iSide[k] = side[k]; iRoute[k] = route[k];
  }
  const q = new THREE.InstancedBufferGeometry();
  q.setAttribute('position', new THREE.BufferAttribute(new Float32Array([-.5, 0, -.5, .5, 0, -.5, .5, 0, .5, -.5, 0, .5]), 3));
  q.setIndex([0, 2, 1, 0, 3, 2]); q.instanceCount = N;
  q.setAttribute('iPos', new THREE.InstancedBufferAttribute(iPos, 3));
  q.setAttribute('iBear', new THREE.InstancedBufferAttribute(iBear, 1));
  q.setAttribute('iSide', new THREE.InstancedBufferAttribute(iSide, 1));
  q.setAttribute('iRoute', new THREE.InstancedBufferAttribute(iRoute, 1));
  wetNow = new Float32Array(N).fill(-1);
  wetAttr = new THREE.InstancedBufferAttribute(wetNow, 1); wetAttr.setUsage(THREE.DynamicDrawUsage);
  q.setAttribute('iWet', wetAttr);
  pointMat = new THREE.ShaderMaterial({
    uniforms: { uScale: { value: 1 } },
    vertexShader: `
      attribute vec3 iPos; attribute float iBear, iSide, iWet, iRoute;
      uniform float uScale; varying float vWet, vRoute; varying vec2 vUv;
      void main(){
        float b = radians(iBear); vec3 dir = vec3(sin(b), 0., -cos(b)); vec3 rt = vec3(-dir.z, 0., dir.x);
        float w = 3.7 * uScale * (1. + 0.0 * iRoute), l = 9.4 * uScale;
        vec3 wp = iPos + rt * position.x * w + dir * position.z * l; wp.y += uScale * 0.5;
        vWet = iWet; vRoute = iRoute; vUv = position.xz + .5;
        gl_Position = projectionMatrix * viewMatrix * vec4(wp, 1.);
      }`,
    fragmentShader: `
      precision highp float; varying float vWet, vRoute; varying vec2 vUv;
      ${glslRamp}
      void main(){
        vec3 c = vWet < 0. ? vec3(.25,.28,.34) : ramp(vWet);
        if (vRoute > .5) { vec2 e = abs(vUv - .5); if (max(e.x * 1.0, e.y * .5) > .4 && e.x > .36) c = mix(c, vec3(.37,.95,1.), .9); }
        gl_FragColor = vec4(c, 1.);
      }`,
    side: THREE.DoubleSide,
  });
  pointsMesh = new THREE.Mesh(q, pointMat); pointsMesh.frustumCulled = false; pointsMesh.renderOrder = 2;
  scene.add(pointsMesh);
}

/* ------------------------------------------------------------------ route + beacons */
const ROUTE = { mid: new THREE.Vector3(), start: null, bus: null };
const labelEls = [];
const beacons = [];
function buildRoute(info) {
  if (!info.route || info.route.length < 2) return;
  const lift = (p) => new THREE.Vector3(p[0], Math.max(p[1], terrainH ? terrainH.at(p[0], p[2]) : 0) + 3, p[2]);
  const rp = info.route.map(lift);
  const curve = new THREE.CatmullRomCurve3(rp, false, 'catmullrom', 0.1);
  const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 300, 2.4, 6, false), new THREE.MeshBasicMaterial({ color: 0x5ef2ff }));
  tube.renderOrder = 3; scene.add(tube);
  ROUTE.mid.copy(rp[Math.floor(rp.length / 2)]);
  ROUTE.start = rp[0]; ROUTE.bus = info.bus_stop ? lift(info.bus_stop) : rp[rp.length - 1];
  const mk = (p, text, col) => {
    const m = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 700, 14, 1, true), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: .22, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
    m.position.set(p.x, p.y + 350, p.z); scene.add(m); beacons.push(m);
    const el = document.createElement('div'); el.className = 'lbl'; el.textContent = text; $('labels').appendChild(el); labelEls.push({ el, p: p.clone().add(new THREE.Vector3(0, 30, 0)) });
  };
  mk(ROUTE.start, 'Start: 20th & Kansas', 0x5ef2ff);
  mk(ROUTE.bus, 'Bus stop: 15th & Rhode Island', 0xf2c230);
}

/* ------------------------------------------------------------------ fog layer */
let fogLayers = [], fogUniforms = null, fogTex = null;
function buildFog(png, g) {
  clearFog();
  return new Promise((resolve) => {
    new THREE.TextureLoader().load(png, (tex) => {
      tex.minFilter = tex.magFilter = THREE.LinearFilter; tex.generateMipmaps = false; tex.flipY = false; tex.colorSpace = THREE.NoColorSpace;
      fogTex = tex;
      const wm = g.w * g.cell_m, hm = g.h * g.cell_m, ox = g.x0 - meta.x0, oz = -(g.y0 - meta.y0);
      const seg = 2;
      fogUniforms = {
        uTex: { value: tex }, uFrame: { value: 0 }, uTiles: { value: new THREE.Vector4(g.cols, g.rows, g.w, g.h) }, uNT: { value: g.cols * g.rows },
        uNorthUp: { value: g.row0 === 'north' ? 1 : 0 }, uTexel: { value: new THREE.Vector2(1 / tex.image.width, 1 / tex.image.height) }, uOpacity: { value: 1 },
      };
      [[28, 1.0], [70, 0.6], [130, 0.35]].forEach(([off, op]) => {
        const geo = new THREE.PlaneGeometry(wm, hm, Math.round(g.w / seg), Math.round(g.h / seg));
        geo.rotateX(-Math.PI / 2); geo.translate(ox + wm / 2, 0, oz - hm / 2);
        const p = geo.attributes.position;
        for (let k = 0; k < p.count; k++) p.setY(k, (terrainH ? Math.max(0, terrainH.at(p.getX(k), p.getZ(k))) : 0) + off);
        const mat = new THREE.ShaderMaterial({
          uniforms: { ...fogUniforms, uLayer: { value: op } }, transparent: true, depthWrite: false, side: THREE.DoubleSide,
          vertexShader: `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.); }`,
          fragmentShader: `
            precision highp float; varying vec2 vUv; uniform sampler2D uTex; uniform float uFrame, uNT, uNorthUp, uLayer; uniform vec4 uTiles; uniform vec2 uTexel;
            float tile(float k, vec2 uv){
              k = clamp(k, 0., uNT - 1.); float r = floor(k / uTiles.x), c = k - r * uTiles.x;
              float s = uNorthUp > .5 ? 1. - uv.y : uv.y;           // row 0 is south unless north-up
              vec2 tp = vec2(uv.x * uTiles.z, s * uTiles.w);
              tp = clamp(tp, vec2(.5), uTiles.zw - .5);
              vec2 px = vec2(c * uTiles.z + tp.x, r * uTiles.w + tp.y);
              return texture2D(uTex, px * uTexel).r;
            }
            void main(){
              float a = floor(uFrame), t = uFrame - a;
              float v = mix(tile(a, vUv), tile(a + 1., vUv), t);
              float e = smoothstep(0., .04, min(min(vUv.x, 1. - vUv.x), min(vUv.y, 1. - vUv.y)));
              float al = smoothstep(.05, .75, v) * .5 * uLayer * e;
              gl_FragColor = vec4(mix(vec3(.62,.72,.86), vec3(.82,.88,.97), v), al);
            }`,
        });
        const m = new THREE.Mesh(geo, mat); m.renderOrder = 1; m.frustumCulled = false; scene.add(m); fogLayers.push(m);
      });
      resolve(true);
    }, undefined, () => { toast('Fog layer unavailable'); resolve(false); });
  });
}
function clearFog() { fogLayers.forEach((m) => { scene.remove(m); m.geometry.dispose(); m.material.dispose(); }); fogLayers = []; if (fogTex) fogTex.dispose(); fogTex = null; }

/* ------------------------------------------------------------------ replay data */
let index = null, base = '', night = null, streets = null, weather = null, fogGrid = null;
let tNow = 0, playing = false, speed = 1, lastTs = 0;

async function loadIndex() {
  let lastErr;
  for (const d of SRC_DIRS) {
    try {
      const idx = await withTimeout(getJSON(d + 'index.json'), 15000, 'index');
      if (idx && Array.isArray(idx.nights) && idx.nights.length) return [idx, d];
      lastErr = new Error(d + 'index.json has no nights');
    } catch (e) { lastErr = e; }
  }
  throw lastErr || new Error('no replay data');
}

async function selectNight(i) {
  const n = index.nights[i]; setStatus(`Loading ${n.label}…`); overlay.classList.remove('hide');
  const rows = (n.n_frames), want = rows * pts.N;
  const [sb, wx] = await Promise.all([
    withTimeout(getBuf(base + n.streets), 60000, 'street data'),
    getJSON(base + n.weather).catch(() => null),
  ]);
  if (sb.byteLength !== want) throw new Error(`streets.u8 has ${sb.byteLength} bytes, expected ${want}`);
  night = n; streets = new Uint8Array(sb); weather = wx; fogGrid = n.fog_grid;
  $('slider').max = n.n_frames - 1;
  const ok = n.fog && fogGrid ? await buildFog(base + n.fog, fogGrid) : false;
  $('fogtoggle').disabled = !ok; if (!ok) $('fogtoggle').checked = false;
  fogLayers.forEach((m) => (m.visible = $('fogtoggle').checked));
  tNow = clamp(tNow, 0, n.n_frames - 1); applyTime(true);
  overlay.classList.add('hide');
}

/* wall-clock label from ISO string (ignores offset: shows local time as written) */
function wall(iso, addMin) {
  const m = /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)/.exec(iso); if (!m) return { time: '--:--', day: '' };
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5] + Math.round(addMin)));
  const p = (x) => String(x).padStart(2, '0');
  return { time: `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`, day: d.toLocaleDateString('en-US', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }) };
}

let lastFrameKey = -1;
function applyTime(force) {
  if (!night) return;
  const nf = night.n_frames, f = clamp(tNow, 0, nf - 1), a = Math.floor(f), b = Math.min(a + 1, nf - 1), u = f - a;
  const w = wall(night.start_iso, f * night.step_min);
  $('clock').textContent = w.time; $('clock-sub').textContent = w.day + ' PST';
  if (document.activeElement !== $('slider') || force) $('slider').value = f;
  const key = Math.round(f * 20);
  if (force || key !== lastFrameKey) {
    lastFrameKey = key; const N = pts.N, A = a * N, B = b * N;
    for (let k = 0; k < N; k++) wetNow[k] = (streets[A + k] * (1 - u) + streets[B + k] * u) / 100 * 100;
    wetAttr.needsUpdate = true;
    if (weather) {
      const i = Math.round(f), g = (arr, d = 0) => (arr && arr[i] != null ? arr[i].toFixed(d) : '--');
      $('wx-temp').textContent = g(weather.temp_c, 1) + ' °C'; $('wx-dew').textContent = g(weather.dewpoint_c, 1) + ' °C';
      $('wx-wind').textContent = g(weather.wind_ms, 1) + ' m/s ' + (weather.wind_dir ? compass(weather.wind_dir[i]) : '');
      $('wx-cloud').textContent = g(weather.cloud_low_pct, 0) + ' %';
      const s = weather.sun_el ? weather.sun_el[i] : null; $('wx-sun').textContent = s == null ? '--' : (s > 0 ? 'up ' : 'down ') + s.toFixed(0) + '°';
    }
    if (hoverIdx >= 0) showTip(hoverIdx, hoverXY);
  }
  if (fogUniforms) fogUniforms.uFrame.value = (f * night.step_min) / fogGrid.frame_min;
}
function compass(d) { return d == null ? '' : ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(d / 45) % 8]; }

/* ------------------------------------------------------------------ controls */
function setPlaying(p) { playing = p; $('play').innerHTML = p ? '&#10074;&#10074;' : '&#9654;'; $('play').setAttribute('aria-label', p ? 'Pause' : 'Play'); lastTs = 0; }
$('play').onclick = () => { if (night && tNow >= night.n_frames - 1.001) tNow = 0; setPlaying(!playing); };
$('slider').addEventListener('input', (e) => { tNow = parseFloat(e.target.value); applyTime(true); });
$('speed').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return; speed = +b.dataset.s;
  [...$('speed').children].forEach((c) => c.classList.toggle('on', c === b));
});
$('night').addEventListener('change', async (e) => { setPlaying(false); try { await selectNight(+e.target.value); } catch (err) { setStatus('Could not load this night: ' + err.message, true); } });
$('fogtoggle').addEventListener('change', (e) => fogLayers.forEach((m) => (m.visible = e.target.checked)));
$('bldtoggle').addEventListener('change', (e) => { if (buildingsMesh) buildingsMesh.visible = e.target.checked; });

/* camera flights */
let flight = null;
function flyTo(pos, tgt, dur = 2600) {
  flight = { t0: performance.now(), dur, p0: camera.position.clone(), t0v: controls.target.clone(), p1: pos.clone(), t1: tgt.clone() };
}
$('btn-city').onclick = () => flyTo(CITY.pos, CITY.target, 2200);
$('btn-route').onclick = () => {
  if (!ROUTE.start) { toast('Route not available'); return; }
  const m = ROUTE.mid;
  const k = innerWidth < innerHeight ? 1.9 : 1; flyTo(new THREE.Vector3(m.x + 520 * k, m.y + 430 * k, m.z + 560 * k), new THREE.Vector3(m.x, m.y - 20, m.z - 40), 2800);
};
const ease = (t) => (t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/* ------------------------------------------------------------------ picking */
const tip = $('tip'); let hoverIdx = -1, hoverXY = [0, 0];
const v3 = new THREE.Vector3();
function pick(cx, cy, maxPx) {
  if (!pts || !night) return -1;
  if (camera.position.distanceTo(controls.target) > 9000) return -1;
  camera.updateMatrixWorld(); const m = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse).elements;
  const W = innerWidth, H = innerHeight; let best = -1, bd = maxPx * maxPx;
  for (let k = 0; k < pts.N; k++) {
    const x = pts.f[k * 4], y = elevY[k], z = pts.f[k * 4 + 2];
    const w = m[3] * x + m[7] * y + m[11] * z + m[15]; if (w <= 0) continue;
    const sx = ((m[0] * x + m[4] * y + m[8] * z + m[12]) / w * .5 + .5) * W, sy = (1 - ((m[1] * x + m[5] * y + m[9] * z + m[13]) / w * .5 + .5)) * H;
    const d = (sx - cx) ** 2 + (sy - cy) ** 2; if (d < bd) { bd = d; best = k; }
  }
  return best;
}
function showTip(k, xy) {
  const v = wetNow[k], cat = CATS[v < 20 ? 0 : v < 50 ? 1 : v < 80 ? 2 : v < 95 ? 3 : 4];
  tip.innerHTML = `<span class="sw" style="background:${rgbCss(rampColor(v))}"></span><b>${Math.round(v)}</b> · ${cat}<br>${pts.streets[pts.st[k]]}, ${pts.side[k] ? 'right' : 'left'} side${pts.route[k] ? ' · on route' : ''}`;
  tip.hidden = false;
  tip.style.left = clamp(xy[0], 125, innerWidth - 125) + 'px'; tip.style.top = Math.max(xy[1], 90) + 'px';
}
function hideTip() { hoverIdx = -1; tip.hidden = true; }
let down = null;
canvas.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY, t: performance.now() }; if (e.pointerType === 'touch') hideTip(); });
canvas.addEventListener('pointerup', (e) => {
  if (!down) return; const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y), dt = performance.now() - down.t; down = null;
  if (moved < 8 && dt < 500 && e.pointerType !== 'mouse') {
    const k = pick(e.clientX, e.clientY, 30); if (k >= 0) { hoverIdx = k; hoverXY = [e.clientX, e.clientY - 6]; showTip(k, hoverXY); } else hideTip();
  }
});
let moveQ = 0;
canvas.addEventListener('pointermove', (e) => {
  if (e.pointerType !== 'mouse' || e.buttons) { if (e.buttons) hideTip(); return; }
  const now = performance.now(); if (now - moveQ < 40) return; moveQ = now;
  const k = pick(e.clientX, e.clientY, 16);
  if (k >= 0) { hoverIdx = k; hoverXY = [e.clientX, e.clientY - 4]; showTip(k, hoverXY); } else hideTip();
});
canvas.addEventListener('pointerleave', () => { if (!isTouch) hideTip(); });

/* ------------------------------------------------------------------ main loop */
function frame(ts) {
  requestAnimationFrame(frame);
  if (playing && night) {
    if (lastTs) { tNow += Math.min(0.1, (ts - lastTs) / 1000) * speed; if (tNow >= night.n_frames - 1) { tNow = night.n_frames - 1; setPlaying(false); } }
    lastTs = ts; applyTime(false);
  }
  if (flight) {
    const t = clamp((performance.now() - flight.t0) / flight.dur, 0, 1), e = ease(t);
    camera.position.lerpVectors(flight.p0, flight.p1, e); controls.target.lerpVectors(flight.t0v, flight.t1, e);
    if (t >= 1) flight = null;
  }
  controls.update();
  const dist = camera.position.distanceTo(controls.target);
  if (pointMat) pointMat.uniforms.uScale.value = clamp(dist / 450, 1, 12);
  const bs = clamp(dist / 450, 1, 80); beacons.forEach((b) => b.scale.set(bs * 1.5, 1, bs * 1.5));
  const showLbl = dist < 3500;
  for (const L of labelEls) {
    v3.copy(L.p).project(camera);
    const vis = showLbl && v3.z < 1 && Math.abs(v3.x) < 1.1 && Math.abs(v3.y) < 1.1;
    L.el.style.display = vis ? '' : 'none';
    if (vis) L.el.style.transform = `translate(-50%,-100%) translate(${(v3.x * .5 + .5) * innerWidth}px, ${(1 - (v3.y * .5 + .5)) * innerHeight}px)`;
  }
  renderer.render(scene, camera);
}

/* ------------------------------------------------------------------ boot */
(async function boot() {
  try {
    setStatus('Loading city…');
    meta = await withTimeout(getJSON('data/meta.json'), 15000, 'meta');
    const [pb, pj] = await Promise.all([withTimeout(getBuf('data/points.bin'), 30000, 'points'), getJSON('data/points.json')]);
    setStatus('Loading terrain and buildings…');
    await loadMeshes();
    buildPoints(pb, pj); buildRoute(pj);
    requestAnimationFrame(frame);
    setStatus('Loading replay…');
    let idx, dir;
    try { [idx, dir] = await loadIndex(); } catch (e) {
      console.warn(e); overlay.classList.add('hide'); toast('No replay data yet');
      $('night').innerHTML = '<option>No replay data</option>'; $('night').disabled = $('play').disabled = $('slider').disabled = true;
      return;
    }
    index = idx; base = dir; $('badge').hidden = !idx.mock; if (idx.mock) $('badge').textContent = 'Mock data';
    $('night').innerHTML = idx.nights.map((n, i) => `<option value="${i}">${n.label}</option>`).join('');
    const firstFog = Math.max(0, idx.nights.findIndex((n) => n.kind === 'fog')); $('night').value = firstFog;
    tNow = Math.min(60, idx.nights[firstFog].n_frames - 1);
    await selectNight(firstFog);
    window.__ready = true;
  } catch (e) {
    console.error(e); setStatus('Could not load the 3-D data: ' + e.message, true);
  }
})();

window.__dbg = { camera, controls, flyTo, get tNow() { return tNow; }, set tNow(v) { tNow = v; applyTime(true); }, setPlaying, pick };
