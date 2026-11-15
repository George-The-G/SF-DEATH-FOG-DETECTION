/* Bus positions as a pure function of SIMULATION time (beta replay mode of buses.js).
   Every GPS report is kept (the log), so a bus can be drawn at any moment the simulation clock visits: forward, in reverse, or after a scrub.
   Each bus is a timeline of points (time, distance s along the district's street polyline). Where real GPS exists it is used and interpolated
   (monotone cubic, so a bus that stood at a stop stays flat there). Where there is no GPS (a gap, the future) the scheduled trip it belongs to
   fills in, shifted by how late or early the bus last was. When GPS for such a stretch arrives later, it replaces the estimate automatically,
   because estimates are only used where no GPS point is near. Estimated stretches are flagged (est: true) so the viewer can draw them fainter.
   No wall clock is used in here; everything takes the time to draw as an argument. */
import { llToScene } from './busgeo.js';

export const ON_ROUTE_M = 60;   // a report farther than this from the street polyline is "not on the street"
const GPS_NEAR = 150;           // s: estimates are used only inside gaps between GPS points longer than this
const MAX_GPS_GAP = 240;        // s: GPS points farther apart than this are not joined (the bus may have left the street)
const MAX_EST_GAP = 420;        // s: same for joins that involve an estimate point (scheduled stops can be a few minutes apart)
const MARGIN = 20;              // s: estimate points stay this far away from GPS points
const EXTRAP_S = 90, VMAX = 15; // no estimate and no GPS ahead: coast on the last speed for this long (and at most this fast)
const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

/* Fritsch-Carlson monotone cubic slopes for points p[i] = {t, s}; joined[i] says whether interval i..i+1 is interpolated at all */
function slopes(P, joined) {
  const n = P.length, d = new Array(n - 1), m = new Array(n).fill(0);
  for (let i = 0; i < n - 1; i++) d[i] = joined[i] ? (P[i + 1].s - P[i].s) / (P[i + 1].t - P[i].t) : null;
  for (let i = 0; i < n; i++) {
    const a = i > 0 ? d[i - 1] : null, b = i < n - 1 ? d[i] : null;
    if (a == null && b == null) m[i] = 0;
    else if (a == null) m[i] = b; else if (b == null) m[i] = a;
    else if (a * b <= 0) m[i] = 0;
    else { const h0 = P[i].t - P[i - 1].t, h1 = P[i + 1].t - P[i].t, w1 = 2 * h1 + h0, w2 = h1 + 2 * h0; m[i] = (w1 + w2) / (w1 / a + w2 / b); }
  }
  return m;
}
const hermite = (p0, p1, m0, m1, t) => {
  const h = p1.t - p0.t, u = (t - p0.t) / h, u2 = u * u, u3 = u2 * u;
  const s = (2 * u3 - 3 * u2 + 1) * p0.s + (u3 - 2 * u2 + u) * h * m0 + (-2 * u3 + 3 * u2) * p1.s + (u3 - u2) * h * m1;
  const v = ((6 * u2 - 6 * u) * p0.s + (3 * u2 - 4 * u + 1) * h * m0 + (-6 * u2 + 6 * u) * p1.s + (3 * u2 - 2 * u) * h * m1) / h;
  return { s, v };
};

/* a scheduled trip: pts = [{t, s}] ascending in t (s in metres along the polyline); only the part on the street */
export class Trip {
  constructor(id, pts) { this.id = id; this.pts = pts; this.claimedBy = null; this.dir = pts.length > 1 ? Math.sign(pts[pts.length - 1].s - pts[0].s) : 0; }
  get t0() { return this.pts[0].t; } get t1() { return this.pts[this.pts.length - 1].t; }
  /* the scheduled time the trip is at distance s (first crossing), or null if it never is */
  timeAtS(s) {
    const P = this.pts;
    for (let i = 0; i < P.length - 1; i++) {
      const a = P[i], b = P[i + 1]; if ((s - a.s) * (s - b.s) <= 0 && a.s !== b.s) return a.t + ((s - a.s) / (b.s - a.s)) * (b.t - a.t);
    }
    if (P.length && Math.abs(P[0].s - s) < 1) return P[0].t; return null;
  }
}

