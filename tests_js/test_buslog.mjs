// node tests_js/test_buslog.mjs : the Worker's GPS log (mock D1) and /api/history, no network
import assert from 'node:assert/strict';
import { handle } from '../edge/bus-proxy/worker.js';
const KEY = 'SECRETKEY123', T = 1.8e9, O = 'https://ok.example';
const act = (line, id, dt, lat, lon, b) => ({ RecordedAtTime: new Date((T + dt) * 1000).toISOString(), MonitoredVehicleJourney: { LineRef: line, VehicleRef: id, Bearing: String(b), VehicleLocation: { Latitude: String(lat), Longitude: String(lon) } } });
let batch = []; const mkVeh = () => JSON.stringify({ Siri: { ServiceDelivery: { VehicleMonitoringDelivery: { VehicleActivity: batch } } } });
const store = new Map(); let calls = 0, now = T;
const rows = new Map(); let pruned = 0; // mock D1: key id|t, like the real PRIMARY KEY (id, t)
const db = { insert: async (rs) => { for (const r of rs) if (!rows.has(r.id + '|' + r.t)) rows.set(r.id + '|' + r.t, r); },
  query: async (q) => [...rows.values()].filter((r) => r.line === q.line && r.t >= q.since && r.lat >= q.lat0 && r.lat <= q.lat1 && r.lon >= q.lon0 && r.lon <= q.lon1).sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : a.t - b.t)),
  prune: async (before) => { pruned = before; for (const [k, r] of rows) if (r.t < before) rows.delete(k); } };
const deps = { now: () => now, db, cache: { match: async (r) => { const v = store.get(r.url); return v ? new Response(v) : undefined; }, put: async (r, res) => { store.set(r.url, await res.text()); } },
  fetch: async () => { calls++; return new Response(mkVeh()); } };
const env = { API_KEY_511: KEY, ALLOWED_ORIGINS: O, DISTRICTS: JSON.stringify([{ id: 'potrero', line: '19' }, { id: 'sunset', line: '66' }]) };
const get = (path) => handle(new Request('https://w.example' + path, { headers: { Origin: O } }), env, deps);
batch = [act('19', '7001', -20, 37.7675, -122.403, 181), act('66', '8002', -20, 37.76, -122.47, 90)];
await get('/api/buses'); assert.equal(rows.size, 2, 'both buses logged on the first (fresh) call');
await get('/api/buses'); await get('/api/buses'); assert.equal(rows.size, 2, 'cached answers are not logged again'); assert.equal(calls, 1);
// 511 keeps reporting the same position for a while: the same (id, recorded time) is stored once even after the cache expires
now = T + 61; await get('/api/buses'); assert.equal(rows.size, 2, 'same report read twice is one row'); assert.equal(calls, 2);
// new reports: stored, in order
now = T + 122; batch = [act('19', '7001', 100, 37.7662, -122.4031, 181), act('66', '8002', 100, 37.761, -122.4701, 90)]; await get('/api/buses'); assert.equal(rows.size, 4);
now = T + 130;
let r = await get('/api/history?line=19&since=' + (T - 3600) + '&bbox=37.75,-122.42,37.78,-122.39'), h = await r.json();
assert.ok(h.ok); assert.equal(h.vehicles.length, 1); assert.equal(h.vehicles[0].id, '7001'); assert.deepEqual(h.vehicles[0].f.map((f) => f[0]), [T - 20, T + 100]); assert.equal(h.vehicles[0].f[0].length, 4);
assert.ok(!JSON.stringify(h).includes(KEY));
r = await get('/api/history?line=19&since=' + (T - 3600) + '&bbox=37.0,-121.0,37.1,-120.9'); assert.equal((await r.json()).vehicles.length, 0, 'outside the box: nothing');
r = await get('/api/history?line=19%27%3B--'); assert.equal(r.status, 400, 'line is validated');
assert.equal((await handle(new Request('https://w.example/api/history?line=19', { headers: { Origin: 'https://evil.example' } }), env, deps)).status, 403);
r = await handle(new Request('https://w.example/api/history?line=19', { headers: { Origin: O } }), env, { ...deps, db: null }); assert.equal(r.status, 503, 'no D1 bound: history says so, buses still work');
const r2 = await handle(new Request('https://w.example/api/buses', { headers: { Origin: O } }), env, { ...deps, db: null }); assert.ok((await r2.json()).ok);
// a failing database never breaks the live answer
const bad = { ...deps, db: { insert: async () => { throw new Error('d1 down ' + KEY); }, query: async () => { throw new Error('x'); }, prune: async () => {} } };
now = T + 400; batch = [act('19', '7001', 300, 37.766, -122.4032, 181)]; const rb = await handle(new Request('https://w.example/api/buses', { headers: { Origin: O } }), env, bad); assert.ok((await rb.json()).ok, 'log failure does not fail the request');
// pruning runs once an hour and keeps LOG_DAYS (7 default)
now = Math.ceil(T / 3600) * 3600; batch = [act('19', '7001', now - T + 5, 37.7661, -122.4033, 181)]; store.clear(); await get('/api/buses'); assert.equal(pruned, Math.floor(now - 7 * 86400), 'pruned at the top of the hour');
console.log('buslog tests passed');
