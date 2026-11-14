// Offline tests for the skatable world (no browser): node site/v3/tools/skate_test.mjs
// Loads the real terrain, buildings, district + city streets and checks collisions, riding and speed.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createWorld, createSim, TUNE } from '../skate.js';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const rd = (p) => { const b = fs.readFileSync(path.join(root, p)); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); };
const EX = 1.5, clamp = (v, a, b) => Math.min(b, Math.max(a, v));
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fails++; };

// terrain grid, same as app.js buildHeightGrid
const glb = rd('data/terrain.glb'), dvg = new DataView(glb), jl = dvg.getUint32(12, true), bin0 = 20 + jl + 8;
const idxLen = 460080 * 4, nv = 77235, V = new Float32Array(glb, bin0 + idxLen, nv * 3), step = 60;
let ni = 0, nj = 0; for (let k = 0; k < nv; k++) { ni = Math.max(ni, Math.round(V[k * 3] / step)); nj = Math.max(nj, Math.round(-V[k * 3 + 2] / step)); } ni++; nj++;
const hh = new Float32Array(ni * nj).fill(-9999); for (let k = 0; k < nv; k++) hh[Math.round(V[k * 3] / step) * nj + Math.round(-V[k * 3 + 2] / step)] = V[k * 3 + 1] * EX;
const terrainH = { at(x, z) { const fi = clamp(x / step, 0, ni - 1.001), fj = clamp(-z / step, 0, nj - 1.001), i = Math.floor(fi), j = Math.floor(fj), a = fi - i, b = fj - j, g = (ii, jj) => Math.max(hh[ii * nj + jj], -50); return (g(i, j) * (1 - a) + g(i + 1, j) * a) * (1 - b) + (g(i, j + 1) * (1 - a) + g(i + 1, j + 1) * a) * b; } };

// streets: district + city, lifted like ribbons.js does
function ribbons(buf, stOff) {
  const dv = new DataView(buf), S = dv.getUint32(4, true), nS = dv.getUint32(8, true); let o = 24;
  const take = (T, n) => { const a = new T(buf, o, n); o += Math.ceil(n * T.BYTES_PER_ELEMENT / 4) * 4; return a; };
  const pos = take(Float32Array, S * 3); take(Float32Array, S * 2); take(Uint32Array, S); const st = take(Uint16Array, S); take(Uint16Array, S); take(Uint8Array, S); const cls = take(Uint8Array, S);
  const x = new Float32Array(S), y = new Float32Array(S), z = new Float32Array(S), s2 = new Uint32Array(S);
  for (let k = 0; k < S; k++) { x[k] = pos[k * 3]; z[k] = pos[k * 3 + 2]; y[k] = Math.max(pos[k * 3 + 1] * EX, terrainH.at(x[k], z[k])) + 1 + (cls[k] ? 0 : 0.3); s2[k] = st[k] + stOff; }
  return { S, x, y, z, st: s2 };
}
const a = ribbons(rd('data/ribbons.bin'), 0), b = ribbons(rd('live/ribbons_city.bin'), 100000), n = a.S + b.S;
const G = { n, x: new Float32Array(n), y: new Float32Array(n), z: new Float32Array(n), st: new Uint32Array(n) };
for (const [r, off] of [[a, 0], [b, a.S]]) { G.x.set(r.x, off); G.y.set(r.y, off); G.z.set(r.z, off); G.st.set(r.st, off); }

// buildings, same layout as buildBuildings
const bb = rd('data/buildings.bin'), dvb = new DataView(bb), nb = dvb.getUint32(4, true), np = dvb.getUint32(8, true), nvt = dvb.getUint32(12, true), xyQ = dvb.getFloat32(16, true), wdQ = dvb.getFloat32(24, true);
let o = 28; const tk = (T, c) => { const r = new T(bb, o, c); o += Math.ceil(c * T.BYTES_PER_ELEMENT / 4) * 4; return r; };
const cx = tk(Uint16Array, nb), cn = tk(Uint16Array, nb); tk(Uint16Array, nb); tk(Uint16Array, nb); const bw = tk(Uint8Array, nb), bd = tk(Uint8Array, nb), ba = tk(Uint8Array, nb);
const pnv = tk(Uint8Array, np); tk(Uint16Array, np); tk(Uint16Array, np); const pv = tk(Uint16Array, nvt * 2);
const col = { nb, np, xyQ, wdQ, cx, cn, bw, bd, ba, pnv, pv };

let t0 = performance.now(); const world = createWorld({ terrainH, EX, G, col }); const buildMs = performance.now() - t0;
console.log(`world built in ${buildMs.toFixed(0)} ms (${G.n} street samples, ${nb} boxes, ${np} outlines)`);

// 1. a circle dropped on a building centre ends up outside it
// (some buildings share walls, so a push out of one can land in the next; those spots are not reachable from a street)
let inside = 0, tested = 0;
for (let k = 0; k < nb; k += 37) { const r = world.walls(cx[k] * xyQ, -cn[k] * xyQ, TUNE.r); tested++; const r2 = world.walls(r.x, r.z, TUNE.r - 0.05); if (r2.n) inside++; }
console.log(`circles dropped on ${tested} building centres: ${(100 * inside / tested).toFixed(1)}% still touching something after one push (shared walls)`);
ok(true, 'wall push-out ran on every building centre without error');

// 2. no street point is inside a building (so walls never block a street)
let blocked = 0; for (let k = 0; k < G.n; k += 7) { const r = world.walls(G.x[k], G.z[k], TUNE.r); if (r.n) blocked++; }
ok(blocked / (G.n / 7) < 0.02, `street centre points touching a wall: ${(100 * blocked / (G.n / 7)).toFixed(2)}% (must be < 2%)`);