export class TimelineTrack {
  /* path: object with proj(x,z) -> {s,d}, at(s) -> {x,z,tx,tz}, L (a PathTrack works). stopsS: distances where buses dwell (only used to flag "dwelling"). */
  constructor(path, id, o = {}) {
    this.path = path; this.id = id; this.stopsS = o.stopsS || []; this.fix = []; this.trip = o.trip || null; this.ghost = !!o.ghost;
    this.lastRep = -1e9; this.fx = { p: 0, v: 0 }; this.sign = this.trip && this.trip.dir ? this.trip.dir : 1; this._m = null;
  }
  /* log one GPS report. Same signature as BusTrack.report, so buses.js can call either. tRep is when the bus was there (epoch s). */
  report(x, z, tRep, tNow, deg) {
    const pr = this.path.proj(x, z), on = pr.d < ON_ROUTE_M, F = this.fix;
    let i = F.length; while (i > 0 && F[i - 1].t > tRep) i--;
    if (i > 0 && F[i - 1].t === tRep) return false; // already logged
    let sg = 0;
    if (on && Number.isFinite(deg)) { const a = this.path.at(pr.s), b = deg * Math.PI / 180, dot = Math.sin(b) * a.tx - Math.cos(b) * a.tz; sg = Math.abs(dot) < 0.2 ? 0 : Math.sign(dot); }
    F.splice(i, 0, { t: tRep, s: pr.s, on, sg }); if (tRep > this.lastRep) this.lastRep = tRep; this._m = null; return true;
  }
  /* the points to interpolate, newest build cached: GPS points, plus (shifted) estimate points inside gaps and beyond the ends */
  merged() {
    if (this._m) return this._m;
    const F = this.fix, E = this.trip ? this.trip.pts : null, out = [];
    const delayAt = (g) => { const te = this.trip.timeAtS(g.s); return te == null ? null : g.t - te; };
    const addEst = (t0, t1, dA, dB) => { // estimate points whose shifted time lies in (t0, t1)
      for (const e of E) {
        let dly; // how much later than the schedule the bus is: the delay at the nearer GPS end, or blended across a gap
        if (dA == null && dB == null) dly = 0; else if (dA == null) dly = dB; else if (dB == null) dly = dA;
        else { const eA = t0 - dA, eB = t1 - dB, f = Math.min(1, Math.max(0, (e.t - eA) / Math.max(1, eB - eA))); dly = dA + (dB - dA) * f; }
        const tt = e.t + dly;
        if (tt > t0 + MARGIN && tt < t1 - MARGIN) out.push({ t: tt, s: e.s, on: true, est: true });
      }
    };
    const gps = F.filter((p) => p.on);
    if (!F.length) { if (E) for (const e of E) out.push({ t: e.t, s: e.s, on: true, est: true }); }
    else {
      // estimates before the first fix and after the last on-street fix
      if (E && gps.length) addEst(-Infinity, gps[0].t, null, delayAt(gps[0]));
      for (let i = 0; i < F.length; i++) {
        out.push({ t: F[i].t, s: F[i].s, on: F[i].on, sg: F[i].sg });
        const a = F[i], b = F[i + 1];
        if (E && b && a.on && b.on && b.t - a.t > GPS_NEAR) addEst(a.t, b.t, delayAt(a), delayAt(b));
      }
      if (E && gps.length) { const L = gps[gps.length - 1]; addEst(L.t, Infinity, delayAt(L), null); }
    }
    out.sort((p, q) => p.t - q.t);
    const joined = out.slice(0, -1).map((p, i) => { const q = out[i + 1]; return p.on && q.on && q.t - p.t <= (p.est || q.est ? MAX_EST_GAP : MAX_GPS_GAP); });
    this._m = { P: out, joined, m: out.length ? slopes(out, joined) : [] }; return this._m;
  }
  /* where is the bus at time t: { s, v, est, alpha } or null (not on the street then) */
  at(t) {
    const { P, joined, m } = this.merged(), n = P.length; if (!n) return null;
    let lo = 0, hi = n - 1; if (t < P[0].t) return null;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (P[mid].t <= t) lo = mid; else hi = mid - 1; }
    const i = lo, p = P[i];
    if (i < n - 1) {
      if (!joined[i]) return t - p.t < 1e-6 && p.on ? { s: p.s, v: 0, est: !!p.est, alpha: p.est ? 0.55 : 1 } : null;
      const q = P[i + 1], r = hermite(p, q, m[i], m[i + 1], t), est = !!(p.est || q.est || q.t - p.t > GPS_NEAR);
      return { s: Math.min(this.path.L, Math.max(0, r.s)), v: r.v, est, alpha: est ? 0.55 : 1 };
    }
    // at or beyond the last point
    if (!p.on) return null;
    if (p.est) return t - p.t < 1 ? { s: p.s, v: 0, est: true, alpha: 0.55 } : null; // the scheduled trip is over
    const age = t - p.t; if (age > 180) return null;
    let v = 0; if (i > 0 && joined[i - 1]) v = Math.max(-VMAX, Math.min(VMAX, m[i]));
    const ex = Math.min(age, EXTRAP_S), s = Math.min(this.path.L, Math.max(0, p.s + v * ex * (1 - ex / (2 * 180))));
    const alpha = age < 60 ? 1 : age < 90 ? 1 - 0.5 * smooth(60, 90, age) : 0.5 * (1 - smooth(90, 180, age));
    return { s, v: age < EXTRAP_S ? v : 0, est: age > 60, alpha };
  }
  /* same shape as PathTrack.step, with est: true while the position is an estimate */
  step(t) {
    const r = this.at(t); if (!r || r.alpha <= 0) return null;
    const pos = this.path.at(r.s); if (Math.abs(r.v) > 1.2) this.sign = Math.sign(r.v);
    this.fx.p = r.s; this.fx.v = r.v;
    const dwelling = Math.abs(r.v) < 0.4 && this.stopsS.some((x) => Math.abs(x - r.s) < 12);
    return { x: pos.x, z: pos.z, heading: Math.atan2(pos.tx * this.sign, pos.tz * this.sign), speed: Math.abs(r.v), age: t - this.lastRep, alpha: r.alpha, dwelling, est: r.est };
  }
  /* time >= t0 at which the bus reaches distance s moving toward larger s, from the points we have (linear inside a segment), or null */
  crossTime(s, t0) {
    const { P, joined } = this.merged();
    for (let i = 0; i < P.length - 1; i++) {
      if (!joined[i] || P[i + 1].t < t0) continue; const a = P[i], b = P[i + 1];
      if (a.s < s && b.s >= s) { const tc = a.t + ((s - a.s) / (b.s - a.s)) * (b.t - a.t); if (tc >= t0) return tc; }
    }
    return null;
  }
  get firstT() { const m = this.merged().P; return m.length ? m[0].t : null; }
  get lastT() { const m = this.merged().P; return m.length ? m[m.length - 1].t : null; }
}

