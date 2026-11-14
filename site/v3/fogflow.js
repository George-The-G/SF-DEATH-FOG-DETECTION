/* fogflow.js: terrain-aware wind for the fog layer, hourly fog-motion estimate, GLSL helpers for advected noise.
   Plain ES module, no imports; runs in browsers and in Node (tools/fogflow_test.mjs tests and benchmarks it).

   Grid convention (same as the fog atlas tiles, live/index.json fog_grid): arrays are row-major w*h, row 0 = NORTH edge,
   column 0 = WEST edge, square cells of cellM metres. Velocities: u = east, v = north, m/s. Wind directions are
   meteorological: dirFrom = where the wind blows FROM, degrees clockwise from north (270 = westerly, blowing east).
   In three.js scene axes (x = east, z = -north) a velocity (u, v) is (u, 0, -v).

   solveWind(opts)             mass-consistent 2D wind in the fog layer -> { u, v, rg, scale, ms, info }
   motionAlongWind(A, B, opts) how far the fog pattern moved along the wind between two frames -> { shift, conf, ms, info }
   FLOW_GLSL                   GLSL ES 3.00 helpers for two-phase flow-mapped (advected) noise lookups */

const now = () => (typeof performance !== 'undefined' && performance.now ? performance.now() : Date.now());
const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const smoothstep = (a, b, x) => { if (!(b > a)) return x >= b ? 1 : 0; const t = clamp01((x - a) / (b - a)); return t * t * (3 - 2 * t); };

/* ================================================================== 1. wind
   Model: the fog layer is a 2D sheet under the inversion (topM). Start from the uniform wind u* (plus optional curl-noise
   eddies); give every cell an openness phi = 1 - smoothstep(blockFrac*topM, topM, ground) (1 = free, 0 = the terrain reaches
   the fog top) and every cell face the transmissibility T = min(phi of its two cells); then project the face fluxes T*u* onto
   the divergence-free set: find p with  sum over faces of T (u*.n - dp/dn) = 0  in every cell, i.e. div(T grad p) = div(T u*).
   Finite volumes, velocities on the cell faces (MAC grid), p at the cell centres, Dirichlet p = 0 in a ghost ring outside the
   grid (open edges: the flow may enter or leave anywhere; terrain touching an edge just blocks it there). Open pockets that
   are enclosed by blocked terrain (not connected to an edge) are treated as blocked (stagnant), so the system is never singular.
   Solver: flexible conjugate gradients preconditioned by one geometric multigrid V(1,1) cycle per iteration (red-black
   Gauss-Seidel, true cell geometry on every level), until max |cell flux imbalance| <= tol * speed (default 1e-2); method 'sor' = plain
   red-black SOR sweeps instead (slow, for comparison).
   Output = the face flux velocity T*(u* - grad p) averaged to the cell centres: exactly 0 in fully blocked cells, reduced in
   the soft band, faster in gaps; the face fluxes are divergence-free to tol. Same inputs -> bit-identical outputs (the eddies
   are seeded). */

/* Multigrid levels carry their true geometry in fine-cell units (centres cx/cy, sizes wx/hy; odd sizes give narrower last
   cells) so every level discretises the same problem: face coefficient = mean fine transmissibility along the face (txA/tyA)
   * face length / centre distance, with the fine grid's Dirichlet ghost at -0.5 and size + 0.5 on every level. The hierarchy
   (geometry, prolongation tables, work arrays) is cached per grid size; the coefficients are refilled on every call. */
