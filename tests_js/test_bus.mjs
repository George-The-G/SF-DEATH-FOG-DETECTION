// node tests_js/test_bus.mjs   (no dependencies)
import assert from 'node:assert/strict';
import { llToScene } from '../site/v3/busgeo.js';
import { BusTrack } from '../site/v3/busfilter.js';
// geo: the OSM stop "Rhode Island St & 15th St" (37.76747, -122.40298) is route end p = [12483.5, ., -8672.1] in data/routes.json
const g = llToScene(37.76747, -122.40298);
assert.ok(Math.hypot(g.x - 12483.5, g.z + 8672.1) < 25, `geo off: ${JSON.stringify(g)}`);
// filter: a bus moving +z at 8 m/s, reports every 60 s with 12 m noise, 15 s late
let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647 - 0.5;
const tr = new BusTrack(); let maxJump = 0, last = null, err = 0, n = 0;
for (let i = 0; i <= 6000; i++) {
  const t = i / 10;
  if (i % 600 === 0) tr.report(1000 + rnd() * 24, 8 * (t - 15) + rnd() * 24, 1e9 + t - 15, 1e9 + t);
  const s = tr.step(1e9 + t); if (!s) continue;
  if (last && t > 130) { const j = Math.hypot(s.x - last.x, s.z - last.z); maxJump = Math.max(maxJump, j); }
  last = s; if (t > 200) { err += Math.hypot(s.x - 1000, s.z - 8 * t); n++; }
}
assert.ok(maxJump < 2.5, `bus jumped ${maxJump} m in one 0.1 s frame`); // 8 m/s is 0.8 m per frame; corrections may add a few m/s
assert.ok(err / n < 40, `mean error ${err / n} m`);
// stale: no reports for 200 s hides the bus; extrapolation stops at 90 s
const s2 = new BusTrack(); s2.report(0, 0, 1e9, 1e9); s2.report(0, 80, 1e9 + 10, 1e9 + 10);
const a = s2.step(1e9 + 100), b = s2.step(1e9 + 150), c = s2.step(1e9 + 200);
assert.ok(a.alpha === 0.5 || a.alpha > 0.5 && a.alpha < 1, 'fades after 60 s'); assert.equal(b.z, s2.step(1e9 + 160).z); assert.equal(c.alpha, 0);
// dwell: a bus heading for a stop 100 m ahead waits about 20 s there
const d = new BusTrack({ stops: [{ x: 0, z: 100 }] }); d.report(0, 0, 1e9, 1e9); d.report(0, 40, 1e9 + 5, 1e9 + 5); d.report(0, 80, 1e9 + 10, 1e9 + 10);
let held = 0; for (let t = 10; t < 40; t += 0.5) { const s = d.step(1e9 + t); if (s.dwelling) held += 0.5; }
assert.ok(held >= 10 && held <= 24, `dwell held ${held} s`);
// a far-off report re-seeds and blends instead of jumping
const j = new BusTrack(); j.report(0, 0, 1e9, 1e9); const p0 = j.step(1e9 + 1); j.report(0, 500, 1e9 + 2, 1e9 + 2); const p1 = j.step(1e9 + 2);
assert.ok(Math.hypot(p1.x - p0.x, p1.z - p0.z) < 15, 'no jump on re-seed');
console.log('ok');
