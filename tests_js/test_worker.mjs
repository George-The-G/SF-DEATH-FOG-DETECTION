// node tests_js/test_worker.mjs  (mocked 511; shapes follow 511's SIRI JSON docs and are UNVERIFIED against the live service)
import assert from 'node:assert/strict';
import { validateFeed } from '../site/v3/busfeed.js';
import { handle, parseVehicles, parseArrivals } from '../edge/bus-proxy/worker.js';
const KEY = 'SECRETKEY123', T = 1.8e9;
const veh = '﻿' + JSON.stringify({ Siri: { ServiceDelivery: { VehicleMonitoringDelivery: { VehicleActivity: [
  { RecordedAtTime: new Date((T - 20) * 1000).toISOString(), MonitoredVehicleJourney: { LineRef: '19', VehicleRef: '7001', Bearing: '181', VehicleLocation: { Latitude: '37.7675', Longitude: '-122.4030' } } },
  { RecordedAtTime: new Date((T - 20) * 1000).toISOString(), MonitoredVehicleJourney: { LineRef: '66', VehicleRef: '8002', VehicleLocation: { Latitude: '37.7600', Longitude: '-122.4700' } } },
  { RecordedAtTime: new Date(T * 1000).toISOString(), MonitoredVehicleJourney: { LineRef: '38', VehicleRef: '1', VehicleLocation: { Latitude: '37.78', Longitude: '-122.45' } } } ] } } } });
const stopj = JSON.stringify({ ServiceDelivery: { StopMonitoringDelivery: { MonitoredStopVisit: [
  { MonitoredVehicleJourney: { LineRef: '19', MonitoredCall: { ExpectedArrivalTime: new Date((T + 600) * 1000).toISOString() } } },
  { MonitoredVehicleJourney: { LineRef: '19', MonitoredCall: { ExpectedArrivalTime: new Date((T + 240) * 1000).toISOString() } } },
  { MonitoredVehicleJourney: { LineRef: '22', MonitoredCall: { ExpectedArrivalTime: new Date((T + 100) * 1000).toISOString() } } } ] } } });
const districts = [{ id: 'potrero', line: '19', stopCode: '13010' }, { id: 'sunset', line: '66', stopCode: '' }];
assert.equal(parseVehicles(JSON.parse(veh.slice(1)), districts).length, 2);
assert.deepEqual(parseArrivals(JSON.parse(stopj), '19').map((t) => t - T), [240, 600]);
// handler: caching, origin check, key never leaks
const store = new Map(); let calls = 0, urls = [], now = T, fail = false;
const deps = { now: () => now, cache: { match: async (r) => { const v = store.get(r.url); return v ? new Response(v) : undefined; }, put: async (r, res) => { store.set(r.url, await res.text()); } },
  fetch: async (u) => { urls.push(u); calls++; if (fail) return new Response('no', { status: 500 }); return new Response(u.includes('VehicleMonitoring') ? veh : stopj); } };
const env = { API_KEY_511: KEY, ALLOWED_ORIGINS: 'https://ok.example', DISTRICTS: JSON.stringify(districts) };
const req = (o) => new Request('https://w.example/api/buses', { headers: o ? { Origin: o } : {} });
assert.equal((await handle(req('https://evil.example'), env, deps)).status, 403);
assert.equal((await handle(req(), env, deps)).status, 403);
let r = await handle(req('https://ok.example'), env, deps), b = await r.json();
assert.equal(r.headers.get('access-control-allow-origin'), 'https://ok.example'); assert.ok(b.ok); assert.equal(b.vehicles.length, 2); assert.equal(b.arrivals.potrero.next.length, 2); assert.ok(!('sunset' in b.arrivals));
assert.ok(!JSON.stringify(b).includes(KEY), 'key leaked into the response');
validateFeed(b); // the Worker speaks the same contract as the mock, so the viewer cannot tell them apart
const c1 = calls; for (let i = 0; i < 20; i++) await handle(req('https://ok.example'), env, deps); assert.equal(calls, c1, '20 viewers must not cause upstream calls inside the TTL');
now = T + 61; fail = true; r = await handle(req('https://ok.example'), env, deps); b = await r.json(); assert.ok(b.ok && b.stale, 'serves stale copy when 511 fails');
now = T + 400; r = await handle(req('https://ok.example'), env, deps); b = await r.json(); assert.equal(b.ok, false); assert.ok(!JSON.stringify(b).includes(KEY));
assert.equal((await handle(new Request('https://w.example/other', { headers: { Origin: 'https://ok.example' } }), env, deps)).status, 404);
console.log('ok', 'upstream calls so far:', calls);
