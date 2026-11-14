/* Skatable world (beta): "Take control" during the route flyover.
   Loaded with import() only when somebody taps Take control (?beta=1), so the normal page never fetches it. No three.js import: app.js passes THREE in.

   Everything here uses data the page already holds, no new files:
     - ground height: the terrain grid (terrainH.at) and, on a street, the street's own height (G.y, the lifted ribbon samples)
     - streets: the ribbon sample table G (x, y, z, st), indexed in a 32 m grid so "nearest street" is a handful of comparisons
     - walls: building boxes and outlines from buildings.bin (group.userData.col, written by buildBuildings), indexed in a 32 m grid
     - water and very steep ground (off a street) block, like walls

   Units: scene metres. Horizontal = real metres. Heights in the scene are stretched by EX (1.5), so slopes are divided by EX before they push the skater.
   The skater is a "toy": drawn about 7x life size under a high chase camera (same look as the flyover), but moves at toy speeds so a 900 m street takes about a minute.

   Sections: 1 world (ground, streets, walls) 2 simulation 3 chase camera 4 input and joystick 5 controller. 1 and 2 have no DOM or three.js and are tested by tools/skate_test.mjs. */

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
const angDiff = (a, b) => { let d = (a - b) % (Math.PI * 2); if (d > Math.PI) d -= Math.PI * 2; if (d < -Math.PI) d += Math.PI * 2; return d; };

export const TUNE = {
  road: { half: 8.25, blend: 3, find: 14 },          // how far from a street sample counts as the street: district streets have two lane lines 10 m apart, each a 6.5 m ribbon (5 + 3.25); city streets one line. Blend to terrain over 3 m beyond it; find = search radius
  r: 1.8,                                            // collision radius of the skater (m)
  push: 9, vPush: 12, vMax: 24, gEff: 15,            // push accel (m/s2), speed the push fades out at, top speed, gravity per unit true grade (m/s2)
  brake: 16, drag: 0.05, roll: 0.4, offDrag: 4,      // brake, air drag (1/s), rolling friction (m/s2), off-street drag multiplier
  turn: 2.0, grip: 9,                                // max turn rate (rad/s), side-slip decay (1/s) on dry
  slopeBlock: 0.45,                                  // off-street, true grade steeper than this uphill is a wall
  cam: { back: 70, up: 28, look: 30, lookUp: 6, follow: 2.2, spring: 4 },
  scale: 7,                                          // toy size of the skater model
  lane: null,                                        // metres to the right of the street centre line to hold when the stick is centred (null = no lane keeping)
  boost: { vmult: 1.3, push: 1.8, dur: 1.6, regen: 7, min: 0.25 }, // boost button: top speed x vmult, push x push, lasts dur s from full, refills in regen s, usable again once the charge is back over min
};

/* Controllable things. Each one is a bundle of sim tuning (anything in TUNE can be overridden), a boost, camera and UI options, so every
   entity gets the same joystick, boost button and stop button and only the numbers differ. Add an entry here to make something else drivable
   (a bus, a cable car, ...): the sim, input and UI (createSim, createInput) read only this. Values are "arcade": speeds are toy speeds (m/s). */
export const ENTITIES = {
  skater: { id: 'skater', label: 'Skater', exit: null, tune: {}, cam: {}, pitch: false },
  bus: {
    id: 'bus', label: 'Bus', exit: 'Stop driving', pitch: true,
    tune: { r: 4.2, vMax: 17, push: 5.5, vPush: 12, brake: 13, turn: 1.15, grip: 8, lane: 2.6, boost: { vmult: 1.45, push: 2.2, dur: 2.5, regen: 9, min: 0.25 } }, // 17 m/s is about 38 mph, boost about 55 mph
    cam: { back: 78, up: 46, look: 22, lookUp: 5, follow: 0.8, spring: 3.2 },
  },
};

/* ---------------------------------------------------------------------- 1. world */
const CELL = 32;

function csrGrid(n, x0, z0, nx, nz, cover) { // cover(i, add(cx, cz)) calls add for every cell item i touches
  const counts = new Int32Array(nx * nz + 1); let items = null;
  for (let pass = 0; pass < 2; pass++) {
    const fill = pass === 1 ? counts.slice() : null; // fill = running write offsets
    for (let i = 0; i < n; i++) {
      cover(i, (cx, cz) => {
        if (cx < 0 || cz < 0 || cx >= nx || cz >= nz) return;
        const c = cz * nx + cx;
        if (pass === 0) counts[c + 1]++; else items[fill[c]++] = i;
      });
    }
    if (pass === 0) { for (let c = 0; c < nx * nz; c++) counts[c + 1] += counts[c]; items = new Int32Array(counts[nx * nz]); }
  }
  return { off: counts, items, nx, nz, x0, z0 };
}

export function buildStreetIndex(G) {
  let x0 = 1e18, x1 = -1e18, z0 = 1e18, z1 = -1e18;
  for (let k = 0; k < G.n; k++) { x0 = Math.min(x0, G.x[k]); x1 = Math.max(x1, G.x[k]); z0 = Math.min(z0, G.z[k]); z1 = Math.max(z1, G.z[k]); }
  x0 -= CELL; z0 -= CELL;
  const nx = Math.ceil((x1 - x0) / CELL) + 2, nz = Math.ceil((z1 - z0) / CELL) + 2;
  const g = csrGrid(G.n, x0, z0, nx, nz, (k, add) => add(Math.floor((G.x[k] - x0) / CELL), Math.floor((G.z[k] - z0) / CELL)));
  return g;
}

