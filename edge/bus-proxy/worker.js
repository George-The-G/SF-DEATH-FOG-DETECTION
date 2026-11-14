/* Bus proxy (Cloudflare Worker). The ONLY place the 511 key lives (Worker secret API_KEY_511). The beta page asks this Worker for
   the two skate-shuttle lines and never sees the key.
   - GET /api/health: setup check (+ ?near=lat,lon stop finder, ?line=&near=&to= stop picker) (status of the 511 call, counts, line ids seen). No key, no positions.
   - GET /api/buses returns { ok, ts, stale, vehicles:[{id,district,line,lat,lon,bearing,t}], arrivals:{<district>:{t,next:[epoch s]}} }
   - Results are cached in Cloudflare's cache: vehicles VEH_TTL seconds (default 60), arrivals ARR_TTL (default 180). So 511 is called at
     most about once a minute (and only when someone is looking), however many people view the page. 511's default limit is 60 calls/hour.
   - Only the two configured lines are ever requested, and only from allowed origins (ALLOWED_ORIGINS).
   - The key is never logged, echoed, or put in an error message. 511 field names are from its SIRI JSON docs and have NOT been tested
     against the live service from this repo's build container; check the first real response (see README, "First run"). */
const UP = 'https://api.511.org/transit/';
const num = (v) => (v == null || v === '' ? NaN : Number(v));
const epoch = (s) => { const t = Date.parse(s); return Number.isFinite(t) ? t / 1000 : NaN; };
const parseJSON = (txt) => JSON.parse(String(txt).replace(/^﻿/, ''));
const arr = (x) => (x == null ? [] : Array.isArray(x) ? x : [x]);

export function parseVehicles(j, districts) {
  const acts = arr(j?.Siri?.ServiceDelivery?.VehicleMonitoringDelivery?.VehicleActivity ?? j?.Siri?.ServiceDelivery?.VehicleMonitoringDelivery?.[0]?.VehicleActivity);
  const byLine = new Map(districts.map((d) => [String(d.line), d.id])), out = [];
  for (const a of acts) {
    const m = a?.MonitoredVehicleJourney; if (!m) continue; const line = String(m.LineRef ?? ''); if (!byLine.has(line)) continue;
    const lat = num(m.VehicleLocation?.Latitude), lon = num(m.VehicleLocation?.Longitude); if (!(lat > 37 && lat < 38.5 && lon < -121 && lon > -123.5)) continue;
    const t = epoch(a.RecordedAtTime) || Date.now() / 1000;
    out.push({ id: String(m.VehicleRef ?? m.FramedVehicleJourneyRef?.DatedVehicleJourneyRef ?? out.length), district: byLine.get(line), line, lat, lon, bearing: num(m.Bearing) || 0, t });
  }
  return out;
}
export function parseArrivals(j, line) {
  const v = arr(j?.ServiceDelivery?.StopMonitoringDelivery?.MonitoredStopVisit ?? j?.Siri?.ServiceDelivery?.StopMonitoringDelivery?.MonitoredStopVisit);
  const next = [];
  for (const s of v) {
    const m = s?.MonitoredVehicleJourney; if (!m || String(m.LineRef) !== String(line)) continue;
    const c = m.MonitoredCall || {}, t = epoch(c.ExpectedArrivalTime || c.ExpectedDepartureTime || c.AimedArrivalTime); if (Number.isFinite(t)) next.push(t);
  }
  return next.sort((a, b) => a - b).slice(0, 3);
}

const redact = (s, key) => String(s).split(key).join('***');
async function getUp(deps, env, path, params) {
  const u = new URL(UP + path); u.searchParams.set('api_key', env.API_KEY_511); u.searchParams.set('format', 'json');
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  const r = await deps.fetch(u.toString(), { headers: { Accept: 'application/json' } });
  if (!r.ok) throw new Error(`511 HTTP ${r.status}`); // status only: never the URL (it holds the key)
  return parseJSON(await r.text());
}
/* cache helper: a stored copy is "fresh" for ttl seconds, and usable as stale for up to 180 s more if 511 fails */
async function cached(deps, name, ttl, produce) {
  const key = new Request('https://bus-proxy.invalid/' + name), now = deps.now();
  const hit = await deps.cache.match(key); let old = null;
  if (hit) { try { old = await hit.json(); } catch { old = null; } if (old && now - old.at < ttl) return { v: old.v, at: old.at, stale: false }; }
  try {
    const v = await produce(); await deps.cache.put(key, new Response(JSON.stringify({ at: now, v }), { headers: { 'Cache-Control': 'max-age=900' } }));
    return { v, at: now, stale: false };
  } catch (e) {
    if (old && now - old.at < ttl + 180) return { v: old.v, at: old.at, stale: true };
    throw e;
  }
}
const inflight = new Map(); // one 511 call per kind at a time inside this instance
const once = (k, f) => { if (!inflight.has(k)) inflight.set(k, f().finally(() => inflight.delete(k))); return inflight.get(k); };

