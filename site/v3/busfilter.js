/* Smooth, believable bus motion from one GPS report a minute.
   Each axis (east, north) has its own constant-velocity Kalman filter: state [position, velocity], process noise q (how hard a bus can
   speed up or brake, in m^2/s^3), measurement noise r (GPS plus map error, metres^2). Reports are applied at the time they were
   RECORDED, then the filter runs forward to now, so a report that is 20 s old still corrects the right moment.
   What is drawn = filter position + a correction offset that melts away over a few seconds, so a new report never makes a bus jump.
   Dwell: if a stop list is given and the extrapolated bus reaches a stop, it waits there (default 20 s) and then pulls away.
   This is simulation between reports; the popup times always come from the feed's own predictions. */
const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
function ax(p, t, v = 0, P11 = 100) { return { p, v, P00: 15 * 15, P01: 0, P11, t }; }
function predict(s, t, q) {
  const dt = t - s.t; if (dt <= 0) return; s.p += s.v * dt;
  const P00 = s.P00 + 2 * dt * s.P01 + dt * dt * s.P11 + q * dt ** 3 / 3, P01 = s.P01 + dt * s.P11 + q * dt * dt / 2, P11 = s.P11 + q * dt;
  s.P00 = P00; s.P01 = P01; s.P11 = P11; s.t = t;
}
function update(s, z, r) {
  const S = s.P00 + r, k0 = s.P00 / S, k1 = s.P01 / S, y = z - s.p, p01 = s.P01;
  s.p += k0 * y; s.v += k1 * y; s.P00 = (1 - k0) * s.P00; s.P01 = (1 - k0) * s.P01; s.P11 = s.P11 - k1 * p01;
}
export class BusTrack {
  constructor(o = {}) {
    this.q = o.q ?? 1.5; this.r = o.r ?? 15 * 15; this.vmax = o.vmax ?? 22; this.gate = o.gate ?? 150;
    this.tau = o.tau ?? 3; this.v0 = o.v0 ?? 5.5; this.dwell = o.dwell ?? 20; this.stops = o.stops || []; this.stopR = o.stopR ?? 12;
    this.fx = null; this.fz = null; this.lastRep = -1e9; this.nRep = 0;
    this.offX = 0; this.offZ = 0; this.offT = 0; this.offTau = this.tau;
    this.hist = []; this.dwellUntil = 0; this.atStop = -1; this.resume = -1e9; this.heading = o.heading ?? 0;
  }
  pos(t) { return { x: this.fx.p + this.offX * Math.exp(-(t - this.offT) / this.offTau), z: this.fz.p + this.offZ * Math.exp(-(t - this.offT) / this.offTau) }; }
  /* x, z scene metres; tRep when the bus was there (epoch s); tNow the clock. Returns false if ignored. */
  report(x, z, tRep, tNow, bearingDeg) {
    if (tRep <= this.lastRep) return false;
    if (!this.fx) {
      // first sighting: no speed yet, but the feed's compass bearing and a typical city-bus pace give a fair start (else it would sit still until the next report)
      const hasB = Number.isFinite(bearingDeg), sv = this.seedVel(bearingDeg);
      this.fx = ax(x, tRep, sv[0], hasB ? 16 : 100); this.fz = ax(z, tRep, sv[1], hasB ? 16 : 100); this.lastRep = tRep; this.nRep = 1; this.offT = tNow; this.snap(tRep); this.run(tNow); return true;
    }
    this.run(tNow); const before = this.pos(tNow); // where it is drawn right now
    this.rewind(tRep); // back to the filter state just before the moment the report describes
    this.run(tRep); const inn = Math.hypot(x - this.fx.p, z - this.fz.p);
    if (inn > this.gate) { // far off (or the first reports were slow): re-seed from the new report, with the speed implied by the move, and ease across
      const dtr = tRep - this.lastRep, ox = this.fx.p, oz = this.fz.p; this.fx = ax(x, tRep); this.fz = ax(z, tRep);
      if (dtr > 0 && dtr < 150) { this.fx.v = (x - ox) / dtr; this.fz.v = (z - oz) / dtr; }
      this.offTau = Math.max(4, inn / (0.6 * this.vmax)); // catch up no faster than a bus can drive
    } else { update(this.fx, x, this.r); update(this.fz, z, this.r); this.offTau = this.tau; }
    this.clampV(); this.alignBearing(bearingDeg); this.lastRep = tRep; this.nRep++;
    this.run(tNow);
    const after = { x: this.fx.p, z: this.fz.p }; this.offX = before.x - after.x; this.offZ = before.z - after.z; this.offT = tNow; this.offTau = Math.max(this.offTau, Math.hypot(this.offX, this.offZ) / 10); // a correction never makes the drawn bus move more than ~10 m/s faster than it drives
    return true;
  }
  snap(t) { const h = this.hist, l = h[h.length - 1]; if (l && t - l.t < 1) return; h.push({ t, fx: { ...this.fx }, fz: { ...this.fz }, d: this.dwellUntil, a: this.atStop, r: this.resume }); if (h.length > 200) h.shift(); }
  rewind(t) { // restore the newest snapshot not later than t (if none that old, keep the current state)
    const h = this.hist; let i = h.length - 1; while (i >= 0 && h[i].t > t) i--;
    if (i < 0) return; const o = h[i]; this.fx = { ...o.fx }; this.fz = { ...o.fz }; this.dwellUntil = o.d; this.atStop = o.a; this.resume = o.r; h.length = i + 1;
  }
  seedVel(deg) { if (!Number.isFinite(deg)) return [0, 0]; const b = deg * Math.PI / 180; return [Math.sin(b) * this.v0, -Math.cos(b) * this.v0]; }
  clampPos() {}
  alignBearing(deg) { // if the feed's compass bearing says the bus now points the other way (turned round, left the stop), flip the speed to match
    if (!Number.isFinite(deg)) return; const b = deg * Math.PI / 180, ux = Math.sin(b), uz = -Math.cos(b), sp = Math.hypot(this.fx.v, this.fz.v);
    if (this.fx.v * ux + this.fz.v * uz < 0.3 * sp) { const s2 = Math.min(Math.max(sp, 2), this.v0); this.fx.v = ux * s2; this.fz.v = uz * s2; this.fx.P11 = this.fz.P11 = 16; }
  }
  clampV() { const v = Math.hypot(this.fx.v, this.fz.v); if (v > this.vmax) { this.fx.v *= this.vmax / v; this.fz.v *= this.vmax / v; } }
  run(tNow) { // run the filter forward in small steps so a stop can hold the bus
    const sx = this.fx, sz = this.fz; let t = sx.t;
    while (t < tNow - 1e-6) {
      const dt = Math.min(0.5, tNow - t), t2 = t + dt;
      if (this.dwellUntil > t2) { sx.t = sz.t = t2; sx.P00 += this.q * dt * 0.1; sz.P00 += this.q * dt * 0.1; t = t2; continue; }
      const k = this.resume > -1e8 ? smooth(0, 4, t2 - this.resume) : 1, vx = sx.v, vz = sz.v; // gentle pull-away after a stop
      sx.v = vx * k; sz.v = vz * k; predict(sx, t2, this.q); predict(sz, t2, this.q); sx.v = vx; sz.v = vz;
      if (this.stops.length) this.checkStops(t2);
      t = t2; this.snap(t);
    }
    sx.t = sz.t = tNow;
  }
  checkStops(t) {
    const x = this.fx.p, z = this.fz.p; let near = -1;
    for (let i = 0; i < this.stops.length; i++) if (Math.hypot(x - this.stops[i].x, z - this.stops[i].z) < this.stopR) { near = i; break; }
    if (near >= 0 && near !== this.atStop && Math.hypot(this.fx.v, this.fz.v) > 0.5) { this.atStop = near; this.dwellUntil = t + this.dwell; this.resume = t + this.dwell; }
    else if (near < 0) this.atStop = -1;
  }
  /* state for drawing at clock time tNow (epoch s) */
  step(tNow) {
    if (!this.fx) return null;
    const age = tNow - this.lastRep;
    this.run(age > 90 ? this.lastRep + 90 : tNow); // beyond 90 s with no report it stops extrapolating
    const p = this.pos(Math.min(tNow, this.fx.t + 1e9));
    const sp = Math.hypot(this.fx.v, this.fz.v);
    if (sp > 1.5 && !(this.dwellUntil > tNow)) this.heading = Math.atan2(this.fx.v, this.fz.v); // radians, 0 = +z, scene y-rotation
    const alpha = age < 60 ? 1 : age < 90 ? 1 - 0.5 * smooth(60, 90, age) : age < 180 ? 0.5 * (1 - smooth(90, 180, age)) : 0;
    return { x: p.x, z: p.z, heading: this.heading, speed: sp, age, alpha, dwelling: this.dwellUntil > tNow };
  }
}