export function buildWallIndex(col) { // col = buildings group.userData.col
  const { nb, np, xyQ, wdQ, cx, cn, bw, bd, pnv, pv } = col;
  const polyStart = new Uint32Array(np + 1), pbox = new Float32Array(np * 4);
  for (let k = 0; k < np; k++) {
    const s = polyStart[k], n = pnv[k]; polyStart[k + 1] = s + n;
    let a = 1e18, b = -1e18, c = 1e18, d = -1e18;
    for (let i = 0; i < n; i++) { const x = pv[(s + i) * 2] * xyQ, z = -pv[(s + i) * 2 + 1] * xyQ; a = Math.min(a, x); b = Math.max(b, x); c = Math.min(c, z); d = Math.max(d, z); }
    pbox[k * 4] = a; pbox[k * 4 + 1] = b; pbox[k * 4 + 2] = c; pbox[k * 4 + 3] = d;
  }
  const x0 = -CELL, z0 = -16500, nx = Math.ceil((17400 - x0) / CELL), nz = Math.ceil((200 - z0) / CELL), M = 3;
  const grid = csrGrid(nb + np, x0, z0, nx, nz, (i, add) => {
    let a, b, c, d;
    if (i < nb) { const e = Math.hypot(bw[i] * wdQ, bd[i] * wdQ) / 2 + M, X = cx[i] * xyQ, Z = -cn[i] * xyQ; a = X - e; b = X + e; c = Z - e; d = Z + e; }
    else { const k = i - nb; a = pbox[k * 4] - M; b = pbox[k * 4 + 1] + M; c = pbox[k * 4 + 2] - M; d = pbox[k * 4 + 3] + M; }
    const i0 = Math.floor((a - x0) / CELL), i1 = Math.floor((b - x0) / CELL), j0 = Math.floor((c - z0) / CELL), j1 = Math.floor((d - z0) / CELL);
    for (let j = j0; j <= j1; j++) for (let ii = i0; ii <= i1; ii++) add(ii, j);
  });
  return { ...grid, polyStart, pbox };
}