const HIER = new Map();
function mgLevel(w, h, fw, fh) {
  return {
    w, h, fw, fh, tx: new Float64Array((w + 1) * h), ty: new Float64Array(w * (h + 1)), txA: new Float64Array((w + 1) * h), tyA: new Float64Array(w * (h + 1)),
    d: new Float64Array(w * h), id: new Float64Array(w * h), p: new Float64Array((w + 2) * (h + 2)), b: new Float64Array(w * h), r: new Float64Array(w * h),
    cx: null, wx: null, cy: null, hy: null,
    // prolongation from this level to the next finer one: per fine column/row the parent, the neighbour (-1 or n = ghost), the parent weight
    pxI: null, pxN: null, pxA: null, pyJ: null, pyN: null, pyA: null,
  };
}
function mgAxis(cF, sF, n, ext) { // coarse centres/sizes along one axis + prolongation weights (Dirichlet 0 at -0.5 and ext + 0.5)
  const nc = (n + 1) >> 1, c = new Float64Array(nc), s = new Float64Array(nc), par = new Int32Array(n), nb = new Int32Array(n), a = new Float64Array(n);
  for (let I = 0; I < nc; I++) {
    const i0 = 2 * I, i1 = Math.min(2 * I + 1, n - 1), x0 = cF[i0] - sF[i0] / 2, x1 = cF[i1] + sF[i1] / 2;
    c[I] = (x0 + x1) / 2; s[I] = x1 - x0;
  }
  for (let i = 0; i < n; i++) {
    const I = i >> 1, In = cF[i] < c[I] ? I - 1 : I + 1, xn = In < 0 ? -0.5 : In >= nc ? ext + 0.5 : c[In];
    par[i] = I; nb[i] = In; a[i] = Math.abs(cF[i] - c[I]) < 1e-9 ? 1 : (xn - cF[i]) / (xn - c[I]);
  }
  return { nc, c, s, par, nb, a };
}
function mgHierarchy(w, h) {
  const key = w + 'x' + h; let H = HIER.get(key);
  if (H) return H;
  const L0 = mgLevel(w, h, w, h);
  L0.cx = new Float64Array(w).map((_, i) => i + 0.5); L0.wx = new Float64Array(w).fill(1);
  L0.cy = new Float64Array(h).map((_, j) => j + 0.5); L0.hy = new Float64Array(h).fill(1);
  const Ls = [L0];
  while (Ls.length < 8) {
    const F = Ls[Ls.length - 1]; if (F.w <= 6 || F.h <= 6) break;
    const ax = mgAxis(F.cx, F.wx, F.w, w), ay = mgAxis(F.cy, F.hy, F.h, h), C = mgLevel(ax.nc, ay.nc, w, h);
    C.cx = ax.c; C.wx = ax.s; C.cy = ay.c; C.hy = ay.s; C.pxI = ax.par; C.pxN = ax.nb; C.pxA = ax.a; C.pyJ = ay.par; C.pyN = ay.nb; C.pyA = ay.a;
    Ls.push(C);
  }
  const n = w * h, P = (w + 2) * (h + 2);
  const W1 = w + 1;
  H = { Ls, x: new Float64Array(P), dd: new Float64Array(P), q: new Float64Array(n), rr: new Float64Array(n),
    // scratch for solveWind (reused between calls; every use fully overwrites it)
    phi: new Float32Array(n), tx: new Float64Array(W1 * h), ty: new Float64Array(w * (h + 1)), reach: new Uint8Array(n), queue: new Int32Array(n),
    usx: new Float64Array(W1 * h), usy: new Float64Array(w * (h + 1)), U: new Float64Array(W1 * h), V: new Float64Array(w * (h + 1)) };
  HIER.set(key, H);
  return H;
}
function mgCoefs(L) { // operator coefficients from mean transmissibilities and geometry, then the diagonal
  const { w, h, tx, ty, txA, tyA, cx, cy, wx, hy, d, id } = L, W1 = w + 1;
  for (let j = 0; j < h; j++) for (let i = 0; i <= w; i++) {
    const dist = i === 0 ? cx[0] + 0.5 : i === w ? L.fw + 0.5 - cx[w - 1] : cx[i] - cx[i - 1];
    tx[j * W1 + i] = txA[j * W1 + i] * hy[j] / dist;
  }
  for (let j = 0; j <= h; j++) for (let i = 0; i < w; i++) {
    const dist = j === 0 ? cy[0] + 0.5 : j === h ? L.fh + 0.5 - cy[h - 1] : cy[j] - cy[j - 1];
    ty[j * w + i] = tyA[j * w + i] * wx[i] / dist;
  }
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
    const k = j * w + i, s = tx[j * W1 + i] + tx[j * W1 + i + 1] + ty[k] + ty[k + w];
    d[k] = s; id[k] = s > 1e-12 ? 1 / s : 0;
  }
}
function mgFill(Ls, tx, ty) { // fine transmissibilities -> every level (length-weighted means of the child faces)
  Ls[0].txA.set(tx); Ls[0].tyA.set(ty); mgCoefs(Ls[0]);
  for (let l = 1; l < Ls.length; l++) {
    const F = Ls[l - 1], C = Ls[l], fW1 = F.w + 1, cW1 = C.w + 1;
    for (let J = 0; J < C.h; J++) for (let I = 0; I <= C.w; I++) {
      const fi = Math.min(2 * I, F.w), j1 = Math.min(2 * J + 2, F.h); let s = 0, len = 0;
      for (let jj = 2 * J; jj < j1; jj++) { s += F.txA[jj * fW1 + fi] * F.hy[jj]; len += F.hy[jj]; }
      C.txA[J * cW1 + I] = s / len;
    }
    for (let J = 0; J <= C.h; J++) for (let I = 0; I < C.w; I++) {
      const fj = Math.min(2 * J, F.h), i1 = Math.min(2 * I + 2, F.w); let s = 0, len = 0;
      for (let ii = 2 * I; ii < i1; ii++) { s += F.tyA[fj * F.w + ii] * F.wx[ii]; len += F.wx[ii]; }
      C.tyA[J * C.w + I] = s / len;
    }
    mgCoefs(C);
  }
}
function mgRelax(L, sweeps, omega, reverse) { // red-black Gauss-Seidel / SOR; the ghost ring of p stays 0
  const { w, h, tx, ty, id, p, b } = L, W1 = w + 1, W2 = w + 2;
  for (let s = 0; s < sweeps; s++) for (let c = 0; c < 2; c++) {
    const color = reverse ? 1 - c : c;
    for (let j = 0; j < h; j++) {
      const kp = (j + 1) * W2 + 1, kx = j * W1, ky = j * w;
      if (omega === 1) {
        for (let i = (j + color) & 1; i < w; i += 2) {
          const k = kp + i, q = ky + i;
          p[k] = (b[q] + tx[kx + i] * p[k - 1] + tx[kx + i + 1] * p[k + 1] + ty[q] * p[k - W2] + ty[q + w] * p[k + W2]) * id[q];
        }
      } else {
        for (let i = (j + color) & 1; i < w; i += 2) {
          const k = kp + i, q = ky + i;
          const gs = (b[q] + tx[kx + i] * p[k - 1] + tx[kx + i + 1] * p[k + 1] + ty[q] * p[k - W2] + ty[q + w] * p[k + W2]) * id[q];
          p[k] += omega * (gs - p[k]);
        }
      }
    }
  }
}
function mgResidual(L) { // r = b - A p, returns max |r|
  const { w, h, tx, ty, d, p, b, r } = L, W1 = w + 1, W2 = w + 2; let mx = 0;
  for (let j = 0; j < h; j++) {
    const kp = (j + 1) * W2 + 1, kx = j * W1, ky = j * w;
    for (let i = 0; i < w; i++) {
      const k = kp + i, q = ky + i;
      const v = b[q] + tx[kx + i] * p[k - 1] + tx[kx + i + 1] * p[k + 1] + ty[q] * p[k - W2] + ty[q + w] * p[k + W2] - d[q] * p[k];
      r[q] = v; const a = v < 0 ? -v : v; if (a > mx) mx = a;
    }
  }
  return mx;
}
function mgApply(L, x, out) { // out = A x (x padded, out unpadded)
  const { w, h, tx, ty, d } = L, W1 = w + 1, W2 = w + 2;
  for (let j = 0; j < h; j++) {
    const kp = (j + 1) * W2 + 1, kx = j * W1, ky = j * w;
    for (let i = 0; i < w; i++) {
      const k = kp + i, q = ky + i;
      out[q] = d[q] * x[k] - tx[kx + i] * x[k - 1] - tx[kx + i + 1] * x[k + 1] - ty[q] * x[k - W2] - ty[q + w] * x[k + W2];
    }
  }
}
function mgRestrict(F, C) { // coarse rhs = sum of the children's residuals (cells hold integrated flux imbalances)
  const cb = C.b, cw = C.w, fr = F.r, fw = F.w; cb.fill(0);
  for (let j = 0; j < F.h; j++) { const J = (j >> 1) * cw, o = j * fw; for (let i = 0; i < fw; i++) cb[J + (i >> 1)] += fr[o + i]; }
}
function mgProlong(C, F) { // F.p += bilinear(C.p) on the true centres; ghosts (outside the grid, and blocked cells) count as 0
  const Wc2 = C.w + 2, Wf2 = F.w + 2, cp = C.p, fp = F.p, pxI = C.pxI, pxN = C.pxN, pxA = C.pxA;
  for (let j = 0; j < F.h; j++) {
    const ay = C.pyA[j], by = 1 - ay, oJ = (C.pyJ[j] + 1) * Wc2 + 1, oN = (C.pyN[j] + 1) * Wc2 + 1, of = (j + 1) * Wf2 + 1;
    for (let i = 0; i < F.w; i++) {
      const I = pxI[i], In = pxN[i], ax = pxA[i], bx = 1 - ax;
      fp[of + i] += ay * (ax * cp[oJ + I] + bx * cp[oJ + In]) + by * (ax * cp[oN + I] + bx * cp[oN + In]);
    }
  }
}
function mgVcycle(Ls, l) {
  const L = Ls[l];
  if (l === Ls.length - 1) { mgRelax(L, 20, 1, false); mgRelax(L, 20, 1, true); return; }
  mgRelax(L, 1, 1, false);
  mgResidual(L);
  const C = Ls[l + 1];
  mgRestrict(L, C); C.p.fill(0);
  mgVcycle(Ls, l + 1);
  mgProlong(C, L);
  mgRelax(L, 1, 1, true);
}
/* Flexible CG (Notay's FCG(1)) with one V-cycle as preconditioner. x (padded) = solution; returns { it, res } (max |r|). */
function solveFCG(H, maxIt, target) {
  const Ls = H.Ls, L0 = Ls[0], w = L0.w, h = L0.h, W2 = w + 2, x = H.x, dd = H.dd, q = H.q, r = H.rr, z = L0.p;
  x.fill(0); dd.fill(0); r.set(L0.b);
  let res = 0; for (let k = 0; k < r.length; k++) { const a = Math.abs(r[k]); if (a > res) res = a; }
  if (res <= target) return { it: 0, res };
  const precond = () => { L0.b.set(r); z.fill(0); mgVcycle(Ls, 0); };
  precond(); dd.set(z);
  let it = 0;
  while (it < maxIt) {
    it++;
    mgApply(L0, dd, q);
    let dq = 0, dr = 0;
    for (let j = 0; j < h; j++) { const kp = (j + 1) * W2 + 1, ky = j * w; for (let i = 0; i < w; i++) { const dv = dd[kp + i]; dq += dv * q[ky + i]; dr += dv * r[ky + i]; } }
    if (!(dq > 0)) break;
    const alpha = dr / dq; res = 0;
    for (let j = 0; j < h; j++) {
      const kp = (j + 1) * W2 + 1, ky = j * w;
      for (let i = 0; i < w; i++) { x[kp + i] += alpha * dd[kp + i]; const v = r[ky + i] - alpha * q[ky + i]; r[ky + i] = v; const a = v < 0 ? -v : v; if (a > res) res = a; }
    }
    if (res <= target || it >= maxIt) break;
    precond();
    let zq = 0;
    for (let j = 0; j < h; j++) { const kp = (j + 1) * W2 + 1, ky = j * w; for (let i = 0; i < w; i++) zq += z[kp + i] * q[ky + i]; }
    const beta = -zq / dq;
    for (let j = 0; j < h; j++) { const kp = (j + 1) * W2 + 1; for (let i = 0; i < w; i++) dd[kp + i] = z[kp + i] + beta * dd[kp + i]; }
  }
  return { it, res };
}

