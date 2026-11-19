/* Pre-baked city-wide continuous SF road ribbon & intersection renderer (ROAD1).
   Zero runtime geometry rebuild: uploads pre-baked vertex/index streams directly to WebGL
   and exposes a connected topological road graph (worldRoad) for games (skate.js, etc.).
   Retains exact smooth microclimate color blending (wetness, sun, road temperature). */

const MAGIC = 0x31444152; // 'ROAD1'
const VERSION = 1;
const TEXW = 4096;
const SENTINEL = 255;
const FULL_D = 6000;
const CELL = 32;

// @sec: ROAD1 binary parser (zero-copy TypedArray views)
/// Parse a pre-baked ROAD1 ArrayBuffer into zero-copy TypedArray mesh and graph views.
export function parseRoad1(buffer) {
  const dv = new DataView(buffer);
  const magic = dv.getUint32(0, true);
  const version = dv.getUint32(4, true);
  if (magic !== MAGIC || version !== VERSION) {
    throw new Error(`bad ROAD1 header: magic=0x${magic.toString(16)}, ver=${version}`);
  }
  const nVerts = dv.getUint32(8, true);
  const nStreetIdx = dv.getUint32(12, true);
  const nRouteIdx = dv.getUint32(16, true);
  const nTiles = dv.getUint32(20, true);
  const nNodes = dv.getUint32(24, true);
  const nEdges = dv.getUint32(28, true);
  const S1 = dv.getUint32(32, true);
  const nAdj = dv.getUint32(36, true);
  const nPoly = dv.getUint32(40, true);

  let off = 44;
  const take = (T, count) => {
    const arr = new T(buffer, off, count);
    const nb = count * T.BYTES_PER_ELEMENT;
    off += nb + ((4 - (nb % 4)) % 4);
    return arr;
  };

  const mesh = {
    S1,
    nVerts,
    nStreetIdx,
    nRouteIdx,
    nTiles,
    pos: take(Float32Array, nVerts * 3),
    nrm: take(Float32Array, nVerts * 3),
    aInfo: take(Float32Array, nVerts * 4),
    aB: take(Float32Array, nVerts),
    aRt: take(Float32Array, nVerts * 3),
    tileBounds: take(Float32Array, nTiles * 4),
    tileRanges: take(Uint32Array, nTiles * 4),
    streetIdx: take(Uint32Array, nStreetIdx),
    routeIdx: take(Uint32Array, nRouteIdx),
  };

  const graph = {
    nNodes,
    nEdges,
    nAdj,
    nPoly,
    nodePos: take(Float32Array, nNodes * 3),
    nodeEdgeSpan: take(Uint32Array, nNodes * 2),
    nodeAdj: take(Uint32Array, nAdj),
    edgeMeta: take(Uint32Array, nEdges * 4),
    edgeProps: take(Float32Array, nEdges * 4),
    polyPts: take(Float32Array, nPoly * 4),
    polySample: take(Uint32Array, nPoly),
  };

  return { header: { version, nVerts, nStreetIdx, nRouteIdx, nTiles, nNodes, nEdges, S1 }, mesh, graph };
}

// @sec: connected road graph & game surface query API
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

