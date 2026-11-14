import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { createFog } from './fog.js';
import { createFogVol } from './fog2.js'; // beta volumetric fog, used only with ?beta=1&fog=vol (falls back to fog.js sheets by itself)
import { createRibbons } from './ribbons.js';
import { createSkater } from './skater.js';
import { ICON_SKATE_PLAY, ICON_FOG_PLAY, ICON_FOG_PAUSE } from './icons.js';

/* ------------------------------------------------------------------ config */
const SRC_Q = new URLSearchParams(location.search).get('src');
const SRC_DIRS = SRC_Q === 'mock' ? ['replay_mock/'] : SRC_Q === 'synth' ? ['replay_synth/'] : ['replay/', 'replay_mock/']; // synth = tools/make_synthetic_ribbons.py fixture
const QP = new URLSearchParams(location.search);
const UI2 = QP.get('ui') !== '1'; // redesigned chrome (body.ui2) for everyone; ?ui=1 keeps the old chrome (replay only), see UI_REDESIGN_PLAN.md
if (UI2) document.body.classList.add('ui2');
let BX = null; // data layers (beta_ui.js), loaded with import() only when UIX (promoted public output, or ?beta=1); null otherwise
let RNG = null; // ui2 live: visible frame window {lo, hi} of the time bar (null = every frame)
const LIVE = UI2 && !(QP.get('live') === '0' || QP.has('night') || QP.has('src')); // live data is the default; ?live=0, ?night=<id> or ?src= open the past-night replays
const BETA = QP.has('beta') && QP.get('beta') !== '0'; // ?beta=1: the beta preview channel (live_beta/, the NEXT settings under test) with a Beta tag; never the default
/* The data layers UI (beta_ui.js) is on for everyone whose public live output says it was built with the promoted settings (status.json "promoted", written by `build --promoted`).
   If the scheduled job falls back to the old settings (or DEATHFOG_LIVE_PROMOTE is false) that flag is absent and the page is exactly the old public page. ?beta=1 is unchanged. */
const PROMO_P = BETA ? Promise.resolve(null) : (() => { const ac = new AbortController(), tm = setTimeout(() => ac.abort(), 5000); return fetch('live/status.json', { cache: 'no-store', signal: ac.signal }).then((r) => (r.ok ? r.json() : null)).catch(() => null).finally(() => clearTimeout(tm)); })();
const PST = await PROMO_P; // the public live status (small); bootLive reuses it
const PROMO = !!(PST && PST.promoted && !BETA);
const UIX = BETA || PROMO; // data layers UI on
const RAMP = [[0, '#3a8696'], [18, '#6fb09f'], [24, '#f4e04d'], [50, '#f2c230'], [65, '#ee8a2c'], [80, '#d9531e'], [90, '#b3202f'], [100, '#7a0f3b']];
const CATS = ['dry', 'damp', 'slick', 'wet', 'icy-fog'];
const $ = (id) => document.getElementById(id);
$('flyhdr').querySelector('.ic').innerHTML = ICON_SKATE_PLAY;
$('play').querySelector('.ic').innerHTML = ICON_FOG_PLAY;
const ICON_PAUSE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M9 5v14M15 5v14"/></svg>';
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const EX = 1.5; // vertical exaggeration (terrain, buildings, streets, fog all share it)
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
document.querySelectorAll('.ramp').forEach((e) => { e.style.background = `linear-gradient(90deg, ${RAMP.map(([s, c]) => `${c} ${s}%`).join(',')})`; });
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

async function getJSON(url, opt) { const r = await fetch(url, opt); if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`); return r.json(); }
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

const hemi = new THREE.HemisphereLight(0x8fa8d8, 0x2a3348, 1.6); scene.add(hemi);
const moon = new THREE.DirectionalLight(0xc8d4ff, 2.2);
moon.position.set(-6000, 9000, 5000); scene.add(moon);
const amb = new THREE.AmbientLight(0x202840, 0.8); scene.add(amb);
const skater = createSkater({ THREE, reduceMotion: matchMedia('(prefers-reduced-motion: reduce)').matches }); // shown only during the route flyover
scene.add(skater.group);
/* Beta sky and perf HUD (sky.js, loaded only with ?beta=1&sky=1 and/or &hud=1): live sun, moon, stars for the time on screen. The normal page never fetches it. */
let SKY = null;
if (BETA && (QP.get('sky') !== '0' || QP.get('hud') === '1')) { // beta default: live sky; ?sky=0 turns it off
  import('./sky.js').then((m) => m.createSky({ THREE, scene, camera, renderer, starsUrl: 'stars.bin', sky: QP.get('sky') !== '0', hud: QP.get('hud') === '1' }))
    .then((s) => { SKY = s; if (FOGV) s.setExtra(() => fog.info()); if (night) skyTime(curF); }).catch((e) => console.warn('sky unavailable', e));
}
function skyTime(f) { if (SKY && night) { SKY.setTime(Date.parse(night.start_iso) + f * night.step_min * 60000); if (FOGV && SKY.light) fog.setLight(SKY.light); } }

let meta = null, pts = null, terrainH = null, T = null;
const CITY = { target: new THREE.Vector3(8300, 0, -8000), pos: new THREE.Vector3(3900, 8800, 7600) };
camera.position.copy(CITY.pos); controls.target.copy(CITY.target);

function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h; camera.fov = w < h ? 62 : 50; camera.updateProjectionMatrix();
  setBandVars();
}
function setBandVars() { // heights of the glass stacks, used to place popovers and keep tooltips clear of them
  const r = document.documentElement.style;
  r.setProperty('--ctl-h', $('controls').offsetHeight + 'px'); r.setProperty('--top-h', $('top').offsetHeight + 'px');
}
addEventListener('resize', resize); { const ro = new ResizeObserver(setBandVars); ro.observe($('controls')); ro.observe($('top')); } resize();

/* ------------------------------------------------------------------ terrain height lookup */
function buildHeightGrid(geom, step) {
  const p = geom.attributes.position;
  let maxI = 0, maxJ = 0;
  for (let k = 0; k < p.count; k++) { maxI = Math.max(maxI, Math.round(p.getX(k) / step)); maxJ = Math.max(maxJ, Math.round(-p.getZ(k) / step)); }
  const ni = maxI + 1, nj = maxJ + 1, h = new Float32Array(ni * nj).fill(-9999);
  for (let k = 0; k < p.count; k++) h[Math.round(p.getX(k) / step) * nj + Math.round(-p.getZ(k) / step)] = p.getY(k) * EX;
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
    terrainMesh = gl.scene; terrainMesh.scale.y = EX; scene.add(terrainMesh);
  } catch (e) { console.warn(e); toast('Terrain failed to load'); }
  try {
    buildingsMesh = buildBuildings(await withTimeout(getBuf('data/buildings.bin'), 60000, 'buildings'));
    buildingsMesh.scale.y = EX; scene.add(buildingsMesh);
    const bt = $('bldtoggle'); if (bt) buildingsMesh.visible = bt.checked;
  } catch (e) { console.warn(e); toast('Buildings failed to load'); }
}

/* ------------------------------------------------------------------ buildings (data/buildings.bin, see microclimate/meshes.py)
   Near-rectangular footprints are instanced oriented boxes (one InstancedMesh per 1 km tile so off-screen tiles are culled);
   complex or very large footprints are simplified true polygons, extruded into one mesh. Bases come from the terrain mesh. */
function buildBuildings(buf) {
  const dv = new DataView(buf);
  if (dv.getUint32(0, true) !== 0x31444c42) throw new Error('bad buildings.bin');
  const nb = dv.getUint32(4, true), np = dv.getUint32(8, true), nvt = dv.getUint32(12, true);
  const xyQ = dv.getFloat32(16, true), hQ = dv.getFloat32(20, true), wdQ = dv.getFloat32(24, true);
  let o = 28;
  const take = (T, n) => { const a = new T(buf, o, n); o += Math.ceil(n * T.BYTES_PER_ELEMENT / 4) * 4; return a; };
  const cx = take(Uint16Array, nb), cn = take(Uint16Array, nb), bb = take(Uint16Array, nb), bh = take(Uint16Array, nb);
  const bw = take(Uint8Array, nb), bd = take(Uint8Array, nb), ba = take(Uint8Array, nb);
  const pnv = take(Uint8Array, np), pb = take(Uint16Array, np), ph = take(Uint16Array, np), pv = take(Uint16Array, nvt * 2);

  const lo = new THREE.Color(0x56647f), hi = new THREE.Color(0xaabcdf), col = new THREE.Color();
  const tint = (h, k) => { // taller = lighter, plus a little per-building variation
    const t = Math.pow(clamp((h - 6) / 90, 0, 1), 0.7), j = 0.9 + 0.2 * (((k * 2654435761) >>> 8) % 1000) / 1000;
    return col.copy(lo).lerp(hi, t).multiplyScalar(j);
  };
  const group = new THREE.Group();

  // unit box, no bottom face: x,z in [-.5,.5], y in [0,1]
  const P = [], N = [];
  const quad = (a, b, c, d, n) => { for (const v of [a, b, c, a, c, d]) { P.push(...v); N.push(...n); } };
  quad([-.5, 0, .5], [.5, 0, .5], [.5, 1, .5], [-.5, 1, .5], [0, 0, 1]);
  quad([.5, 0, -.5], [-.5, 0, -.5], [-.5, 1, -.5], [.5, 1, -.5], [0, 0, -1]);
  quad([.5, 0, .5], [.5, 0, -.5], [.5, 1, -.5], [.5, 1, .5], [1, 0, 0]);
  quad([-.5, 0, -.5], [-.5, 0, .5], [-.5, 1, .5], [-.5, 1, -.5], [-1, 0, 0]);
  quad([-.5, 1, .5], [.5, 1, .5], [.5, 1, -.5], [-.5, 1, -.5], [0, 1, 0]);
  const boxGeo = new THREE.BufferGeometry();
  boxGeo.setAttribute('position', new THREE.Float32BufferAttribute(P, 3));
  boxGeo.setAttribute('normal', new THREE.Float32BufferAttribute(N, 3));
  const mat = new THREE.MeshLambertMaterial({ color: 0xffffff, emissive: 0x0b1224 });

  const tiles = new Map();
  for (let k = 0; k < nb; k++) {
    const key = Math.floor(cx[k] * xyQ / 1000) * 1000 + Math.floor(cn[k] * xyQ / 1000);
    let a = tiles.get(key); if (!a) tiles.set(key, a = []); a.push(k);
  }
  const dummy = new THREE.Object3D();
  for (const ids of tiles.values()) {
    const im = new THREE.InstancedMesh(boxGeo, mat, ids.length);
    ids.forEach((k, n) => {
      dummy.position.set(cx[k] * xyQ, bb[k] * hQ, -cn[k] * xyQ);
      dummy.rotation.set(0, ba[k] * Math.PI / 256, 0);
      dummy.scale.set(bw[k] * wdQ, bh[k] * hQ, bd[k] * wdQ);
      dummy.updateMatrix(); im.setMatrixAt(n, dummy.matrix); im.setColorAt(n, tint(bh[k] * hQ, k));
    });
    im.instanceMatrix.needsUpdate = true; im.computeBoundingSphere(); im.matrixAutoUpdate = false;
    group.add(im);
  }

  // true polygons: walls + flat roof, non-indexed, one mesh
  const pos = [], colr = [];
  let vi = 0;
  for (let k = 0; k < np; k++) {
    const n = pnv[k], y0 = pb[k] * hQ, y1 = y0 + ph[k] * hQ, c = tint(ph[k] * hQ, nb + k), ring = [];
    for (let i = 0; i < n; i++) ring.push(new THREE.Vector2(pv[(vi + i) * 2] * xyQ, -pv[(vi + i) * 2 + 1] * xyQ));
    vi += n;
    const push = (x, y, z) => { pos.push(x, y, z); colr.push(c.r, c.g, c.b); };
    for (let i = 0; i < n; i++) {
      const a = ring[i], b = ring[(i + 1) % n];
      push(a.x, y0, a.y); push(b.x, y0, b.y); push(b.x, y1, b.y);
      push(a.x, y0, a.y); push(b.x, y1, b.y); push(a.x, y1, a.y);
    }
    const tri = THREE.ShapeUtils.triangulateShape(ring, []);
    for (const t of tri) for (const i of t) push(ring[i].x, y1, ring[i].y);
  }
  if (pos.length) {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(colr, 3));
    g.computeVertexNormals();
    group.add(new THREE.Mesh(g, new THREE.MeshLambertMaterial({ vertexColors: true, emissive: 0x0b1224, side: THREE.DoubleSide })));
  }
  return group;
}

/* ------------------------------------------------------------------ street points */
let wetAttr = null, pointsMesh = null, pointMat = null, elevY = null, wetNow = null, routeAttr = null, routeBuf = null;
let PB = null, PJ = null; // raw points.bin / points.json, kept so the quad renderer can be built lazily (old path, ?ribbons=0)
function parsePts(bin, info) {
  const N = info.n;
  const f = new Float32Array(bin, 0, N * 4), side = new Uint8Array(bin, N * 16, N), route = new Uint8Array(bin, N * 17, N), st = new Uint16Array(bin, N * 18, N);
  const mask = new Uint16Array(bin, N * 20, N), zone = new Uint8Array(bin, N * 22, N); // bit i of mask = on route i (routes.json index)
  pts = { N, f, side, route, st, mask, zone, streets: info.streets, zones: info.zones };
}
function buildPoints() {
  if (pointsMesh) return;
  const { N, f, side, route } = pts;
  elevY = new Float32Array(N);
  const iPos = new Float32Array(N * 3), iBear = new Float32Array(N), iSide = new Float32Array(N), iRoute = new Float32Array(N);
  for (let k = 0; k < N; k++) {
    const x = f[k * 4], z = f[k * 4 + 2], y = Math.max(f[k * 4 + 1] * EX, terrainH ? terrainH.at(x, z) : 0) + 1.5;
    elevY[k] = y; iPos.set([x, y, z], k * 3); iBear[k] = f[k * 4 + 3]; iSide[k] = side[k]; iRoute[k] = 0;
  }
  const q = new THREE.InstancedBufferGeometry();
  q.setAttribute('position', new THREE.BufferAttribute(new Float32Array([-.5, 0, -.5, .5, 0, -.5, .5, 0, .5, -.5, 0, .5]), 3));
  q.setIndex([0, 2, 1, 0, 3, 2]); q.instanceCount = N;
  q.setAttribute('iPos', new THREE.InstancedBufferAttribute(iPos, 3));
  q.setAttribute('iBear', new THREE.InstancedBufferAttribute(iBear, 1));
  q.setAttribute('iSide', new THREE.InstancedBufferAttribute(iSide, 1));
  routeAttr = new THREE.InstancedBufferAttribute(iRoute, 1); routeAttr.setUsage(THREE.DynamicDrawUsage); routeBuf = iRoute; q.setAttribute('iRoute', routeAttr);
  wetNow = new Float32Array(N).fill(-1);
  wetAttr = new THREE.InstancedBufferAttribute(wetNow, 1); wetAttr.setUsage(THREE.DynamicDrawUsage);
  q.setAttribute('iWet', wetAttr);
  pointMat = new THREE.ShaderMaterial({
    uniforms: { uScale: { value: 1 }, uRouteCol: { value: new THREE.Color(0x5ef2ff) } },
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
      precision highp float; varying float vWet, vRoute; varying vec2 vUv; uniform vec3 uRouteCol;
      ${glslRamp}
      void main(){
        vec3 c = vWet < 0. ? vec3(.25,.28,.34) : ramp(vWet);
        if (vRoute > .5) { vec2 e = abs(vUv - .5); if (max(e.x * 1.0, e.y * .5) > .4 && e.x > .36) c = mix(c, uRouteCol, .9); }
        gl_FragColor = vec4(c, 1.);
      }`,
    side: THREE.DoubleSide, transparent: true, // transparent pass so it sorts after the fog sheets (alpha stays 1)
  });
  pointsMesh = new THREE.Mesh(q, pointMat); pointsMesh.frustumCulled = false; pointsMesh.renderOrder = 10; // after the fog sheets so street colours stay legible
  scene.add(pointsMesh);
}