// deterministic smooth value noise for the optional eddies
function hash2(ix, iy, seed) {
  let x = Math.imul(ix | 0, 0x27d4eb2d) ^ Math.imul(iy | 0, 0x165667b1) ^ Math.imul((seed | 0) + 0x3c6ef372, 0x85ebca6b);
  x = Math.imul(x ^ (x >>> 15), 0x2c1b3c6d); x = Math.imul(x ^ (x >>> 12), 0x297a2d39); x ^= x >>> 15;
  return (x >>> 0) / 2147483648 - 1;
}
function vnoise(x, y, seed) {
  const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
  const sx = fx * fx * fx * (fx * (fx * 6 - 15) + 10), sy = fy * fy * fy * (fy * (fy * 6 - 15) + 10);
  const a = hash2(ix, iy, seed), b = hash2(ix + 1, iy, seed), c = hash2(ix, iy + 1, seed), d = hash2(ix + 1, iy + 1, seed);
  return a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy;
}
/* Curl noise on the MAC faces from a stream function psi at the cell corners: u = dpsi/dnorth, v = -dpsi/deast. It is exactly
   divergence-free on the grid before the obstacles are applied; the projection then makes it go around them like the mean wind.
   amp = rms of the face-normal components (m/s). */
function addCurlNoise(usx, usy, w, h, featCells, amp, seed) {
  const W1 = w + 1, psi = new Float64Array(W1 * (h + 1)), f = 1 / Math.max(featCells, 1e-3);
  for (let j = 0; j <= h; j++) for (let i = 0; i <= w; i++)
    psi[j * W1 + i] = vnoise(i * f, j * f, seed) + 0.5 * vnoise(i * f * 2 + 17.31, j * f * 2 + 5.17, seed + 101);
  const ex = new Float64Array(usx.length), ey = new Float64Array(usy.length); let ss = 0;
  for (let j = 0; j < h; j++) for (let i = 0; i <= w; i++) { const v = psi[j * W1 + i] - psi[(j + 1) * W1 + i]; ex[j * W1 + i] = v; ss += v * v; }
  for (let j = 0; j <= h; j++) for (let i = 0; i < w; i++) { const v = psi[j * W1 + i] - psi[j * W1 + i + 1]; ey[j * w + i] = v; ss += v * v; }
  const sc = amp / Math.sqrt(ss / (ex.length + ey.length) + 1e-30);
  for (let k = 0; k < ex.length; k++) usx[k] += ex[k] * sc;
  for (let k = 0; k < ey.length; k++) usy[k] += ey[k] * sc;
}

