/* Street ribbons: continuous, terrain-lifted, gradient-shaded lane ribbons: every district street (cls 1, underneath) and the routes (cls 0, on top:
   dashed when not selected, one thick solid-centre line when selected).
   Data: data/ribbons.bin (RIB1, written by tools/make_ribbons.py) + per-night streets_r.u8 (uint8 [n_frames, S], 0-100, 255 = missing).
   One BufferGeometry mesh, one draw call. Wetness lives in a frame-major R8 texture; the vertex shader fetches the two bracketing
   frames and interpolates, the fragment shader applies the colour ramp to the interpolated value (never mixes colours).
   Scrubbing = three uniform writes. */

const TEXW = 4096;
const SENTINEL = 255;

/* Geometry rebuild settings (all done in the browser from the unchanged ribbons.bin, so the sample order and count, and therefore the live job's column map, stay as they are) */
const SM_IT = 2, SM_LAM = 0.3;      // centre-line smoothing: two Laplacian passes
const SUB_DEG = 8, SUB_MAX = 4;     // curve subdivision: one extra vertex per ~8 degrees of turn, at most 4 pieces per segment
const SIG_Y = 25;                   // height profile low-pass (metres of arc, Gaussian sigma) that removes the stairsteps
const JOIN_R = 4.5, JOIN_DOT = -0.7; // two strip ends closer than this and heading the same way are bridged instead of capped
const CAP_N = 4;                    // round cap: vertices on the half circle
const TILE_M = 2500, FULL_D = 6000; // beyond this camera distance one draw per layer; closer, per-tile draws that frustum-cull

const VERT_T = `
  attribute vec2 aOff;      // normal offset in xz (metres at width scale 1): nrm * sign * width/2 * class factor
  attribute vec4 aInfo;     // sample A id, across (-1..1), class (0 route, 1 district street), t = weight toward sample B
  attribute float aB;       // sample B id (== A for original samples, so t is 0)
  attribute float aMask;    // route_mask bits
  attribute vec3 aRt;       // arc length in metres along the strip, lane side (0 left, 1 right), lowest route index in the mask (-1 none)
  uniform sampler2D uWet;
  uniform vec3 uCols[16];   // route colours by route index (bit number in the mask)
  uniform float uRows, uFa, uFb, uU, uWidth, uRouteBit, uDepthK, uThin, uLane, uSelW;
  varying float vWet, vMiss, vAcross, vCls, vSel, vArc;
  varying vec3 vRtCol;
  float wetAt(int s, float fr){ return WETEXPR; }
  void main(){
    int sa = int(aInfo.x + .5), sb = int(aB + .5);
    float a0 = wetAt(sa, uFa), a1 = wetAt(sa, uFb);
    bool am = a0 > 254.5 && a1 > 254.5;
    float wv = a0 > 254.5 ? a1 : (a1 > 254.5 ? a0 : mix(a0, a1, uU));
    bool miss = am;
    if (aInfo.w > .001) { // vertex between two samples: blend their wetness (a missing one yields to the other)
      float b0 = wetAt(sb, uFa), b1 = wetAt(sb, uFb);
      bool bm = b0 > 254.5 && b1 > 254.5;
      float bv = b0 > 254.5 ? b1 : (b1 > 254.5 ? b0 : mix(b0, b1, uU));
      if (am) { wv = bv; miss = bm; } else if (!bm) wv = mix(wv, bv, aInfo.w);
    }
    vMiss = miss ? 1. : 0.;
    vWet = wv;
    vAcross = aInfo.y; vCls = aInfo.z;
    float sel = (uRouteBit >= 0. && mod(floor(aMask / exp2(uRouteBit)), 2.) > .5) ? 1. : 0.;
    vSel = sel;
    vRtCol = uCols[int(max(aRt.z, 0.) + .5)]; // colour of the lowest route in the mask (precomputed on the CPU)
    vArc = aRt.x;
    // routes are ONE centre line per street (right lane dropped, left lane moved to the street centre): thin and dashed when not
    // selected, thick (wide enough to cover the district gradient of the street underneath) when selected
    bool route = aInfo.z < .5, dashed = route && sel < .5;
    vec2 off = aOff * uWidth * (route ? (dashed ? uThin : uSelW) : 1.), sh = vec2(0.);
    if (route) sh = -normalize(aOff * aInfo.y + vec2(1e-6)) * uLane;
    vec3 p = position + vec3(off.x + sh.x, 0., off.y + sh.y);
    p.y += 0.35 * (uWidth - 1.) + (route ? .15 : 0.);
    if (route && aRt.y > .5) { gl_Position = vec4(2., 2., 2., 1.); return; }
    gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(p, 1.);
    // small view-dependent depth bias (metres along the view ray, converted to clip-space depth)
    gl_Position.z -= uDepthK * (1. + 0.003 * gl_Position.w) / gl_Position.w;
  }`;