/// Build a 32 m CSR spatial index over all road centerline segments for O(1) game surface queries.
export function createRoadGraph(graph, streets = [], EX = 1.5) {
  const { nNodes, nEdges, nodePos, nodeEdgeSpan, nodeAdj, edgeMeta, edgeProps, polyPts, polySample } = graph;
  // Build flat segment table: (edgeId, p0) for every consecutive pair along each edge polyline
  let segCount = 0;
  let x0 = 1e18, x1 = -1e18, z0 = 1e18, z1 = -1e18;
  for (let e = 0; e < nEdges; e++) {
    const pStart = edgeMeta[e * 4 + 2], pCnt = edgeMeta[e * 4 + 3];
    if (pCnt < 2) continue;
    segCount += pCnt - 1;
    for (let i = 0; i < pCnt; i++) {
      const x = polyPts[(pStart + i) * 4], z = polyPts[(pStart + i) * 4 + 2];
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (z < z0) z0 = z; if (z > z1) z1 = z;
    }
  }
  const segEdge = new Uint32Array(segCount);
  const segP0 = new Uint32Array(segCount);
  let si = 0;
  for (let e = 0; e < nEdges; e++) {
    const pStart = edgeMeta[e * 4 + 2], pCnt = edgeMeta[e * 4 + 3];
    for (let i = 0; i < pCnt - 1; i++) {
      segEdge[si] = e;
      segP0[si] = pStart + i;
      si++;
    }
  }

  let grid = null;
  if (segCount > 0) {
    x0 -= CELL; z0 -= CELL;
    const nx = Math.ceil((x1 - x0) / CELL) + 2, nz = Math.ceil((z1 - z0) / CELL) + 2;
    const counts = new Int32Array(nx * nz + 1);
    const cover = (sIdx, fn) => {
      const p0 = segP0[sIdx], p1 = p0 + 1;
      const ax = polyPts[p0 * 4], az = polyPts[p0 * 4 + 2];
      const bx = polyPts[p1 * 4], bz = polyPts[p1 * 4 + 2];
      const i0 = Math.max(0, Math.floor((Math.min(ax, bx) - x0) / CELL));
      const i1 = Math.min(nx - 1, Math.floor((Math.max(ax, bx) - x0) / CELL));
      const j0 = Math.max(0, Math.floor((Math.min(az, bz) - z0) / CELL));
      const j1 = Math.min(nz - 1, Math.floor((Math.max(az, bz) - z0) / CELL));
      for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) fn(i, j);
    };
    for (let s = 0; s < segCount; s++) cover(s, (i, j) => { counts[j * nx + i + 1]++; });
    for (let c = 0; c < nx * nz; c++) counts[c + 1] += counts[c];
    const items = new Int32Array(counts[nx * nz]);
    const fill = counts.slice();
    for (let s = 0; s < segCount; s++) cover(s, (i, j) => { items[fill[j * nx + i]++] = s; });
    grid = { off: counts, items, nx, nz, x0, z0 };
  }

  const res = {
    found: false, edgeId: -1, s: 0, d: 0, e: 0, y: 0, grade: 0,
    tx: 1, tz: 0, nx: 0, ny: 1, nz: 0, widthM: 7.5, streetIdx: 0, k: -1, onJunction: false,
  };

  /// Query the closest connected road edge surface at (x, z), biased toward heading (hx, hz).
  function roadAt(x, z, hx, hz, maxR = 16) {
    res.found = false;
    if (!grid) return res;
    const ci = Math.floor((x - grid.x0) / CELL), cj = Math.floor((z - grid.z0) / CELL);
    const have = hx !== undefined && hz !== undefined;
    let best = 1e18;

    for (let j = cj - 1; j <= cj + 1; j++) {
      if (j < 0 || j >= grid.nz) continue;
      for (let i = ci - 1; i <= ci + 1; i++) {
        if (i < 0 || i >= grid.nx) continue;
        const c = j * grid.nx + i;
        for (let p = grid.off[c]; p < grid.off[c + 1]; p++) {
          const sIdx = grid.items[p];
          const p0 = segP0[sIdx], p1 = p0 + 1;
          const ax = polyPts[p0 * 4], ay = polyPts[p0 * 4 + 1], az = polyPts[p0 * 4 + 2], as = polyPts[p0 * 4 + 3];
          const bx = polyPts[p1 * 4], by = polyPts[p1 * 4 + 1], bz = polyPts[p1 * 4 + 2], bs = polyPts[p1 * 4 + 3];
          const dx = bx - ax, dz = bz - az, L2 = dx * dx + dz * dz;
          if (L2 < 1e-6) continue;
          const t = clamp(((x - ax) * dx + (z - az) * dz) / L2, 0, 1);
          const px = ax + t * dx - x, pz = az + t * dz - z;
          const d = Math.hypot(px, pz);
          const edgeId = segEdge[sIdx];
          const widthM = edgeProps[edgeId * 4];
          const lim = Math.max(maxR, widthM * 0.5 + 4);
          if (d > lim) continue;
          const L = Math.sqrt(L2);
          const align = have ? Math.abs((dx * hx + dz * hz) / L) : 1;
          const score = d + (have ? 7.0 * (1 - align) : 0);
          if (score < best) {
            best = score;
            let tx = dx / L, tz = dz / L, grade = (by - ay) / L;
            if (have && tx * hx + tz * hz < 0) { tx = -tx; tz = -tz; grade = -grade; }
            const lenN = Math.hypot(grade, 1) || 1;
            const edgeLen = edgeProps[edgeId * 4 + 1];
            const sPos = as + t * (bs - as);
            res.found = true;
            res.edgeId = edgeId;
            res.s = sPos;
            res.d = d;
            res.e = px * tz - pz * tx;
            res.y = (ay + t * (by - ay)) * EX;
            res.grade = grade;
            res.tx = tx; res.tz = tz;
            res.nx = -tx * grade / lenN; res.ny = 1 / lenN; res.nz = -tz * grade / lenN;
            res.widthM = widthM;
            res.streetIdx = Math.round(edgeProps[edgeId * 4 + 3]);
            res.k = t < 0.5 ? polySample[p0] : polySample[p1];
            res.onJunction = sPos < 4.0 || (edgeLen - sPos) < 4.0;
          }
        }
      }
    }
    return res;
  }

  /// Return topological intersection info (position and CCW-ordered incident edges) for a node ID.
  function intersectionAt(nodeId) {
    if (nodeId < 0 || nodeId >= nNodes) return null;
    const sp = nodeEdgeSpan[nodeId * 2], cnt = nodeEdgeSpan[nodeId * 2 + 1];
    const edges = [];
    for (let i = 0; i < cnt; i++) edges.push(nodeAdj[sp + i]);
    return {
      id: nodeId,
      x: nodePos[nodeId * 3],
      y: nodePos[nodeId * 3 + 1] * EX,
      z: nodePos[nodeId * 3 + 2],
      edges,
    };
  }

  /// Find the nearest connected road centerline point within maxR metres (for respawn / snap-to-road).
  function nearestRoad(x, z, maxR = 400) {
    if (!grid) return null;
    const rings = Math.ceil(maxR / CELL);
    const ci = Math.floor((x - grid.x0) / CELL), cj = Math.floor((z - grid.z0) / CELL);
    let bestD2 = maxR * maxR, bestOut = null;
    for (let ring = 0; ring <= rings; ring++) {
      for (let j = cj - ring; j <= cj + ring; j++) {
        if (j < 0 || j >= grid.nz) continue;
        for (let i = ci - ring; i <= ci + ring; i++) {
          if (Math.max(Math.abs(i - ci), Math.abs(j - cj)) !== ring || i < 0 || i >= grid.nx) continue;
          const c = j * grid.nx + i;
          for (let p = grid.off[c]; p < grid.off[c + 1]; p++) {
            const sIdx = grid.items[p], p0 = segP0[sIdx], p1 = p0 + 1;
            const ax = polyPts[p0 * 4], az = polyPts[p0 * 4 + 2];
            const bx = polyPts[p1 * 4], bz = polyPts[p1 * 4 + 2];
            const dx = bx - ax, dz = bz - az, L2 = dx * dx + dz * dz || 1;
            const t = clamp(((x - ax) * dx + (z - az) * dz) / L2, 0, 1);
            const rx = ax + t * dx, rz = az + t * dz;
            const d2 = (rx - x) ** 2 + (rz - z) ** 2;
            if (d2 < bestD2) {
              bestD2 = d2;
              const L = Math.sqrt(L2);
              bestOut = { x: rx, z: rz, tx: dx / L, tz: dz / L, k: t < 0.5 ? polySample[p0] : polySample[p1], edgeId: segEdge[sIdx] };
            }
          }
        }
      }
      if (bestOut && Math.sqrt(bestD2) <= ring * CELL) break;
    }
    return bestOut;
  }

  return {
    roadAt,
    intersectionAt,
    nearestRoad,
    streets,
    stats: () => ({ nodes: nNodes, edges: nEdges, segments: segCount }),
  };
}