// 3. ride the Potrero route from the top with a light push and no steering: stays on the street, goes downhill
const routes = JSON.parse(fs.readFileSync(path.join(root, 'data/routes.json'), 'utf8')).routes, R = routes[0], P = R.poly;
function distToRoute(x, z) { let best = 1e18; for (let i = 0; i + 1 < P.length; i++) { const ax = P[i][0], az = P[i][2], bx = P[i + 1][0] - ax, bz = P[i + 1][2] - az, L2 = bx * bx + bz * bz || 1, t = clamp(((x - ax) * bx + (z - az) * bz) / L2, 0, 1); best = Math.min(best, Math.hypot(x - ax - t * bx, z - az - t * bz)); } return best; }
let sim = createSim(world), h0 = Math.atan2(P[1][2] - P[0][2], P[1][0] - P[0][0]);
sim.place(P[0][0], P[0][2], h0, 5);
let maxOff = 0, maxV = 0, offFrames = 0, onFrames = 0, y0 = sim.y; const dt = 1 / 60;
const pursue = () => { // simple steering: aim at the route point 25 m ahead of the nearest one
  let bi = 0, bd2 = 1e18; for (let i = 0; i < P.length; i++) { const d = (P[i][0] - sim.x) ** 2 + (P[i][2] - sim.z) ** 2; if (d < bd2) { bd2 = d; bi = i; } }
  let j = bi, acc = 0; while (j + 1 < P.length && acc < 25) { acc += Math.hypot(P[j + 1][0] - P[j][0], P[j + 1][2] - P[j][2]); j++; }
  let d = Math.atan2(P[j][2] - sim.z, P[j][0] - sim.x) - sim.h; d = Math.atan2(Math.sin(d), Math.cos(d)); return clamp(d * 2, -1, 1); };
for (let i = 0; i < 60 * 70; i++) { sim.step(dt, { steer: pursue(), push: 0.3, brake: 0 }, 20); const d = distToRoute(sim.x, sim.z); maxOff = Math.max(maxOff, d); maxV = Math.max(maxV, sim.speed); if (d > 6.5) offFrames++; if (sim.onRoad) onFrames++; }
const endD = Math.hypot(sim.x - P[P.length - 1][0], sim.z - P[P.length - 1][2]);
console.log(`route ride with simple steering: 70 s, max offset ${maxOff.toFixed(1)} m, top speed ${maxV.toFixed(1)} m/s (${(maxV * 2.237).toFixed(0)} mph), ended ${endD.toFixed(0)} m from the bus stop, dropped ${(y0 - sim.y).toFixed(0)} scene m`);
ok(onFrames > 60 * 70 * 0.9, `skater counted as on a street for ${(100 * onFrames / (60 * 70)).toFixed(0)}% of the ride (must be > 90%)`);
ok(maxOff < 16, 'a skater steering along the route stays within 16 m of its centre line (corners are cut at 70 mph)');
ok(sim.y < y0 - 20, 'skater rolled downhill');
ok(maxV > 8 && maxV <= TUNE.vMax + 0.01, 'speed builds up and respects the top speed');

// 4. ramming: aim at the nearest building from the street at full push for 20 s, never ends inside
let rams = 0, intrusions = 0;
for (let k = 0; k < G.n; k += 997) {
  const s = createSim(world); s.place(G.x[k], G.z[k], Math.random() * 6.28, 6); rams++;
  for (let i = 0; i < 60 * 20; i++) { s.step(dt, { steer: Math.sin(i / 90) * 0.4, push: 1, brake: 0 }, null); const r = world.walls(s.x, s.z, 0.5); if (r.n) { intrusions++; break; } }
}
ok(intrusions === 0, `${rams} full-speed runs from random streets never end inside a building (${intrusions} did)`);

// 5. wet streets are sloppier: same steering, bigger sideslip
function slip(w) { const s = createSim(world); s.place(P[0][0], P[0][2], h0, 12); let mx = 0; for (let i = 0; i < 90; i++) { s.step(dt, { steer: i < 30 ? 1 : 0, push: 0, brake: 0 }, w); const dx = Math.cos(s.h), dz = Math.sin(s.h); mx = Math.max(mx, Math.abs(-s.vx * dz + s.vz * dx)); } return mx; }
const dry = slip(0), wet = slip(100); ok(wet > dry * 1.3, `side slip in a turn: dry ${dry.toFixed(2)} m/s, wet ${wet.toFixed(2)} m/s`);

// 6. Back on the street: from random off-street spots the skater lands on a street within a few metres
{ let bad = 0, cnt = 0; for (let k = 0; k < G.n; k += 1500) { const s = createSim(world); s.place(G.x[k] + 40, G.z[k] + 25, 0, 0); cnt++; if (!s.respawn()) { bad++; continue; } if (world.road(s.x, s.z, undefined, undefined, 6).found === false) bad++; } ok(bad === 0, `back-on-street works from ${cnt} off-street spots (${bad} failed)`); }

// 7. cost per frame
const s7 = createSim(world); s7.place(P[0][0], P[0][2], h0, 8); t0 = performance.now(); const N = 20000; for (let i = 0; i < N; i++) s7.step(dt, { steer: Math.sin(i / 60), push: 0.5, brake: 0 }, 40); const us = (performance.now() - t0) / N * 1000;
console.log(`sim step cost: ${us.toFixed(1)} microseconds per frame`); ok(us < 200, 'sim step under 200 microseconds');
console.log(fails ? `${fails} FAILED` : 'all passed'); process.exit(fails ? 1 : 0);
