/* Live skate-shuttle buses (closed beta). Loaded by app.js only with ?beta=1&buses=1.
   Per district: one Muni line (19 Polk for Potrero Hill, 66 Quintara for Sunset), a shelter at the bottom stop with a pinned
   "next bus / then" popup (busstop.js), and the line's buses drawn as small boxes that glide between the once-a-minute reports
   (busfilter.js). Data comes from our own Cloudflare Worker (edge/bus-proxy), which holds the 511 key; this page never sees a key.
   With no worker URL set (data/bus_config.json "api", or ?busapi=URL) it runs on a built-in DEMO feed that is labelled as such.
   Honest limits: positions between reports are simulated; the popup times are the feed's own predictions. */
import { llToScene } from './busgeo.js';
import { BusTrack } from './busfilter.js';
import { createStop } from './busstop.js';

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const POLL_MS = 30000;

export async function createBuses({ THREE, scene, camera, canvas, EX, getJSON, ground, getDist, QP, toast }) {
  const cfg = await getJSON('data/bus_config.json');
  const rj = await getJSON('data/routes.json');
  const api = QP.get('busapi') || cfg.api || '';
  const demo = !api;
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
    g.userData = { mats: [mat, win.material, sp.material] }; scene.add(g); return g;
  };

  /* ---------- feed ---------- */
  let feed = null, feedErr = 0, lastOk = 0, timer = 0;
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
        if (!b) { b = { track: new BusTrack({ stops: o.stopsXZ }), mesh: mkBus(o) }; o.buses.set(id, b); }
        b.track.report(p.x, p.z, v.t || now, now);
      }
      const a = (f.arrivals || {})[o.d.id]; if (a) o.stop.setArrivals({ next: a.next || [], t: a.t || f.ts || now }, 'live');
    }
  }
  async function poll() {
    if (document.hidden) return;
    try {
      const f = demo ? demoFeed(D) : await getJSON(api, { cache: 'no-store' });
      if (f.ok === false) throw new Error('feed not ok'); feed = f; ingest(f);
    } catch (e) { feedErr++; if (feedErr === 3 && toast) toast('Live buses unavailable right now'); }
    if (!demo && nowS() - lastOk > 180) for (const o of D) o.stop.setArrivals(null, 'down');
  }
  poll(); timer = setInterval(poll, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  if (demo && toast) toast('Beta buses: DEMO data (no live feed connected)');

  /* ---------- per frame ---------- */
  function update() {
    const now = nowS(), s = scale();
    for (const o of D) {
      o.stop.update(now);
      for (const [id, b] of o.buses) {
        const st = b.track.step(now);
        if (!st || st.alpha <= 0) { b.mesh.visible = false; if (st && now - b.track.lastRep > 600) { scene.remove(b.mesh); o.buses.delete(id); } continue; }
        b.mesh.visible = true; const y = (ground(st.x, st.z) ?? o.stop.group.position.y) + .2;
        b.mesh.position.set(st.x, y, st.z); b.mesh.rotation.y = st.heading; b.mesh.scale.setScalar(Math.max(1, s * 0.9));
        for (const m of b.mesh.userData.mats) m.opacity = st.alpha;
      }
    }
  }
  return { update, stops: D.map((o) => o.stop), dispose() { clearInterval(timer); D.forEach((o) => { o.stop.dispose(); o.buses.forEach((b) => scene.remove(b.mesh)); }); }, get demo() { return demo; } };
}

/* DEMO feed: two buses per line looping up the skate route's street (bottom to top) every ~4 min, reported with a little noise,
   and a timetable that repeats every 9 and 19 minutes. Used only when no worker URL is configured. */
function demoFeed(D) {
  const now = Date.now() / 1000, vehicles = [], arrivals = {};
  for (const o of D) {
    const P = o.R.poly.slice().reverse(), cum = [0]; for (let i = 1; i < P.length; i++) cum.push(cum[i - 1] + Math.hypot(P[i][0] - P[i - 1][0], P[i][2] - P[i - 1][2]));
    const Ltot = cum[cum.length - 1], T = 240;
    for (let k = 0; k < 2; k++) {
      const tRec = now - 4, ph = ((tRec / T + k * 0.5) % 1), d = ph * Ltot; let i = 1; while (i < cum.length - 1 && cum[i] < d) i++;
      const f = (d - cum[i - 1]) / ((cum[i] - cum[i - 1]) || 1), j = () => (Math.random() - .5) * 10;
      vehicles.push({ id: o.d.id + k, district: o.d.id, x: P[i - 1][0] + (P[i][0] - P[i - 1][0]) * f + j(), z: P[i - 1][2] + (P[i][2] - P[i - 1][2]) * f + j(), t: tRec });
    }
    arrivals[o.d.id] = { t: now, next: [now + (540 - (now % 540)), now + (540 - (now % 540)) + 600] };
  }
  return { ok: true, ts: now, vehicles, arrivals };
}