export async function handle(request, env, deps) {
  const url = new URL(request.url), origin = request.headers.get('Origin') || '';
  const allowed = String(env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim()).filter(Boolean);
  const cors = { 'Access-Control-Allow-Origin': allowed.includes(origin) ? origin : 'null', Vary: 'Origin', 'Cache-Control': 'no-store' };
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: { ...cors, 'Access-Control-Allow-Methods': 'GET', 'Access-Control-Max-Age': '600' } });
  if (url.pathname === '/api/health') { // setup check, open it in a browser tab: no key, no live positions, only what 511 said (status, counts, line ids). Cached 2 min so it adds almost no 511 calls.
    const H = { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' };
    if (!env.API_KEY_511) return new Response(JSON.stringify({ configured: false, problem: 'secret API_KEY_511 is missing' }), { status: 503, headers: H });
    let ds; try { ds = JSON.parse(env.DISTRICTS || '[]'); } catch { return new Response(JSON.stringify({ configured: true, problem: 'DISTRICTS is not valid JSON' }), { status: 500, headers: H }); }
    const near = (url.searchParams.get('near') || '').split(',').map(Number), rad = Math.min(1000, Number(url.searchParams.get('r')) || 250);
    const to = (url.searchParams.get('to') || '').split(',').map(Number), line = url.searchParams.get('line');
    if (line && near.length === 2 && to.length === 2 && near.concat(to).every(Number.isFinite)) { // ?line=19&near=lat,lon&to=lat,lon: stops of the line near the bottom that lead toward the top (the side the shuttle picks up on)
      try {
        const op = env.AGENCY || 'SF';
        const st = await cached(deps, 'stops', 3600, () => once('stops', async () => {
          const j = await getUp(deps, env, 'stops', { operator_id: op }), pts = arr(j?.Contents?.dataObjects?.ScheduledStopPoint);
          return pts.map((p) => [String(p.id), String(p.Name || ''), num(p.Location?.Latitude), num(p.Location?.Longitude)]).filter((p) => Number.isFinite(p[2]));
        }));
        const pj = await getUp(deps, env, 'patterns', { operator_id: op, line_id: line }), seqs = [];
        (function walk(o) { if (Array.isArray(o)) { if (o.length && o.every((x) => x && typeof x === 'object' && x.ScheduledStopPointRef != null)) seqs.push(o.slice().sort((a, b) => num(a.Order) - num(b.Order)).map((x) => String(x.ScheduledStopPointRef))); else o.forEach(walk); } else if (o && typeof o === 'object') Object.values(o).forEach(walk); })(pj);
        const pos = new Map(st.v.map((p) => [p[0], p])), k = Math.cos(near[0] * Math.PI / 180) * 111320, dist = (p, q) => Math.hypot((p[3] - q[1]) * k, (p[2] - q[0]) * 110540);
        const best = new Map(), tr = Math.min(3000, Number(url.searchParams.get('tr')) || 400), seen = new Map();
        for (const q of seqs) {
          const P = q.map((id) => pos.get(id)); let jt = -1, dt = 1e9; P.forEach((p, i) => { if (p) { const d = dist(p, to); if (d < dt) { dt = d; jt = i; } } });
          P.forEach((p, i) => { if (p) { const m0 = Math.round(dist(p, near)); if (m0 <= 1500 && (!seen.has(p[0]) || seen.get(p[0]).m > m0)) seen.set(p[0], { stopCode: p[0], name: p[1], m: m0, toTopM: Math.round(dt) }); } if (p && dist(p, near) <= rad && jt > i && dt < tr) { const m = Math.round(dist(p, near)), o = best.get(p[0]); if (!o || m < o.m) best.set(p[0], { stopCode: p[0], name: p[1], m, stopsToTop: jt - i }); } });
        }
        return new Response(JSON.stringify({ line, patterns: seqs.length, towardTop: [...best.values()].sort((x, y) => x.m - y.m), ...(best.size ? {} : { lineStopsNearBottom: [...seen.values()].sort((x, y) => x.m - y.m).slice(0, 8) }) }), { headers: H });
      } catch (e) { return new Response(JSON.stringify({ upstream: redact(e.message, env.API_KEY_511) }), { status: 502, headers: H }); }
    }
    if (near.length === 2 && near.every(Number.isFinite)) { // stop finder: ?near=lat,lon&r=metres lists Muni stops around a point (to pick stopCode values)
      try {
        const st = await cached(deps, 'stops', 3600, () => once('stops', async () => {
          const j = await getUp(deps, env, 'stops', { operator_id: env.AGENCY || 'SF' }), pts = arr(j?.Contents?.dataObjects?.ScheduledStopPoint);
          return pts.map((p) => [String(p.id), String(p.Name || ''), num(p.Location?.Latitude), num(p.Location?.Longitude)]).filter((p) => Number.isFinite(p[2]));
        }));
        const k = Math.cos(near[0] * Math.PI / 180) * 111320, out = st.v.map((p) => ({ stopCode: p[0], name: p[1], m: Math.round(Math.hypot((p[3] - near[1]) * k, (p[2] - near[0]) * 110540)) })).filter((p) => p.m <= rad).sort((x, y) => x.m - y.m).slice(0, 15);
        return new Response(JSON.stringify({ near, radiusM: rad, of: st.v.length, stops: out }), { headers: H });
      } catch (e) { return new Response(JSON.stringify({ upstream: redact(e.message, env.API_KEY_511) }), { status: 502, headers: H }); }
    }
    try {
      const h = await cached(deps, 'health', 120, () => once('health', async () => {
        const j = await getUp(deps, env, 'VehicleMonitoring', { agency: env.AGENCY || 'SF' }), acts = arr(j?.Siri?.ServiceDelivery?.VehicleMonitoringDelivery?.VehicleActivity ?? j?.Siri?.ServiceDelivery?.VehicleMonitoringDelivery?.[0]?.VehicleActivity);
        const lines = [...new Set(acts.map((a) => String(a?.MonitoredVehicleJourney?.LineRef ?? '?')))].sort().slice(0, 40);
        return { upstream: 'ok', allVehicles: acts.length, topKeys: Object.keys(j || {}).slice(0, 5), lineIdsSeen: lines, matched: parseVehicles(j, ds).length };
      }));
      return new Response(JSON.stringify({ configured: true, origins: allowed.length, districts: ds.map((d) => d.line), ...h.v }), { headers: H });
    } catch (e) { return new Response(JSON.stringify({ configured: true, upstream: redact(e.message, env.API_KEY_511) }), { status: 502, headers: H }); }
  }
  if (url.pathname !== '/api/buses') return new Response('not found', { status: 404 });
  if (!allowed.includes(origin)) return new Response('forbidden', { status: 403, headers: cors });
  if (!env.API_KEY_511) return new Response(JSON.stringify({ ok: false, error: 'not configured' }), { status: 503, headers: { ...cors, 'Content-Type': 'application/json' } });
  let districts; try { districts = JSON.parse(env.DISTRICTS || '[]'); } catch { districts = []; }
  const agency = env.AGENCY || 'SF', vttl = Number(env.VEH_TTL) || 60, attl = Number(env.ARR_TTL) || 180, now = deps.now();
  const body = { ok: false, ts: now, stale: false, vehicles: [], arrivals: {} };
  try {
    const v = await cached(deps, 'veh', vttl, () => once('veh', async () => parseVehicles(await getUp(deps, env, 'VehicleMonitoring', { agency }), districts)));
    body.vehicles = v.v; body.stale = v.stale; body.ok = true;
  } catch (e) { body.error = 'vehicles unavailable'; console.log('veh error', redact(e.message, env.API_KEY_511)); }
  for (const d of districts) {
    if (!d.stopCode) continue;
    try {
      const a = await cached(deps, 'arr-' + d.id, attl, () => once('arr-' + d.id, async () => parseArrivals(await getUp(deps, env, 'StopMonitoring', { agency, stopCode: d.stopCode }), d.line)));
      body.arrivals[d.id] = { t: a.at, next: a.v, stale: a.stale };
    } catch (e) { console.log('arr error', d.id, redact(e.message, env.API_KEY_511)); }
  }
  return new Response(JSON.stringify(body), { headers: { ...cors, 'Content-Type': 'application/json' } });
}
export default { fetch: (request, env, ctx) => handle(request, env, { fetch: (...a) => fetch(...a), cache: caches.default, now: () => Date.now() / 1000 }) };
