/* Live skate-shuttle buses (closed beta). Loaded by app.js only with ?beta=1&buses=1.
   Per district: one Muni line (19 Polk for Potrero Hill, 66 Quintara for Sunset), a shelter at the bottom stop with a pinned
   "next bus / then" popup (busstop.js), and the line's buses drawn as small boxes that glide between the once-a-minute reports
   (busfilter.js). Data comes from our own Cloudflare Worker (edge/bus-proxy), which holds the 511 key; this page never sees a key.
   The only thing it reads is the feed contract (busfeed.js). With no worker URL set (data/bus_config.json "api", or ?busapi=URL) it uses the
   MOCK source (busmock.js), labelled as such; ?busmock=normal|jumpy|stale|empty|notok|down forces the mock and picks a scenario.
   Honest limits: positions between reports are simulated; the popup times are the feed's own predictions. */
import { llToScene } from './busgeo.js';
import { BusTrack, PathTrack } from './busfilter.js';
import { createStop, createBusCard } from './busstop.js';
import { createWorld, createChaseCam, createSim, createInput } from './skate.js';
import { validateFeed, httpSource } from './busfeed.js';
import { createMockFeed, SCENARIOS } from './busmock.js';

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const POLL_MS = 30000;

const angDiff = (a, b) => { let d = (a - b) % (Math.PI * 2); if (d > Math.PI) d -= Math.PI * 2; if (d < -Math.PI) d += Math.PI * 2; return d; };
const BUS_SCALE = 1.1; // buses are drawn 10% larger than first built