const fetchTex = (tex) => `texelFetch(${tex}, ivec2(s & 4095, int(fr + .5) * int(uRows + .5) + (s >> 12)), 0).r * 255.`;
const VERT = VERT_T.replace('WETEXPR', fetchTex('uWet'));

const frag = (glslRamp) => `
  precision highp float;
  varying float vWet, vMiss, vAcross, vCls, vSel, vArc;
  varying vec3 vRtCol;
  uniform float uDistFade, uDistVis, uDash, uWidth;
  uniform vec3 uRouteCol;
  ${glslRamp}
  void main(){
    vec3 c = vMiss > .5 ? vec3(.25, .28, .34) : ramp(vWet);
    float ea = abs(vAcross), a;
    if (vCls < .5 && vSel < .5) { // route that is not selected: thin dashed line, route colour inside, wetness ramp on the edge band
      float f = vArc / (uDash * uWidth), ph = fract(f), aa = clamp(fwidth(f), 1e-4, .2);
      float on = smoothstep(0., aa, ph) * (1. - smoothstep(.58, .58 + aa, ph));
      vec3 d = mix(vRtCol, c, smoothstep(.42, .5, ea));
      d = mix(d, vec3(.03, .06, .11), smoothstep(.84, .92, ea));
      if (on < .03) discard;
      gl_FragColor = vec4(d, on);
      return;
    }
    if (vCls < .5) { c = min(c * 1.07, vec3(1.)); a = 1.; }
    else { c *= .94; a = .85 * uDistFade * uDistVis; }
    if (vCls < .5) { // route strips: thick wetness-gradient line with a thin two-tone border (inner light or route colour, outer dark) that reads on every ramp colour
      vec3 inner = mix(vec3(.93, .95, 1.), uRouteCol, vSel); // solid identity interior; the wetness ramp stays on the edge band
      c = mix(inner, c, smoothstep(.8, .84, ea)); // thick line: solid centre, thin wetness-ramp edge
      c = mix(c, vec3(.03, .06, .11), smoothstep(.94, .97, ea));
    } else a *= 1. - .35 * smoothstep(.8, 1., ea); // district strips: wetness gradient only
    if (a < .02) discard;
    gl_FragColor = vec4(c, a);
  }`;

/* Beta preview only (createRibbons({ alt: true })): a second and third frame-major texture (sunshine, u8 = W/m2 / 10; road temperature, u8 = (degC + 10) * 4) and a uMode uniform (0 = wetness / slick ramp, 1 = sun ramp, 2 = road temperature ramp).
   The public page builds the shaders above unchanged. */
const SUN_GLSL = `
  vec3 sunramp(float v){ float t = clamp(v / 90., 0., 1.);
    vec3 a = vec3(.1412,.1882,.3020), b = vec3(.3569,.3098,.4941), c = vec3(.7098,.4039,.2902), d = vec3(.9412,.6353,.2275), e = vec3(1.,.8784,.5412);
    if (t < .22) return mix(a, b, t / .22); if (t < .48) return mix(b, c, (t - .22) / .26); if (t < .72) return mix(c, d, (t - .48) / .24); return mix(d, e, (t - .72) / .28); }
  vec3 tempramp(float v){ float f = (v / 4. - 10.) * 1.8 + 32.; float t = clamp((f - 40.) / 40., 0., 1.);
    vec3 a = vec3(.2314,.4353,.8784), b = vec3(.3725,.7059,.9098), c = vec3(.9098,.9020,.7843), d = vec3(.9490,.6510,.3529), e = vec3(.8980,.3255,.2392);
    if (t < .28) return mix(a, b, t / .28); if (t < .52) return mix(b, c, (t - .28) / .24); if (t < .76) return mix(c, d, (t - .52) / .24); return mix(d, e, (t - .76) / .24); }
`;
const altPick = `(uMode > 1.5 ? ${fetchTex('uTs')} : uMode > .5 ? ${fetchTex('uSun')} : ${fetchTex('uWet')})`;
const altVert = () => VERT_T.replace('WETEXPR', altPick).replace('uniform sampler2D uWet;', 'uniform sampler2D uWet, uSun, uTs; uniform float uMode;');
const altFrag = (f) => f.replace('uniform float uDistFade, uDistVis, uDash, uWidth;', 'uniform float uDistFade, uDistVis, uDash, uWidth, uMode;')
  .replace('void main(){', SUN_GLSL + 'void main(){')
  .replace('vec3 c = vMiss > .5 ? vec3(.25, .28, .34) : ramp(vWet);', 'vec3 c = vMiss > .5 ? vec3(.25, .28, .34) : (uMode > 1.5 ? tempramp(vWet) : uMode > .5 ? sunramp(vWet) : ramp(vWet));');