/* Same filter, but the bus is tied to a street polyline: the state is distance along the street (s), so extrapolation follows corners,
   stops at the end, and the heading is the street's. Use only for buses that really are on that street; BusTrack (free space) is the fallback.
   path: [[x, y, z], ...] scene metres (as routes.json "poly"). stopsS: distances along the path (metres) where the bus dwells. */
export class PathTrack extends BusTrack {
  constructor(path, o = {}) {
    super(o); this.P = path; this.cum = [0];
    for (let i = 1; i < path.length; i++) this.cum.push(this.cum[i - 1] + Math.hypot(path[i][0] - path[i - 1][0], path[i][2] - path[i - 1][2]));
    this.L = this.cum[this.cum.length - 1]; this.stopsS = o.stopsS || []; this.stops = this.stopsS; this.sign = 1; this.lastD = 0;
  }
  proj(x, z) { // nearest point on the polyline: distance along it and how far off it the point is
    let best = { s: 0, d: 1e9 };
    for (let i = 1; i < this.P.length; i++) {
      const a = this.P[i - 1], b = this.P[i], dx = b[0] - a[0], dz = b[2] - a[2], l2 = dx * dx + dz * dz || 1, f = Math.min(1, Math.max(0, ((x - a[0]) * dx + (z - a[2]) * dz) / l2));
      const d = Math.hypot(x - a[0] - dx * f, z - a[2] - dz * f); if (d < best.d) best = { s: this.cum[i - 1] + Math.sqrt(l2) * f, d };
    }
    return best;
  }
  at(s) {
    s = Math.min(this.L, Math.max(0, s)); let i = 1; while (i < this.cum.length - 1 && this.cum[i] < s) i++;
    const f = (s - this.cum[i - 1]) / ((this.cum[i] - this.cum[i - 1]) || 1), a = this.P[i - 1], b = this.P[i], dx = b[0] - a[0], dz = b[2] - a[2], l = Math.hypot(dx, dz) || 1;
    return { x: a[0] + dx * f, z: a[2] + dz * f, tx: dx / l, tz: dz / l };
  }
  tangentSign(deg) { // +1 if the bearing points along increasing s, -1 against; 0 if unknown
    if (!Number.isFinite(deg)) return 0; const t = this.at(this.fx ? this.fx.p : 0), b = deg * Math.PI / 180, dot = Math.sin(b) * t.tx + -Math.cos(b) * t.tz; return Math.abs(dot) < 0.2 ? 0 : Math.sign(dot);
  }
  seedVel(deg) { const t = this.tangentSign0 ?? 0; return [t * this.v0, 0]; }
  report(x, z, tRep, tNow, deg) {
    const pr = this.proj(x, z); this.lastD = pr.d;
    if (!this.fx) { const t = this.at(pr.s), b = (deg || 0) * Math.PI / 180; this.tangentSign0 = Number.isFinite(deg) ? Math.sign(Math.sin(b) * t.tx - Math.cos(b) * t.tz) : 0; }
    return super.report(pr.s, 0, tRep, tNow, deg);
  }
  alignBearing(deg) { const sg = this.tangentSign(deg); if (!sg) return; if (this.fx.v * sg < 0) { this.fx.v = sg * Math.min(Math.max(Math.abs(this.fx.v), 2), this.v0); this.fx.P11 = 16; } }
  clampPos() { const s = this.fx; if (s.p > this.L) { s.p = this.L; s.v = 0; } else if (s.p < 0) { s.p = 0; s.v = 0; } this.fz.p = 0; this.fz.v = 0; }
  checkStops(t) {
    const s = this.fx.p; let near = -1;
    for (let i = 0; i < this.stopsS.length; i++) if (Math.abs(s - this.stopsS[i]) < this.stopR) { near = i; break; }
    if (near >= 0 && near !== this.atStop && Math.abs(this.fx.v) > 0.5) { this.atStop = near; this.dwellUntil = t + this.dwell; this.resume = t + this.dwell; }
    else if (near < 0) this.atStop = -1;
  }
  step(tNow) {
    const st = super.step(tNow); if (!st) return null;
    const pos = this.at(st.x), v = this.fx.v; if (Math.abs(v) > 1.5 && !st.dwelling) this.sign = Math.sign(v);
    return { ...st, x: pos.x, z: pos.z, heading: Math.atan2(pos.tx * this.sign, pos.tz * this.sign), speed: Math.abs(v) };
  }
}