export async function createBuses({ THREE, scene, camera, controls, canvas, EX, getJSON, ground, getTerrain, getCol, getG, getDist, QP, toast }) {
  const cfg = await getJSON('data/bus_config.json');
  const rj = await getJSON('data/routes.json');
  const api = QP.get('busapi') || cfg.api || '';
  const demo = !api || QP.has('busmock');
  const scale = () => clamp(getDist() / 450, 1, 80);
  const D = [];
  for (const d of cfg.districts) {
    const R = rj.routes.find((r) => r.id === d.route); if (!R) { console.warn('buses: no route', d.route); continue; }
    const poly = R.poly, e = poly[poly.length - 1], pr = poly[poly.length - 2];
    let sx, sz;
    if (d.stop) { const s = llToScene(d.stop.lat, d.stop.lon); sx = s.x; sz = s.z; }
    else { // fallback: the bottom end of the skate route, nudged 6.5 m to the sidewalk side
      const dx = e[0] - pr[0], dz = e[2] - pr[2], L = Math.hypot(dx, dz) || 1; sx = e[0] + (-dz / L) * 6.5; sz = e[2] + (dx / L) * 6.5;
    }
    const sy = Math.max(e[1] * EX, ground(sx, sz) ?? 0);
    const dx = e[0] - pr[0], dz = e[2] - pr[2], L = Math.hypot(dx, dz) || 1, side = { x: -dz / L, z: dx / L };
    const color = new THREE.Color(d.color).getHex();
    const stop = createStop({ THREE, scene, canvas, camera, id: d.id, line: d.line, name: d.name, dirText: d.dir + (d.stop ? '' : ' (stop position approximate)'), color,
      position: new THREE.Vector3(sx, sy, sz), yaw: Math.atan2(-side.x, -side.z), getScale: scale, demo });
    D.push({ d, R, stop, color, sx, sz, buses: new Map(), stopsXZ: (d.stops || []).map((s) => llToScene(s.lat, s.lon)) });
  }

  const mock = demo ? createMockFeed({ districts: D.map((o) => ({ id: o.d.id, line: o.d.line, poly: o.R.poly, offset: o.d.mock_offset || 0, headway: o.d.mock_headway })), scenario: QP.get('busmock') || 'normal' }) : null;
  const source = demo ? mock : httpSource(api, getJSON);

  /* bus meshes: one shared geometry per kind, a number badge sprite per line */
  const busGeo = new THREE.BoxGeometry(2.6, 3, 11), winGeo = new THREE.BoxGeometry(2.64, .9, 9);
  const badgeTex = (line, col) => {
    const c = document.createElement('canvas'); c.width = c.height = 64; const x = c.getContext('2d');
    x.fillStyle = '#' + new THREE.Color(col).getHexString(); x.beginPath(); x.arc(32, 32, 30, 0, 7); x.fill();
    x.fillStyle = '#0b1118'; x.font = 'bold 30px sans-serif'; x.textAlign = 'center'; x.textBaseline = 'middle'; x.fillText(line, 32, 34); return new THREE.CanvasTexture(c);
  };
  for (const o of D) o.badge = badgeTex(o.d.line, o.color);
  const mkBus = (o) => {
    const g = new THREE.Group(), mat = new THREE.MeshLambertMaterial({ color: 0xd8452f, emissive: 0x4a1a10, transparent: true });
    const body = new THREE.Mesh(busGeo, mat); body.position.y = 1.8; g.add(body);
    const win = new THREE.Mesh(winGeo, new THREE.MeshLambertMaterial({ color: 0xbfe6f2, transparent: true })); win.position.y = 2.4; g.add(win);
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: o.badge, transparent: true, depthWrite: false })); sp.scale.set(5, 5, 1); sp.position.y = 6.5; g.add(sp);
    const ring = new THREE.Mesh(new THREE.RingGeometry(7, 8.6, 40), new THREE.MeshBasicMaterial({ color: o.color, side: THREE.DoubleSide, transparent: true, opacity: .9, depthWrite: false }));
    ring.rotation.x = -Math.PI / 2; ring.position.y = .5; ring.visible = false; g.add(ring);
    g.userData = { mats: [mat, win.material, sp.material], ring }; scene.add(g); return g;
  };

  /* a bus that first shows up within 40 m of the district's street polyline is tied to it (PathTrack: follows corners, dwells at the stop);
     any other bus uses the free-space filter, which needs no map of the line */
  for (const o of D) { o.path = new PathTrack(o.R.poly); o.stopS = o.path.proj(o.sx, o.sz).s; }
  const newTrack = (o, p) => (o.path.proj(p.x, p.z).d < 40 ? new PathTrack(o.R.poly, { stopsS: [o.stopS] }) : new BusTrack({ stops: o.stopsXZ }));

  /* ---------- feed ---------- */
  let feed = null, feedOk = false, feedErr = 0, lastOk = 0, timer = 0;
  const nowS = () => Date.now() / 1000;
  function ingest(f) {
    const now = nowS(); lastOk = now; feedErr = 0;
    for (const o of D) {
      const seen = new Set();
      for (const v of (f.vehicles || [])) {
        if (v.district && v.district !== o.d.id) continue;
        if (!v.district && String(v.line) !== String(o.d.line)) continue;
        const p = v.x != null ? { x: v.x, z: v.z } : llToScene(v.lat, v.lon);
        if (Math.hypot(p.x - o.sx, p.z - o.sz) > (o.d.radius_m || 1000)) continue; // only buses near this district
        const id = String(v.id); seen.add(id);
        let b = o.buses.get(id);
        if (!b) { b = { track: newTrack(o, p), mesh: mkBus(o), off: 0 }; o.buses.set(id, b); }
        if (b.track instanceof PathTrack) { // tied to the street while it really is on it; free-space filter otherwise
          if (b.track.proj(p.x, p.z).d > 60) { if (++b.off >= 2) { b.track = new BusTrack({ stops: o.stopsXZ }); b.off = 0; } } else b.off = 0;
        }
        b.track.report(p.x, p.z, v.t || now, now, v.bearing);
      }
      const a = (f.arrivals || {})[o.d.id]; if (a) o.stop.setArrivals({ next: a.next || [], t: a.t || f.ts || now }, 'live');
    }
  }
  async function poll(force) {
    if (document.hidden && !force) return;
    try {
      const f = validateFeed(await source.get(nowS()));
      if (f.ok === false) throw new Error('feed not ok');
      feed = f; ingest(f); feedOk = true; for (const o of D) if (!f.arrivals[o.d.id]) o.stop.setArrivals(null, 'down');
    } catch (e) { feedErr++; feedOk = false; if (feedErr === 3 && toast) toast('Live buses unavailable right now'); }
    if (!feedOk && (force || nowS() - lastOk > 180 || feedErr >= 3)) for (const o of D) o.stop.setArrivals(null, 'down');
  }
  poll(); timer = setInterval(() => poll(), POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  if (demo && toast) toast('Beta buses: MOCK data (no live feed connected)');
  if (demo) mockPanel(mock, poll);

  /* ---------- tap a bus: card hovering above it with its time to the bottom stop, camera follows until the card is closed ---------- */
  const S = { o: null, id: null, b: null, card: null, cam: null, world: null, hasB: false, hasG: false, drive: null, t: performance.now() };
  function ensureWorld() { // the shared chase camera needs ground height and building roofs (skate.js world); built on first use, rebuilt once buildings have loaded
    const th = getTerrain && getTerrain(), col = getCol && getCol(); if (!th) return null;
    const G = getG ? getG() : null;
    if (!S.world || (!S.hasB && col && col.bh) || (!S.hasG && G && G.n)) { S.world = createWorld({ terrainH: th, EX, G, col }); S.hasB = !!(col && col.bh); S.hasG = !!(G && G.n); S.cam = null; S.camStarted = false; }
    return S.world;
  }
  const simOf = (b, st) => ({ x: st.x, y: b.mesh.position.y + 2, z: st.z, h: Math.PI / 2 - b.hs, speed: st.speed }); // skate.js heading: forward = (cos h, sin h); ours: (sin t, cos t)
  function select(o, id, b) {
    const w = ensureWorld(); if (!w) return;
    const first = !S.card || !S.card.isOpen;
    if (S.b) S.b.mesh.userData.ring.visible = false;
    if (S.card && S.o !== o) { S.card.dispose(); S.card = null; }
    if (S.drive && S.b !== b) stopDrive();
    S.o = o; S.id = id; S.b = b;
    if (!S.card) S.card = createBusCard({ THREE, camera, line: o.d.line, color: o.color, onClose: stopFollow, onDrive: (on) => (on ? startDrive() : stopDrive()) });
    S.card.show(o.d.name + ' bus');
    if (!S.cam) { S.cam = createChaseCam({ THREE, world: w, opts: { back: 78, up: 46, look: 22, lookUp: 5, follow: 0.8, spring: 3.2 } }); first || (S.camStarted = false); }
    const st = b.st || { x: b.mesh.position.x, z: b.mesh.position.z, speed: 0 };
    if (first || !S.camStarted) { S.cam.start(camera, controls.target, simOf(b, st)); S.camStarted = true; }
    controls.enabled = false;
  }
  function stopFollow() { stopDrive(); if (S.cam) S.cam.stop(); S.camStarted = false; controls.enabled = true; if (S.b) S.b.mesh.userData.ring.visible = false; S.b = null; }
  /* ---------- drive a bus like the skater: same sim and controls (skate.js), feed positions stop moving the bus until you stop ---------- */
  const BUS_DRIVE = { r: 4.2, vMax: 15, push: 6, vPush: 10, turn: 1.3, brake: 14, grip: 7 }; // a bigger body, slower and heavier than the skater
  const touch = typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches;
  function startDrive() {
    const b = S.b, st = b && b.st; if (!b || !st || S.drive) return;
    if (!ensureWorld()) return;
    const sim = createSim(S.world, BUS_DRIVE), h = Math.PI / 2 - (b.hs == null ? st.heading : b.hs);
    sim.place(st.x, st.z, h, Math.min(BUS_DRIVE.vMax, st.speed || 0));
    const input = createInput({ isTouch: touch, onBack: () => sim.respawn() }); input.show(true);
    S.drive = { sim, input }; b.driven = true; b.blend = null;
    if (S.card) S.card.setDriving(true);
  }
  function stopDrive() {
    const d = S.drive; if (!d) return; S.drive = null;
    const b = S.b; d.input.show(false); d.input.destroy();
    if (b) { b.driven = false; if (b.st && b.st.alpha > 0) b.blend = { x: d.sim.x - b.st.x, z: d.sim.z - b.st.z }; } // glide back to the real position
    if (S.card && S.card.driving) S.card.setDriving(false);
  }
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && S.drive) stopDrive(); });
  function busInfo(o, b, now) {
    const t = b.track, st = b.st, age = now - t.lastRep, info = { big: '--', bigLabel: 'To the bottom stop', status: '', mood: 'ok', extra: '' };
    if (b.driven) { info.bigLabel = 'You are driving'; info.status = 'Live position paused while you drive'; info.mood = 'stale'; return info; }
    if (!st || st.alpha <= 0) { info.status = 'Bus no longer reporting'; info.mood = 'down'; return info; }
    let eta = null, where = '';
    if (t instanceof PathTrack) {
      const d = o.stopS - t.fx.p, v = t.fx.v;
      if (Math.abs(d) < 12) { eta = 0; where = st.dwelling ? 'At the stop now' : v < -0.5 ? 'Just left the stop, heading uphill' : 'At the stop'; }
      else if (d > 0) eta = d / Math.max(v > 0.3 ? v : 0, 3.5);
      else where = 'Past the stop, heading uphill';
    } else {
      const dx = o.sx - st.x, dz = o.sz - st.z, dist = Math.hypot(dx, dz), toward = (Math.sin(st.heading) * dx + Math.cos(st.heading) * dz) / (dist || 1);
      if (dist < 15) { eta = 0; where = 'At the stop'; } else if (toward > 0.3) eta = dist * 1.25 / Math.max(st.speed, 3.5); else where = 'Heading away from the stop';
    }
    if (eta != null && eta > 0) { // prefer the feed's own prediction when it matches this bus
      let best = null; for (const a of o.stop.arrivals()) { const e = a - now; if (Math.abs(e - eta) < 90 && (best == null || Math.abs(e - eta) < Math.abs(best - eta))) best = e; }
      if (best != null) { eta = best; info.extra = '511 prediction'; } else info.extra = 'Estimated from the bus position';
    }
    info.big = eta == null ? '--' : eta <= 0 ? 'now' : eta < 60 ? 'due' : Math.round(eta / 60) + ' min';
    if (eta == null) info.bigLabel = 'Not heading to the stop';
    info.status = where || (age < 75 ? `Position updated ${Math.round(age)} s ago` : `Last report ${Math.round(age)} s ago`);
    if (where && age >= 75) info.status += ` · last report ${Math.round(age)} s ago`;
    if (age > 90) info.mood = 'stale'; if (demo) info.status += ' · mock data';
    return info;
  }
  // tap on a bus: a generous sphere around each visible bus
  const ray = new THREE.Raycaster(), mv = new THREE.Vector2(), sph = new THREE.Sphere(), hitP = new THREE.Vector3(); let dn = null;
  canvas.addEventListener('pointerdown', (e) => { dn = { x: e.clientX, y: e.clientY, t: performance.now() }; }, true);
  canvas.addEventListener('pointerup', (e) => {
    if (!dn) return; const moved = Math.hypot(e.clientX - dn.x, e.clientY - dn.y), dt = performance.now() - dn.t; dn = null;
    if (moved > 8 || dt > 600) return;
    const r = canvas.getBoundingClientRect(); mv.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1); ray.setFromCamera(mv, camera);
    let best = null, bd = 1e18;
    for (const o of D) for (const [id, b] of o.buses) {
      if (!b.mesh.visible) continue; const k = b.mesh.scale.x; sph.center.set(b.mesh.position.x, b.mesh.position.y + 3 * k, b.mesh.position.z); sph.radius = 10 * k;
      if (ray.ray.intersectSphere(sph, hitP)) { const d = hitP.distanceTo(camera.position); if (d < bd) { bd = d; best = { o, id, b }; } }
    }
    if (best) select(best.o, best.id, best.b);
  }, true);
  // while following: drag turns/tilts the view, wheel and pinch zoom (OrbitControls is switched off meanwhile)
  const pts = new Map(); let pinch0 = 0;
  canvas.addEventListener('pointerdown', (e) => { pts.set(e.pointerId, [e.clientX, e.clientY]); if (pts.size === 2) { const [a, b] = [...pts.values()]; pinch0 = Math.hypot(a[0] - b[0], a[1] - b[1]); } }, true);
  canvas.addEventListener('pointermove', (e) => {
    const p = pts.get(e.pointerId); if (!p || !S.b || !S.cam) return;
    if (pts.size === 1) S.cam.nudge(-(e.clientX - p[0]) * 0.006, (e.clientY - p[1]) * 0.35);
    pts.set(e.pointerId, [e.clientX, e.clientY]);
    if (pts.size === 2) { const [a, b] = [...pts.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]); if (pinch0 > 0) S.cam.zoomBy(pinch0 / d); pinch0 = d; }
  }, true);
  const endp = (e) => { pts.delete(e.pointerId); }; canvas.addEventListener('pointerup', endp, true); canvas.addEventListener('pointercancel', endp, true);
  canvas.addEventListener('wheel', (e) => { if (S.b && S.cam) { e.preventDefault(); S.cam.zoomBy(Math.exp(e.deltaY * 0.001)); } }, { passive: false, capture: true });

  /* ---------- per frame ---------- */
  function update() {
    const now = nowS(), s = scale(), t = performance.now(), dt = Math.min(0.1, (t - S.t) / 1000); S.t = t;
    for (const o of D) {
      o.stop.update(now);
      for (const [id, b] of o.buses) {
        const st = b.track.step(now); b.st = st; // keeps running for a driven bus too, so the real position is known when you stop
        if (b.driven && S.drive) { // the player owns this bus: the feed does not move it
          const d = S.drive; d.sim.step(dt, d.input.read(), null); b.hs = Math.PI / 2 - d.sim.h;
          b.mesh.visible = true; b.mesh.position.set(d.sim.x, (ground(d.sim.x, d.sim.z) ?? o.stop.group.position.y) + .2, d.sim.z); b.mesh.rotation.y = b.hs; b.mesh.scale.setScalar(BUS_SCALE * Math.max(1, s * 0.9));
          for (const m of b.mesh.userData.mats) m.opacity = 1;
          continue;
        }
        if (!st || st.alpha <= 0) { b.mesh.visible = false; if (b !== S.b && st && now - b.track.lastRep > 600) { scene.remove(b.mesh); o.buses.delete(id); } continue; }
        b.hs = b.hs == null ? st.heading : b.hs + angDiff(st.heading, b.hs) * (1 - Math.exp(-dt * 4)); // turn smoothly at corners
        let bx = st.x, bz = st.z;
        if (b.blend) { const k = Math.exp(-dt / 3); b.blend.x *= k; b.blend.z *= k; bx += b.blend.x; bz += b.blend.z; if (Math.hypot(b.blend.x, b.blend.z) < 0.5) b.blend = null; }
        b.mesh.visible = true; const y = (ground(bx, bz) ?? o.stop.group.position.y) + .2;
        b.mesh.position.set(bx, y, bz); b.mesh.rotation.y = b.hs; b.mesh.scale.setScalar(BUS_SCALE * Math.max(1, s * 0.9));
        for (const m of b.mesh.userData.mats) m.opacity = st.alpha;
      }
    }
    if (S.b && S.card && S.card.isOpen) {
      const b = S.b; b.mesh.userData.ring.visible = true;
      if (S.cam) { if (S.drive) S.cam.update(S.drive.sim, camera, controls, dt); else if (b.st && b.st.alpha > 0) S.cam.update({ x: b.mesh.position.x, y: b.mesh.position.y + 2, z: b.mesh.position.z, h: Math.PI / 2 - b.hs, speed: b.st.speed }, camera, controls, dt); }
      const k = b.mesh.scale.x; S.card.update(hitP.set(b.mesh.position.x, b.mesh.position.y + 9 * k, b.mesh.position.z), busInfo(S.o, b, now));
    }
  }
  return { update, poll, mock, stops: D.map((o) => o.stop), dispose() { stopDrive(); clearInterval(timer); if (S.card) S.card.dispose(); controls.enabled = true; D.forEach((o) => { o.stop.dispose(); o.buses.forEach((b) => scene.remove(b.mesh)); }); }, get demo() { return demo; } };
}

/* small beta-only selector to try the mock scenarios (only shown when the mock source is in use) */
function mockPanel(mock, poll) {
  const el = document.createElement('label');
  el.style.cssText = 'position:fixed;left:8px;bottom:230px;z-index:25;font:12px Inter,system-ui,sans-serif;color:#e8eef4;background:rgba(16,24,34,.92);border:1px solid #2a3745;border-radius:8px;padding:5px 8px;display:flex;gap:6px;align-items:center';
  el.innerHTML = 'Mock buses <select style="font:inherit;color:inherit;background:#0e141b;border:1px solid #2a3745;border-radius:5px">' + SCENARIOS.map((s) => `<option${s === mock.scenario ? ' selected' : ''}>${s}</option>`).join('') + '</select>';
  el.querySelector('select').addEventListener('change', (e) => { mock.setScenario(e.target.value); poll(true); });
  document.body.appendChild(el);
}