export function createWorld({ terrainH, EX = 1.5, G, col }) {
  const T = TUNE.road;
  const sg = G && G.n ? buildStreetIndex(G) : null;
  const wg = col ? buildWallIndex(col) : null;
  const q = { found: false, d: 0, e: 0, tx: 1, tz: 0, y: 0, k: -1 }; // reused result of road()

  /* nearest street segment within R of (x, z). With a heading (hx, hz) streets running across it count as further away, so the skater
     follows the street it is on at a crossing. e = signed offset from the centre line (to the right of the travel direction positive). */
  function road(x, z, hx, hz, R = T.find) {
    q.found = false; if (!sg) return q;
    const ci = Math.floor((x - sg.x0) / CELL), cj = Math.floor((z - sg.z0) / CELL), have = hx !== undefined;
    let best = 1e18;
    for (let j = cj - 1; j <= cj + 1; j++) for (let i = ci - 1; i <= ci + 1; i++) {
      if (i < 0 || j < 0 || i >= sg.nx || j >= sg.nz) continue;
      const c = j * sg.nx + i;
      for (let p = sg.off[c]; p < sg.off[c + 1]; p++) {
        const k0 = sg.items[p];
        for (let s = 0; s < 2; s++) { // segments (k0, k0+1) and (k0-1, k0)
          const a = s ? k0 - 1 : k0, b = a + 1;
          if (a < 0 || b >= G.n || G.st[a] !== G.st[b]) continue;
          const ax = G.x[a], az = G.z[a], bx = G.x[b] - ax, bz = G.z[b] - az, L2 = bx * bx + bz * bz;
          if (L2 < 1e-6 || L2 > 3600) continue; // coincident points, or a jump between separate strips (> 60 m)
          const t = clamp(((x - ax) * bx + (z - az) * bz) / L2, 0, 1), px = ax + t * bx - x, pz = az + t * bz - z, d = Math.hypot(px, pz);
          if (d > R) continue;
          const L = Math.sqrt(L2);
          const score = d + (have ? 8 * (1 - Math.abs((bx * hx + bz * hz) / L)) : 0);
          if (score < best) {
            best = score; q.found = true; q.d = d; q.k = t < 0.5 ? a : b;
            let tx = bx / L, tz = bz / L; if (have && tx * hx + tz * hz < 0) { tx = -tx; tz = -tz; }
            q.tx = tx; q.tz = tz; q.y = G.y[a] + t * (G.y[b] - G.y[a]);
            q.e = px * tz - pz * tx; // signed offset of the skater from the centre line: (skater - nearest) . (-tz, tx)
          }
        }
      }
    }
    return q;
  }

  /* ground height under (x, z): terrain, blended onto the street's own height near a street. Returns the number; w (0..1, street weight) is left in ground.w */
  function ground(x, z) {
    const ty = terrainH ? terrainH.at(x, z) : 0;
    road(x, z, undefined, undefined, T.half + T.blend + 1);
    if (!q.found) { ground.w = 0; return ty; }
    const w = 1 - smooth(T.half, T.half + T.blend, q.d); ground.w = w;
    return ty + (q.y - ty) * w;
  }
  ground.w = 0;

  /* push a circle (x, z, r) out of buildings. Returns hits[] of {x, z, nx, nz} after moving it: out.x/out.z are the corrected position, out.n the number of contacts. */
  const out = { x: 0, z: 0, n: 0, nx: 0, nz: 0, fail: false };
  function walls(x, z, r) {
    out.x = x; out.z = z; out.n = 0; out.nx = 0; out.nz = 0; out.fail = false;
    if (!wg) return out;
    const { nb, xyQ, wdQ, cx, cn, bw, bd, ba, pv } = col;
    for (let iter = 0; iter < 3; iter++) {
      const ci = Math.floor((out.x - wg.x0) / CELL), cj = Math.floor((out.z - wg.z0) / CELL);
      if (ci < 0 || cj < 0 || ci >= wg.nx || cj >= wg.nz) break;
      const c = cj * wg.nx + ci; let moved = false;
      for (let p = wg.off[c]; p < wg.off[c + 1]; p++) {
        const id = wg.items[p];
        if (id < nb) {
          const th = ba[id] * Math.PI / 256, co = Math.cos(th), si = Math.sin(th), dx = out.x - cx[id] * xyQ, dz = out.z + cn[id] * xyQ;
          const lx = dx * co - dz * si, lz = dx * si + dz * co, hw = bw[id] * wdQ / 2, hd = bd[id] * wdQ / 2;
          if (Math.abs(lx) > hw + r || Math.abs(lz) > hd + r) continue;
          const qx = clamp(lx, -hw, hw), qz = clamp(lz, -hd, hd), ex = lx - qx, ez = lz - qz, d2 = ex * ex + ez * ez;
          if (d2 >= r * r) continue;
          let nlx, nlz, pen;
          if (d2 > 1e-9) { const d = Math.sqrt(d2); nlx = ex / d; nlz = ez / d; pen = r - d; }
          else { const px = hw - Math.abs(lx), pz = hd - Math.abs(lz); if (px < pz) { nlx = lx < 0 ? -1 : 1; nlz = 0; pen = px + r; } else { nlx = 0; nlz = lz < 0 ? -1 : 1; pen = pz + r; } }
          const nx = nlx * co + nlz * si, nz = -nlx * si + nlz * co;
          out.x += nx * pen; out.z += nz * pen; out.nx += nx; out.nz += nz; out.n++; moved = true;
        } else {
          const k = id - nb, a0 = wg.pbox, s0 = wg.polyStart[k], n = wg.polyStart[k + 1] - s0;
          if (out.x < a0[k * 4] - r || out.x > a0[k * 4 + 1] + r || out.z < a0[k * 4 + 2] - r || out.z > a0[k * 4 + 3] + r) continue;
          let bd2 = 1e18, bnx = 0, bnz = 0, inside = false;
          for (let i = 0, j = n - 1; i < n; j = i++) {
            const ax = pv[(s0 + j) * 2] * xyQ, az = -pv[(s0 + j) * 2 + 1] * xyQ, bx = pv[(s0 + i) * 2] * xyQ, bz = -pv[(s0 + i) * 2 + 1] * xyQ;
            if ((az > out.z) !== (bz > out.z) && out.x < (bx - ax) * (out.z - az) / (bz - az) + ax) inside = !inside;
            const ux = bx - ax, uz = bz - az, L2 = ux * ux + uz * uz || 1, t = clamp(((out.x - ax) * ux + (out.z - az) * uz) / L2, 0, 1);
            const ex = out.x - (ax + t * ux), ez = out.z - (az + t * uz), d2 = ex * ex + ez * ez;
            if (d2 < bd2) { bd2 = d2; bnx = ex; bnz = ez; }
          }
          const d = Math.sqrt(bd2);
          if (!inside && d >= r) continue;
          let nx, nz, pen;
          if (d > 1e-6) { nx = bnx / d; nz = bnz / d; } else { nx = 1; nz = 0; }
          if (inside) { nx = -nx; nz = -nz; pen = d + r; } else pen = r - d; // inside: leave through the nearest edge
          out.x += nx * pen; out.z += nz * pen; out.nx += nx; out.nz += nz; out.n++; moved = true;
        }
      }
      if (!moved) break;
      if (iter === 2) out.fail = true; // still being pushed after 3 passes: wedged between overlapping buildings
    }
    const l = Math.hypot(out.nx, out.nz); if (l > 1e-9) { out.nx /= l; out.nz /= l; }
    return out;
  }

  /* closest street point to (x, z) within maxR that is clear of walls (for "Back on the street"). Returns {x, z, tx, tz, k} or null. */
  function nearestStreet(x, z, maxR = 400) {
    if (!sg) return null;
    const rings = Math.ceil(maxR / CELL), ci = Math.floor((x - sg.x0) / CELL), cj = Math.floor((z - sg.z0) / CELL);
    let best = 1e18, bk = -1;
    for (let ring = 0; ring <= rings; ring++) {
      for (let j = cj - ring; j <= cj + ring; j++) for (let i = ci - ring; i <= ci + ring; i++) {
        if (Math.max(Math.abs(i - ci), Math.abs(j - cj)) !== ring || i < 0 || j < 0 || i >= sg.nx || j >= sg.nz) continue;
        const c = j * sg.nx + i;
        for (let p = sg.off[c]; p < sg.off[c + 1]; p++) {
          const k = sg.items[p], d = (G.x[k] - x) ** 2 + (G.z[k] - z) ** 2;
          if (d < best && !walls(G.x[k], G.z[k], TUNE.r + 0.3).n) { best = d; bk = k; }
        }
      }
      if (bk >= 0 && Math.sqrt(best) <= ring * CELL) break;
    }
    if (bk < 0 || Math.sqrt(best) > maxR) return null;
    let tx = 1, tz = 0; const a = G.st[bk + 1] === G.st[bk] ? bk : bk - 1, b = a + 1;
    if (a >= 0 && b < G.n && G.st[a] === G.st[b]) { const l = Math.hypot(G.x[b] - G.x[a], G.z[b] - G.z[a]) || 1; tx = (G.x[b] - G.x[a]) / l; tz = (G.z[b] - G.z[a]) / l; }
    return { x: G.x[bk], z: G.z[bk], tx, tz, k: bk };
  }

  /* roof height (world y, already stretched by EX) of the tallest building covering (x, z), or -Infinity. Needs bb, bh, pb, ph, hQ in col (older tests omit them: then -Infinity).
     Used by the chase camera so it never ends up inside, or looks through, a building. */
  function roof(x, z) {
    if (!wg || !col.bh) return -Infinity;
    const ci = Math.floor((x - wg.x0) / CELL), cj = Math.floor((z - wg.z0) / CELL);
    if (ci < 0 || cj < 0 || ci >= wg.nx || cj >= wg.nz) return -Infinity;
    const { nb, xyQ, hQ, wdQ, cx, cn, bw, bd, ba, bb, bh, pb, ph, pv } = col, c = cj * wg.nx + ci; let top = -Infinity;
    for (let p = wg.off[c]; p < wg.off[c + 1]; p++) {
      const id = wg.items[p];
      if (id < nb) {
        const th = ba[id] * Math.PI / 256, co = Math.cos(th), si = Math.sin(th), dx = x - cx[id] * xyQ, dz = z + cn[id] * xyQ;
        const lx = dx * co - dz * si, lz = dx * si + dz * co;
        if (Math.abs(lx) <= bw[id] * wdQ / 2 && Math.abs(lz) <= bd[id] * wdQ / 2) top = Math.max(top, (bb[id] + bh[id]) * hQ * EX);
      } else {
        const k = id - nb, s0 = wg.polyStart[k], n = wg.polyStart[k + 1] - s0, b = wg.pbox;
        if (x < b[k * 4] || x > b[k * 4 + 1] || z < b[k * 4 + 2] || z > b[k * 4 + 3]) continue;
        let inside = false;
        for (let i = 0, j = n - 1; i < n; j = i++) {
          const ax = pv[(s0 + j) * 2] * xyQ, az = -pv[(s0 + j) * 2 + 1] * xyQ, bx = pv[(s0 + i) * 2] * xyQ, bz = -pv[(s0 + i) * 2 + 1] * xyQ;
          if ((az > z) !== (bz > z) && x < (bx - ax) * (z - az) / (bz - az) + ax) inside = !inside;
        }
        if (inside) top = Math.max(top, (pb[k] + ph[k]) * hQ * EX);
      }
    }
    return top;
  }

  return { road, ground, roof, walls, nearestStreet, EX, terrainH, hasWalls: !!wg, hasStreets: !!sg, q };
}