/* ------------------------------------------------------------------ routes: picker, beacons, fly-along
   data/routes.json (written by `python -m microclimate routes`): per route poly [x,y,z] scene metres, s = cumulative metres,
   st = street index per vertex into streets, start/end {label, p}. All routes show as thin dim lines; the selected one is bright. */
let ROUTES = [], selRoute = -1, fly = null, flySpeed = 1;
const labelEls = [];
const beacons = [];
const mi = (m) => (m / 1609.344).toFixed(2), ft = (m) => Math.round(m * 3.28084).toLocaleString('en-US');
const routeStats = (R) => `${mi(R.len)} mi \u00b7 ${ft(R.def.drop_m)} ft drop` + (R.def.climb_m >= 3 ? ` \u00b7 ${ft(R.def.climb_m)} ft climb` : ''); // one wording: list, header, everywhere
const lift = (p) => new THREE.Vector3(p[0], Math.max(p[1] * EX, terrainH ? terrainH.at(p[0], p[2]) : 0) + 3, p[2]);
const shortName = (r, zones) => r.name.replace((zones.find((z) => z.id === r.zone) || {}).name + ': ', '');

function buildRoutes(rj) {
  ROUTES = rj.routes.map((def) => {
    const p3 = def.poly.map(lift), curve = new THREE.CatmullRomCurve3(p3, false, 'catmullrom', 0.1), segs = Math.min(1200, p3.length * 3);
    const mat = (op) => new THREE.MeshBasicMaterial({ color: def.color, transparent: op < 1, opacity: op });
    const dim = new THREE.Mesh(new THREE.TubeGeometry(curve, segs, 3.2, 5, false), mat(0.5));
    const hi = new THREE.Mesh(new THREE.TubeGeometry(curve, segs, 5.5, 6, false), mat(1));
    dim.renderOrder = hi.renderOrder = 3; hi.visible = false; scene.add(dim, hi);
    const xs = p3.map((p) => p.x), zs = p3.map((p) => p.z), ys = p3.map((p) => p.y);
    const ext = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...zs) - Math.min(...zs), 500);
    return { def, p3, curve, dim, hi, len: def.s[def.s.length - 1], bins: null,
      bb: { cx: (Math.min(...xs) + Math.max(...xs)) / 2, cz: (Math.min(...zs) + Math.max(...zs)) / 2, cy: ys.reduce((a, b) => a + b, 0) / ys.length, ext } };
  });
  for (const col of [0x5ef2ff, 0xf2c230]) {
    const m = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 700, 14, 1, true), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: .22, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
    m.visible = false; scene.add(m); beacons.push(m);
    const el = document.createElement('div'); el.className = 'lbl'; el.style.display = 'none'; $('labels').appendChild(el); labelEls.push({ el, p: new THREE.Vector3(), on: false });
  }
  // picker: grouped by zone, colour swatch per route
  const list = $('rlist'); list.innerHTML = '';
  { const b = document.createElement('button'); b.type = 'button'; b.className = 'ritem on'; b.dataset.city = '1'; b.setAttribute('role', 'option');
    b.innerHTML = '<span class="sw city"></span><span class="rn">Whole city</span><span class="rm">' + (UI2 ? 'All streets' : 'No route selected') + '</span><span class="ck">&#10003;</span>'; list.appendChild(b); }
  for (const z of rj.zones) {
    const rs = ROUTES.map((r, i) => [r, i]).filter(([r]) => r.def.zone === z.id);
    if (!rs.length) continue;
    const h = document.createElement('div'); h.className = 'rzone'; h.textContent = z.name; list.appendChild(h);
    for (const [r, i] of rs) {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'ritem'; b.dataset.i = i; b.setAttribute('role', 'option');
      b.innerHTML = `<span class="sw" style="background:${r.def.color}"></span><span class="rn"></span><span class="rm"></span><span class="ck">&#10003;</span>${UI2 ? '<span class="mp"></span><span class="wn"></span>' : ''}`;
      b.querySelector('.rn').textContent = shortName(r.def, rj.zones); b.querySelector('.rm').textContent = routeStats(r);
      list.appendChild(b);
    }
  }
  ZONES = rj.zones;
  $('rbtn').disabled = !ROUTES.length; clearRoute(false);
}
let ZONES = [];

/* no route selected: whole-city state (chip says "Whole city", route outline and beacons off, flyover button disabled) */
function clearRoute(goCity = true) {
  stopFly(); selRoute = -1;
  ROUTES.forEach((r) => { r.hi.visible = false; r.dim.visible = true; });
  if (rib) rib.setRoute(-1);
  if (routeBuf) { routeBuf.fill(0); routeAttr.needsUpdate = true; }
  beacons.forEach((b) => { b.visible = false; }); labelEls.forEach((l) => { l.on = false; l.el.style.display = 'none'; });
  $('rsw').style.background = 'transparent'; $('rsw').classList.add('city'); $('rlabel').textContent = 'Whole city'; $('rstat').textContent = UI2 ? 'Pick a route to fly it' : 'No route selected';
  $('flyhdr').disabled = true;
  [...$('rlist').querySelectorAll('.ritem')].forEach((b) => b.classList.toggle('on', !!b.dataset.city));
  flyUI(); closePicker();
  if (goCity) flyTo(CITY.pos, CITY.target, 2200);
  ui2Refresh(true);
}

function setRouteHighlight(i) {
  const R = ROUTES[i], ri = R.def.index;
  if (rib) rib.setRoute(ri, R.def.color);
  if (routeBuf) { for (let k = 0; k < pts.N; k++) routeBuf[k] = (pts.mask[k] >> ri) & 1; routeAttr.needsUpdate = true; pointMat.uniforms.uRouteCol.value.set(R.def.color); }
}