/**
 * Mass-consistent 2D wind field of the fog layer over the terrain.
 * @param {object} o
 *   w, h, cellM   grid (row 0 = north), cell size in metres
 *   ground        Float32Array w*h, terrain height per cell (null = flat). Same units as topM: only ground/topM matters, so
 *                 metres, or scene units (metres * EX, like fog2.js's height texture) with topM * EX, give the same field.
 *   topM          fog top (inversion height): ground >= topM is solid, ground <= blockFrac*topM is free, smooth in between
 *   speed         m/s (city mean wind); dirFrom: degrees, where the wind blows FROM
 *   iters = 60    cap on CG iterations ('mg', usually 3..6 are used) or on SOR sweeps ('sor')
 *   blockFrac = 0.85; tol = 1e-2: stop when every cell's flux imbalance is below tol * speed (divergence < tol*speed/cellM)
 *   method = 'mg' | 'sor', omega = 1.9 (sor only)
 *   eddy = 0      optional curl-noise eddies: rms of each velocity component as a fraction of speed (try 0.15..0.3)
 *   eddyM = 1500  eddy size in metres; seed = 0 (change it per hour for variety)
 * @returns {{u: Float32Array, v: Float32Array, rg: Uint8Array, scale: number, ms: number, info: object}}
 *   u, v: m/s per cell (east, north). rg: w*h*2 bytes (R = u, G = v), byte 128 = 0 m/s, value = (byte - 128) * scale m/s;
 *   upload as an RG8 texture, row 0 = north = texture row 0 (flipY false, like the ground-height texture in fog2.js).
 *   info: { method, iterations, residual, divBefore: {max, mean}, divAfter: {max, mean} (1/s, on the cell faces),
 *           blocked, soft, enclosed (cell counts), maxSpeed, msSolve }
 */