/* ---------------------------------------------------------------------- 2. simulation */
export function createSim(world, over) { // over: optional TUNE overrides (the bus uses a bigger collision radius and a lower top speed)
  const T = over ? { ...TUNE, ...over } : TUNE, EX = world.EX;
  const s = { charge: 1, boosting: false, lock: false, x: 0, z: 0, h: 0, vx: 0, vz: 0, y: 0, speed: 0, grade: 0, onRoad: false, wall: 0, bump: 0, stuck: 0, fx: 1, fy: 0, fz: 0, k: -1, blocked: false };
  const out = { respawned: false };

  s.place = (x, z, h, v = 0) => {
    s.x = x; s.z = z; s.h = h; s.vx = Math.cos(h) * v; s.vz = Math.sin(h) * v; s.stuck = 0;
    s.y = world.ground(x, z) + 0.4; s.speed = v;
  };

  /* input: steer -1 (left) .. 1 (right) relative to the skater, push 0..1, brake 0..1. wet: 0..100 or null (street wetness under the skater). */
  s.step = (dt, input, wet) => {
    dt = Math.min(dt, 0.05); out.respawned = false; s.bump = 0;
    const t = wet == null ? 0 : smooth(20, 100, wet);
    let dx = Math.cos(s.h), dz = Math.sin(s.h);
    const q = world.road(s.x, s.z, dx, dz);
    const qFound = q.found, tgx = q.tx, tgz = q.tz, e = q.e, onRoad = qFound && q.d <= T.road.half + 1.2; // copies: q is reused by later lookups
    s.onRoad = onRoad; s.k = qFound ? q.k : -1;

    // slope along the heading (true grade: the scene is stretched by EX). positive = uphill ahead
    const gA = world.ground(s.x + dx * 5, s.z + dz * 5), gB = world.ground(s.x - dx * 5, s.z - dz * 5);
    const grade = clamp((gA - gB) / 10 / EX, -0.4, 0.4); s.grade = grade;

    // steering, slower the faster you go and sloppier when it is wet; a little turn is allowed from standstill
    let along = s.vx * dx + s.vz * dz;
    // boost: drains while the button is held, refills when it is not; after running dry it needs B.min back before it fires again
    const B = T.boost, wantBoost = (input.boost || 0) > 0.5;
    if (s.lock && s.charge > B.min) s.lock = false;
    if (wantBoost && !s.lock && s.charge > 0) { s.charge = Math.max(0, s.charge - dt / B.dur); s.boosting = true; if (s.charge <= 0) s.lock = true; }
    else { s.boosting = false; s.charge = Math.min(1, s.charge + dt / B.regen); }
    const pm = s.boosting ? B.push : 1, vm = s.boosting ? B.vmult : 1;
    const sp = Math.abs(along), steer = clamp(input.steer || 0, -1, 1);
    s.h += steer * T.turn / (1 + sp / 16) * (1 - 0.45 * t) * clamp(sp / 3 + 0.35, 0, 1) * dt;
    // stay-on-street help: when the stick is near centre, ease the heading toward the street direction and the skater toward its centre line
    if (onRoad) {
      const free = 1 - Math.min(1, Math.abs(steer) * 3);
      s.h += angDiff(Math.atan2(tgz, tgx), s.h) * clamp(dt * 1.6 * (1 - 0.6 * t) * free, 0, 1);
    }
    dx = Math.cos(s.h); dz = Math.sin(s.h);

    // forces along the heading
    along = s.vx * dx + s.vz * dz;
    let lx = s.vx - along * dx, lz = s.vz - along * dz; // sideways part of the velocity
    let a = (s.boosting ? Math.max(input.push || 0, 1) : (input.push || 0)) * T.push * pm * clamp(1 - along / (T.vPush * vm), 0, 1.2) - grade * T.gEff;
    const dm = onRoad ? 1 : T.offDrag;
    a -= (T.drag * along + T.roll * Math.sign(along)) * dm;
    if ((input.brake || 0) > 0) a -= input.brake * T.brake * Math.sign(along || 1) * (1 - 0.5 * t);
    const before = along; along += a * dt;
    if ((input.brake || 0) > 0 && before * along < 0) along = 0; // braking stops, it does not reverse
    if (along > T.vMax * vm) along = Math.max(T.vMax * vm, along - 14 * dt); else if (along > T.vMax && !s.boosting) along = Math.max(T.vMax, along - 10 * dt); // boost wears off smoothly
    along = Math.max(-8, along);
    const g = Math.exp(-T.grip * (1 - 0.75 * t) * dt); lx *= g; lz *= g;
    if (onRoad) { // soft pull toward the centre line (acceleration across the street)
      const pull = clamp(Math.abs(e) - 6.6, 0, 3) * 1.5 * -Math.sign(e); // only near the kerb edge: in the middle of a two-lane street nothing pulls
      lx += -tgz * pull * dt; lz += tgx * pull * dt;
    }
    if (onRoad && T.lane != null) { // keep to the right lane when the stick is centred (e = offset to the right of travel)
      const free = 1 - Math.min(1, Math.abs(steer) * 3), want = clamp((T.lane - e) * 4, -8, 8) * free;
      lx += -tgz * want * dt; lz += tgx * want * dt;
    }
    s.vx = along * dx + lx; s.vz = along * dz + lz;

    // move, then blocks: water and steep ground off a street, then buildings
    const ox = s.x, oz = s.z; let nx = s.x + s.vx * dt, nz = s.z + s.vz * dt;
    s.blocked = false;
    if (!onRoad) {
      const ty = world.terrainH ? world.terrainH.at(nx, nz) : 0; world.road(nx, nz, undefined, undefined, T.road.half + 1.2);
      const nearRoad = world.q.found;
      const steep = nearRoad ? 0 : (world.terrainH.at(nx + dx * 6, nz + dz * 6) - ty) / 6 / EX;
      if (!nearRoad && (ty < 0.9 || steep > T.slopeBlock)) { nx = ox; nz = oz; s.vx *= 0.2; s.vz *= 0.2; s.blocked = true; }
    }
    const w = world.walls(nx, nz, T.r);
    s.wall = w.n;
    if (w.fail) { nx = ox; nz = oz; s.vx = s.vz = 0; s.bump = 6; } // wedged between overlapping buildings: stay where we were
    else if (w.n) {
      nx = w.x; nz = w.z;
      const vn = s.vx * w.nx + s.vz * w.nz;
      if (vn < 0) { s.vx -= vn * w.nx; s.vz -= vn * w.nz; s.vx *= 0.94; s.vz *= 0.94; s.bump = -vn; }
    }
    s.x = nx; s.z = nz;
    s.speed = Math.hypot(s.vx, s.vz);
    s.fx = dx; s.fz = dz;

    // height, smoothed so small data steps do not make the board hop
    const gy = world.ground(s.x, s.z) + 0.4;
    s.y += (gy - s.y) * (1 - Math.exp(-dt * 18));
    s.fy = (gA - gB) / 10;
    const fl = Math.hypot(s.fx, s.fy, s.fz); s.fx /= fl; s.fy /= fl; s.fz /= fl;

    // stuck for 3 s while trying to move: back to the nearest street, facing along it
    if ((input.push || 0) > 0.3 && s.speed < 0.6) s.stuck += dt; else s.stuck = Math.max(0, s.stuck - dt * 2);
    if (s.stuck > 3) { out.respawned = s.respawn(); s.stuck = 0; }
    return out;
  };

  s.respawn = () => {
    const r = world.nearestStreet(s.x, s.z); if (!r) return false;
    s.place(r.x, r.z, Math.atan2(r.tz, r.tx), 0);
    return true;
  };
  return s;
}

