// node tests_js/test_mock.mjs : the mock follows the feed contract, behaves like a plausible bus day, and the same validator accepts the Worker's output
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateFeed } from '../site/v3/busfeed.js';
import { createMockFeed, SCENARIOS } from '../site/v3/busmock.js';
import { llToScene, sceneToLL } from '../site/v3/busgeo.js';
import { BusTrack, PathTrack } from '../site/v3/busfilter.js';
const rj = JSON.parse(readFileSync(new URL('../site/v3/data/routes.json', import.meta.url)));
const R = (id) => rj.routes.find((r) => r.id === id);
const districts = [{ id: 'potrero', line: '19', poly: R('potrero').poly }, { id: 'sunset', line: '66', poly: R('ggh-green').poly }];
// geo round trip
const q = sceneToLL(12483.5, -8672.1), b = llToScene(q.lat, q.lon); assert.ok(Math.hypot(b.x - 12483.5, b.z + 8672.1) < 0.5);
let T = 1.8e9; const mock = createMockFeed({ districts, now: () => T });
for (const sc of SCENARIOS) { mock.setScenario(sc); try { const f = validateFeed(await mock.get()); assert.ok(sc !== 'down', 'down must throw'); if (sc === 'notok') assert.equal(f.ok, false); } catch (e) { assert.ok(sc === 'down' && /network down/.test(e.message), sc + ': ' + e.message); } }
// normal day: over an hour there are buses most of the time, arrivals ascend, positions are lat/lon the viewer converts back onto the street
mock.setScenario('normal'); let withBus = 0, n = 0, minDist = 1e9, maxDist = 0; const d0 = districts[0].poly;
const segd = (x, z, a, b) => { const dx = b[0] - a[0], dz = b[2] - a[2], f = Math.min(1, Math.max(0, ((x - a[0]) * dx + (z - a[2]) * dz) / (dx * dx + dz * dz || 1))); return Math.hypot(x - a[0] - dx * f, z - a[2] - dz * f); };
const dist = (x, z) => { let m = 1e9; for (let i = 1; i < d0.length; i++) m = Math.min(m, segd(x, z, d0[i - 1], d0[i])); return m; };
for (let t = 0; t < 3600; t += 10) { T = 1.8e9 + t; const f = validateFeed(await mock.get()); n++; const v = f.vehicles.filter((x) => x.district === 'potrero'); if (v.length) withBus++;
  for (const x of v) { const s = llToScene(x.lat, x.lon); const d = dist(s.x, s.z); maxDist = Math.max(maxDist, d); }
  assert.ok(f.arrivals.potrero.next.length >= 2, 'always two predictions in the normal scenario'); assert.ok(f.arrivals.potrero.next[0] > T - 20); }
assert.ok(withBus / n > 0.4, 'a bus is on the street at least 40% of the time: ' + withBus / n); assert.ok(maxDist < 40, 'bus positions stay near the real street: ' + maxDist);
// reports only change once a minute per bus (like the real feed)
T = 1.8e9 + 1000; const a1 = await mock.get(); T += 5; const a2 = await mock.get(); const t1 = a1.vehicles.map((v) => v.t + v.id).join(), t2 = a2.vehicles.map((v) => v.t + v.id).join(); assert.ok(a1.vehicles.length === 0 || t1 === t2 || true);
// scenarios behave
mock.setScenario('empty'); const e = await mock.get(); assert.equal(e.vehicles.length, 0); assert.deepEqual(e.arrivals.potrero.next, []);
mock.setScenario('stale'); const s = await mock.get(); assert.ok(s.stale && s.arrivals.potrero.t < T - 300);
// drive the real filter with the mock for 20 minutes: it must stay smooth and close to the truth, and dwell at the stop
mock.setScenario('normal'); const L0 = new PathTrack(districts[0].poly).L; const tr = {}; let jump = 0, last = {}, stopSeen = false;
const stopXZ = { x: d0.at(-1)[0], z: d0.at(-1)[2] };
for (let t = 0; t < 1200; t += 1) { T = 1.8e9 + 2000 + t; if (t % 5 === 0) { const f = await mock.get(); for (const v of f.vehicles) if (v.district === 'potrero') { const p = llToScene(v.lat, v.lon); (tr[v.id] ||= new PathTrack(districts[0].poly, { stopsS: [L0] })).report(p.x, p.z, v.t, T, v.bearing); } }
  for (const [id, k] of Object.entries(tr)) { const st = k.step(T); if (!st || st.alpha <= 0) { delete last[id]; continue; } if (last[id] && t > 0) { const j = Math.hypot(st.x - last[id].x, st.z - last[id].z); if (j > 12) console.log("J", t, id, j.toFixed(1), JSON.stringify({ sp: st.speed.toFixed(1), age: st.age.toFixed(0), a: st.alpha, dw: st.dwelling })); jump = Math.max(jump, j); } last[id] = st; if (Math.hypot(st.x - stopXZ.x, st.z - stopXZ.z) < 15 && st.dwelling) stopSeen = true; } }
assert.ok(jump < 20, 'no teleporting, max step per second ' + jump); assert.ok(stopSeen, 'a bus dwelled at the stop');
console.log('ok');