export function solveWind(o) {
  const t0 = now(); o = o || {};
  const w = o.w | 0, h = o.h | 0, n = w * h;
  if (!(w > 1 && h > 1)) throw new Error('solveWind: w and h must be > 1');
  const cellM = +o.cellM || 80, ground = o.ground || null, topM = +o.topM;
  const speed = Math.max(0, +o.speed || 0), dirFrom = +o.dirFrom || 0;
  const iters = o.iters == null ? 60 : Math.max(0, o.iters | 0), blockFrac = o.blockFrac == null ? 0.85 : +o.blockFrac;
  const tol = o.tol == null ? 1e-2 : +o.tol, method = o.method === 'sor' ? 'sor' : 'mg', omega = +o.omega || 1.9;
  const eddy = Math.max(0, +o.eddy || 0), eddyM = +o.eddyM || 1500, seed = o.seed | 0;
  if (ground && ground.length < n) throw new Error('solveWind: ground must have w*h values');

  // openness and face transmissibilities (boundary faces take the edge cell's openness)
  const H = mgHierarchy(w, h), L0 = H.Ls[0];
  const phi = H.phi, useT = !!ground && topM > 0 && isFinite(topM), g0 = blockFrac * topM;
  for (let k = 0; k < n; k++) phi[k] = useT ? 1 - smoothstep(g0, topM, ground[k]) : 1;
  const W1 = w + 1, W2 = w + 2, tx = H.tx, ty = H.ty;
  for (let j = 0; j < h; j++) {
    const o0 = j * w, x0 = j * W1;
    tx[x0] = phi[o0]; tx[x0 + w] = phi[o0 + w - 1];
    for (let i = 1; i < w; i++) tx[x0 + i] = Math.min(phi[o0 + i - 1], phi[o0 + i]);
  }
  for (let i = 0; i < w; i++) { ty[i] = phi[i]; ty[h * w + i] = phi[(h - 1) * w + i]; }
  for (let j = 1; j < h; j++) for (let i = 0; i < w; i++) ty[j * w + i] = Math.min(phi[(j - 1) * w + i], phi[j * w + i]);

  // open cells not connected to a grid edge (enclosed by terrain above the fog top) are stagnant: block them
  const reach = H.reach, queue = H.queue; reach.fill(0); let qt = 0;
  const seedCell = (k) => { if (!reach[k]) { reach[k] = 1; queue[qt++] = k; } };
  for (let i = 0; i < w; i++) { if (ty[i] > 0) seedCell(i); if (ty[h * w + i] > 0) seedCell((h - 1) * w + i); }
  for (let j = 0; j < h; j++) { if (tx[j * W1] > 0) seedCell(j * w); if (tx[j * W1 + w] > 0) seedCell(j * w + w - 1); }
  for (let qh = 0; qh < qt; qh++) {
    const k = queue[qh], j = (k / w) | 0, i = k - j * w;
    if (i > 0 && tx[j * W1 + i] > 0) seedCell(k - 1);
    if (i < w - 1 && tx[j * W1 + i + 1] > 0) seedCell(k + 1);
    if (j > 0 && ty[k] > 0) seedCell(k - w);
    if (j < h - 1 && ty[k + w] > 0) seedCell(k + w);
  }
  let blocked = 0, soft = 0, enclosed = 0;
  for (let k = 0; k < n; k++) {
    if (!reach[k] && phi[k] > 0) { enclosed++; phi[k] = 0; const j = (k / w) | 0, i = k - j * w; tx[j * W1 + i] = tx[j * W1 + i + 1] = ty[k] = ty[k + w] = 0; }
    if (phi[k] <= 0) blocked++; else if (phi[k] < 1) soft++;
  }

  // predicted face velocities u* (uniform wind towards dirFrom + 180, plus eddies)
  const th = dirFrom * Math.PI / 180, ue = -Math.sin(th) * speed, vn = -Math.cos(th) * speed;
  const usx = H.usx.fill(ue), usy = H.usy.fill(vn);
  if (eddy > 0 && speed > 0) addCurlNoise(usx, usy, w, h, eddyM / cellM, eddy * speed, seed);

  // multigrid hierarchy, rhs b = -div(T u*), and the "before" divergence (masked uniform wind)
  mgFill(H.Ls, tx, ty);
  const b = L0.b; let dbMax = 0, dbSum = 0;
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
    const kx = j * W1 + i, ky = j * w + i;
    const div = tx[kx + 1] * usx[kx + 1] - tx[kx] * usx[kx] + ty[ky] * usy[ky] - ty[ky + w] * usy[ky + w];
    b[ky] = -div; const a = Math.abs(div); dbSum += a; if (a > dbMax) dbMax = a;
  }

  // pressure solve
  const tS = now(), target = tol * Math.max(speed, 1e-9);
  let it = 0, res = 0, p;
  if (method === 'sor') {
    L0.p.fill(0); res = mgResidual(L0);
    while (it < iters && res > target) { mgRelax(L0, 1, omega, false); it++; if ((it & 7) === 0 || it === iters) res = mgResidual(L0); }
    p = L0.p;
  } else {
    const s = solveFCG(H, iters, target); it = s.it; res = s.res; p = H.x;
  }
  const msSolve = now() - tS;

  // face flux velocities -> cell centres; "after" divergence
  const U = H.U, V = H.V;
  for (let j = 0; j < h; j++) {
    const kp = (j + 1) * W2; // padded: p(i, j) = p[kp + i + 1], ghost p = 0
    for (let i = 0; i <= w; i++) { const k = j * W1 + i; U[k] = tx[k] * (usx[k] - (p[kp + i + 1] - p[kp + i])); }
  }
  for (let j = 0; j <= h; j++) for (let i = 0; i < w; i++) { // face j: north cell j-1, south cell j
    const k = j * w + i; V[k] = ty[k] * (usy[k] - (p[j * W2 + i + 1] - p[(j + 1) * W2 + i + 1]));
  }
  const u = new Float32Array(n), v = new Float32Array(n);
  let daMax = 0, daSum = 0, maxAbs = 0, maxSpeed = 0;
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
    const kx = j * W1 + i, ky = j * w + i;
    const uu = 0.5 * (U[kx] + U[kx + 1]), vv = 0.5 * (V[ky] + V[ky + w]);
    u[ky] = uu; v[ky] = vv;
    const div = Math.abs(U[kx + 1] - U[kx] + V[ky] - V[ky + w]); daSum += div; if (div > daMax) daMax = div;
    const au = Math.abs(uu), av = Math.abs(vv); if (au > maxAbs) maxAbs = au; if (av > maxAbs) maxAbs = av;
    const sp = uu * uu + vv * vv; if (sp > maxSpeed) maxSpeed = sp;
  }

  // RG8 encoding, 128 = 0
  const scale = maxAbs > 0 ? maxAbs / 127 : 1 / 127, rg = new Uint8Array(2 * n), inv = 1 / scale;
  for (let k = 0; k < n; k++) {
    const a = Math.round(128 + u[k] * inv), c = Math.round(128 + v[k] * inv);
    rg[2 * k] = a < 0 ? 0 : a > 255 ? 255 : a; rg[2 * k + 1] = c < 0 ? 0 : c > 255 ? 255 : c;
  }
  return {
    u, v, rg, scale, ms: now() - t0,
    info: {
      method, iterations: it, residual: res, msSolve, blocked, soft, enclosed, maxSpeed: Math.sqrt(maxSpeed),
      divBefore: { max: dbMax / cellM, mean: dbSum / n / cellM }, divAfter: { max: daMax / cellM, mean: daSum / n / cellM },
    },
  };
}

/* ================================================================== 2. motion between hourly frames
   1D block matching along the wind: both frames are resampled (bilinear) onto a wind-aligned grid (x = downwind,
   y = cross-wind, spacing res cells); for every shift s in [-S, S] (S = maxShiftM / (res*cellM)) the zero-mean normalised
   cross-correlation (ZNCC) of a block x block window of A at x with B at x + s is computed with box sums (the window must lie
   inside the grid in both frames); the best shift (with a weak prior towards 0, so ambiguous cases stay still) is refined to
   sub-cell precision with a parabola. ZNCC ignores gain and offset, so a bank that only thickens or thins reads as 0 motion.
   Confidence = texture * match * peak * coverage:
     texture  = v / (v + minTex^2), v = the smaller block variance (px^2) of A and B: a flat pattern gives ~0
     match    = ZNCC at the best shift (clamped 0..1): the shifted pattern really matches
     peak     = (best - mean ZNCC over all shifts) / 0.5: an along-wind ramp or streaks parallel to the wind match at every
                shift (aperture problem) and give ~0
     coverage = fraction of the shift range that fitted inside the grid
   The result is mapped back to the fog grid and smoothed with confidence weights (two box passes of radius `smooth`). */