/* ---------------------------------------------------------------------- 3. chase camera */
export function createChaseCam({ THREE, world, opts = {} }) {
  const C = { ...TUNE.cam, ...opts }, pos = new THREE.Vector3(), look = new THREE.Vector3(), want = new THREE.Vector3(), lk = new THREE.Vector3();
  const roofAt = world.roof || (() => -Infinity);
  let hdg = 0, started = false, bm = 1, um = 1, tb = 1, tu = 1, probeT = 0, yawOff = 0, upOff = 0, zoom = 1, userT = -1e9, clock = 0;
  const cand = (sim, b, u, out) => { // where the camera would sit for back/up multipliers b, u
    const yaw = hdg + yawOff, dx = Math.cos(yaw), dz = Math.sin(yaw), back = C.back * zoom * b * (1 + Math.min(0.25, sim.speed / 120));
    return out.set(sim.x - dx * back, sim.y + (C.up * zoom + upOff) * u, sim.z - dz * back);
  };
  /* a clear line from the target to the camera: above the ground and above every roof along the way */
  const clear = (sim, c) => {
    for (let i = 1; i <= 14; i++) {
      const f = i / 14, x = sim.x + (c.x - sim.x) * f, y = sim.y + 4 + (c.y - sim.y - 4) * f, z = sim.z + (c.z - sim.z) * f;
      if (y < world.ground(x, z) + 5 || y < roofAt(x, z) + 3) return false;
    }
    return c.y > roofAt(c.x, c.z) + 6;
  };
  const probe = (sim) => { // the smallest change that clears buildings: higher first, then closer in
    for (const b of [1, 0.75, 0.55]) for (const u of [1, 1.4, 2, 3]) if (clear(sim, cand(sim, b, u, lk))) { tb = b; tu = u; return; }
    tb = 0.45; tu = 3.5; // boxed in: straight up and close
  };
  return {
    /* start from the current camera so the hand-over is a smooth glide */
    start(camera, target, sim) { pos.copy(camera.position); look.copy(target); hdg = sim.h; started = true; bm = tb = um = tu = 1; yawOff = 0; upOff = 0; zoom = 1; probeT = 0; },
    stop() { started = false; },
    nudge(dyaw, dup) { yawOff += dyaw; upOff = Math.min(140, Math.max(-30, upOff + dup)); userT = clock; }, // user dragged: free look that drifts back
    zoomBy(f) { zoom = Math.min(3, Math.max(0.9, zoom * f)); },
    update(sim, camera, controls, dt) {
      if (!started) return;
      dt = Math.min(dt, 0.1); clock += dt;
      const turn = angDiff(sim.h, hdg); hdg += turn * (1 - Math.exp(-dt * C.follow));
      if (clock - userT > 3) { yawOff *= Math.exp(-dt / 5); upOff *= Math.exp(-dt / 8); }
      probeT -= dt; if (probeT <= 0) { probeT = 0.12; probe(sim); }
      const km = 1 - Math.exp(-dt * 3.5); bm += (tb - bm) * km; um += (tu - um) * km;
      cand(sim, bm, um, want);
      const ah = hdg;
      lk.set(sim.x + Math.cos(ah) * C.look, sim.y + C.lookUp, sim.z + Math.sin(ah) * C.look);
      // never under the ground, and never with a hill between the target and the camera: raise the camera until the line to the target clears the ground
      let need = world.ground(want.x, want.z) + 16;
      for (const f of [0.25, 0.5, 0.75]) {
        const px = sim.x + (want.x - sim.x) * f, pz = sim.z + (want.z - sim.z) * f;
        need = Math.max(need, sim.y + (world.ground(px, pz) + 10 - sim.y) / f);
      }
      if (want.y < need) want.y = need;
      const k = 1 - Math.exp(-dt * C.spring);
      pos.lerp(want, k); look.lerp(lk, k);
      if (pos.y < need - 4) pos.y += (need - 4 - pos.y) * Math.min(1, dt * 10); // spring lag must not dip below the floor either (eased, so a crest ahead does not jerk the camera)
      const roofFloor = roofAt(pos.x, pos.z) + 5; if (pos.y < roofFloor) pos.y += (roofFloor - pos.y) * Math.min(1, dt * 10); // nor into a roof (eased up, not snapped, when the camera slides over a roof edge)
      camera.position.copy(pos); controls.target.copy(look);
    },
  };
}

