import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { createFog } from './fog.js';
import { createRibbons } from './ribbons.js';
import { createSkater } from './skater.js';

/* ------------------------------------------------------------------ config */
const SRC_Q = new URLSearchParams(location.search).get('src');
const SRC_DIRS = SRC_Q === 'mock' ? ['replay_mock/'] : SRC_Q === 'synth' ? ['replay_synth/'] : ['replay/', 'replay_mock/']; // synth = tools/make_synthetic_ribbons.py fixture
const RAMP = [[0, '#3a8696'], [18, '#6fb09f'], [24, '#f4e04d'], [50, '#f2c230'], [65, '#ee8a2c'], [80, '#d9531e'], [90, '#b3202f'], [100, '#7a0f3b']];
const CATS = ['dry', 'damp', 'slick', 'wet', 'icy-fog'];
const $ = (id) => document.getElementById(id);
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
const skater = createSkater({ THREE, reduceMotion: matchMedia('(prefers-reduced-motion: reduce)').matches }); // shown only during the route flyover
scene.add(skater.group);

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
    b.innerHTML = '<span class="sw" style="background:transparent"></span><span class="rn">Whole city</span><span class="rm">Reset the camera, no route selected</span>'; list.appendChild(b); }
  for (const z of rj.zones) {
    const rs = ROUTES.map((r, i) => [r, i]).filter(([r]) => r.def.zone === z.id);
    if (!rs.length) continue;
    const h = document.createElement('div'); h.className = 'rzone'; h.textContent = z.name; list.appendChild(h);
    for (const [r, i] of rs) {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'ritem'; b.dataset.i = i; b.setAttribute('role', 'option');
      b.innerHTML = `<span class="sw" style="background:${r.def.color}"></span><span class="rn"></span><span class="rm">${mi(r.len)} mi · ${ft(r.def.drop_m)} ft drop</span>`;
      b.querySelector('.rn').textContent = shortName(r.def, rj.zones);
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
  $('rsw').style.background = 'transparent'; $('rlabel').textContent = 'Whole city';
  $('rcsw').style.background = 'transparent'; $('rcname').textContent = 'No route selected'; $('rcstats').textContent = '';
  $('fly').disabled = true; $('flyhdr').disabled = true;
  [...$('rlist').querySelectorAll('.ritem')].forEach((b) => b.classList.toggle('on', !!b.dataset.city));
  flyUI(); closePicker();
  if (goCity) flyTo(CITY.pos, CITY.target, 2200);
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
  $('rsw').style.background = R.def.color; $('rlabel').textContent = shortName(R.def, ZONES); $('fly').disabled = false; $('flyhdr').disabled = false;
  $('rcsw').style.background = R.def.color; $('rcname').textContent = R.def.name;
  $('rcstats').textContent = `${mi(R.len)} mi · drops ${ft(R.def.drop_m)} ft` + (R.def.climb_m >= 3 ? ` · ${ft(R.def.climb_m)} ft of climbing` : '');
  $('routecard').hidden = false;
  [...$('rlist').querySelectorAll('.ritem')].forEach((b) => b.classList.toggle('on', !b.dataset.city && +b.dataset.i === i));
  flyUI(); closePicker();
  if (flyCam) flyToRoute(R);
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
const LSK = 'df3.sheetOpen';
const lsGet = () => { try { return localStorage.getItem(LSK); } catch { return null; } };
const lsSet = (v) => { try { localStorage.setItem(LSK, v); } catch { /* private mode etc.: state just does not persist */ } };
function setSheet(open) {
  $('sheet').hidden = !open; $('menubtn').setAttribute('aria-expanded', String(open)); $('sheettab').setAttribute('aria-expanded', String(open));
  document.body.classList.toggle('sheet-closed', !open);
  if (open) closePicker();
  if (deskMQ.matches) lsSet(open ? '1' : '0');
}
function closePicker() { $('rpop').hidden = true; $('rbtn').setAttribute('aria-expanded', 'false'); }
function openPicker(o) { $('rpop').hidden = !o; $('rbtn').setAttribute('aria-expanded', String(o)); if (o && !deskMQ.matches) setSheet(false); }
$('rbtn').onclick = () => openPicker($('rpop').hidden);
$('menubtn').onclick = () => setSheet($('sheet').hidden);
$('sheettab').onclick = () => setSheet($('sheet').hidden);
document.querySelector('#sheet .grab').onclick = () => setSheet(false);
$('legend').onclick = () => { setSheet(true); $('fullleg').scrollIntoView({ block: 'nearest' }); };
$('wxbtn').onclick = () => { const o = $('wxgrid').hidden; $('wxgrid').hidden = !o; $('wxbtn').setAttribute('aria-expanded', String(o)); };
function initSheet() { // phone: starts closed; desktop: starts open unless the user collapsed it
  const v = lsGet(); setSheet(deskMQ.matches ? v !== '0' : false);
  if (deskMQ.matches) lsSet(v === '0' ? '0' : '1');
}
deskMQ.addEventListener('change', () => { const v = lsGet(); setSheet(deskMQ.matches ? v !== '0' : false); });
$('rlist').addEventListener('click', (e) => { const b = e.target.closest('.ritem'); if (!b) return; if (b.dataset.city) clearRoute(true); else selectRoute(+b.dataset.i, true); });
document.addEventListener('pointerdown', (e) => {
  if (!e.target.closest('#rpop, #rbtn')) closePicker();
  if (!deskMQ.matches && !e.target.closest('#sheet, #menubtn, #legend')) setSheet(false);
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closePicker(); if (!deskMQ.matches) setSheet(false); stopFly(); } });

/* fly-along: chase camera that looks ahead along the route polyline */
const FLY = { back: 150, ahead: 130, up: 62 };
function flyUI() {
  const on = !!fly; document.body.classList.toggle('flying', on);
  { const fh = $('flyhdr'); fh.innerHTML = on ? '&#9632;' : '&#9654;'; fh.setAttribute('aria-label', on ? 'Stop the flyover' : 'Fly the route'); fh.title = fh.getAttribute('aria-label'); } if (on) { closePicker(); if (!deskMQ.matches) setSheet(false); }
  $('fly').hidden = on; $('flypause').hidden = !on; $('flystop').hidden = !on; $('flyspeed').hidden = !on; $('rcinfo').hidden = !on;
  if (on) {
    const paused = fly.paused;
    $('flypause').innerHTML = fly.done ? '&#8635;' : paused ? '&#9654;' : '&#10074;&#10074;';
    $('flypause').setAttribute('aria-label', fly.done ? 'Replay' : paused ? 'Resume' : 'Pause');
  }
  [...$('flyspeed').children].forEach((c) => c.classList.toggle('on', +c.dataset.s === flySpeed));
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
  const w0 = routeWetAt(R, s), w = w0 == null ? null : Math.round(w0);
  const txt = `${street}|${w}`;
  if (txt !== fly.txt) {
    fly.txt = txt;
    $('rcinfo').innerHTML = `<span class="rc-st"></span>` + (w == null ? '' : ` <span class="sw" style="background:${rgbCss(rampColor(w))}"></span><b>${w}</b> wetness`);
    $('rcinfo').querySelector('.rc-st').textContent = street;
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
  flyReadout(s);
  placeSkater(R, s, dt, c);
}
/* The skateboarder rides the street ahead of the chase camera. Its arc position is chosen so that the board sits just above the
   glass control stack at the bottom of the screen (bisection on the projected height), its size is a fixed share of the screen height
   (so it reads from 390x844 to 1440x900), and it wobbles with the interpolated wetness at that point of the route. */
const _sp = new THREE.Vector3(), _sf = new THREE.Vector3();
function placeSkater(R, s, dt, camGoal) {
  if (!fly || fly.cam.distanceTo(camGoal) > 90) { skater.setVisible(false); return; } // not while the camera is still flying in
  camera.lookAt(controls.target); camera.updateMatrixWorld(); // same pose OrbitControls.update() is about to set
  const H = innerHeight, yT = Math.min(H, $('controls').getBoundingClientRect().top) - 18;
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
$('fly').onclick = startFly;
$('flyhdr').onclick = () => { if (fly) stopFly(); else startFly(); };
$('flystop').onclick = stopFly;
$('flypause').onclick = () => { if (!fly) return; if (fly.done) { fly.u = 0; fly.done = false; fly.paused = false; } else fly.paused = !fly.paused; flyUI(); };
$('flyspeed').addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; flySpeed = +b.dataset.s; flyUI(); });

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
const fog = createFog({ THREE, scene, EX, isTouch, getMeta: () => meta, getTerrainH: () => terrainH, toast });

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
  const r = createRibbons({ THREE, scene, EX, terrainH, glslRamp, isTouch });
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
  ROUTES.forEach((R, k) => { R.bins = null; if (!r) { R.hi.visible = k === selRoute; R.dim.visible = k !== selRoute; } });
  if (selRoute >= 0) setRouteHighlight(selRoute);
  wetDirty = true; document.body.classList.toggle('ribbons', r);
}
function ensureWet() { if (useRib && wetDirty) { rib.fillWet(wetNow, curF); wetDirty = false; } }