function blurU8(src, w, h, r) { // separable box blur, edge-normalised, -> Float32Array
  const tmp = new Float32Array(w * h), out = new Float32Array(w * h);
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
    let s = 0, c = 0; for (let k = Math.max(0, i - r); k <= Math.min(w - 1, i + r); k++) { s += src[j * w + k]; c++; } tmp[j * w + i] = s / c;
  }
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
    let s = 0, c = 0; for (let k = Math.max(0, j - r); k <= Math.min(h - 1, j + r); k++) { s += tmp[k * w + i]; c++; } out[j * w + i] = s / c;
  }
  return out;
}
function boxSum(src, W, H, R, out, tmp) { // centred (2R+1)^2 sums, zeros outside
  for (let y = 0; y < H; y++) {
    const o = y * W; let s = 0;
    for (let x = 0; x < R && x < W; x++) s += src[o + x];
    for (let x = 0; x < W; x++) { if (x + R < W) s += src[o + x + R]; tmp[o + x] = s; if (x >= R) s -= src[o + x - R]; }
  }
  for (let x = 0; x < W; x++) {
    let s = 0;
    for (let y = 0; y < R && y < H; y++) s += tmp[y * W + x];
    for (let y = 0; y < H; y++) { if (y + R < H) s += tmp[(y + R) * W + x]; out[y * W + x] = s; if (y >= R) s -= tmp[(y - R) * W + x]; }
  }
}
function boxBlurInPlace(a, w, h, r, tmp) { // separable box mean, edge-normalised
  for (let j = 0; j < h; j++) {
    const o = j * w; let s = 0, c = 0;
    for (let i = 0; i < r && i < w; i++) { s += a[o + i]; c++; }
    for (let i = 0; i < w; i++) { if (i + r < w) { s += a[o + i + r]; c++; } tmp[o + i] = s / c; if (i >= r) { s -= a[o + i - r]; c--; } }
  }
  for (let i = 0; i < w; i++) {
    let s = 0, c = 0;
    for (let j = 0; j < r && j < h; j++) { s += tmp[j * w + i]; c++; }
    for (let j = 0; j < h; j++) { if (j + r < h) { s += tmp[(j + r) * w + i]; c++; } a[j * w + i] = s / c; if (j >= r) { s -= tmp[(j - r) * w + i]; c--; } }
  }
}

/**
 * How far the fog pattern moved along the wind between two consecutive frames.
 * @param {Uint8Array} frameA  w*h, row 0 north (earlier frame)
 * @param {Uint8Array} frameB  w*h (later frame)
 * @param {object} o  w, h, cellM, dirFrom (deg, wind FROM), maxShiftM = 4000, block = 5 (cells, matching window),
 *   res = 1 (wind-aligned grid spacing in cells; 2 is ~6x faster at 160 m shift resolution), minTex = 1 (px rms below which a
 *   block counts as flat), prior = 0.02 (ZNCC penalty at the largest shift, linear in |s|), smooth = block (box radius, cells)
 * @returns {{shift: Float32Array, conf: Float32Array, ms: number, info: object}}
 *   shift: metres moved DOWNWIND per cell (negative = upwind), conf: 0..1. Motion-compensated interpolation at a in 0..1:
 *   sample A at x - a*shift*conf*dirTo and B at x + (1-a)*shift*conf*dirTo, then mix(A, B, a) (FLOW_GLSL flowMCOffsets).
 */