/* ---------------------------------------------------------------------- 4. input and joystick */
export function createInput({ isTouch, doc = document, win = window, onBack, onExit, entity = ENTITIES.skater }) { // onExit: shows a stop button (and Esc) for things you drive on from outside the flyover
  const st = { x: 0, y: 0 }, keys = new Set(), pad = { x: 0, y: 0 }, br = { on: false }, bo = { on: false };
  let root = null, knob = null, boostEl = null, active = null, on = false, R = 52;

  const build = () => {
    root = doc.createElement('div'); root.id = 'skctl'; root.hidden = true;
    root.innerHTML = '<div id="skpad" aria-label="Skate joystick: up to push, down to brake, left and right to steer" role="application"><i class="skring"></i><i class="skknob"></i></div>' +
      '<button id="skboost" type="button" aria-label="Boost (Shift)" title="Boost (Shift)"><b>BOOST</b></button>' +
      '<div id="skbtns">' + (onExit ? '<button id="skexit" type="button" aria-label="' + (entity.exit || 'Stop') + ' (Esc)" title="' + (entity.exit || 'Stop') + ' (Esc)">&#10005; ' + (entity.exit || 'Stop') + '</button>' : '') + '<button id="skhome" type="button" aria-label="Back on the street" title="Back on the street (R)">&#8635; Street</button></div>' +
      '<div id="skhint" aria-hidden="true">' + (isTouch ? 'Drag the stick: up to push, down to brake' : 'W A S D or arrow keys · Shift boost' + (onExit ? ' · Esc to stop' : '')) + '</div>';
    doc.body.appendChild(root);
    const padEl = root.querySelector('#skpad'); knob = root.querySelector('.skknob');
    const move = (e) => {
      const r = padEl.getBoundingClientRect(); R = r.width / 2;
      let dx = e.clientX - (r.left + R), dy = e.clientY - (r.top + R); const l = Math.hypot(dx, dy), m = Math.min(1, l / R);
      if (l > 0) { dx = dx / l * m; dy = dy / l * m; } else { dx = dy = 0; }
      pad.x = Math.abs(dx) < 0.1 ? 0 : dx; pad.y = Math.abs(dy) < 0.1 ? 0 : -dy; // up = positive
      knob.style.transform = `translate(${dx * R * 0.62}px, ${dy * R * 0.62}px)`;
    };
    const end = (e) => { if (active !== e.pointerId) return; active = null; pad.x = pad.y = 0; knob.style.transform = ''; };
    padEl.addEventListener('pointerdown', (e) => { active = e.pointerId; try { padEl.setPointerCapture(e.pointerId); } catch (x) { /* ok */ } move(e); e.preventDefault(); });
    padEl.addEventListener('pointermove', (e) => { if (active === e.pointerId) { move(e); e.preventDefault(); } });
    padEl.addEventListener('pointerup', end); padEl.addEventListener('pointercancel', end); padEl.addEventListener('lostpointercapture', end);
    root.querySelector('#skhome').addEventListener('click', () => onBack && onBack());
    if (onExit) root.querySelector('#skexit').addEventListener('click', () => onExit());
    boostEl = root.querySelector('#skboost');
    boostEl.addEventListener('pointerdown', (e) => { bo.on = true; try { boostEl.setPointerCapture(e.pointerId); } catch (x) { /* ok */ } e.preventDefault(); });
    const bend = () => { bo.on = false; }; boostEl.addEventListener('pointerup', bend); boostEl.addEventListener('pointercancel', bend); boostEl.addEventListener('lostpointercapture', bend);
    root.addEventListener('contextmenu', (e) => e.preventDefault());
  };
  const KEYMAP = { ArrowUp: 'u', w: 'u', W: 'u', ArrowDown: 'd', s: 'd', S: 'd', ArrowLeft: 'l', a: 'l', A: 'l', ArrowRight: 'r', d: 'r', D: 'r', ' ': 'd', r: 'home', R: 'home', Shift: 'boost', e: 'boost', E: 'boost', Escape: 'exit' };
  const kd = (e) => {
    if (!on || e.ctrlKey || e.metaKey || e.altKey) return; const t = e.target && e.target.tagName; if (t === 'INPUT' || t === 'SELECT' || t === 'TEXTAREA') return;
    const k = KEYMAP[e.key]; if (!k) return;
    if (k === 'home') { if (!e.repeat && onBack) onBack(); return; }
    if (k === 'exit') { if (onExit && !e.repeat) onExit(); return; }
    keys.add(k); e.preventDefault();
  };
  const ku = (e) => { const k = KEYMAP[e.key]; if (k) keys.delete(k); };
  const blur = () => { keys.clear(); pad.x = pad.y = 0; br.on = false; bo.on = false; };
  win.addEventListener('keydown', kd); win.addEventListener('keyup', ku); win.addEventListener('blur', blur);

  return {
    show(v) { on = v; if (!root && v) build(); if (root) root.hidden = !v; if (!v) blur(); },
    /* read once per frame: {steer, push, brake}, the strongest of stick, keys, gamepad and the brake button */
    read() {
      let x = pad.x, y = pad.y, gpBoost = false;
      const kx = (keys.has('r') ? 1 : 0) - (keys.has('l') ? 1 : 0), ky = (keys.has('u') ? 1 : 0) - (keys.has('d') ? 1 : 0);
      if (Math.abs(kx) > Math.abs(x)) x = kx; if (Math.abs(ky) > Math.abs(y)) y = ky;
      try {
        const gp = (win.navigator.getGamepads && win.navigator.getGamepads() || [])[0];
        if (gp) {
          const gx = Math.abs(gp.axes[0]) > 0.15 ? gp.axes[0] : 0, gy = Math.abs(gp.axes[1]) > 0.15 ? -gp.axes[1] : 0;
          if (Math.abs(gx) > Math.abs(x)) x = gx; if (Math.abs(gy) > Math.abs(y)) y = gy;
          if (gp.buttons[0] && gp.buttons[0].pressed || gp.buttons[7] && gp.buttons[7].pressed) y = Math.max(y, 1);
          if (gp.buttons[2] && gp.buttons[2].pressed || gp.buttons[5] && gp.buttons[5].pressed) gpBoost = true;
          if (gp.buttons[1] && gp.buttons[1].pressed || gp.buttons[6] && gp.buttons[6].pressed) y = Math.min(y, -1);
        }
      } catch (e) { /* no gamepad API */ }
      return { steer: clamp(x, -1, 1), push: Math.max(0, y), brake: br.on ? 1 : Math.max(0, -y), boost: bo.on || keys.has('boost') || gpBoost ? 1 : 0 };
    },
    /* show the boost charge on the button: c 0..1, active = boost firing */
    setCharge(c, active) { if (boostEl) { boostEl.style.setProperty('--ch', c.toFixed(3)); boostEl.classList.toggle('on', !!active); boostEl.classList.toggle('low', c < 0.25 && !active); } },
    destroy() { win.removeEventListener('keydown', kd); win.removeEventListener('keyup', ku); win.removeEventListener('blur', blur); if (root) root.remove(); root = null; },
  };
}