// @sec: shaders (smooth wetness/sun/temp blending + subtle SF hill shading)
const VERT_T = `
  attribute vec4 aInfo;     // ida, across (-1..1), cls (0 route, 1 street), t
  attribute float aB;       // idb
  attribute vec3 aRt;       // arc, mask, routeIdx
  uniform sampler2D uWet;
  uniform float uRows, uFa, uFb, uU, uEx, uDepthK, uMaxS;
  varying float vWet, vMiss, vAcross, vCls, vSlopeShade;
  float wetAt(int s, float fr){
    if (s < 0 || float(s) >= uMaxS) return 255.;
    return WETEXPR;
  }
  void main(){
    int sa = int(aInfo.x + .5), sb = int(aB + .5);
    float a0 = wetAt(sa, uFa), a1 = wetAt(sa, uFb);
    bool am = a0 > 254.5 && a1 > 254.5;
    float wv = a0 > 254.5 ? a1 : (a1 > 254.5 ? a0 : mix(a0, a1, uU));
    bool miss = am;
    if (aInfo.w > .001) {
      float b0 = wetAt(sb, uFa), b1 = wetAt(sb, uFb);
      bool bm = b0 > 254.5 && b1 > 254.5;
      float bv = b0 > 254.5 ? b1 : (b1 > 254.5 ? b0 : mix(b0, b1, uU));
      if (am) { wv = bv; miss = bm; } else if (!bm) wv = mix(wv, bv, aInfo.w);
    }
    vMiss = miss ? 1. : 0.;
    vWet = wv;
    vAcross = aInfo.y;
    vCls = aInfo.z;
    // Subtle directional hill shading so steep Potrero Hill lips read cleanly in 3D
    vSlopeShade = 0.93 + 0.07 * clamp(dot(normalize(normal), normalize(vec3(0.35, 0.85, 0.40))), 0., 1.);
    vec3 p = vec3(position.x, position.y * uEx, position.z);
    gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(p, 1.);
    gl_Position.z -= uDepthK * (1. + 0.003 * gl_Position.w) / gl_Position.w;
  }`;