export function motionAlongWind(frameA, frameB, o) {
  const t0 = now(); o = o || {};
  const w = o.w | 0, h = o.h | 0, cellM = +o.cellM || 80, dirFrom = +o.dirFrom || 0;
  if (!(w > 1 && h > 1) || !frameA || !frameB || frameA.length < w * h || frameB.length < w * h) throw new Error('motionAlongWind: bad frames or size');
  const maxShiftM = o.maxShiftM == null ? 4000 : +o.maxShiftM, block = Math.max(1, o.block == null ? 5 : o.block | 0);
  const res = Math.max(1, o.res | 0 || 1), minTex = o.minTex == null ? 1 : +o.minTex, prior = o.prior == null ? 0.02 : +o.prior;
  const smoothR = Math.max(0, o.smooth == null ? block : o.smooth | 0);
  const R = Math.max(1, Math.round((block / res - 1) / 2)), nB = (2 * R + 1) * (2 * R + 1);
  const S = Math.max(1, Math.round(maxShiftM / (cellM * res)));

  // wind-aligned grid: x along d (downwind), y along p; positions in fog-grid cell units (col, row), row grows southward
  const th = dirFrom * Math.PI / 180, dc = -Math.sin(th), dr = Math.cos(th), pc = -dr, pr = dc;
  let sMin = Infinity, sMax = -Infinity, qMin = Infinity, qMax = -Infinity;
  for (const [c, r] of [[0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1]]) {
    const s = c * dc + r * dr, q = c * pc + r * pr;
    if (s < sMin) sMin = s; if (s > sMax) sMax = s; if (q < qMin) qMin = q; if (q > qMax) qMax = q;
  }
  const W = Math.floor((sMax - sMin) / res + 1e-6) + 1, H = Math.floor((qMax - qMin) / res + 1e-6) + 1, N = W * H;
  const srcA = res > 1 ? blurU8(frameA, w, h, res >> 1) : frameA, srcB = res > 1 ? blurU8(frameB, w, h, res >> 1) : frameB;
  const A = new Float32Array(N), B = new Float32Array(N), lo = new Int32Array(H).fill(W), hi = new Int32Array(H).fill(-1), eps = 1e-6;
  for (let y = 0; y < H; y++) {
    const q = qMin + y * res;
    for (let x = 0; x < W; x++) {
      const s = sMin + x * res; let c = s * dc + q * pc, r = s * dr + q * pr;
      if (c < -eps || r < -eps || c > w - 1 + eps || r > h - 1 + eps) continue;
      c = Math.min(Math.max(c, 0), w - 1); r = Math.min(Math.max(r, 0), h - 1);
      const c0 = Math.min(Math.floor(c), w - 2), r0 = Math.min(Math.floor(r), h - 2), fc = c - c0, fr = r - r0, k0 = r0 * w + c0;
      const w00 = (1 - fc) * (1 - fr), w10 = fc * (1 - fr), w01 = (1 - fc) * fr, w11 = fc * fr, k = y * W + x;
      A[k] = srcA[k0] * w00 + srcA[k0 + 1] * w10 + srcA[k0 + w] * w01 + srcA[k0 + w + 1] * w11;
      B[k] = srcB[k0] * w00 + srcB[k0 + 1] * w10 + srcB[k0 + w] * w01 + srcB[k0 + w + 1] * w11;
      if (x < lo[y]) lo[y] = x; if (x > hi[y]) hi[y] = x;
    }
  }
  // rows whose whole block window is inside the grid: x range [bLo, bHi] for window centres
  const bLo = new Int32Array(H).fill(W), bHi = new Int32Array(H).fill(-1);
  for (let y = R; y < H - R; y++) {
    let a = -1, z = W;
    for (let yy = y - R; yy <= y + R; yy++) { if (hi[yy] < lo[yy]) { a = W; break; } if (lo[yy] > a) a = lo[yy]; if (hi[yy] < z) z = hi[yy]; }
    bLo[y] = a + R; bHi[y] = z - R;
  }
  // block statistics
  const tmp = new Float32Array(N), SA = new Float32Array(N), SB = new Float32Array(N), sq = new Float32Array(N);
  const mA = new Float32Array(N), mB = new Float32Array(N), vA = new Float32Array(N), vB = new Float32Array(N), iA = new Float32Array(N), iB = new Float32Array(N);
  boxSum(A, W, H, R, SA, tmp); boxSum(B, W, H, R, SB, tmp);
  for (let k = 0; k < N; k++) sq[k] = A[k] * A[k];
  boxSum(sq, W, H, R, vA, tmp);
  for (let k = 0; k < N; k++) sq[k] = B[k] * B[k];
  boxSum(sq, W, H, R, vB, tmp);
  for (let k = 0; k < N; k++) {
    const ma = SA[k] / nB, mb = SB[k] / nB, va = Math.max(vA[k] / nB - ma * ma, 0), vb = Math.max(vB[k] / nB - mb * mb, 0);
    mA[k] = ma; mB[k] = mb; vA[k] = va; vB[k] = vb; iA[k] = 1 / Math.sqrt(va + 1e-6); iB[k] = 1 / Math.sqrt(vb + 1e-6);
  }
  // search
  const bestV = new Float32Array(N).fill(-Infinity), bestZ = new Float32Array(N), leftV = new Float32Array(N), rightV = new Float32Array(N);
  const prevV = new Float32Array(N).fill(-Infinity), sumZ = new Float32Array(N), bestS = new Int16Array(N), cnt = new Int16Array(N);
  const P = sq, SP = new Float32Array(N), pk = prior / S; // sq is free again: reuse it for the products
  for (let s = -S; s <= S; s++) {
    P.fill(0);
    for (let y = 0; y < H; y++) {
      const o0 = y * W, xa = Math.max(lo[y], lo[y] - s), xb = Math.min(hi[y], hi[y] - s);
      for (let x = xa; x <= xb; x++) P[o0 + x] = A[o0 + x] * B[o0 + x + s];
    }
    boxSum(P, W, H, R, SP, tmp);
    const pen = pk * (s < 0 ? -s : s);
    for (let y = R; y < H - R; y++) {
      const o0 = y * W, xa = Math.max(bLo[y], bLo[y] - s), xb = Math.min(bHi[y], bHi[y] - s);
      for (let x = xa; x <= xb; x++) {
        const k = o0 + x, kb = k + s;
        const z = (SP[k] / nB - mA[k] * mB[kb]) * iA[k] * iB[kb], v = z - pen;
        sumZ[k] += z; cnt[k]++;
        if (v > bestV[k]) { bestV[k] = v; bestZ[k] = z; bestS[k] = s; leftV[k] = prevV[k]; rightV[k] = -Infinity; }
        else if (bestS[k] === s - 1 && cnt[k] > 1) rightV[k] = v;
        prevV[k] = v;
      }
    }
  }
  // per wind-grid cell: sub-cell shift and confidence
  const shR = new Float32Array(N), cfR = new Float32Array(N), tex2 = minTex * minTex; let evaluated = 0;
  for (let k = 0; k < N; k++) {
    if (!cnt[k]) continue; evaluated++;
    let sub = 0; const l = leftV[k], r = rightV[k], c = bestV[k];
    if (l > -Infinity && r > -Infinity) { const den = l - 2 * c + r; if (den < 0) sub = Math.max(-0.5, Math.min(0.5, 0.5 * (l - r) / den)); }
    const kb = k + bestS[k], vmin = Math.min(vA[k], vB[kb]);
    const cTex = vmin / (vmin + tex2), cMatch = clamp01(bestZ[k]), cPeak = clamp01((bestZ[k] - sumZ[k] / cnt[k]) / 0.5), cCov = Math.min(1, cnt[k] / (S + 1));
    shR[k] = (bestS[k] + sub) * res * cellM; cfR[k] = cTex * cMatch * cPeak * cCov;
  }
  // back to the fog grid (bilinear, confidence-weighted), then smooth
  const n = w * h, sc = new Float32Array(n), cf = new Float32Array(n);
  for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) {
    const x = (c * dc + r * dr - sMin) / res, y = (c * pc + r * pr - qMin) / res;
    const x0 = Math.max(0, Math.min(W - 2, Math.floor(x))), y0 = Math.max(0, Math.min(H - 2, Math.floor(y)));
    const fx = clamp01(x - x0), fy = clamp01(y - y0), k = y0 * W + x0;
    const w00 = (1 - fx) * (1 - fy), w10 = fx * (1 - fy), w01 = (1 - fx) * fy, w11 = fx * fy;
    const cc = cfR[k] * w00 + cfR[k + 1] * w10 + cfR[k + W] * w01 + cfR[k + W + 1] * w11;
    sc[r * w + c] = shR[k] * cfR[k] * w00 + shR[k + 1] * cfR[k + 1] * w10 + shR[k + W] * cfR[k + W] * w01 + shR[k + W + 1] * cfR[k + W + 1] * w11;
    cf[r * w + c] = cc;
  }
  if (smoothR > 0) { const t2 = new Float32Array(n); for (let pass = 0; pass < 2; pass++) { boxBlurInPlace(sc, w, h, smoothR, t2); boxBlurInPlace(cf, w, h, smoothR, t2); } }
  const shift = new Float32Array(n), conf = new Float32Array(n);
  for (let k = 0; k < n; k++) { const c = cf[k]; conf[k] = c; shift[k] = c > 1e-4 ? sc[k] / c : 0; }
  return { shift, conf, ms: now() - t0, info: { dirFrom, res, block: 2 * R + 1, shifts: 2 * S + 1, stepM: res * cellM, gridW: W, gridH: H, evaluated } };
}