/* All buses of one district: logged GPS tracks (by vehicle id) plus the scheduled trips that no GPS bus has claimed yet (drawn as estimates). */
export class BusReplay {
  constructor(path, o = {}) { this.path = path; this.stopsS = o.stopsS || []; this.tracks = new Map(); this.trips = []; this.ghosts = new Map(); this.claimWin = o.claimWin ?? 900; }
  /* trips: [{ id, pts: [[t_epoch_s, lat, lon], ...] }] (the format of data/bus_schedule.json). Only points on the street polyline are kept. */
  setSchedule(trips) {
    this.trips = []; this.ghosts.clear();
    for (const T of trips || []) {
      const pts = [];
      for (const [t, lat, lon] of T.p || T.pts || []) { const g = llToScene(lat, lon), pr = this.path.proj(g.x, g.z); if (pr.d < ON_ROUTE_M) pts.push({ t, s: pr.s }); }
      pts.sort((a, b) => a.t - b.t); if (pts.length < 2) continue;
      this.trips.push(new Trip(String(T.id), pts));
    }
    for (const tr of this.tracks.values()) tr.trip = null; // re-claim against the new schedule on the next sync
    this.sync();
  }
  track(id) { let t = this.tracks.get(id); if (!t) { t = new TimelineTrack(this.path, id, { stopsS: this.stopsS }); this.tracks.set(id, t); } return t; }
  /* match each GPS bus that has no trip yet to the closest unclaimed scheduled trip (same direction, within claimWin seconds of the schedule) */
  sync() {
    for (const T of this.trips) if (T.claimedBy && !this.tracks.get(T.claimedBy)) T.claimedBy = null;
    for (const tr of this.tracks.values()) {
      if (tr.trip) { tr.trip.claimedBy = tr.id; continue; }
      const f = tr.fix.find((p) => p.on); if (!f) continue;
      let best = null, bd = 1e9;
      for (const T of this.trips) {
        if (T.claimedBy && T.claimedBy !== tr.id) continue;
        if (f.sg && T.dir && f.sg !== T.dir) continue;
        const te = T.timeAtS(f.s); if (te == null) continue; const d = Math.abs(f.t - te); if (d < this.claimWin && d < bd) { bd = d; best = T; }
      }
      if (best) { tr.trip = best; best.claimedBy = tr.id; tr._m = null; }
    }
    for (const T of this.trips) {
      if (T.claimedBy) this.ghosts.delete(T.id);
      else if (!this.ghosts.has(T.id)) this.ghosts.set(T.id, new TimelineTrack(this.path, 'sched:' + T.id, { stopsS: this.stopsS, trip: T, ghost: true }));
    }
  }
  /* every track that could be on screen at time t, as [id, track] */
  active(t) {
    const out = [];
    for (const [id, tr] of this.tracks) { const a = tr.firstT, b = tr.lastT; if (a != null && t >= a - 1 && t <= b + 200) out.push([id, tr]); }
    for (const [id, tr] of this.ghosts) { const a = tr.firstT, b = tr.lastT; if (a != null && t >= a - 1 && t <= b + 1) out.push([id, tr]); }
    return out;
  }
  /* the next arrival times (epoch s) at distance s moving toward larger s, from t0, ascending */
  arrivalsAt(s, t0, n = 2) {
    const out = []; for (const tr of [...this.tracks.values(), ...this.ghosts.values()]) { const c = tr.crossTime(s, t0); if (c != null) out.push(c); }
    return out.sort((a, b) => a - b).slice(0, n);
  }
}