const fetchTex = (tex) => `texelFetch(${tex}, ivec2(s & 4095, int(fr + .5) * int(uRows + .5) + (s >> 12)), 0).r * 255.`;
const VERT = VERT_T.replace('WETEXPR', fetchTex('uWet'));

const frag = (glslRamp) => `
  precision highp float;
  varying float vWet, vMiss, vAcross, vCls, vSlopeShade;
  uniform float uDistFade, uDistVis;
  ${glslRamp}
  void main(){
    vec3 c = vMiss > .5 ? vec3(.25, .28, .34) : ramp(vWet);
    c *= 0.95 * vSlopeShade;
    float ea = abs(vAcross);
    float a = 0.88 * uDistFade * uDistVis * (1. - .28 * smoothstep(.82, 1., ea));
    if (a < .02) discard;
    gl_FragColor = vec4(c, a);
  }`;

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
const altFrag = (f) => f
  .replace('uniform float uDistFade, uDistVis;', 'uniform float uDistFade, uDistVis, uMode;')
  .replace('void main(){', SUN_GLSL + 'void main(){')
  .replace('vec3 c = vMiss > .5 ? vec3(.25, .28, .34) : ramp(vWet);', 'vec3 c = vMiss > .5 ? vec3(.25, .28, .34) : (uMode > 1.5 ? tempramp(vWet) : uMode > .5 ? sunramp(vWet) : ramp(vWet));');