/* ================================================================== 3. GLSL: two-phase flow-mapped noise
   Neyret 2003 (advected textures) / Vlachos 2010 (Portal 2 water flow): the noise is looked up at p - vel * tau, where tau is
   the time since that layer's last reset; two layers half a period apart are cross-faded with triangle weights, so each one
   resets while invisible. Detail drifts with the local wind forever, but a layer is never distorted by more than
   |grad vel| * period. Each restart jumps to a fresh part of the noise (low-discrepancy offset per cycle) so the period does
   not show as pulsing, and the blend keeps the contrast constant. No uniforms are declared; everything is an argument.
   Keep t moderate (seconds since load or since the window start, < ~1e5 periods) for float precision. */
export const FLOW_GLSL = /* glsl */ `
// ---- fogflow.js FLOW_GLSL (GLSL ES 3.00) ----
// Wind texture from solveWind().rg (RG8, linear filtering, row 0 = north): (u east, v north) in m/s.
vec2 flowWind(vec2 rg, float scale) { return (rg * 255.0 - 128.0) * scale; }
// Unit downwind vector (east, north) for a meteorological wind-FROM direction in degrees.
vec2 flowDirTo(float dirFromDeg) { float a = radians(dirFromDeg); return -vec2(sin(a), cos(a)); }
// Two phases for time t (same units as period): x = time since layer 0 reset, y = same for layer 1 (half a period later),
// z = weight of layer 0 (layer 1 gets 1 - z); each layer has weight 0 at its own reset.
vec3 flowPhases(float t, float period) {
  float f0 = fract(t / period), f1 = fract(t / period + 0.5);
  return vec3(f0 * period, f1 * period, 1.0 - abs(2.0 * f0 - 1.0));
}
// Per-cycle offset for layer 0. or 1. (noise-texture units, each component in [0,1)): restarts land on fresh noise.
vec3 flowJump(float t, float period, float layer) {
  float cyc = floor(t / period + 0.5 * layer) + 37.0 * layer;
  return fract(vec3(0.7548776662, 0.5698402910, 0.4142135624) * cyc);
}
// Contrast-preserving cross-fade of two decorrelated noise samples around their mean (a plain mix is greyer mid-fade).
float flowBlend(float n0, float n1, float w0, float mean) {
  float w1 = 1.0 - w0;
  return mean + (w0 * (n0 - mean) + w1 * (n1 - mean)) * inversesqrt(w0 * w0 + w1 * w1);
}
// Advected 3D noise, channel r. p: position; vel: velocity in p-units per t-unit (three.js metres: vec3(u, 0., -v), times a
// visual speed factor if wanted); freq: texture repeats per p-unit (vec3 allows anisotropic noise, e.g. vec3(1./700., 1./250., 1./700.)).
float flowNoise3(highp sampler3D tex, vec3 p, vec3 vel, float t, float period, vec3 freq) {
  vec3 ph = flowPhases(t, period);
  float n0 = texture(tex, (p - vel * ph.x) * freq + flowJump(t, period, 0.0)).r;
  float n1 = texture(tex, (p - vel * ph.y) * freq + flowJump(t, period, 1.0)).r;
  return flowBlend(n0, n1, ph.z, 0.5);
}
// Same for a 2D noise texture (p, vel in the horizontal plane, e.g. vec2(east, north) and vec2(u, v)).
float flowNoise2(highp sampler2D tex, vec2 p, vec2 vel, float t, float period, vec2 freq) {
  vec3 ph = flowPhases(t, period);
  float n0 = texture(tex, (p - vel * ph.x) * freq + flowJump(t, period, 0.0).xy).r;
  float n1 = texture(tex, (p - vel * ph.y) * freq + flowJump(t, period, 1.0).xy).r;
  return flowBlend(n0, n1, ph.z, 0.5);
}
// Motion-compensated interpolation between hourly frames A (a = 0) and B (a = 1) with motionAlongWind() output:
// returns (offset for A, offset for B) in metres (east, north): sample A at pos + .xy, B at pos + .zw, then mix(A, B, a).
// conf = 0 gives a plain cross-fade (stationary banks stay still).
vec4 flowMCOffsets(vec2 dirTo, float shiftM, float conf, float a) {
  vec2 d = dirTo * (shiftM * conf);
  return vec4(-a * d, (1.0 - a) * d);
}
/* Example (fragment shader; your uniforms):
     uniform highp sampler3D uNoise; uniform sampler2D uWind; uniform float uWindScale, uTime, uPeriod;
     vec2 gridUV = vec2(east / gridWidthM, (northEdgeM - north) / gridHeightM);  // texture row 0 = north
     vec2 wnd = flowWind(texture(uWind, gridUV).rg, uWindScale);              // m/s
     float n = flowNoise3(uNoise, worldPos, vec3(wnd.x, 0.0, -wnd.y), uTime, uPeriod, vec3(1.0 / 700.0, 1.0 / 250.0, 1.0 / 700.0));
   Pick period so the drift per period (|wind| * period) is about half a noise wavelength (e.g. 700 m noise, 6 m/s: ~60 s). */
`;