async function selectNight(i) {
  const n = index.nights[i]; setStatus(`Loading ${n.label}…`); overlay.classList.remove('hide');
  const flag = new URLSearchParams(location.search).get('ribbons');
  let useR = flag !== '0' && !!(n.streets_r && n.ribbons);
  if (flag === '1' && !useR) toast('No ribbon data for this night: using the old street quads');
  let sb = null, wx = null, ru8 = null;
  if (useR) { // continuous ribbons: ribbons.bin/meta once, streets_r.u8 per night
    try {
      setStatus('Loading street geometry…'); await ensureRibbons(n); setStatus(`Loading ${n.label}: street wetness…`);
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
  $('clock').textContent = w.time; $('clock-sub').textContent = w.day + ' ' + tzName(night.start_iso);
  if (document.activeElement !== $('slider') || force) $('slider').value = f;
  const key = Math.round(f * 20);
  curF = f;
  if (useRib) rib.setFrame(f); // three uniform writes, nothing else
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
      const s = weather.sun_el ? weather.sun_el[i] : null; $('wx-sun').textContent = s == null ? '--' : (s > 0 ? 'up ' : 'down ') + s.toFixed(0) + '°';
      const t = $('wx-temp').textContent.replace(' ', ''), dp = $('wx-dew').textContent.replace(' °F', '°'), wd = $('wx-wind').textContent.trim() + ($('wx-note').hidden ? '' : '*');
      $('wxline').textContent = `${t} · dew ${dp} · ${wd} · ${$('wx-cloud').textContent.replace(' ', '')} cloud · sun ${s == null ? '--' : Math.round(s) + '°'}`;
    }
    if (hoverIdx >= 0) showTip(hoverIdx, hoverXY);
  }
  fog.setFrame((f * night.step_min) / fogGrid.frame_min);
}
function tzName(iso) { return /-07:00$/.test(iso) ? 'PDT' : /-08:00$/.test(iso) ? 'PST' : ''; }
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
$('fogtoggle').addEventListener('change', (e) => fog.setVisible(e.target.checked));
$('fogdens').addEventListener('input', (e) => fog.setDensity(+e.target.value / 100));
/* "District streets": every street of Potrero Hill and Golden Gate Heights as a wetness gradient (default on). */
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
  for (let k = 0; k < G.n; k++) {
    if (G.cls[k] && skipDist) continue;
    const x = G.x[k], y = G.y[k], z = G.z[k];
    const w = m[3] * x + m[7] * y + m[11] * z + m[15]; if (w <= 0) continue;
    const sx = ((m[0] * x + m[4] * y + m[8] * z + m[12]) / w * .5 + .5) * W, sy = (1 - ((m[1] * x + m[5] * y + m[9] * z + m[13]) / w * .5 + .5)) * H;
    const d = (sx - cx) ** 2 + (sy - cy) ** 2; if (d < bd) { bd = d; best = k; }
  }
  return best;
}
function showTip(k, xy) {
  ensureWet();
  const v = wetNow[k], cat = CATS[v < 20 ? 0 : v < 50 ? 1 : v < 80 ? 2 : v < 95 ? 3 : 4];
  const when = night ? wall(night.start_iso, curF * night.step_min).time : '';
  tip.innerHTML = v < 0 ? `no data<br>${G.names[G.st[k]]}` : `<span class="sw" style="background:${rgbCss(rampColor(v))}"></span><b>${Math.round(v)}</b> · ${cat}${useRib ? ' (modelled)' : ''}<br>${G.names[G.st[k]]}, ${G.side[k] ? 'right' : 'left'} side${selRoute >= 0 && ((G.mask[k] >> ROUTES[selRoute].def.index) & 1) ? ' · on route' : G.cls[k] ? ' · district street' : ''} · ${when}`;
  tip.hidden = false;
  const topH = $('top').getBoundingClientRect().bottom, botY = $('controls').getBoundingClientRect().top; // keep the tooltip clear of the glass stacks
  tip.style.left = clamp(xy[0], 125, innerWidth - 125) + 'px'; tip.style.top = clamp(xy[1], topH + 70, botY - 6) + 'px';
}
function hideTip() { hoverIdx = -1; tip.hidden = true; }
let down = null;
canvas.addEventListener('pointerdown', (e) => { if (fly) stopFly(); down = { x: e.clientX, y: e.clientY, t: performance.now() }; if (e.pointerType === 'touch') hideTip(); });
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
  flyStep(ts);
  controls.update();
  const dist = camera.position.distanceTo(controls.target);
  fog.update(camera, dist, ts / 1000);
  if (pointMat && !useRib) pointMat.uniforms.uScale.value = clamp(dist / 450, 1, 12);
  if (useRib) {
    rib.setLod(dist);
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
    PB = pb; PJ = pj; parsePts(pb, pj); await initRoutes();
    requestAnimationFrame(frame);
    setStatus('Loading replay…');
    let idx, dir;
    try { [idx, dir] = await loadIndex(); } catch (e) {
      console.warn(e); overlay.classList.add('hide'); toast('No replay data yet');
      $('night').innerHTML = '<option>No replay data</option>'; $('night').disabled = $('play').disabled = $('slider').disabled = true;
      return;
    }
    index = idx; base = dir; $('badge').hidden = !idx.mock; if (idx.mock) $('badge').textContent = 'Mock data';
    $('night').innerHTML = idx.nights.map((n, i) => `<option value="${i}">${n.label}${n.approx_direction ? ' *' : ''}</option>`).join('');
    const qn = new URLSearchParams(location.search).get('night');
    let firstFog = idx.nights.findIndex((n) => n.id === qn);
    if (firstFog < 0) { // default: strongest fog night
      firstFog = 0; let best = -1;
      idx.nights.forEach((n, i) => { const w = n.kind === 'fog' ? (n.w_dawn_mean ?? 1) : -1; if (w > best) { best = w; firstFog = i; } });
    } $('night').value = firstFog;
    tNow = Math.min(60, idx.nights[firstFog].n_frames - 1);
    initSheet();
    await selectNight(firstFog);
    applyUrlRoute();
    window.__ready = true;
  } catch (e) {
    console.error(e); setStatus('Could not load the 3-D data: ' + e.message, true);
  }
})();

window.__dbg = { get skater() { return skater; }, stats: () => ({ calls: renderer.info.render.calls, triangles: renderer.info.render.triangles }), get rib() { return rib; }, get mode() { return useRib ? 'ribbons' : 'quads'; }, camera, controls, flyTo, get tNow() { return tNow; }, set tNow(v) { tNow = v; applyTime(true); }, setPlaying, pick, selectRoute, startFly, stopFly, get fly() { return fly; }, get routes() { return ROUTES; } };