function selectRoute(i, flyCam = true) {
  const R = ROUTES[i]; if (!R) return;
  stopFly(); selRoute = i;
  ROUTES.forEach((r, k) => { r.hi.visible = k === i; r.dim.visible = k !== i; });
  setRouteHighlight(i);
  [R.def.start, R.def.end].forEach((e, j) => {
    const p = lift(e.p); beacons[j].position.set(p.x, p.y + 350, p.z); beacons[j].visible = true;
    labelEls[j].p.copy(p).add(new THREE.Vector3(0, 30, 0)); labelEls[j].el.textContent = e.label; labelEls[j].on = true;
  });
  $('rsw').classList.remove('city'); $('rsw').style.background = R.def.color; $('rlabel').textContent = shortName(R.def, ZONES); $('rstat').textContent = routeStats(R); $('flyhdr').disabled = false;
  [...$('rlist').querySelectorAll('.ritem')].forEach((b) => b.classList.toggle('on', !b.dataset.city && +b.dataset.i === i));
  flyUI(); closePicker();
  if (flyCam) flyToRoute(R);
  ui2Refresh(true);
}

function flyToRoute(R) {
  const por = innerWidth < innerHeight, b = R.bb, E = b.ext;
  const tgt = new THREE.Vector3(b.cx, b.cy - 10, b.cz);
  const dir = por ? new THREE.Vector3(0.3, 0, 0.95) : new THREE.Vector3(0.86, 0, 0.5), d = E * (por ? 1.25 : 0.95);
  const pos = new THREE.Vector3(b.cx + dir.x * d, b.cy + d * 0.55 + 80, b.cz + dir.z * d);
  if (terrainH) pos.y = Math.max(pos.y, terrainH.at(pos.x, pos.z) + 60);
  flyTo(pos, tgt, 2800);
}

/* popovers: route list and menu sheet, one open at a time. On desktop the sheet is a pinned side panel (open state persisted). */
const deskMQ = matchMedia('(min-width: 700px) and (min-height: 501px)');
const SIDE = () => deskMQ.matches && !UI2; // pinned side panel only in the old desktop layout; ui2 settings always open inside the dock
const LSK = 'df3.sheetOpen';
const lsGet = () => { try { return localStorage.getItem(LSK); } catch { return null; } };
const lsSet = (v) => { try { localStorage.setItem(LSK, v); } catch { /* private mode etc.: state just does not persist */ } };
function setSheet(open) {
  $('sheet').hidden = !open; $('sheettab').setAttribute('aria-expanded', String(open)); { const g = $('gear'); if (g) g.classList.toggle('on', open); }
  document.body.classList.toggle('sheet-closed', !open);
  if (open) closePicker();
  if (SIDE()) lsSet(open ? '1' : '0');
}
function closePicker() { $('rpop').hidden = true; $('rbtn').setAttribute('aria-expanded', 'false'); }
function openPicker(o) { $('rpop').hidden = !o; if (o) ui2List(true); $('rbtn').setAttribute('aria-expanded', String(o)); if (o && !SIDE()) setSheet(false); }
$('rbtn').onclick = () => openPicker($('rpop').hidden);
$('sheettab').onclick = () => setSheet($('sheet').hidden);
document.querySelector('#sheet .grab').onclick = () => setSheet(false);
/* legend strip: settings only. Weather strip: expands the weather grid AND opens the settings sheet on top of it (phone; the desktop panel is pinned). */
$('legend').onclick = () => setSheet($('sheet').hidden);
$('wxbtn').onclick = () => {
  if (UI2) return; // ui2: one fixed weather line, nothing expands
  const o = $('wxgrid').hidden; $('wxgrid').hidden = !o; $('wxbtn').setAttribute('aria-expanded', String(o));
  if (!SIDE()) setSheet(o);
};
$('poc').addEventListener('click', () => { closePicker(); if (!SIDE()) setSheet(false); }); // About and the other popovers: one at a time
function initSheet() { // phone: starts closed; desktop: starts open unless the user collapsed it
  const v = lsGet(); setSheet(SIDE() ? v !== '0' : false);
  if (SIDE()) lsSet(v === '0' ? '0' : '1');
}
deskMQ.addEventListener('change', () => { const v = lsGet(); setSheet(SIDE() ? v !== '0' : false); });
$('rlist').addEventListener('click', (e) => { const b = e.target.closest('.ritem'); if (!b) return; if (b.dataset.city) clearRoute(true); else selectRoute(+b.dataset.i, true); });
document.addEventListener('pointerdown', (e) => {
  if (!e.target.closest('#rpop, #rbtn')) closePicker();
  if (!SIDE() && !e.target.closest('#dock')) setSheet(false);
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closePicker(); if (!SIDE()) setSheet(false); stopFly(); } });

/* fly-along: chase camera that looks ahead along the route polyline */
const FLY = { back: 150, ahead: 130, up: 62 };
function flyUI() {
  const on = !!fly; document.body.classList.toggle('flying', on);
  { const fh = $('flyhdr'); fh.setAttribute('aria-label', on ? 'Exit the flyover' : 'Fly the route'); fh.title = fh.getAttribute('aria-label'); } if (on) { closePicker(); if (!SIDE()) setSheet(false); }
  $('flypill').hidden = !on; $('flypause').hidden = !on;
  if (on) {
    const paused = fly.paused;
    $('flypause').innerHTML = fly.done ? '&#8635;' : paused ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5l11 7-11 7z"/></svg>' : ICON_PAUSE;
    $('flypause').setAttribute('aria-label', fly.done ? 'Replay' : paused ? 'Resume' : 'Pause');
  }
  $('flyspeed').textContent = flySpeed + 'x'; $('flyspeed').dataset.s = flySpeed; $('flyspeed').setAttribute('aria-label', `Fly speed ${flySpeed}x, tap to change`);
}
function startFly() {
  const R = ROUTES[selRoute]; if (!R) { toast('Pick a route first'); return; }
  flight = null; hideTip(); controls.enabled = false;
  fly = { R, u: 0, paused: false, done: false, last: 0, cam: camera.position.clone(), tgt: controls.target.clone(), txt: '' };
  flyUI();
}
function stopFly() { skater.setVisible(false); if (!fly) return; fly = null; controls.enabled = true; controls.update(); flyUI(); }
function routeBins(R) {
  if (R.bins) return R.bins;
  const ri = R.def.index, p = R.def.poly, s = R.def.s, bins = new Map();
  for (let k = 0; k < G.n; k++) {
    if (!((G.mask[k] >> ri) & 1) || G.cls[k]) continue;
    const x = G.x[k], z = G.z[k]; let best = 1e18, bs = 0;
    for (let i = 0; i + 1 < p.length; i++) {
      const ax = p[i][0], az = p[i][2], bx = p[i + 1][0] - ax, bz = p[i + 1][2] - az, L2 = bx * bx + bz * bz || 1;
      const t = clamp(((x - ax) * bx + (z - az) * bz) / L2, 0, 1), dx = x - ax - t * bx, dz = z - az - t * bz, d2 = dx * dx + dz * dz;
      if (d2 < best) { best = d2; bs = s[i] + t * (s[i + 1] - s[i]); }
    }
    const b = Math.floor(bs / 20); if (!bins.has(b)) bins.set(b, []); bins.get(b).push(k);
  }
  return (R.bins = bins);
}
/* wetness (0-100, interpolated in time like the street colours) of the route samples within +-1 bin (20 m) of arc position s; null = no data */
function routeWetAt(R, s) {
  ensureWet();
  const bins = routeBins(R), b0 = Math.floor(s / 20); let sum = 0, n = 0;
  for (let b = b0 - 1; b <= b0 + 1; b++) for (const k of bins.get(b) || []) if (wetNow[k] >= 0) { sum += wetNow[k]; n++; }
  return n ? sum / n : null;
}
function flyReadout(s) {
  const R = fly.R, d = R.def; let lo = 0, hi = d.s.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (d.s[m] <= s) lo = m; else hi = m; }
  const street = d.streets[d.st[lo]] || '';
  const w0 = routeWetAt(R, s), w = w0 == null ? null : Math.round(w0); fly.w = w;
  const txt = `${street}|${w}`;
  if (txt !== fly.txt) {
    fly.txt = txt;
    $('fpst').textContent = street;
    $('fpw').innerHTML = w == null ? '' : `<span class="sw" style="background:${rgbCss(rampColor(w))}"></span><b>${w}</b>`;
    $('fpw').title = $('fpw').ariaLabel = w == null ? '' : `wetness ${w}`;
  }
}
function flyStep(ts) {
  if (!fly) return;
  const dt = fly.last ? Math.min(0.1, (ts - fly.last) / 1000) : 0; fly.last = ts;
  const R = fly.R, dur = clamp(R.len / 80, 15, 25);
  if (!fly.paused) { fly.u = Math.min(1, fly.u + dt * flySpeed / dur); if (fly.u >= 1) { fly.done = true; fly.paused = true; flyUI(); } }
  $('flyfill').style.width = (fly.u * 100).toFixed(1) + '%';
  const u = fly.u, p = 0.5 * u + 0.5 * u * u * (3 - 2 * u), s = p * R.len;
  const at = (m) => R.curve.getPointAt(clamp(m / R.len, 0, 1));
  const c = at(s - FLY.back), t = at(s + FLY.ahead);
  c.y = Math.max(c.y, terrainH ? terrainH.at(c.x, c.z) : c.y) + FLY.up; t.y += 6;
  const k = fly.last && dt ? 1 - Math.exp(-dt * 3.5) : 0;
  fly.cam.lerp(c, k); fly.tgt.lerp(t, k);
  camera.position.copy(fly.cam); controls.target.copy(fly.tgt);
  flyReadout(s); if (UI2) ui2Fly(s);
  placeSkater(R, s, dt, c);
}
/* The skateboarder rides the street ahead of the chase camera. Its arc position is chosen so that the board sits just above the
   glass control stack at the bottom of the screen (bisection on the projected height), its size is a fixed share of the screen height
   (so it reads from 390x844 to 1440x900), and it wobbles with the interpolated wetness at that point of the route. */
