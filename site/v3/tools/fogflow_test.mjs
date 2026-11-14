// node tools/fogflow_test.mjs  -- tests/benchmarks fogflow.js. Needs /tmp/fogflow/frames79.u8 (79 fog frames, 203x214 uint8; see header
// of the dump snippet in the report) for the motion test; skipped if missing. Writes /tmp/fogflow/wind_<dir>.png-ready PPM->PNG via python if available.
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { solveWind, motionAlongWind, FLOW_GLSL } from '../fogflow.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const w = 214, h = 203, n = w * h, cellM = 80, topM = 250, speed = 6;
let fails = 0;
const check = (ok, msg) => { console.log((ok ? 'PASS ' : 'FAIL ') + msg); if (!ok) fails++; };

// synthetic terrain: two hills, and a N-S ridge (x=150) with a gap at row 100
const g = new Float32Array(n);
for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) {
  let z = 0;
  if (c >= 25) {
    z += 280 * Math.exp(-(((c - 90) / 12) ** 2 + ((r - 70) / 12) ** 2) / 2);
    z += 200 * Math.exp(-(((c - 90) / 10) ** 2 + ((r - 140) / 10) ** 2) / 2);
    const taper = Math.min(1, Math.max(0, (r - 15) / 10)) * Math.min(1, Math.max(0, (190 - r) / 10));
    z += 320 * Math.exp(-(((c - 150) / 4) ** 2) / 2) * (1 - 0.85 * Math.exp(-(((r - 100) / 3) ** 2) / 2)) * taper;
  }
  g[r * w + c] = z;
}

console.log('== solveWind  (synthetic terrain, top ' + topM + ' m, ' + speed + ' m/s)');
let last;
for (const dirFrom of [270, 250, 300]) {
  const ms = []; let r;
  for (let k = 0; k < 7; k++) { r = solveWind({ w, h, cellM, ground: g, topM, speed, dirFrom }); ms.push(r.ms); }
  ms.sort((a, b) => a - b);
  let maxIn = 0, nIn = 0;
  for (let k = 0; k < n; k++) if (g[k] >= topM) { nIn++; maxIn = Math.max(maxIn, Math.hypot(r.u[k], r.v[k])); }
  const sp = (c, rr) => Math.hypot(r.u[rr * w + c], r.v[rr * w + c]);
  const gap = sp(150, 100), free = sp(20, 100);
  console.log(`dirFrom ${dirFrom}: div max before ${r.info.divBefore.max.toExponential(2)} after ${r.info.divAfter.max.toExponential(2)} 1/s; mean ${r.info.divBefore.mean.toExponential(2)} -> ${r.info.divAfter.mean.toExponential(2)}; it ${r.info.iterations}`);
  console.log(`   max speed inside obstacles (${nIn} cells >= top): ${maxIn.toFixed(4)} m/s; gap(150,100) ${gap.toFixed(2)} vs upstream ${free.toFixed(2)} m/s (x${(gap / free).toFixed(2)}); max ${r.info.maxSpeed.toFixed(2)}; median ${ms[3].toFixed(2)} ms, min ${ms[0].toFixed(2)} ms`);
  check(maxIn < 0.05 * speed, `dir ${dirFrom}: obstacle speed < 5% of wind`);
  check(r.info.divAfter.max < r.info.divBefore.max * 0.02, `dir ${dirFrom}: divergence reduced 50x`);
  if (dirFrom === 270) check(gap > 1.2 * free, 'gap speed-up > 1.2x');
  check(ms[3] < 40, `dir ${dirFrom}: median runtime < 40 ms (goal 15 ms; this box is slow, see report)`);
  const flat = solveWind({ w, h, cellM, ground: null, topM, speed, dirFrom });
  check(Math.abs(flat.u[5000] - (-Math.sin(dirFrom * Math.PI / 180) * speed)) < 1e-3, `dir ${dirFrom}: flat terrain gives uniform wind`);
  const a = solveWind({ w, h, cellM, ground: g, topM, speed, dirFrom });
  check(a.u.every((x, k) => x === r.u[k]), `dir ${dirFrom}: deterministic`);
  last = r; if (dirFrom === 270) writeImage(r, '/tmp/fogflow/wind_270');
}