/* ---------------------------------------------------------------------- 5. controller used by app.js */
export function createSkate({ THREE, camera, controls, renderer, terrainH, EX, getG, getCol, skater, isTouch, toast, readout }) {
  let world = null, sim = null, cam = null, input = null, active = false, ema = 0.016, slowT = 0, lite = false, pr0 = 0, bumpT = 0;
  const fwd = new THREE.Vector3(), pos = new THREE.Vector3();

  function ensure() {
    if (world) return;
    const t0 = performance.now();
    world = createWorld({ terrainH, EX, G: getG(), col: getCol() });
    sim = createSim(world, ENTITIES.skater.tune); cam = createChaseCam({ THREE, world, opts: ENTITIES.skater.cam });
    input = createInput({ isTouch, entity: ENTITIES.skater, onBack: () => { if (active) sim.respawn(); } });
    world.buildMs = Math.round(performance.now() - t0);
  }

  return {
    get active() { return active; }, get sim() { return sim; }, get world() { return world; },
    /* start from the flyover skater's spot: P = ground point (scene), f = unit travel direction */
    start(P, f, w) {
      ensure(); if (!world.hasStreets) return false;
      sim.place(P.x, P.z, Math.atan2(f.z, f.x), 8); sim.y = P.y + 0.4;
      cam.start(camera, controls.target, sim);
      input.show(true); active = true; ema = 0.016; slowT = 0; lite = false;
      pr0 = renderer.getPixelRatio(); if (isTouch && pr0 > 1.25) renderer.setPixelRatio(1.25); // lighter on phones while skating
      skater.setVisible(true); void w; return true;
    },
    stop() {
      if (!active) return; active = false; if (input) input.show(false);
      if (pr0) renderer.setPixelRatio(pr0);
    },
    /* one frame: wet = street wetness 0-100 at the skater (or null). */
    update(dt, wetAt) {
      if (!active) return;
      const inp = input.read();
      sim.step(dt, inp, wetAt ? wetAt(sim.k) : null); input.setCharge(sim.charge, sim.boosting);
      if (sim.bump > 5 && performance.now() - bumpT > 400) { bumpT = performance.now(); try { navigator.vibrate && navigator.vibrate(12); } catch (e) { /* no haptics */ } }
      pos.set(sim.x, sim.y, sim.z); fwd.set(sim.fx, sim.fy, sim.fz);
      skater.update({ pos, forward: fwd, scale: TUNE.scale, w: wetAt ? wetAt(sim.k) : null, dt });
      cam.update(sim, camera, controls, dt);
      // weak phone guard: if frames stay slow, drop the pixel ratio once, then say so
      ema += (dt - ema) * 0.05; slowT = ema > 0.04 ? slowT + dt : Math.max(0, slowT - dt);
      if (slowT > 4 && !lite) { lite = true; renderer.setPixelRatio(1); toast && toast('Slow device: skating in lite quality'); }
      if (readout) readout(sim);
    },
  };
}