const _sp = new THREE.Vector3(), _sf = new THREE.Vector3();
function placeSkater(R, s, dt, camGoal) {
  if (!fly || fly.cam.distanceTo(camGoal) > 90) { skater.setVisible(false); return; } // not while the camera is still flying in
  camera.lookAt(controls.target); camera.updateMatrixWorld(); // same pose OrbitControls.update() is about to set
  const H = innerHeight, yT = Math.min(H, $(UI2 ? 'dock' : 'controls').getBoundingClientRect().top) - 18;
  const at = (m) => R.curve.getPointAt(clamp(m / R.len, 0, 1));
  const sy = (m) => { _sp.copy(at(m)); _sp.y -= 1.6; _sp.project(camera); return _sp.z > 1 ? -1e9 : (1 - (_sp.y * .5 + .5)) * H; };
  let lo = s - FLY.back + 14, hi = s + 40; // lo = near (lower on screen), hi = far
  let m = lo;
  if (sy(lo) > yT) { for (let i = 0; i < 12; i++) { m = (lo + hi) / 2; if (sy(m) > yT) lo = m; else hi = m; } m = hi; }
  m = clamp(m, 0, R.len);
  const P = at(m).clone(); P.y -= 1.6; // route line sits 3 m above the terrain; the ribbon surface is ~1.3 m
  _sf.copy(R.curve.getTangentAt(clamp(m / R.len, 0, 1))).normalize();
  const d = camera.position.distanceTo(P), hView = 2 * d * Math.tan(camera.fov * Math.PI / 360), px = clamp(0.13 * H, 64, 128);
  const scale = (px / H * hView) / 1.75;
  skater.setVisible(true);
  skater.update({ pos: P, forward: _sf, scale, w: routeWetAt(R, m), dt });
}
$('flyhdr').onclick = () => { if (fly) stopFly(); else startFly(); };
$('flypause').onclick = () => { if (!fly) return; if (fly.done) { fly.u = 0; fly.done = false; fly.paused = false; } else fly.paused = !fly.paused; flyUI(); };
$('flyspeed').addEventListener('click', () => { flySpeed = flySpeed === 1 ? 2 : 1; flyUI(); });

async function initRoutes() {
  try { buildRoutes(await withTimeout(getJSON('data/routes.json'), 15000, 'routes')); } catch (e) { console.warn(e); toast('Routes failed to load'); $('rbtn').disabled = true; }
}
function applyUrlRoute() { // ?route=<id> selects and flies to it, &fly=1 starts the fly-along at once
  if (!ROUTES.length) return;
  const q = new URLSearchParams(location.search), id = q.get('route'), i = ROUTES.findIndex((r) => r.def.id === id);
  if (i < 0) return; // default: whole city, nothing selected
  selectRoute(i, false);
  flyToRoute(ROUTES[i]); if (flight) flight.dur = 1;
  if (q.get('fly')) startFly();
}

/* ------------------------------------------------------------------ fog layer (see fog.js) */
const FOGV = BETA && QP.get('fog') !== 'sheets'; // beta default: volumetric fog; ?fog=sheets gets the old sheets back
const fog = (FOGV ? createFogVol : createFog)({ THREE, scene, EX, isTouch, getMeta: () => meta, getTerrainH: () => terrainH, toast });
if (FOGV) { // volumetric fog looks right at a lower density: slider 5 to 60 percent, starting at 45 (the sheets keep 30 to 200 at 100)
  const fd = document.getElementById('fogdens'); if (fd) { fd.min = 5; fd.max = 60; fd.value = 45; } fog.setDensity(0.45);
}

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

/* ---- street renderer mode: continuous ribbons (default when the night has streets_r + ribbons) or the old instanced quads (?ribbons=0).
   G is the renderer-neutral sample table used by picking, route binning and the fly readout. */
let rib = null, ribPath = '', ribMeta = null, useRib = false, wetDirty = true, curF = 0;
let G = { n: 0, x: null, y: null, z: null, mask: null, cls: null, st: null, side: null, names: [] };
async function ensureRibbons(n) {
  if (rib && ribPath === n.ribbons) return;
  const mp = n.ribbons.replace(/\.bin$/, '_meta.json');
  const [buf, m] = await Promise.all([withTimeout(getBuf(n.ribbons), 60000, 'ribbons'), getJSON(mp)]);
  const r = createRibbons({ THREE, scene, EX, terrainH, glslRamp, isTouch, alt: UIX });
  r.build(buf, m);
  if (rib) rib.dispose();
  rib = r; ribMeta = m; ribPath = n.ribbons; r.setDistrictVisible($('disttoggle').checked); r.setRouteColors(ROUTES.map((R) => R.def.color));
  if (selRoute >= 0) setRouteHighlight(selRoute);
}
function setMode(r) {
  useRib = r;
  if (r) {
    if (pointsMesh) pointsMesh.visible = false;
    rib.setVisible(true);
    const S = rib.samples, x = new Float32Array(S.S), y = new Float32Array(S.S), z = new Float32Array(S.S);
    for (let k = 0; k < S.S; k++) { x[k] = S.pos[k * 3]; y[k] = S.pos[k * 3 + 1]; z[k] = S.pos[k * 3 + 2]; }
    G = { n: S.S, x, y, z, mask: S.mask, cls: S.cls, st: S.st, side: S.side, names: ribMeta.streets };
    wetNow = new Float32Array(S.S).fill(-1);
  } else {
    buildPoints(); pointsMesh.visible = true; if (rib) rib.setVisible(false);
    const N = pts.N, x = new Float32Array(N), z = new Float32Array(N);
    for (let k = 0; k < N; k++) { x[k] = pts.f[k * 4]; z[k] = pts.f[k * 4 + 2]; }
    G = { n: N, x, y: elevY, z, mask: pts.mask, cls: new Uint8Array(N), st: pts.st, side: pts.side, names: pts.streets };
    wetNow = wetAttr.array;
  }
  ROUTES.forEach((R, k) => { R.bins = null; R.grp = null; if (!r) { R.hi.visible = k === selRoute; R.dim.visible = k !== selRoute; } });
  if (selRoute >= 0) setRouteHighlight(selRoute);
  wetDirty = true; document.body.classList.toggle('ribbons', r);
}
function ensureWet() { if (useRib && wetDirty) { rib.fillWet(wetNow, curF); if (rib2) rib2.fillWet(wetNow.subarray(rib.samples.S), curF); wetDirty = false; } }

async function selectNight(i) {
  const n = index.nights[i]; setStatus(`Loading ${nightLabel(n)}…`); overlay.classList.remove('hide');
  const flag = new URLSearchParams(location.search).get('ribbons');
  let useR = flag !== '0' && !!(n.streets_r && n.ribbons);
  if (flag === '1' && !useR) toast('No ribbon data for this night: using the old street quads');
  let sb = null, wx = null, ru8 = null;
  if (useR) { // continuous ribbons: ribbons.bin/meta once, streets_r.u8 per night
    try {
      setStatus('Loading street geometry…'); await ensureRibbons(n); setStatus(`Loading ${nightLabel(n)}: street wetness…`);
      const [rb, w2] = await Promise.all([withTimeout(getBuf(base + n.streets_r), 60000, 'ribbon street data'), getJSON(base + n.weather).catch(() => null)]);
      if (rb.byteLength !== n.n_frames * rib.samples.S) throw new Error(`streets_r.u8 has ${rb.byteLength} bytes, expected ${n.n_frames * rib.samples.S}`);
      ru8 = new Uint8Array(rb); wx = w2;
    } catch (e) {
      console.warn(e); if (!n.streets) throw e;
      toast('Ribbons unavailable (' + e.message + '): using the old street quads'); useR = false;
    }
  }
  if (!useR) {
    const want = n.n_frames * pts.N;
    let w2;
    [sb, w2] = await Promise.all([withTimeout(getBuf(base + n.streets), 60000, 'street data'), getJSON(base + n.weather).catch(() => null)]);
    if (sb.byteLength !== want) throw new Error(`streets.u8 has ${sb.byteLength} bytes, expected ${want}`);
    wx = w2;
  }
  setMode(useR);
  night = n; streets = sb ? new Uint8Array(sb) : null; weather = wx; fogGrid = n.fog_grid;
  if (useR) rib.setNight(ru8, n.n_frames);
  wetDirty = true;
  $('slider').max = n.n_frames - 1; $('wx-note').hidden = !n.approx_direction;
  setStatus('Loading fog…');
  const ok = n.fog && fogGrid ? await fog.build(base + n.fog, fogGrid) : false;
  $('fogtoggle').disabled = !ok; $('fogdens').disabled = !ok; if (!ok) $('fogtoggle').checked = false;
  fog.setVisible($('fogtoggle').checked);
  tNow = clamp(tNow, 0, n.n_frames - 1); applyTime(true);
  overlay.classList.add('hide'); $('status').textContent = ''; // hidden overlay must not keep a stale 'Loading' text
}

const nightLabel = (n) => n.label.replace(/^(\w{3}),? (\d{1,2}) (\w{3})/, '$1 $3 $2'); // 'Thu 1 Oct, fog night' -> 'Thu Oct 1, fog night'
/* wall-clock label from ISO string (ignores offset: shows local time as written) */
function wall(iso, addMin) {
  const m = /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)/.exec(iso); if (!m) return { time: '--:--', day: '' };
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5] + Math.round(addMin)));
  const p = (x) => String(x).padStart(2, '0');
  return { time: `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`, day: d.toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' }) + ' ' + d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }) }; // 'Thu Oct 1'
}

let lastFrameKey = -1;
function applyTime(force) {
  if (!night) return;
  const nf = night.n_frames, f = clamp(tNow, 0, nf - 1), a = Math.floor(f), b = Math.min(a + 1, nf - 1), u = f - a;
  const w = wall(night.start_iso, f * night.step_min);
  $('clock').textContent = w.time; $('clock-sub').textContent = w.day + ' ' + tzName(night.start_iso); if (UI2) ui2Clock(w, f);
  if (document.activeElement !== $('slider') || force) $('slider').value = f;
  const key = Math.round(f * 20);
  curF = f;
  skyTime(f);
  if (BX) BX.frame(f);
  if (useRib) { rib.setFrame(f); if (rib2) rib2.setFrame(f); } // three uniform writes, nothing else
  if (force || key !== lastFrameKey) {
    lastFrameKey = key;
    if (useRib) wetDirty = true;
    else {
      const N = pts.N, A = a * N, B = b * N;
      for (let k = 0; k < N; k++) wetNow[k] = (streets[A + k] * (1 - u) + streets[B + k] * u) / 100 * 100;
      wetAttr.needsUpdate = true;
    }
    if (weather) {
      const i = Math.round(f), g = (arr, d = 0) => (arr && arr[i] != null ? arr[i].toFixed(d) : '--');
      const gF = (arr) => (arr && arr[i] != null ? (arr[i] * 9 / 5 + 32).toFixed(0) : '--'); // deg C to deg F
      $('wx-temp').textContent = gF(weather.temp_c) + ' °F'; $('wx-dew').textContent = gF(weather.dewpoint_c) + ' °F';
      $('wx-wind').textContent = (weather.wind_ms && weather.wind_ms[i] != null ? (weather.wind_ms[i] * 2.23694).toFixed(0) : '--') + ' mph ' + (weather.wind_dir ? compass(weather.wind_dir[i]) : '');
      $('wx-cloud').textContent = g(weather.cloud_low_pct, 0) + ' %';
      if (FOGV) fog.setWeather({ top: weather.fog_top_m ? weather.fog_top_m[i] : null });
      const s = weather.sun_el ? weather.sun_el[i] : null; $('wx-sun').textContent = s == null ? '--' : (s > 0 ? 'up ' : 'down ') + minus(s.toFixed(0)) + '°';
      const t = $('wx-temp').textContent.replace(' ', ''), dp = $('wx-dew').textContent.replace(' °F', '°F'), wd = $('wx-wind').textContent.trim() + ($('wx-note').hidden ? '' : '*');
      if (LV && LV.el) liveWx(i);
      if (UI2) $('wxline').innerHTML = ui2WxLine(i); else $('wxline').textContent = `${t} · dew ${dp} · ${wd} · ${$('wx-cloud').textContent.replace(' ', '')} cloud · sun ${s == null ? '--' : minus(Math.round(s)) + '°'}`;
    }
    if (hoverIdx >= 0) showTip(hoverIdx, hoverXY);
    ui2Refresh(false);
  }
  fog.setFrame((f * night.step_min) / fogGrid.frame_min);
}
function minus(v) { return String(v).replace('-', '\u2212'); } // true minus sign
function tzName(iso) { return /-07:00$/.test(iso) ? 'PDT' : /-08:00$/.test(iso) ? 'PST' : ''; }
function compass(d) { return d == null ? '' : ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(d / 45) % 8]; }