function writeImage(r, base) { // hue = direction, brightness = speed; terrain contours in grey; arrows every 8 cells
  const S = 3, W = w * S, H = h * S, px = new Uint8Array(W * H * 3);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const k = ((y / S) | 0) * w + ((x / S) | 0), sp = Math.hypot(r.u[k], r.v[k]) / (speed * 1.8), i = (y * W + x) * 3;
    const t = Math.min(1, sp), z = Math.min(1, g[k] / topM);
    px[i] = 255 * t * (1 - z * 0.3) | 0; px[i + 1] = 200 * t | 0; px[i + 2] = 255 * (1 - t) * 0.6 + 60 * z | 0;
    if (g[k] >= topM) { px[i] = px[i + 1] = px[i + 2] = 90; }
  }
  const put = (x, y, c) => { x = Math.round(x); y = Math.round(y); if (x >= 0 && y >= 0 && x < W && y < H) { const i = (y * W + x) * 3; px[i] = px[i + 1] = px[i + 2] = c; } };
  for (let rr = 4; rr < h; rr += 8) for (let c = 4; c < w; c += 8) {
    const k = rr * w + c, u = r.u[k], v = -r.v[k], L = 2.2 * S * 3 / speed;
    const x0 = (c + 0.5) * S, y0 = (rr + 0.5) * S;
    for (let s = 0; s <= 1; s += 0.05) put(x0 + u * L * s, y0 + v * L * s, 255);
    put(x0, y0, 0);
  }
  const f = base + '.ppm';
  fs.writeFileSync(f, Buffer.concat([Buffer.from(`P6 ${W} ${H} 255\n`), Buffer.from(px)]));
  try { execFileSync('python3', ['-I', '-c', `from PIL import Image;Image.open('${f}').save('${base}.png')`]); console.log('wrote ' + base + '.png'); }
  catch { console.log('wrote ' + f + ' (no PIL)'); }
}

check(typeof FLOW_GLSL === 'string' && FLOW_GLSL.includes('flowPhases') && FLOW_GLSL.includes('flowNoise3'), 'FLOW_GLSL exported');
// GLSL brace/paren sanity
check([...FLOW_GLSL.replace(/\/\*[\s\S]*?\*\//g, '')].reduce((a, c) => a + (c === '{') - (c === '}'), 0) === 0, 'FLOW_GLSL braces balanced');

console.log('\n== motionAlongWind on real frames');
const FR = '/tmp/fogflow/frames79.u8';
if (!fs.existsSync(FR)) console.log('skip: ' + FR + ' missing');
else {
  const all = fs.readFileSync(FR), fr = (i) => new Uint8Array(all.buffer, all.byteOffset + i * n, n);
  const wx = JSON.parse(fs.readFileSync(path.join(here, '../live/weather.json'), 'utf8'));
  const pct = (a, p) => a[Math.min(a.length - 1, Math.floor(p * a.length))];
  for (const [a, b] of [[37, 38], [38, 39], [47, 48], [63, 64]]) {
    const ws = (wx.wind_ms[a] + wx.wind_ms[b]) / 2, wd = wx.wind_dir[a];
    const r = motionAlongWind(fr(a), fr(b), { w, h, cellM, dirFrom: wd, maxShiftM: 4000, block: 5 });
    const sel = []; let cs = 0, sw = 0;
    for (let k = 0; k < n; k++) if (r.conf[k] > 0.3) sel.push(r.shift[k]);
    for (let k = 0; k < n; k++) { cs += r.conf[k]; sw += r.conf[k] * r.shift[k]; }
    sel.sort((x, y) => x - y);
    const expect = ws * 3600;
    console.log(`${a}->${b}: wind ${ws.toFixed(1)} m/s from ${wd.toFixed(0)} => expect ${expect.toFixed(0)} m; frac conf>0.3 ${(sel.length / n * 100).toFixed(1)}%, mean conf ${(cs / n).toFixed(3)}; ` +
      (sel.length ? `shift p10/p50/p90 ${pct(sel, .1).toFixed(0)}/${pct(sel, .5).toFixed(0)}/${pct(sel, .9).toFixed(0)} m, conf-weighted mean ${(sw / cs).toFixed(0)} m` : 'no confident cells') + `; ${r.ms.toFixed(0)} ms`);
  }
  // control: pair of frames, but assume a wind from a rotated direction (perpendicular) -- should not look like translation
  const wd = wx.wind_dir[37], ctl = motionAlongWind(fr(37), fr(38), { w, h, cellM, dirFrom: wd + 90, maxShiftM: 4000 });
  let cs = 0, sw = 0; for (let k = 0; k < n; k++) { cs += ctl.conf[k]; sw += ctl.conf[k] * ctl.shift[k]; }
  console.log(`control 37->38 with wind rotated 90 deg: mean conf ${(cs / n).toFixed(3)}, conf-weighted mean shift ${(sw / cs).toFixed(0)} m`);
  // synthetic: translate a frame by 3 cells downwind and recover it
  const A = fr(38), B = new Uint8Array(n), d = 3; // wind from 270 -> moves east (+col)
  for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) B[r * w + c] = c >= d ? A[r * w + c - d] : 0;
  const sr = motionAlongWind(A, B, { w, h, cellM, dirFrom: 270 });
  let cs2 = 0, sw2 = 0; for (let k = 0; k < n; k++) { cs2 += sr.conf[k]; sw2 += sr.conf[k] * sr.shift[k]; }
  const rec = sw2 / cs2; console.log(`synthetic +3 cells east: recovered ${rec.toFixed(0)} m (true ${d * cellM})`);
  check(Math.abs(rec - d * cellM) < 40, 'synthetic translation recovered within 40 m');
}
console.log(fails ? `\n${fails} FAILED` : '\nall checks passed');
process.exit(fails ? 1 : 0);
