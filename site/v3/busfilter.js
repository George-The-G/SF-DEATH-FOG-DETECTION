/* Smooth, believable bus motion from one GPS report a minute.
   Each axis (east, north) has its own constant-velocity Kalman filter: state [position, velocity], process noise q (how hard a bus can
   speed up or brake, in m^2/s^3), measurement noise r (GPS plus map error, metres^2). Reports are applied at the time they were
   RECORDED, then the filter runs forward to now, so a report that is 20 s old still corrects the right moment.
   What is drawn = filter position + a correction offset that melts away over a few seconds, so a new report never makes a bus jump.
   Dwell: if a stop list is given and the extrapolated bus reaches a stop, it waits there (default 20 s) and then pulls away.
   This is simulation between reports; the popup times always come from the feed's own predictions. */
const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
function ax(p, t) { return { p, v: 0, P00: 15 * 15, P01: 0, P11: 100, t }; }
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
    this.tau = o.tau ?? 3; this.dwell = o.dwell ?? 20; this.stops = o.stops || []; this.stopR = o.stopR ?? 12;
    this.fx = null; this.fz = null; this.lastRep = -1e9; this.nRep = 0;
    this.offX = 0; this.offZ = 0; this.offT = 0; this.offTau = this.tau;
    this.hist = []; this.dwellUntil = 0; this.atStop = -1; this.resume = -1e9; this.heading = o.heading ?? 0;
  }
  pos(t) { return { x: this.fx.p + this.offX * Math.exp(-(t - this.offT) / this.offTau), z: this.fz.p + this.offZ * Math.exp(-(t - this.offT) / this.offTau) }; }
  /* x, z scene metres; tRep when the bus was there (epoch s); tNow the clock. Returns false if ignored. */
  report(x, z, tRep, tNow) {
    if (tRep <= this.lastRep) return false;
    if (!this.fx) {
      this.fx = ax(x, tRep); this.fz = ax(z, tRep); this.lastRep = tRep; this.nRep = 1; this.offT = tNow; this.snap(tRep); this.run(tNow); return true;
    }
    this.run(tNow); const before = this.pos(tNow); // where it is drawn right now
    this.rewind(tRep); // back to the filter state just before the moment the report describes
    this.run(tRep); const inn = Math.hypot(x - this.fx.p, z - this.fz.p);
    if (inn > this.gate) { // far off (or the first reports were slow): re-seed from the new report, with the speed implied by the move, and ease across
      const dtr = tRep - this.lastRep, ox = this.fx.p, oz = this.fz.p; this.fx = ax(x, tRep); this.fz = ax(z, tRep);
      if (dtr > 0 && dtr < 150) { this.fx.v = (x - ox) / dtr; this.fz.v = (z - oz) / dtr; }
      this.offTau = Math.max(4, inn / (0.6 * this.vmax)); // catch up no faster than a bus can drive
    } else { update(this.fx, x, this.r); update(this.fz, z, this.r); this.offTau = this.tau; }
    this.clampV(); this.lastRep = tRep; this.nRep++;
    this.run(tNow);
    const after = { x: this.fx.p, z: this.fz.p }; this.offX = before.x - after.x; this.offZ = before.z - after.z; this.offT = tNow;
    return true;
  }
  snap(t) { const h = this.hist, l = h[h.length - 1]; if (l && t - l.t < 1) return; h.push({ t, fx: { ...this.fx }, fz: { ...this.fz }, d: this.dwellUntil, a: this.atStop, r: this.resume }); if (h.length > 200) h.shift(); }
  rewind(t) { // restore the newest snapshot not later than t (if none that old, keep the current state)
    const h = this.hist; let i = h.length - 1; while (i >= 0 && h[i].t > t) i--;
    if (i < 0) return; const o = h[i]; this.fx = { ...o.fx }; this.fz = { ...o.fz }; this.dwellUntil = o.d; this.atStop = o.a; this.resume = o.r; h.length = i + 1;
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