/* ------------------------------------------------------------------ controls */
function setPlaying(p) { playing = p; $('play').classList.toggle('playing', p); $('play').querySelector('.ic').innerHTML = p ? ICON_FOG_PAUSE : ICON_FOG_PLAY; $('play').setAttribute('aria-label', p ? 'Pause' : 'Play'); lastTs = 0; }
$('play').onclick = () => { if (night && tNow >= (RNG ? RNG.hi : night.n_frames - 1) - 0.001) tNow = RNG ? RNG.lo : 0; setPlaying(!playing); };
$('slider').addEventListener('input', (e) => { tNow = parseFloat(e.target.value); applyTime(true); });
$('speed').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return; speed = +b.dataset.s;
  [...$('speed').children].forEach((c) => c.classList.toggle('on', c === b));
});
$('night').addEventListener('change', async (e) => { if (e.target.value === 'live' || String(e.target.value).startsWith('r:')) { if (!(LIVE && e.target.value === 'live')) goNight(e.target.value); return; } setPlaying(false); try { await selectNight(+e.target.value); } catch (err) { setStatus('Could not load this night: ' + err.message, true); } });
$('fogtoggle').addEventListener('change', (e) => fog.setVisible(e.target.checked));
$('fogdens').addEventListener('input', (e) => fog.setDensity(+e.target.value / 100));
/* "District streets": every street of Potrero Hill District and Sunset District as a wetness gradient (default on). */
$('disttoggle').addEventListener('change', (e) => { if (rib) rib.setDistrictVisible(e.target.checked); hideTip(); });
$('bldtoggle').addEventListener('change', (e) => { if (buildingsMesh) buildingsMesh.visible = e.target.checked; });

/* camera flights */
let flight = null;
function flyTo(pos, tgt, dur = 2600) {
  flight = { t0: performance.now(), dur, p0: camera.position.clone(), t0v: controls.target.clone(), p1: pos.clone(), t1: tgt.clone() };
}
const ease = (t) => (t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/* ------------------------------------------------------------------ picking */
const tip = $('tip'); let hoverIdx = -1, hoverXY = [0, 0];
const v3 = new THREE.Vector3();
function pick(cx, cy, maxPx) {
  if (!G.n || !night) return -1;
  if (camera.position.distanceTo(controls.target) > 9000) return -1;
  camera.updateMatrixWorld(); const m = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse).elements;
  const W = innerWidth, H = innerHeight; let best = -1, bd = maxPx * maxPx;
  const skipDist = useRib && (!rib.uniforms.uDistVis.value || rib.uniforms.uDistFade.value < 0.3); // a hidden district layer is not pickable
  const skipCity = !!LV && !LV.cityOn; // live: a hidden city layer is not pickable
  for (let k = 0; k < G.n; k++) {
    if (G.cls[k] && skipDist) continue;
    if (skipCity && k >= rib.samples.S) continue;
    const x = G.x[k], y = G.y[k], z = G.z[k];
    const w = m[3] * x + m[7] * y + m[11] * z + m[15]; if (w <= 0) continue;
    const sx = ((m[0] * x + m[4] * y + m[8] * z + m[12]) / w * .5 + .5) * W, sy = (1 - ((m[1] * x + m[5] * y + m[9] * z + m[13]) / w * .5 + .5)) * H;
    const d = (sx - cx) ** 2 + (sy - cy) ** 2; if (d < bd) { bd = d; best = k; }
  }
  return best;
}
const LONG_PRESS_MS = 450, TAP_SLOP = 8;
const longPress = () => !!BX; // data-layers UI: the street card needs a press and hold, with a finger or the mouse (no hover card, a short tap or click does nothing); the plain page keeps tap-to-show and hover
function showTip(k, xy) {
  ensureWet();
  const v = wetNow[k], cat = CATS[v < 20 ? 0 : v < 50 ? 1 : v < 80 ? 2 : v < 95 ? 3 : 4];
  const when = night ? wall(night.start_iso, curF * night.step_min).time : '';
  tip.innerHTML = v < 0 ? `no data<br>${G.names[G.st[k]]}` : `<span class="sw" style="background:${rgbCss(rampColor(v))}"></span><b>${Math.round(v)}</b> wetness · ${cat}${useRib ? ' (modelled)' : ''}<br>${G.names[G.st[k]]}, ${G.side[k] ? 'right' : 'left'} side${selRoute >= 0 && ((G.mask[k] >> ROUTES[selRoute].def.index) & 1) ? ' · on route' : G.cls[k] ? ' · district street' : ''} · ${when}`;
  if (BX) { const bh = BX.tip(k, v, when); if (bh) tip.innerHTML = bh; }
  tip.hidden = false;
  const topH = $('top').getBoundingClientRect().bottom, botY = (BX ? $('dock') : $('controls')).getBoundingClientRect().top; // keep the tooltip clear of the glass stacks
  tip.style.left = clamp(xy[0], 125, innerWidth - 125) + 'px'; tip.style.top = clamp(xy[1], topH + 70, botY - 6) + 'px';
}
function hideTip() { hoverIdx = -1; tip.hidden = true; }
let down = null;
let pressTimer = 0, pressFired = 0;
const cancelPress = () => { clearTimeout(pressTimer); pressTimer = 0; };
canvas.addEventListener('pointerdown', (e) => {
  if (fly) stopFly(); down = { x: e.clientX, y: e.clientY, t: performance.now() }; if (e.pointerType === 'touch' || longPress()) hideTip();
  cancelPress();
  if (longPress() && e.isPrimary && (e.pointerType !== 'mouse' || e.button === 0)) { // press and hold: the card appears under the finger if it has not moved
    const x = e.clientX, y = e.clientY;
    pressTimer = setTimeout(() => {
      pressTimer = 0; if (!down) return;
      const k = pick(x, y, 30); if (k < 0) return;
      pressFired = performance.now(); hoverIdx = k; hoverXY = [x, y - 6]; showTip(k, hoverXY);
      try { if (navigator.vibrate) navigator.vibrate(8); } catch (err) { /* no haptics */ }
    }, LONG_PRESS_MS);
  }
});
canvas.addEventListener('pointercancel', cancelPress);
canvas.addEventListener('contextmenu', (e) => { if (longPress() && performance.now() - pressFired < 1500) e.preventDefault(); }); // Android opens a menu on a long press
canvas.addEventListener('pointerup', (e) => {
  cancelPress();
  if (!down) return; const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y), dt = performance.now() - down.t; down = null;
  if (moved < TAP_SLOP && dt < 500 && e.pointerType !== 'mouse') {
    if (longPress()) return; // a short tap shows nothing; a long press already showed (and keeps) the card
    const k = pick(e.clientX, e.clientY, 30); if (k >= 0) { hoverIdx = k; hoverXY = [e.clientX, e.clientY - 6]; showTip(k, hoverXY); } else hideTip();
  }
});
let moveQ = 0;
canvas.addEventListener('pointermove', (e) => {
  if (down && pressTimer && Math.hypot(e.clientX - down.x, e.clientY - down.y) > TAP_SLOP) cancelPress(); // a drag is not a press
  if (e.pointerType !== 'mouse' || e.buttons) { // dragging hides the card; with press and hold, finger jitter after the card shows must not
    if (e.buttons && (!longPress() || (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 3 * TAP_SLOP))) hideTip();
    return;
  }
  if (longPress()) return; // no hover card: press and hold instead
  const now = performance.now(); if (now - moveQ < 40) return; moveQ = now;
  const k = pick(e.clientX, e.clientY, 16);
  if (k >= 0) { hoverIdx = k; hoverXY = [e.clientX, e.clientY - 4]; showTip(k, hoverXY); } else hideTip();
});
canvas.addEventListener('pointerleave', () => { if (!isTouch) hideTip(); });

/* ------------------------------------------------------------------ main loop */
function frame(ts) {
  requestAnimationFrame(frame);
  if (playing && night) {
    if (lastTs) {
      tNow += Math.min(0.1, (ts - lastTs) / 1000) * speed;
      if (RNG) { if (tNow >= RNG.hi) { if (RNG.hi < LV.hiWant) { tNow = RNG.hi; setPlaying(false); } else tNow = RNG.lo + Math.min(tNow - RNG.hi, RNG.hi - RNG.lo); } } // live: loops inside the chosen range (stops if the next days are still loading)
      else if (tNow >= night.n_frames - 1) { tNow = night.n_frames - 1; setPlaying(false); }
    }
    lastTs = ts; applyTime(false);
  }
  if (flight) {
    const t = clamp((performance.now() - flight.t0) / flight.dur, 0, 1), e = ease(t);
    camera.position.lerpVectors(flight.p0, flight.p1, e); controls.target.lerpVectors(flight.t0v, flight.t1, e);
    if (t >= 1) flight = null;
  }
  flyStep(ts);
  controls.update();
  const dist = camera.position.distanceTo(controls.target);
  fog.update(camera, dist, ts / 1000);
  if (pointMat && !useRib) pointMat.uniforms.uScale.value = clamp(dist / 450, 1, 12);
  if (useRib) {
    rib.setLod(dist);
    if (rib2) { rib2.setLod(dist); rib2.uniforms.uDistFade.value = 1; } // live: city streets stay drawn in the whole-city view
    // the ribbon shader draws every route (dashed unless selected), so the old solid route tubes stay hidden
    ROUTES.forEach((r) => { r.hi.visible = false; r.dim.visible = false; });
  }
  const bs = clamp(dist / 450, 1, 80); beacons.forEach((b) => b.scale.set(bs * 1.5, 1, bs * 1.5));
  const showLbl = dist < 3500;
  const edge = innerWidth < innerHeight ? 0.9 : 1.1;
  for (const L of labelEls) {
    v3.copy(L.p).project(camera);
    const vis = L.on && showLbl && v3.z < 1 && Math.abs(v3.x) < edge && Math.abs(v3.y) < 1.1;
    L.el.style.display = vis ? '' : 'none';
    if (vis) L.el.style.transform = `translate(-50%,-100%) translate(${clamp((v3.x * .5 + .5) * innerWidth, 70, innerWidth - 70)}px, ${(1 - (v3.y * .5 + .5)) * innerHeight}px)`;
  }
  if (BX) BX.render(ts);
  if (SKY) SKY.update(ts);
  if (FOGV) fog.renderFrame(renderer, scene, camera); else renderer.render(scene, camera);
}