// @sec: WebGL controller (direct-upload pre-baked ROAD1 mesh)
/// Create a pre-baked ROAD1 city-wide road renderer with drop-in ribbons API compatibility.
export function createRoadnet({ THREE, scene, EX = 1.5, glslRamp, alt = false }) {
  const uniforms = {
    uWet: { value: null }, uRows: { value: 1 }, uFa: { value: 0 }, uFb: { value: 0 }, uU: { value: 0 },
    uEx: { value: EX }, uDepthK: { value: 16 }, uMaxS: { value: 0 },
    uDistFade: { value: 1 }, uDistVis: { value: 1 },
  };
  if (alt) { uniforms.uSun = { value: null }; uniforms.uTs = { value: null }; uniforms.uMode = { value: 0 }; }

  const matStreet = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: alt ? altVert() : VERT,
    fragmentShader: alt ? altFrag(frag(glslRamp)) : frag(glslRamp),
    transparent: true,
    depthWrite: true,
    depthTest: true,
    depthFunc: THREE.LessDepth,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2,
  });

  const blank = new THREE.DataTexture(new Uint8Array([SENTINEL]), 1, 1, THREE.RedFormat, THREE.UnsignedByteType);
  blank.needsUpdate = true;
  uniforms.uWet.value = blank;
  if (alt) { uniforms.uSun.value = blank; uniforms.uTs.value = blank; }

  let group = null, fullG = null, tileG = null, geoms = [], lodFull = true;
  let tex = null, texAlt = null, texTs = null;
  let S = 0, nF = 0, u8 = null, c0 = 0, stw = 0;

  const api = { mesh: null, worldRoad: null, stats: null, uniforms, material: matStreet };

  /// Upload pre-baked ROAD1 buffers directly to WebGL and build the game road graph.
  api.build = (buffer, meta = null) => {
    const t0 = typeof performance !== 'undefined' ? performance.now() : Date.now();
    const { mesh, graph } = parseRoad1(buffer);

    const shared = {
      position: new THREE.BufferAttribute(mesh.pos, 3),
      normal: new THREE.BufferAttribute(mesh.nrm, 3),
      aInfo: new THREE.BufferAttribute(mesh.aInfo, 4),
      aB: new THREE.BufferAttribute(mesh.aB, 1),
      aRt: new THREE.BufferAttribute(mesh.aRt, 3),
    };

    const mkGeom = (idxSlice, bs) => {
      const g = new THREE.BufferGeometry();
      for (const k in shared) g.setAttribute(k, shared[k]);
      g.setIndex(new THREE.BufferAttribute(idxSlice, 1));
      if (bs) g.boundingSphere = bs;
      return g;
    };

    group = new THREE.Group();
    group.renderOrder = 9;
    group.matrixAutoUpdate = false;
    fullG = new THREE.Group();
    tileG = new THREE.Group();
    group.add(fullG, tileG);

    if (mesh.streetIdx.length > 0) {
      const gFull = mkGeom(mesh.streetIdx, null);
      const mFull = new THREE.Mesh(gFull, matStreet);
      mFull.renderOrder = 0; mFull.matrixAutoUpdate = false; mFull.frustumCulled = false;
      fullG.add(mFull);
      geoms.push(gFull);
    }

    for (let t = 0; t < mesh.nTiles; t++) {
      const sOff = mesh.tileRanges[t * 4], sCnt = mesh.tileRanges[t * 4 + 1];
      if (!sCnt) continue;
      const cx = mesh.tileBounds[t * 4], cy = mesh.tileBounds[t * 4 + 1] * EX, cz = mesh.tileBounds[t * 4 + 2];
      const rad = mesh.tileBounds[t * 4 + 3] * Math.max(1, EX);
      const gTile = mkGeom(mesh.streetIdx.subarray(sOff, sOff + sCnt), new THREE.Sphere(new THREE.Vector3(cx, cy, cz), rad));
      const mTile = new THREE.Mesh(gTile, matStreet);
      mTile.renderOrder = 0; mTile.matrixAutoUpdate = false; mTile.frustumCulled = true;
      tileG.add(mTile);
      geoms.push(gTile);
    }

    tileG.visible = false;
    scene.add(group);
    api.mesh = group;
    api.worldRoad = createRoadGraph(graph, (meta && meta.streets) || [], EX);
    const buildMs = (typeof performance !== 'undefined' ? performance.now() : Date.now()) - t0;
    api.stats = {
      vertices: mesh.nVerts,
      triangles: mesh.nStreetIdx / 3,
      tiles: mesh.nTiles,
      nodes: graph.nNodes,
      edges: graph.nEdges,
      S1: mesh.S1,
      buildMs: Math.round(buildMs * 10) / 10,
    };
    return api;
  };

  /// Bind frame-major wetness data (columns [col0, col0 + colCount) of stride) to the road shader texture.
  api.setNight = (data, nFrames, col0 = 0, stride = data.length / Math.max(nFrames, 1), colCount = stride - col0) => {
    u8 = data; nF = nFrames; c0 = col0; stw = stride; S = colCount;
    const R = Math.ceil(Math.max(S, 1) / TEXW), H = R * nFrames;
    const buf = new Uint8Array(TEXW * H).fill(SENTINEL);
    for (let f = 0; f < nFrames; f++) {
      buf.set(data.subarray(f * stride + col0, f * stride + col0 + S), f * R * TEXW);
    }
    if (tex) tex.dispose();
    tex = new THREE.DataTexture(buf, TEXW, H, THREE.RedFormat, THREE.UnsignedByteType);
    tex.minFilter = tex.magFilter = THREE.NearestFilter;
    tex.generateMipmaps = false; tex.flipY = false; tex.unpackAlignment = 1; tex.needsUpdate = true;
    uniforms.uWet.value = tex;
    uniforms.uRows.value = R;
    uniforms.uMaxS.value = S;
    api.setFrame(uniforms.uFa.value + uniforms.uU.value);
  };

  /// Bind beta alternate data layer ('sun' or 'ts' road temperature) with the same column layout.
  api.setAlt = (data, nFrames, col0 = 0, stride = data.length / Math.max(nFrames, 1), kind = 'sun', colCount = stride - col0) => {
    if (!alt) return;
    const nCol = colCount;
    const R = Math.ceil(Math.max(nCol, 1) / TEXW), H = R * nFrames;
    const buf = new Uint8Array(TEXW * H).fill(SENTINEL);
    for (let f = 0; f < nFrames; f++) {
      buf.set(data.subarray(f * stride + col0, f * stride + col0 + nCol), f * R * TEXW);
    }
    const ts = kind === 'ts';
    if (ts ? texTs : texAlt) (ts ? texTs : texAlt).dispose();
    const t = new THREE.DataTexture(buf, TEXW, H, THREE.RedFormat, THREE.UnsignedByteType);
    t.minFilter = t.magFilter = THREE.NearestFilter;
    t.generateMipmaps = false; t.flipY = false; t.unpackAlignment = 1; t.needsUpdate = true;
    if (ts) { texTs = t; uniforms.uTs.value = t; } else { texAlt = t; uniforms.uSun.value = t; }
  };

  api.setMode = (m) => { if (alt) uniforms.uMode.value = m; };
  api.setFrame = (f) => {
    f = clamp(f, 0, Math.max(nF - 1, 0));
    const a = Math.floor(f);
    uniforms.uFa.value = a;
    uniforms.uFb.value = Math.min(a + 1, Math.max(nF - 1, 0));
    uniforms.uU.value = f - a;
  };
  api.setDistrictVisible = (v) => { uniforms.uDistVis.value = v ? 1 : 0; };
  api.setLod = (d) => {
    if (group) {
      const f = d > FULL_D;
      if (f !== lodFull) { lodFull = f; fullG.visible = f; tileG.visible = !f; }
    }
    const t = clamp((24000 - d) / 6000, 0, 1);
    uniforms.uDistFade.value = t * t * (3 - 2 * t);
  };
  api.setVisible = (v) => { if (group) group.visible = v; };
  api.dispose = () => {
    if (group) scene.remove(group);
    geoms.forEach((g) => g.dispose()); geoms = [];
    if (tex) tex.dispose(); if (texAlt) texAlt.dispose(); if (texTs) texTs.dispose();
    blank.dispose(); matStreet.dispose();
    group = fullG = tileG = tex = texAlt = texTs = null;
  };

  return api;
}