export function createRibbons({ THREE, scene, EX, terrainH, glslRamp, isTouch, alt = false }) {
  const uniforms = {
    uWet: { value: null }, uRows: { value: 1 }, uFa: { value: 0 }, uFb: { value: 0 }, uU: { value: 0 },
    uWidth: { value: 1 }, uRouteBit: { value: -1 }, uRouteCol: { value: new THREE.Color(0x5ef2ff) },
    uDistFade: { value: 1 }, uDistVis: { value: 1 }, uDepthK: { value: 16 }, uSelW: { value: 2.5 },
    uCols: { value: Array.from({ length: 16 }, () => new THREE.Color(0x5ef2ff)) }, uThin: { value: 0.5 }, uLane: { value: 5 }, uDash: { value: 30 },
  };
  if (alt) { uniforms.uSun = { value: null }; uniforms.uTs = { value: null }; uniforms.uMode = { value: 0 }; }
  const matOpts = {
    uniforms, vertexShader: alt ? altVert() : VERT, fragmentShader: alt ? altFrag(frag(glslRamp)) : frag(glslRamp),
    transparent: true, depthWrite: false, depthTest: true, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  };
  const material = new THREE.ShaderMaterial(matOpts); // routes: drawn over the streets, no depth write
  // streets: each pixel is drawn once (depth write + strict less-than), so overlapping strips and caps at junctions do not blend twice
  const matStreet = new THREE.ShaderMaterial({ ...matOpts, depthWrite: true, depthFunc: THREE.LessDepth });
  // placeholder 1x1 texture until a night is set
  const blank = new THREE.DataTexture(new Uint8Array([SENTINEL]), 1, 1, THREE.RedFormat, THREE.UnsignedByteType);
  blank.needsUpdate = true; uniforms.uWet.value = blank; if (alt) { uniforms.uSun.value = blank; uniforms.uTs.value = blank; }

  let group = null, fullG = null, tileG = null, geoms = [], lodFull = true, tex = null, S = 0, nF = 0, u8 = null, widthM = 6.5, c0 = 0, stw = 0;
  const samples = { S: 0, src: null, side: null, st: null, mask: null, cls: null, pos: null };
  const api = { samples, mesh: null, widthM, uniforms, material, stats: null };
  let selected = false, dist = 1e5;

  api.build = (buffer, meta) => {
    const dv = new DataView(buffer);
    if (dv.getUint32(0, true) !== 0x31424952) throw new Error('bad ribbons.bin');
    S = dv.getUint32(4, true); const nStrips = dv.getUint32(8, true), nIdx = dv.getUint32(12, true);
    widthM = dv.getFloat32(16, true); const version = dv.getUint32(20, true);
    if (meta && meta.S != null && meta.S !== S) throw new Error(`ribbons meta S=${meta.S} != bin S=${S}`);
    let o = 24;
    const take = (T, n) => { const a = new T(buffer, o, n); o += Math.ceil(n * T.BYTES_PER_ELEMENT / 4) * 4; return a; };
    const pos = take(Float32Array, S * 3), nrm = take(Float32Array, S * 2), src = take(Uint32Array, S), st = take(Uint16Array, S);
    const mask = take(Uint16Array, S), side = take(Uint8Array, S), cls = take(Uint8Array, S);
    if (version < 3) take(Uint8Array, S); // version 2 only: fade[S] of the old side blocks
    const stripStart = take(Uint32Array, nStrips + 1); take(Float32Array, nStrips * 4);
    take(Uint32Array, nIdx); // the stored quad indices are not used: the strips are rebuilt below (smoothed, joined, capped)

    const half = widthM / 2;
    const h = terrainH ? (x, z) => terrainH.at(x, z) : () => -1e9;
    const lifted = new Float32Array(S * 3);

    // ---- growable vertex buffers
    const mk = (c) => ({ c, a: new Float32Array(c * 8192), n: 0 });
    const B = { P: mk(3), OFF: mk(2), INFO: mk(4), B: mk(1), MASK: mk(1), RT: mk(3) };
    const add = (b, ...v) => { if ((b.n + 1) * b.c > b.a.length) { const t = new Float32Array(b.a.length * 2); t.set(b.a); b.a = t; } b.a.set(v, b.n * b.c); return b.n++; };
    const addV = (x, y, z, ox, oz, ida, across, c, t, idb, mk_, arc, sd, rl) => {
      add(B.P, x, y, z); add(B.OFF, ox, oz); add(B.INFO, ida, across, c, t); add(B.B, idb); add(B.MASK, mk_); return add(B.RT, arc, sd, rl);
    };
    const lowBit = (m) => { for (let i = 0; i < 16; i++) if ((m >> i) & 1) return i; return -1; };
    const G = (s, sg) => Math.exp(-0.5 * (s / sg) * (s / sg));

    // ---- phase 1: every strip -> smoothed centre line, subdivided curves, smooth height profile, vertex pairs
    const strips = [];
    for (let si = 0; si < nStrips; si++) {
      const a = stripStart[si], n = stripStart[si + 1] - a;
      if (n < 1) continue;
      const c = cls[a], wf = c ? 0.9 : 1, sd = side[a], rl = lowBit(mask[a]), mk_ = mask[a];
      let sx = new Float64Array(n), sz = new Float64Array(n);
      for (let i = 0; i < n; i++) { sx[i] = pos[(a + i) * 3]; sz[i] = pos[(a + i) * 3 + 2]; }
      if (n >= 3) for (let it = 0; it < SM_IT; it++) {
        const tx_ = sx.slice(), tz_ = sz.slice();
        for (let i = 1; i < n - 1; i++) { tx_[i] = sx[i] + SM_LAM * ((sx[i - 1] + sx[i + 1]) / 2 - sx[i]); tz_[i] = sz[i] + SM_LAM * ((sz[i - 1] + sz[i + 1]) / 2 - sz[i]); }
        sx = tx_; sz = tz_;
      }
      const ar = new Float64Array(n);
      for (let i = 1; i < n; i++) ar[i] = ar[i - 1] + Math.hypot(sx[i] - sx[i - 1], sz[i] - sz[i - 1]);
      // node tangents (unit) and left normals
      const tx = new Float64Array(n), tz = new Float64Array(n);
      for (let i = 0; i < n; i++) {
        const i0 = Math.max(0, i - 1), i1 = Math.min(n - 1, i + 1);
        let dx = sx[i1] - sx[i0], dz = sz[i1] - sz[i0], l = Math.hypot(dx, dz);
        if (l < 1e-6) { dx = 1; dz = 0; l = 1; }
        tx[i] = dx / l; tz[i] = dz / l;
      }
      // sample heights: smoothed profile along the arc, never below the terrain under the centre, plus the terrain rise across the width
      const hc = new Float64Array(n), raw = new Float64Array(n), ex = new Float64Array(n), ys = new Float64Array(n);
      for (let i = 0; i < n; i++) {
        const nx = -tz[i], nz = tx[i], hx = nx * half * wf, hz = nz * half * wf;
        hc[i] = h(sx[i], sz[i]);
        raw[i] = Math.max(pos[(a + i) * 3 + 1] * EX, hc[i]);
        ex[i] = hc[i] < -1e8 ? 0 : Math.max(0, h(sx[i] + hx, sz[i] + hz) - hc[i], h(sx[i] - hx, sz[i] - hz) - hc[i]);
      }
      for (let i = 0; i < n; i++) {
        let w = 0, r = 0, e = 0;
        for (let j = i; j >= 0 && ar[i] - ar[j] < 2.5 * SIG_Y; j--) { const g = G(ar[i] - ar[j], SIG_Y); w += g; r += g * raw[j]; e += g * ex[j]; }
        for (let j = i + 1; j < n && ar[j] - ar[i] < 2.5 * SIG_Y; j++) { const g = G(ar[j] - ar[i], SIG_Y); w += g; r += g * raw[j]; e += g * ex[j]; }
        ys[i] = Math.max(r / w, hc[i] + 0.25) + e / w + 1.0 + (c ? 0 : 0.3);
        lifted[(a + i) * 3] = pos[(a + i) * 3]; lifted[(a + i) * 3 + 1] = ys[i]; lifted[(a + i) * 3 + 2] = pos[(a + i) * 3 + 2];
      }
      if (n < 2) continue;
      // turn angle at each node (0 at the ends) decides how finely each segment is subdivided
      const turn = new Float64Array(n);
      for (let i = 1; i < n - 1; i++) {
        const d1x = sx[i] - sx[i - 1], d1z = sz[i] - sz[i - 1], d2x = sx[i + 1] - sx[i], d2z = sz[i + 1] - sz[i], l1 = Math.hypot(d1x, d1z), l2 = Math.hypot(d2x, d2z);
        turn[i] = l1 < 1e-6 || l2 < 1e-6 ? 0 : Math.acos(Math.max(-1, Math.min(1, (d1x * d2x + d1z * d2z) / (l1 * l2)))) * 180 / Math.PI;
      }
      // final polyline vertices: (x, z, y, ida, idb, t)
      const fx = [], fz = [], fy = [], fa = [], fb = [], ft = [];
      for (let j = 0; j < n; j++) {
        fx.push(sx[j]); fz.push(sz[j]); fy.push(ys[j]); fa.push(a + j); fb.push(a + j); ft.push(0);
        if (j === n - 1) break;
        const L = ar[j + 1] - ar[j];
        const ns = L < 12 ? 1 : Math.max(1, Math.min(SUB_MAX, Math.ceil(Math.max(turn[j], turn[j + 1]) / SUB_DEG)));
        for (let k = 1; k < ns; k++) {
          const u = k / ns, u2 = u * u, u3 = u2 * u, h00 = 2 * u3 - 3 * u2 + 1, h10 = u3 - 2 * u2 + u, h01 = -2 * u3 + 3 * u2, h11 = u3 - u2;
          fx.push(h00 * sx[j] + h10 * tx[j] * L + h01 * sx[j + 1] + h11 * tx[j + 1] * L);
          fz.push(h00 * sz[j] + h10 * tz[j] * L + h01 * sz[j + 1] + h11 * tz[j + 1] * L);
          fy.push(ys[j] + (ys[j + 1] - ys[j]) * u); fa.push(a + j); fb.push(a + j + 1); ft.push(u);
        }
      }
      // drop duplicate points
      const keep = [0];
      for (let q = 1; q < fx.length; q++) if (Math.hypot(fx[q] - fx[keep[keep.length - 1]], fz[q] - fz[keep[keep.length - 1]]) > 0.05) keep.push(q);
      if (keep.length < 2) continue;
      const m = keep.length, X = keep.map((q) => fx[q]), Z = keep.map((q) => fz[q]), Y = keep.map((q) => fy[q]), IA = keep.map((q) => fa[q]), IB = keep.map((q) => fb[q]), T = keep.map((q) => ft[q]);
      // segment normals; orientation fixed by the stored normal of the first sample (the lane shift of routes depends on it)
      const sgx = new Float64Array(m - 1), sgz = new Float64Array(m - 1);
      for (let q = 0; q < m - 1; q++) { const dx = X[q + 1] - X[q], dz = Z[q + 1] - Z[q], l = Math.hypot(dx, dz) || 1; sgx[q] = -dz / l; sgz[q] = dx / l; }
      const flip = sgx[0] * nrm[a * 2] + sgz[0] * nrm[a * 2 + 1] < 0 ? -1 : 1;
      const base = B.P.n / 2; // vertex pair index of the first final vertex (pairs are consecutive)
      let arc = 0, cx = 0, cz = 0;
      for (let q = 0; q < m; q++) {
        if (q > 0) arc += Math.hypot(X[q] - X[q - 1], Z[q] - Z[q - 1]);
        let nx, nz, sc = 1;
        if (q === 0) { nx = sgx[0]; nz = sgz[0]; } else if (q === m - 1) { nx = sgx[m - 2]; nz = sgz[m - 2]; } else {
          const ux = sgx[q - 1] + sgx[q], uz = sgz[q - 1] + sgz[q], ln = Math.hypot(ux, uz);
          if (ln < 1e-3) { nx = sgx[q]; nz = sgz[q]; } else { nx = ux / ln; nz = uz / ln; sc = Math.min(2, 2 / ln); }
        }
        nx *= flip; nz *= flip;
        for (let g = 0; g < 2; g++) { const sg = g ? 1 : -1; addV(X[q], Y[q], Z[q], nx * sc * sg * half * wf, nz * sc * sg * half * wf, IA[q], sg, c, T[q], IB[q], mk_, arc, sd, rl); }
        cx += X[q]; cz += Z[q];
      }
      strips.push({ c, wf, base, m, X, Z, Y, sgx, sgz, flip, ida: IA, cx: cx / m, cz: cz / m, st: st[a], sd, rl, mk_, joined: [false, false] });
    }

    // ---- phase 2: join strip ends that meet head-on (same street, opposite headings) with a bridge instead of a cap
    const ends = [];
    strips.forEach((s, i) => {
      if (!s.c) return; // routes (dashed, lane-shifted) get no caps or bridges
      const m = s.m;
      ends.push({ i, e: 0, x: s.X[0], z: s.Z[0], tx: s.X[0] - s.X[1], tz: s.Z[0] - s.Z[1] });
      ends.push({ i, e: 1, x: s.X[m - 1], z: s.Z[m - 1], tx: s.X[m - 1] - s.X[m - 2], tz: s.Z[m - 1] - s.Z[m - 2] });
    });
    for (const q of ends) { const l = Math.hypot(q.tx, q.tz) || 1; q.tx /= l; q.tz /= l; }
    const cellOf = (x, z) => Math.floor(x / 8) * 100003 + Math.floor(z / 8);
    const grid = new Map();
    ends.forEach((q, k) => { const key = cellOf(q.x, q.z); let a = grid.get(key); if (!a) grid.set(key, a = []); a.push(k); });
    const nearest = (k) => {
      const q = ends[k]; let best = -1, bd = JOIN_R * JOIN_R;
      for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) for (const j of grid.get(cellOf(q.x + dx * 8, q.z + dz * 8)) || []) {
        const r = ends[j]; if (r.i === q.i || strips[r.i].st !== strips[q.i].st) continue;
        if (q.tx * r.tx + q.tz * r.tz > JOIN_DOT) continue;
        const d = (q.x - r.x) ** 2 + (q.z - r.z) ** 2; if (d < bd) { bd = d; best = j; }
      }
      return best;
    };
    const bridges = []; // pairs of end indices
    ends.forEach((q, k) => { const j = nearest(k); if (j > k && nearest(j) === k) { bridges.push([k, j]); strips[q.i].joined[q.e] = true; strips[ends[j].i].joined[ends[j].e] = true; } });

    // ---- phase 3: indices into per-tile lists (street layer, route layer), caps and bridges
    const tiles = new Map();
    const tileFor = (s) => { const key = Math.floor(s.cx / TILE_M) * 1000 + Math.floor(s.cz / TILE_M); let t = tiles.get(key); if (!t) tiles.set(key, t = { i1: [], i0: [] }); return t; };
    const PP = B.P.a, OO = B.OFF.a;
    for (const s of strips) {
      const t = tileFor(s), L = s.c ? t.i1 : t.i0;
      for (let q = 0; q < s.m - 1; q++) { const k = 2 * (s.base + q); L.push(k, k + 1, k + 2, k + 1, k + 3, k + 2); }
    }
    for (const [ka, kb] of bridges) {
      const qa = ends[ka], qb = ends[kb], sa = strips[qa.i], sb = strips[qb.i], t = tileFor(sa);
      const va = 2 * (sa.base + (qa.e ? sa.m - 1 : 0)), vb = 2 * (sb.base + (qb.e ? sb.m - 1 : 0));
      const wp = (v) => [PP[v * 3] + OO[v * 2], PP[v * 3 + 2] + OO[v * 2 + 1]];
      const d2 = (p, q) => (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2;
      const straight = d2(wp(va), wp(vb)) + d2(wp(va + 1), wp(vb + 1)), crossed = d2(wp(va), wp(vb + 1)) + d2(wp(va + 1), wp(vb));
      const b0 = straight <= crossed ? vb : vb + 1, b1 = straight <= crossed ? vb + 1 : vb;
      t.i1.push(va, va + 1, b1, va, b1, b0);
    }
    for (const s of strips) {
      if (!s.c) continue;
      for (let e = 0; e < 2; e++) {
        if (s.joined[e]) continue;
        const q = e ? s.m - 1 : 0, v = 2 * (s.base + q), x = s.X[q], z = s.Z[q], y = s.Y[q];
        const nx = OO[(v + 1) * 2], nz = OO[(v + 1) * 2 + 1]; // +side offset (already scaled by half * wf)
        const ln = Math.hypot(nx, nz) || 1, ux = nx / ln, uz = nz / ln; // unit +side normal
        // outward direction of the strip end
        const dx = e ? s.X[s.m - 1] - s.X[s.m - 2] : s.X[0] - s.X[1], dz = e ? s.Z[s.m - 1] - s.Z[s.m - 2] : s.Z[0] - s.Z[1], dl = Math.hypot(dx, dz) || 1, tx = dx / dl, tz = dz / dl;
        const r = half * s.wf, ida = s.ida[q], t = tileFor(s), L = t.i1;
        const c0v = addV(x, y, z, 0, 0, ida, 0, 1, 0, ida, s.mk_, 0, s.sd, s.rl);
        const rim = [];
        for (let k = 0; k < CAP_N; k++) {
          const th = Math.PI * k / (CAP_N - 1), cc = Math.cos(th), ss = Math.sin(th);
          rim.push(addV(x, y, z, (ux * cc + tx * ss) * r, (uz * cc + tz * ss) * r, ida, 1, 1, 0, ida, s.mk_, 0, s.sd, s.rl));
        }
        for (let k = 0; k < CAP_N - 1; k++) L.push(c0v, rim[k], rim[k + 1]);
      }
    }

    // ---- geometry: shared vertex attributes, one index list per layer for the whole set and per tile
    const attr = (b) => new THREE.BufferAttribute(b.a.subarray(0, b.n * b.c), b.c);
    const shared = { position: attr(B.P), aOff: attr(B.OFF), aInfo: attr(B.INFO), aB: attr(B.B), aMask: attr(B.MASK), aRt: attr(B.RT) };
    const mkGeom = (idx) => {
      const g = new THREE.BufferGeometry();
      for (const k in shared) g.setAttribute(k, shared[k]);
      g.setIndex(new THREE.BufferAttribute(Uint32Array.from(idx), 1));
      return g;
    };
    const bsFor = (idx) => { // bounding sphere of the used vertices, padded for the widest the shader ever makes a strip
      let x0 = 1e18, x1 = -1e18, y0 = 1e18, y1 = -1e18, z0 = 1e18, z1 = -1e18;
      for (const v of idx) { const x = PP[v * 3], y = PP[v * 3 + 1], z = PP[v * 3 + 2]; if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; if (z < z0) z0 = z; if (z > z1) z1 = z; }
      const c = new THREE.Vector3((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
      return new THREE.Sphere(c, Math.hypot(x1 - x0, y1 - y0, z1 - z0) / 2 + 80);
    };
    group = new THREE.Group(); group.renderOrder = 10; group.matrixAutoUpdate = false;
    fullG = new THREE.Group(); tileG = new THREE.Group(); group.add(fullG, tileG);
    const mesh = (idx, mat, order, parent) => {
      if (!idx.length) return;
      const g = mkGeom(idx), tiled = parent === tileG; if (tiled) g.boundingSphere = bsFor(idx);
      const m = new THREE.Mesh(g, mat); m.renderOrder = order; m.matrixAutoUpdate = false; m.frustumCulled = tiled;
      parent.add(m); geoms.push(g);
    };
    const all1 = [], all0 = [];
    for (const t of tiles.values()) {
      for (const v of t.i1) all1.push(v);
      for (const v of t.i0) all0.push(v);
      mesh(t.i1, matStreet, 0, tileG); mesh(t.i0, material, 1, tileG);
    }
    mesh(all1, matStreet, 0, fullG); mesh(all0, material, 1, fullG);
    tileG.visible = false;
    scene.add(group); api.mesh = group; api.widthM = widthM;
    api.stats = { samples: S, vertices: B.P.n, triangles: all1.length / 3 + all0.length / 3, strips: strips.length, tiles: tiles.size, bridges: bridges.length };
    Object.assign(samples, { S, src, side, st, mask, cls, pos: lifted, stripStart, nStrips, nIdx });
    return api;
  };

  /* col0/stride (live preview only): this ribbon set owns columns [col0, col0 + S) of a frame-major array with `stride` columns per frame
     (district ribbons + city ribbons share one file). Defaults = the replay layout (stride S, col0 0). */
  api.setNight = (data, nFrames, col0 = 0, stride = S) => {
    if (data.length !== nFrames * stride) throw new Error(`streets_r has ${data.length} bytes, expected ${nFrames * stride}`);
    u8 = data; nF = nFrames; c0 = col0; stw = stride;
    const R = Math.ceil(S / TEXW), H = R * nFrames, buf = new Uint8Array(TEXW * H).fill(SENTINEL);
    for (let f = 0; f < nFrames; f++) buf.set(data.subarray(f * stride + col0, f * stride + col0 + S), f * R * TEXW);
    if (tex) tex.dispose();
    tex = new THREE.DataTexture(buf, TEXW, H, THREE.RedFormat, THREE.UnsignedByteType);
    tex.minFilter = tex.magFilter = THREE.NearestFilter; tex.generateMipmaps = false; tex.flipY = false;
    tex.unpackAlignment = 1; tex.needsUpdate = true;
    uniforms.uWet.value = tex; uniforms.uRows.value = R;
    api.setFrame(uniforms.uFa.value + uniforms.uU.value);
  };

  /* beta only: second data set with the same layout as setNight (sunshine); uMode picks which texture the street colours come from */
  let texAlt = null, texTs = null;
  api.setAlt = (data, nFrames, col0 = 0, stride = S, kind = 'sun') => { // kind 'sun' (uMode 1) or 'ts' road temperature (uMode 2)
    if (!alt) throw new Error('ribbons built without alt');
    if (data.length !== nFrames * stride) throw new Error(`alt data has ${data.length} bytes, expected ${nFrames * stride}`);
    const R = Math.ceil(S / TEXW), H = R * nFrames, buf = new Uint8Array(TEXW * H).fill(SENTINEL);
    for (let f = 0; f < nFrames; f++) buf.set(data.subarray(f * stride + col0, f * stride + col0 + S), f * R * TEXW);
    const ts = kind === 'ts';
    if (ts ? texTs : texAlt) (ts ? texTs : texAlt).dispose();
    const t = new THREE.DataTexture(buf, TEXW, H, THREE.RedFormat, THREE.UnsignedByteType);
    t.minFilter = t.magFilter = THREE.NearestFilter; t.generateMipmaps = false; t.flipY = false; t.unpackAlignment = 1; t.needsUpdate = true;
    if (ts) { texTs = t; uniforms.uTs.value = t; } else { texAlt = t; uniforms.uSun.value = t; }
  };
  api.setMode = (m) => { if (alt) uniforms.uMode.value = m; };

  api.setFrame = (f) => {
    f = Math.min(Math.max(f, 0), Math.max(nF - 1, 0));
    const a = Math.floor(f); uniforms.uFa.value = a; uniforms.uFb.value = Math.min(a + 1, Math.max(nF - 1, 0)); uniforms.uU.value = f - a;
  };

  /* CPU copy of the displayed wetness (same sentinel rules as the shader); -1 = missing */
  api.fillWet = (out, f) => {
    if (!u8) { out.fill(-1); return out; }
    f = Math.min(Math.max(f, 0), nF - 1);
    const a = Math.floor(f), b = Math.min(a + 1, nF - 1), u = f - a, A = a * stw + c0, B = b * stw + c0;
    for (let k = 0; k < S; k++) {
      const w0 = u8[A + k], w1 = u8[B + k];
      out[k] = w0 === SENTINEL ? (w1 === SENTINEL ? -1 : w1) : w1 === SENTINEL ? w0 : w0 + (w1 - w0) * u;
    }
    return out;
  };

  api.setRoute = (bit, hex) => { uniforms.uRouteBit.value = bit; selected = bit >= 0; if (hex != null) uniforms.uRouteCol.value.set(hex); api.setLod(dist); };
  api.setRouteColors = (hexes) => { hexes.forEach((hx, i) => { if (i < 16) uniforms.uCols.value[i].set(hx); }); };
  api.setDistrictVisible = (v) => { uniforms.uDistVis.value = v ? 1 : 0; }; // the district street gradient layer
  api.setLod = (d) => {
    dist = d;
    if (group) { const f = d > FULL_D; if (f !== lodFull) { lodFull = f; fullG.visible = f; tileG.visible = !f; } }
    uniforms.uWidth.value = Math.min(8, Math.max(1, d / 450));
    const t = Math.min(1, Math.max(0, (24000 - d) / 6000)); // district streets stay drawn at every useful zoom; only the whole-region view thins them out
    uniforms.uDistFade.value = t * t * (3 - 2 * t);
  };
  api.setVisible = (v) => { if (group) group.visible = v; };
  api.dispose = () => {
    if (group) scene.remove(group);
    geoms.forEach((g) => g.dispose()); geoms = []; if (tex) tex.dispose(); if (texAlt) texAlt.dispose(); blank.dispose(); material.dispose(); matStreet.dispose();
    group = fullG = tileG = tex = null;
  };
  return api;
}