/* ------------------------------------------------------------------ live preview (?live=1), see PLAN_LIVE_DATA.md section 9
   live/index.json + day0..2.u8 (frame-major, columns = data/ribbons.bin samples then live/ribbons_city.bin samples, hourly frames),
   fog0..2.png, status.json. Day 0 is shown first; days 1-2 load in the background and the bar grows when they arrive. */
let rib2 = null; // city ribbons (live only)
const loadImg = (u) => new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error('image ' + u)); i.src = u; });
const ptFmt = (iso, o) => new Date(iso).toLocaleString('en-US', { timeZone: 'America/Los_Angeles', hour12: false, ...o });
let LV = null; // { idx, st, ST, S1, el }
const LQ = new URLSearchParams(location.search).get('livetest'); // preview testing: 'delayed' | 'forecast'
function liveSetData(u8, nf) { LV.u8 = u8; LV.nf = nf; rib.setNight(u8, nf, 0, LV.ST); rib2.setNight(u8, nf, LV.S1, LV.ST); wetDirty = true; }
function liveExtendG() { // picking / tooltips / route bins see the city samples too (appended after the district+route samples)
  const a = rib.samples, b = rib2.samples, n = a.S + b.S, off = ribMeta.streets.length;
  const cat = (T, p, q) => { const o = new T(n); o.set(p); o.set(q, a.S); return o; };
  const x = new Float32Array(n), y = new Float32Array(n), z = new Float32Array(n), st = new Uint32Array(n);
  for (let k = 0; k < a.S; k++) { x[k] = a.pos[k * 3]; y[k] = a.pos[k * 3 + 1]; z[k] = a.pos[k * 3 + 2]; st[k] = a.st[k]; }
  for (let k = 0; k < b.S; k++) { x[a.S + k] = b.pos[k * 3]; y[a.S + k] = b.pos[k * 3 + 1]; z[a.S + k] = b.pos[k * 3 + 2]; st[a.S + k] = b.st[k] + off; }
  G = { n, x, y, z, mask: cat(Uint16Array, a.mask, b.mask), cls: cat(Uint8Array, a.cls, b.cls), st, side: cat(Uint8Array, a.side, b.side), names: ribMeta.streets.concat(rib2Meta.streets) };
  wetNow = new Float32Array(n).fill(-1);
}
let rib2Meta = null;
async function composeFog(idx) { // the three day PNGs -> one contiguous tile sheet (frame i = tile i), so the shader can interpolate across day boundaries
  const g = idx.fog_grid, imgs = await Promise.all(idx.fog_files.map((f) => loadImg(base + f + LV.V))), rows = Math.ceil(idx.n_frames / g.cols);
  const cv = document.createElement('canvas'); cv.width = g.cols * g.w; cv.height = rows * g.h;
  const cx = cv.getContext('2d');
  idx.frame_ranges.forEach(([a, b], k) => { for (let j = 0; j < b - a; j++) { const gi = a + j; cx.drawImage(imgs[k], (j % g.cols) * g.w, Math.floor(j / g.cols) * g.h, g.w, g.h, (gi % g.cols) * g.w, Math.floor(gi / g.cols) * g.h, g.w, g.h); } });
  const blob = await new Promise((r) => cv.toBlob(r, 'image/png'));
  return { url: URL.createObjectURL(blob), grid: { ...g, rows } };
}
const ageNow = () => (LQ === 'delayed' ? 130 : LV && LV.real && LV.asof ? (Date.now() - Date.parse(LV.asof)) / 60000 : null);
const fmtAge = (m) => { m = Math.round(m); return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`; };
function liveBar(idx, st) { // ui2 only: status inputs, data notices in the dock, City streets switch, weather extras
  const asof = st && st.generated_iso ? st.generated_iso : idx.data_as_of;
  const real = !!(st && st.scheduled); // false for the static preview snapshot: it never updates, so its age is not shown (a banner would be permanent)
  const ageMin = LQ === 'delayed' ? 130 : real && asof ? (Date.now() - Date.parse(asof)) / 60000 : null;
  LV.asofTxt = asof ? ptFmt(asof, { weekday: 'short', hour: '2-digit', minute: '2-digit' }).replace(',', '') : 'unknown time';
  LV.real = real; LV.ageMin = ageMin; LV.asof = asof;
  const fo = LQ === 'forecast' || !!(st && st.mode && st.mode !== 'observations_nudged') || idx.observed_until_frame == null;
  const stack = document.createElement('div'); stack.id = 'lvstack'; $('dock').prepend(stack);
  const bl = BETA ? String((st && st.beta && st.beta.label) || 'settings not stated').replace(/[<>&"]/g, '') : PROMO ? String((st && st.promoted && st.promoted.label) || '').replace(/[<>&"]/g, '') : '';
  if (BETA) document.title = 'Beta preview · ' + document.title;
  LV.betaLabel = bl; LV.betaBanner = BETA ? `<div class="lvb warn">Beta preview: ${bl}. Not the public forecast.</div>` : ''; // the dock banner is the fallback only: beta_ui.js shows the Beta tag on the status line and a note in settings instead
  stack.innerHTML = (ageMin != null && ageMin > 90 ? `<div class="lvb warn">Data delayed. Last update was ${fmtAge(ageMin)} ago. Showing the most recent run.</div>` : '') +
    (fo ? '<div class="lvb info">Forecast only. No recent station or satellite readings, so nothing is corrected by observations.</div>' : '');
  // City streets layer switch (next to District streets), default on
  const lab = document.createElement('label'); lab.innerHTML = '<input type="checkbox" id="citytoggle" checked><span>City streets</span>';
  $('disttoggle').closest('label').before(lab);
  $('citytoggle').addEventListener('change', (e) => { LV.cityOn = e.target.checked; rib2.setVisible(e.target.checked); hideTip(); });
  // weather: rain row (kept for the hidden grid) and the source line
  const rain = document.createElement('div'); rain.className = 'wx'; rain.innerHTML = '<span class="k">Rain</span><span class="v" id="wx-rain">--</span>';
  $('wxgrid').insertBefore(rain, $('wx-note'));
  const src = document.createElement('div'); src.className = 'note'; src.id = 'wx-src'; $('wxgrid').appendChild(src);
  // Time range switch (settings, between Night/Speed and Layers): total bar length around the data time
  const hrs = Math.round((idx.n_frames - 1) * idx.step_min / 60), row = document.createElement('div'); row.className = 'sec'; row.id = 'rngrow';
  row.innerHTML = `<h4>Time range</h4><div class="seg" id="range" role="group" aria-label="Time range"><button type="button" data-r="24">24 h</button><button type="button" data-r="48">48 h</button><button type="button" data-r="all">All \u00b7 ${hrs} h</button></div><div class="note" id="rngnote"></div>`;
  $('sheet').querySelector('.two.sec').after(row);
  $('range').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return; LV.range = b.dataset.r;
    try { localStorage.setItem(RNG_KEY, LV.range); } catch { /* not persisted */ }
    applyRange(); applyTime(true); ui2Track(); ui2Refresh(true);
  });
  LV.el = { ld: $('trkld'), load: { textContent: '' } };
  $('trkld').hidden = false;
  new ResizeObserver(ui2Track).observe($('trk')); setBandVars();
}
function liveWx(i) { // rain row + source line for the selected hour
  const p = weather && weather.precip_mm ? weather.precip_mm[i] : null;
  $('wx-rain').textContent = p == null ? '--' : p.toFixed(1) + ' mm/h';
  const fr = LV.idx.observed_until_frame, obs = fr != null && i <= fr;
  $('wx-src').textContent = obs ? 'Observed hours: model nudged to Fort Point (CO-OPS), KSFO/KOAK/KHAF and GOES-18 satellite.' : 'Forecast hours: NWS RRFS model via Open-Meteo.';
}
const RNG_OPTS = { 24: [6, 18], 48: [12, 36], all: null }, RNG_KEY = 'df_live_range'; // [frames back, frames ahead] of the data tick
function rangeLoad() { try { const v = localStorage.getItem(RNG_KEY); if (v != null && v in RNG_OPTS) return v; } catch { /* no storage */ } return '24'; }
function applyRange() { // slider min/max = visible window; frame data and shader time are untouched. Days not loaded yet limit the right end until they arrive.
  if (!LV || !night) return;
  const nF = LV.idx.n_frames, o = RNG_OPTS[LV.range], a = LV.idx.observed_until_frame != null ? LV.idx.observed_until_frame : LV.anchor;
  const lo = o ? Math.max(0, a - o[0]) : 0, want = o ? Math.min(nF - 1, a + o[1]) : nF - 1, hi = Math.min(want, night.n_frames - 1);
  LV.lo = lo; LV.hiWant = want; RNG = { lo, hi };
  const sl = $('slider'); sl.min = lo; sl.max = hi; tNow = clamp(tNow, lo, hi);
  const t0 = Date.parse(LV.idx.start_iso), H = (f) => ptFmt(new Date(t0 + f * LV.idx.step_min * 60000).toISOString(), { weekday: 'short', hour: 'numeric', hour12: true }).replace(',', '');
  LV.spanTxt = H(lo) + ' \u2013 ' + H(want);
  const n = $('rngnote'); if (n) n.textContent = `${LV.spanTxt} \u00b7 ${Math.round((a - lo) * LV.idx.step_min / 60)} h back \u00b7 ${Math.round((want - a) * LV.idx.step_min / 60)} h ahead`;
  const rg = $('range'); if (rg) [...rg.children].forEach((b) => b.classList.toggle('on', b.dataset.r === LV.range));
  trkSig = ''; stSig = '';
}
function liveRouteCols(R) { const ks = []; for (const a of routeBins(R).values()) for (const k of a) ks.push(k); return ks; }
function livePeak(R) { // frame where the route's mean wetness is highest inside the visible time range (loaded frames only)
  const lo = RNG ? RNG.lo : 0, hi = Math.min(RNG ? LV.hiWant : LV.nf - 1, LV.nf - 1), key = lo + '|' + hi + '|' + LV.nf;
  if (R.lvPk && R.lvPk.key === key) return R.lvPk.f;
  const ks = liveRouteCols(R); let bf = -1, bv = -1;
  for (let f = lo; f <= hi; f++) {
    let s = 0, n = 0; const o = f * LV.ST;
    for (const k of ks) { const v = LV.u8[o + k]; if (v !== 255) { s += v; n++; } }
    if (n && s / n > bv) { bv = s / n; bf = f; }
  }
  R.lvPk = { key, f: bf }; return bf;
}
const goNight = (v) => { // the Night list switches between live and a past night by reloading in that mode
  const q = new URLSearchParams(location.search); for (const k of ['live', 'night', 'liveerr', 'route']) q.delete(k);
  if (v !== 'live') q.set('night', v.replace(/^r:/, ''));
  const s = q.toString(); location.href = location.pathname + (s ? '?' + s : '');
};
function liveNightList() { // live first; the past nights are added once their index has loaded
  const sel = $('night'); sel.innerHTML = `<option value="live">${UIX ? 'Live' : 'Live (beta)'}</option>`;
  loadIndex().then(([ri]) => {
    sel.insertAdjacentHTML('beforeend', '<optgroup label="Past nights">' + ri.nights.map((n) => `<option value="r:${n.id}">${nightLabel(n)}${n.approx_direction ? ' *' : ''}</option>`).join('') + '</optgroup>');
    sel.value = 'live';
  }).catch((e) => console.warn('past nights list unavailable', e));
}
async function bootLive() {
  base = BETA ? 'live_beta/' : 'live/';
  const st = !BETA && PST ? PST : await getJSON(base + 'status.json', { cache: 'no-store' }).catch(() => null);
  const V = st && st.generated_iso ? '?v=' + encodeURIComponent(st.generated_iso) : ''; // a new run is a new URL, so index, days and fog always belong together
  const idx = await withTimeout(getJSON(base + 'index.json' + V), 15000, 'live index');
  const S1 = idx.ribbons_n, S2 = idx.ribbons_city_n, ST = S1 + S2, nF = idx.n_frames, f0 = idx.frame_ranges[0][1] - idx.frame_ranges[0][0];
  LV = { idx, st, ST, S1, V, el: null, cityOn: true, u8: null, nf: 0, range: rangeLoad(), anchor: 0, lo: 0, hiWant: 0, spanTxt: '' };
  if (LQ === 'forecast') idx.observed_until_frame = null;
  index = { nights: [idx] }; liveNightList();
  setStatus('Loading street geometry…');
  await ensureRibbons({ ribbons: 'data/ribbons.bin' });
  if (rib.samples.S !== S1) throw new Error(`live index expects ${S1} district samples, ribbons.bin has ${rib.samples.S}`);
  const [cb, cm] = await Promise.all([withTimeout(getBuf(base + idx.ribbons_city), 60000, 'city ribbons'), getJSON(base + idx.ribbons_city_meta)]);
  rib2 = createRibbons({ THREE, scene, EX, terrainH, glslRamp, isTouch, alt: UIX }); rib2.build(cb, cm); rib2.mesh.renderOrder = 9; rib2Meta = cm; // under the district/route ribbons
  setStatus('Loading live street wetness…');
  const [d0, wx] = await Promise.all([withTimeout(getBuf(base + idx.frame_files[0] + V), 60000, 'day 0'), getJSON(base + idx.weather + V).catch(() => null)]);
  if (d0.byteLength !== f0 * ST) throw new Error(`${idx.frame_files[0]} has ${d0.byteLength} bytes, expected ${f0 * ST}`);
  setMode(true); liveExtendG();
  night = { ...idx, label: 'Live snapshot', n_frames: f0 }; weather = wx;
  liveSetData(new Uint8Array(d0), f0);
  fogGrid = { ...idx.fog_grid, rows: idx.fog_grid.rows[0] };
  setStatus('Loading fog…');
  const ok = await fog.build(base + idx.fog_files[0] + V, fogGrid);
  $('fogtoggle').disabled = !ok; $('fogdens').disabled = !ok; if (!ok) $('fogtoggle').checked = false;
  fog.setVisible($('fogtoggle').checked);
  $('wx-note').hidden = true;
  const asof = st && st.generated_iso ? Date.parse(st.generated_iso) : Date.now(), t0 = Date.parse(idx.start_iso);
  LV.anchor = clamp(Math.round((asof - t0) / 3600000), 0, nF - 1); // frame of the data time; used when there is no observed_until_frame
  tNow = clamp(LV.anchor, 0, f0 - 1);
  liveBar(idx, st);
  await initBeta();
  applyRange();
  applyTime(true); ui2Track();
  overlay.classList.add('hide'); $('status').textContent = '';
  liveRest(idx, d0, f0, nF, ST); // background: days 1-2 (not awaited)
}
async function liveRest(idx, d0, f0, nF, ST) {
  try {
    LV.el.load.textContent = 'loading the next days…';
    const rest = await Promise.all(idx.frame_files.slice(1).map((f) => getBuf(base + f + LV.V)));
    const full = new Uint8Array(nF * ST); full.set(new Uint8Array(d0), 0);
    let o = f0 * ST;
    rest.forEach((b, i) => { const k = i + 1, nfk = idx.frame_ranges[k][1] - idx.frame_ranges[k][0]; if (b.byteLength !== nfk * ST) throw new Error(`${idx.frame_files[k]} has ${b.byteLength} bytes, expected ${nfk * ST}`); full.set(new Uint8Array(b), o); o += b.byteLength; });
    const cf = await composeFog(idx);
    liveSetData(full, nF); night.n_frames = nF;
    fogGrid = cf.grid; await fog.build(cf.url, fogGrid); fog.setVisible($('fogtoggle').checked);
    applyRange(); LV.el.load.textContent = ''; LV.el.ld.hidden = true; ui2Track(); applyTime(true); ui2Refresh(true);
    window.__liveFull = true;
  } catch (e) { console.warn(e); LV.el.load.textContent = 'later days unavailable'; LV.el.ld.hidden = true; }
}

/* ------------------------------------------------------------------ redesigned chrome (body.ui2: ?live=1 and ?ui=2), see UI_REDESIGN_PLAN.md
   One dock (weather+legend+settings / fog play+time track+clock / status), route chip with wetness line and profile strip,
   route list with mini profiles. The existing elements and handlers are reused; this block only builds the extra bits and updates them. */
const UI2_GEAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/></svg>';
const UI2_INFO = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg>';
const UI2_DROP = '<svg class="ci" viewBox="0 0 24 24" aria-label="rain"><path d="M12 3.5S6.5 10 6.5 14.2a5.5 5.5 0 0 0 11 0C17.5 10 12 3.5 12 3.5Z"/></svg>';
const UI2_CLOUD = '<svg class="ci" viewBox="0 0 24 24" aria-hidden="true"><path d="M5.5 20.5a4.6 4.6 0 0 1-.55-9.2 6.8 6.8 0 0 1 12.9-1.3A5.3 5.3 0 0 1 19 20.5Z"/></svg>';
const PROF_N = 24, PROF_NODATA = 'rgba(157,176,201,.25)';

function ui2Build() {
  $('rbtn').querySelector('.tx').insertAdjacentHTML('beforeend', '<span class="wt" id="rwet" hidden></span>');
  $('rbtn').insertAdjacentHTML('beforeend', '<span class="prof" id="rprof" hidden></span>');
  $('fpst').insertAdjacentHTML('beforebegin', '<span id="fptx"><span class="nm" id="fpnm"></span><span class="st" id="fpsub"></span></span>');
  const g = document.createElement('button'); g.id = 'gear'; g.type = 'button'; g.className = 'ib'; g.title = 'Settings'; g.setAttribute('aria-label', 'Settings'); g.innerHTML = UI2_GEAR;
  g.onclick = () => setSheet($('sheet').hidden); $('legend').after(g);
  $('legend').querySelector('.ticks').innerHTML = '<span>dry</span><span>wet</span>';
  const tr = document.createElement('div'); tr.id = 'trk';
  tr.innerHTML = '<div id="trkdays"></div><div class="rail"></div><div class="obs" id="trkobs"></div><div class="played" id="trkplay"></div><i class="now" id="trknow" hidden></i><i class="thumb" id="trkthumb"></i><i class="ld" id="trkld" hidden></i>';
  $('slider').before(tr); tr.appendChild($('slider'));
  const st = document.createElement('div'); st.id = 'st3'; st.innerHTML = '<span class="stl" id="st3l"></span>'; $('timebar').after(st);
  st.appendChild($('poc')); $('poc').innerHTML = '<span class="pill">' + UI2_INFO + '5 m sim · not a forecast</span>';
}

/* time track: observed part solid, forecast part dotted, yellow tick at the data time, played part cyan, day labels at local midnights */
let trkSig = '';
function ui2Track() {
  const tr = $('trk'); if (!tr || !night) return;
  const sl = $('slider'), max = +sl.max, min = +sl.min || 0, W = tr.clientWidth; if (!(max > min) || !W) return;
  const x = (f) => 8 + clamp((f - min) / (max - min), 0, 1) * (W - 16);
  const fr = LV ? LV.idx.observed_until_frame : max; // replay (?ui=2): the whole night is drawn as observed
  $('trkthumb').style.left = x(tNow) + 'px'; $('trkplay').style.width = Math.max(0, x(tNow) - 8) + 'px';
  $('trkobs').style.width = fr == null ? '0px' : Math.max(0, x(fr) - 8) + 'px';
  const nw = $('trknow'); nw.hidden = !(LV && fr != null); if (!nw.hidden) nw.style.left = x(fr) + 'px';
  if (BX) BX.track(x, min, max);
  const sig = min + '|' + max + '|' + W + '|' + night.start_iso; if (sig === trkSig) return; trkSig = sig;
  const dl = $('trkdays'); dl.textContent = ''; const marks = [];
  for (let f = Math.ceil(min); f <= max; f++) { const w = wall(night.start_iso, f * night.step_min); if (f !== Math.ceil(min) && w.time !== '00:00') continue; marks.push({ t: w.day.split(' ')[0], x: x(f) }); }
  marks.forEach((m, i) => { // drop a label that would collide with the next one
    if (i + 1 < marks.length && marks[i + 1].x - m.x < 44) return;
    const s = document.createElement('span'); s.textContent = m.t; s.style.left = m.x + 'px'; dl.appendChild(s);
  });
}
let stSig = '';
function ui2Clock(w, f) { // clock second line, status line, track
  const i = Math.round(f), fr = LV ? LV.idx.observed_until_frame : null, fc = !!LV && (fr == null || i > fr);
  $('clock-sub').textContent = LV ? `${w.day.split(' ')[0]} · ${fc ? 'forecast' : 'observed'}` : w.day + ' ' + tzName(night.start_iso);
  $('clock-sub').classList.toggle('fc', fc);
  let t = LV
    ? (fc ? (LV.real && ageNow() != null ? `Forecast (NWS RRFS) \u00b7 updated ${fmtAge(ageNow())} ago` : 'Forecast hour \u00b7 NWS RRFS model') : LV.real ? `As of ${LV.asofTxt}` + (ageNow() != null ? ` \u00b7 updated ${fmtAge(ageNow())} ago` : '') : `Snapshot \u00b7 as of ${LV.asofTxt}`)
    : nightLabel(night) + ' · replay';
  if (LV && LV.range !== 'all' && LV.spanTxt) { // short range: the visible span first, then where the data comes from
    const sh = LV.real && ageNow() != null ? `updated ${fmtAge(ageNow())} ago` : fc ? 'forecast hour' : LV.real ? `as of ${LV.asofTxt}` : `snapshot ${LV.asofTxt}`;
    t = `${LV.spanTxt} \u00b7 ${sh}`;
  }
  if (t !== stSig) { $('st3l').textContent = t; stSig = t; }
  ui2Track();
}
function ui2WxLine(i) { // one line: 66° dew 57° · SW 11 mph · cloud 47% · rain 0
  const g = (a) => (a && a[i] != null ? a[i] : null), F = (c) => (c == null ? '--' : Math.round(c * 9 / 5 + 32));
  const ws = g(weather.wind_ms), c = g(weather.cloud_low_pct), p = g(weather.precip_mm), dot = '<span>·</span>';
  const wind = (weather.wind_dir ? compass(weather.wind_dir[i]) + ' ' : '') + (ws == null ? '--' : Math.round(ws * 2.23694)) + '\u00a0mph' + ($('wx-note').hidden ? '' : '*');
  return `${F(g(weather.temp_c))}°F <span>dew</span> ${F(g(weather.dewpoint_c))}°F ${dot} ${wind} ${dot} ${UI2_CLOUD}${c == null ? '--' : Math.round(c)}%` + (p == null ? '' : ` ${dot} ${UI2_DROP}${p < 0.05 ? '0' : p.toFixed(1)}\u00a0mm/h`);
}

/* route profile: the route's samples grouped into 24 equal stretches along its length, mean wetness per stretch, drawn as hard-stop gradient */
function routeGroups(R) {
  if (R.grp) return R.grp;
  const bins = routeBins(R), nb = Math.max(1, Math.ceil(R.len / 20)), g = Array.from({ length: PROF_N }, () => []);
  for (const [b, ks] of bins) { const a = g[Math.min(PROF_N - 1, Math.floor(b * PROF_N / nb))]; for (const k of ks) a.push(k); }
  return (R.grp = g);
}
function routeStat(R) {
  const grp = routeGroups(R), prof = []; let sum = 0, n = 0, mx = -1;
  for (const ks of grp) {
    let s = 0, m = 0;
    for (const k of ks) { const v = wetNow[k]; if (v >= 0) { s += v; m++; if (v > mx) mx = v; } }
    prof.push(m ? s / m : -1); sum += s; n += m;
  }
  return { prof, avg: n ? sum / n : -1, mx };
}
function profGradient(vals) {
  return 'linear-gradient(90deg,' + vals.map((v, i) => `${v < 0 ? PROF_NODATA : rgbCss(rampColor(v))} ${(i / PROF_N * 100).toFixed(2)}% ${((i + 1) / PROF_N * 100).toFixed(2)}%`).join(',') + ')';
}
let chipT = 0, chipTO = 0;
function ui2Chip(force) { // route chip: wet line (avg, worst, peak hour) and profile strip; chip height follows the selection
  document.body.classList.toggle('ui2-route', selRoute >= 0);
  const R = selRoute >= 0 ? ROUTES[selRoute] : null, w = $('rwet'), p = $('rprof');
  if (!R || !night || !G.n) { w.hidden = true; p.hidden = true; if (BX) BX.chip(null); return; }
  const now = performance.now();
  if (!force && now - chipT < 120) { if (!chipTO) chipTO = setTimeout(() => { chipTO = 0; ui2Chip(true); }, 130); return; }
  chipT = now; ensureWet();
  const s = routeStat(R); if (s.avg < 0) { w.hidden = true; p.hidden = true; if (BX) BX.chip(null); return; }
  let pk = '';
  if (LV && LV.u8) { const f = livePeak(R); if (f >= 0) pk = ' · peaks ' + ptFmt(new Date(Date.parse(LV.idx.start_iso) + f * LV.idx.step_min * 60000).toISOString(), { weekday: 'short', hour: 'numeric', hour12: true }).replace(',', ''); }
  w.innerHTML = `Wet <b>${Math.round(s.avg)}</b> avg · <b style="color:${rgbCss(rampColor(s.mx))}">${Math.round(s.mx)}</b> worst${pk}`; w.hidden = false;
  p.style.background = profGradient(s.prof); p.hidden = false;
  if (BX) BX.chip(R);
}
let listT = 0, listTO = 0;
function ui2List(force) { // route list rows: mini profile and current mean wetness
  if ($('rpop').hidden || !night || !G.n) return;
  const now = performance.now();
  if (!force && now - listT < 200) { if (!listTO) listTO = setTimeout(() => { listTO = 0; ui2List(true); }, 210); return; }
  listT = now; ensureWet();
  for (const b of $('rlist').querySelectorAll('.ritem[data-i]')) {
    const s = routeStat(ROUTES[+b.dataset.i]), wn = b.querySelector('.wn');
    b.querySelector('.mp').style.background = profGradient(s.prof);
    wn.textContent = s.avg < 0 ? '' : Math.round(s.avg); wn.style.color = s.avg < 0 ? '' : rgbCss(rampColor(s.avg));
  }
}
function ui2Refresh(force) { if (UI2) { ui2Chip(force); ui2List(force); } }
function ui2Fly(s) { // flyover panel: route name and "0.33 of 0.78 mi · wet 84 here"
  const R = fly.R, a = shortName(R.def, ZONES), b = `${mi(s)} of ${mi(R.len)} mi` + (fly.w == null ? '' : BX && BX.slick() ? ` · ${BX.flyHere(R, s, fly.w)}` : ` · wet ${fly.w} here`);
  if (fly.nm !== a) { $('fpnm').textContent = a; fly.nm = a; }
  if (fly.sub !== b) { $('fpsub').textContent = b; fly.sub = b; }
}
if (UI2) ui2Build();

/* ------------------------------------------------------------------ beta preview layers (?beta=1 only): stations, satellite, sunshine, water depth, see beta_ui.js */
function betaCtx() {
  return {
    THREE, scene, camera, controls, EX, canvas, $, isTouch, beta: BETA, CITY, rampColor, rgbCss, wall, ptFmt, compass, routeBins, liveRouteCols, flyTo, setSheet, hideTip,
    lights: { hemi, moon, amb },
    ROUTES: () => ROUTES, refresh: () => { ui2Refresh(true); if (hoverIdx >= 0) showTip(hoverIdx, hoverXY); }, trackNow: () => ui2Track(), getBuf, getJSON,
    get LV() { return LV; }, get idx() { return LV ? LV.idx : null; }, get status() { return LV ? LV.st : null; }, get night() { return night; }, get weather() { return weather; },
    get G() { return G; }, get wetNow() { return wetNow; }, get curF() { return curF; }, get meta() { return meta; }, get terrainH() { return terrainH; },
    get rib() { return rib; }, get rib2() { return rib2; }, get base() { return base; }, get selRoute() { return selRoute; },
  };
}
async function initBeta() {
  if (!UIX || BX || !UI2) return;
  try { const m = await import('./beta_ui.js'); await m.init(betaCtx()); BX = m; if (typeof BX.frame === 'function' && night) BX.frame(curF); }
  catch (e) { console.warn('beta layers unavailable', e); BX = null; const s = $('lvstack'); if (s && LV && LV.betaBanner) s.insertAdjacentHTML('afterbegin', LV.betaBanner); }
}

/* ------------------------------------------------------------------ boot */
(async function boot() {
  try {
    setStatus('Loading city…');
    meta = await withTimeout(getJSON('data/meta.json'), 15000, 'meta');
    const [pb, pj] = await Promise.all([withTimeout(getBuf('data/points.bin'), 30000, 'points'), getJSON('data/points.json')]);
    setStatus('Loading terrain and buildings…');
    await loadMeshes();
    PB = pb; PJ = pj; parsePts(pb, pj); await initRoutes();
    requestAnimationFrame(frame);
    if (LIVE) {
      initSheet();
      try { await bootLive(); applyUrlRoute(); window.__ready = true; return; } catch (e) { // live files missing or unreadable: reload into the past-night replay
        console.warn('live failed, falling back to the replay', e); const q = new URLSearchParams(location.search); q.set('live', '0'); q.set('liveerr', '1'); location.replace('?' + q.toString()); return;
      }
    }
    setStatus('Loading replay…');
    let idx, dir;
    try { [idx, dir] = await loadIndex(); } catch (e) {
      console.warn(e); overlay.classList.add('hide'); toast('No replay data yet');
      $('night').innerHTML = '<option>No replay data</option>'; $('night').disabled = $('play').disabled = $('slider').disabled = true;
      return;
    }
    index = idx; base = dir; $('badge').hidden = !idx.mock; if (idx.mock) $('badge').textContent = 'Mock data';
    $('night').innerHTML = (UI2 ? `<option value="live">${UIX ? 'Live' : 'Live (beta)'}</option>` : '') + idx.nights.map((n, i) => `<option value="${i}">${nightLabel(n)}${n.approx_direction ? ' *' : ''}</option>`).join('');
    const qn = new URLSearchParams(location.search).get('night');
    let firstFog = idx.nights.findIndex((n) => n.id === qn);
    if (firstFog < 0) { // default: strongest fog night
      firstFog = 0; let best = -1;
      idx.nights.forEach((n, i) => { const w = n.kind === 'fog' ? (n.w_dawn_mean ?? 1) : -1; if (w > best) { best = w; firstFog = i; } });
    } $('night').value = firstFog;
    tNow = Math.min(60, idx.nights[firstFog].n_frames - 1);
    initSheet();
    await selectNight(firstFog);
    await initBeta(); // past night with the beta channel flag: tag and regrouped settings only (no beta data for a replay)
    if (QP.get('liveerr')) toast('Live data is unavailable right now. Showing a past night.');
    applyUrlRoute();
    window.__ready = true;
  } catch (e) {
    console.error(e); setStatus('Could not load the 3-D data: ' + e.message, true);
  }
})();

window.__dbg = { get skater() { return skater; }, stats: () => ({ calls: renderer.info.render.calls, triangles: renderer.info.render.triangles }), get rib() { return rib; }, get rib2() { return rib2; }, get mode() { return useRib ? 'ribbons' : 'quads'; }, camera, controls, flyTo, get tNow() { return tNow; }, set tNow(v) { tNow = v; applyTime(true); }, setPlaying, pick, selectRoute, startFly, stopFly, get fly() { return fly; }, get routes() { return ROUTES; } };
