/* Street ribbons: continuous, terrain-lifted, gradient-shaded lane ribbons for the routes plus one side block per intersection.
   Data: data/ribbons.bin (RIB1, written by tools/make_ribbons.py) + per-night streets_r.u8 (uint8 [n_frames, S], 0-100, 255 = missing).
   One BufferGeometry mesh, one draw call. Wetness lives in a frame-major R8 texture; the vertex shader fetches the two bracketing
   frames and interpolates, the fragment shader applies the colour ramp to the interpolated value (never mixes colours).
   Scrubbing = three uniform writes. */

const TEXW = 4096;
const SENTINEL = 255;

const VERT = `
  attribute vec2 aOff;      // normal offset in xz (metres at width scale 1): nrm * sign * width/2 * class factor
  attribute vec4 aInfo;     // sample id, across (-1..1), class (0 route, 1 side), fade 0..1
  attribute float aMask;    // route_mask bits
  attribute float aDepth;   // side-block depth 1..3 (street blocks from the route intersection); 1 for route strips and v1 data
  attribute vec2 aRt;       // arc length in metres along the strip, lane side (0 left, 1 right)
  uniform sampler2D uWet;
  uniform vec3 uCols[16];   // route colours by route index (bit number in the mask)
  uniform float uRows, uFa, uFb, uU, uWidth, uRouteBit, uDepthK, uThin, uLane;
  varying float vWet, vMiss, vAcross, vCls, vFade, vSel, vDepth, vArc;
  varying vec3 vRtCol;
  void main(){
    int s = int(aInfo.x + .5), R = int(uRows + .5);
    int ix = s & 4095, iy = s >> 12;
    float w0 = texelFetch(uWet, ivec2(ix, int(uFa + .5) * R + iy), 0).r * 255.;
    float w1 = texelFetch(uWet, ivec2(ix, int(uFb + .5) * R + iy), 0).r * 255.;
    bool m0 = w0 > 254.5, m1 = w1 > 254.5;
    vMiss = (m0 && m1) ? 1. : 0.;
    vWet = m0 ? w1 : (m1 ? w0 : mix(w0, w1, uU));
    vDepth = aDepth; vAcross = aInfo.y; vCls = aInfo.z; vFade = aInfo.w;
    float sel = (uRouteBit >= 0. && mod(floor(aMask / exp2(uRouteBit)), 2.) > .5) ? 1. : 0.;
    vSel = sel;
    // route colour of the lowest route in the mask (the selected route's own colour is used when it is selected)
    vRtCol = uCols[0]; bool found = false;
    for (int i = 0; i < 16; i++) if (!found && mod(floor(aMask / exp2(float(i))), 2.) > .5) { vRtCol = uCols[i]; found = true; }
    vArc = aRt.x;
    // not-selected routes: ONE thin dashed centre line per street (right lane dropped, left lane moved to the street centre)
    bool dashed = aInfo.z < .5 && sel < .5;
    vec2 off = aOff * uWidth * (dashed ? uThin : 1.), sh = vec2(0.);
    if (dashed) sh = -normalize(aOff * aInfo.y + vec2(1e-6)) * uLane;
    vec3 p = position + vec3(off.x + sh.x, 0., off.y + sh.y);
    p.y += 0.35 * (uWidth - 1.) + (dashed ? .15 : 0.);
    if (dashed && aRt.y > .5) { gl_Position = vec4(2., 2., 2., 1.); return; }
    gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(p, 1.);
    // small view-dependent depth bias (metres along the view ray, converted to clip-space depth)
    gl_Position.z -= uDepthK * (1. + 0.003 * gl_Position.w) / gl_Position.w;
  }`;

const frag = (glslRamp) => `
  precision highp float;
  varying float vWet, vMiss, vAcross, vCls, vFade, vSel, vDepth, vArc;
  varying vec3 vRtCol;
  uniform float uSideFade, uSideVis, uSideDepth, uDash, uWidth;
  uniform vec3 uRouteCol;
  ${glslRamp}
  void main(){
    if (vCls > .5 && vDepth > uSideDepth + .5) discard;
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
    else { c *= .94; a = .8 * vFade * uSideFade * uSideVis; }
    if (vCls < .5) { // route strips: thick wetness-gradient line with a thin two-tone border (inner light or route colour, outer dark) that reads on every ramp colour
      vec3 inner = mix(vec3(.93, .95, 1.), uRouteCol, vSel); // solid identity interior; the wetness ramp stays on the edge band
      c = mix(inner, c, smoothstep(.6, .64, ea));
      c = mix(c, vec3(.03, .06, .11), smoothstep(.93, .96, ea));
    } else a *= 1. - .35 * smoothstep(.8, 1., ea); // side strips: wetness gradient only, no route colour
    if (a < .02) discard;
    gl_FragColor = vec4(c, a);
  }`;

export function createRibbons({ THREE, scene, EX, terrainH, glslRamp, isTouch }) {
  const uniforms = {
    uWet: { value: null }, uRows: { value: 1 }, uFa: { value: 0 }, uFb: { value: 0 }, uU: { value: 0 },
    uWidth: { value: 1 }, uRouteBit: { value: -1 }, uRouteCol: { value: new THREE.Color(0x5ef2ff) },
    uSideFade: { value: 1 }, uSideVis: { value: 1 }, uSideDepth: { value: 3 }, uDepthK: { value: 16 },
    uCols: { value: Array.from({ length: 16 }, () => new THREE.Color(0x5ef2ff)) }, uThin: { value: 0.5 }, uLane: { value: 5 }, uDash: { value: 30 },
  };
  const material = new THREE.ShaderMaterial({
    uniforms, vertexShader: VERT, fragmentShader: frag(glslRamp),
    transparent: true, depthWrite: false, depthTest: true, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  });
  // placeholder 1x1 texture until a night is set
  const blank = new THREE.DataTexture(new Uint8Array([SENTINEL]), 1, 1, THREE.RedFormat, THREE.UnsignedByteType);
  blank.needsUpdate = true; uniforms.uWet.value = blank;

  let mesh = null, geom = null, tex = null, S = 0, nF = 0, u8 = null, widthM = 6.5;
  const samples = { S: 0, src: null, side: null, st: null, mask: null, cls: null, pos: null };
  const api = { samples, mesh: null, widthM, uniforms, material };
  let sideVis = true, selected = false, dist = 1e5;

  api.build = (buffer, meta) => {
    const dv = new DataView(buffer);
    if (dv.getUint32(0, true) !== 0x31424952) throw new Error('bad ribbons.bin');
    S = dv.getUint32(4, true); const nStrips = dv.getUint32(8, true), nIdx = dv.getUint32(12, true);
    widthM = dv.getFloat32(16, true); const version = dv.getUint32(20, true);
    if (meta && meta.S != null && meta.S !== S) throw new Error(`ribbons meta S=${meta.S} != bin S=${S}`);
    let o = 24;
    const take = (T, n) => { const a = new T(buffer, o, n); o += Math.ceil(n * T.BYTES_PER_ELEMENT / 4) * 4; return a; };
    const pos = take(Float32Array, S * 3), nrm = take(Float32Array, S * 2), src = take(Uint32Array, S), st = take(Uint16Array, S);
    const mask = take(Uint16Array, S), side = take(Uint8Array, S), cls = take(Uint8Array, S), fade = take(Uint8Array, S);
    const stripStart = take(Uint32Array, nStrips + 1); take(Float32Array, nStrips * 4);
    const idx = take(Uint32Array, nIdx);
    const depth = version >= 2 ? take(Uint8Array, S) : new Uint8Array(S).fill(1); // v2 appends u8 depth[S] after idx
    let maxDepth = 1; for (let k = 0; k < S; k++) if (depth[k] > maxDepth) maxDepth = depth[k];

    const V = S * 2, half = widthM / 2;
    const P = new Float32Array(V * 3), OFF = new Float32Array(V * 2), INFO = new Float32Array(V * 4), MASK = new Float32Array(V), DEPTH = new Float32Array(V);
    const lifted = new Float32Array(S * 3);
    const RT = new Float32Array(V * 2); // arc length along the strip (dash pattern) and lane side
    const h = terrainH ? (x, z) => terrainH.at(x, z) : () => -1e9;
    let arc = 0, nextStrip = 0;
    for (let k = 0; k < S; k++) {
      const x = pos[k * 3], z = pos[k * 3 + 2], nx = nrm[k * 2], nz = nrm[k * 2 + 1];
      if (k === stripStart[nextStrip]) { arc = 0; nextStrip++; } else arc += Math.hypot(x - pos[k * 3 - 3], z - pos[k * 3 - 1]);
      RT[2 * (2 * k)] = RT[2 * (2 * k + 1)] = arc; RT[2 * (2 * k) + 1] = RT[2 * (2 * k + 1) + 1] = side[k];
      const wf = cls[k] ? 0.9 : 1, hx = nx * half * wf, hz = nz * half * wf;
      let y = Math.max(pos[k * 3 + 1] * EX, h(x, z), h(x + hx, z + hz), h(x - hx, z - hz)) + 1.0 + (cls[k] ? 0 : 0.3);
      lifted[k * 3] = x; lifted[k * 3 + 1] = y; lifted[k * 3 + 2] = z;
      for (let sg = 0; sg < 2; sg++) {
        const v = 2 * k + sg, sign = sg ? 1 : -1;
        P[v * 3] = x; P[v * 3 + 1] = y; P[v * 3 + 2] = z;
        OFF[v * 2] = nx * sign * half * wf; OFF[v * 2 + 1] = nz * sign * half * wf;
        INFO[v * 4] = k; INFO[v * 4 + 1] = sign; INFO[v * 4 + 2] = cls[k]; INFO[v * 4 + 3] = fade[k] / 255;
        MASK[v] = mask[k]; DEPTH[v] = depth[k];
      }
    }
    geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(P, 3));
    geom.setAttribute('aOff', new THREE.BufferAttribute(OFF, 2));
    geom.setAttribute('aInfo', new THREE.BufferAttribute(INFO, 4));
    geom.setAttribute('aMask', new THREE.BufferAttribute(MASK, 1));
    geom.setAttribute('aDepth', new THREE.BufferAttribute(DEPTH, 1));
    geom.setAttribute('aRt', new THREE.BufferAttribute(RT, 2));
    geom.setIndex(new THREE.BufferAttribute(idx, 1));
    mesh = new THREE.Mesh(geom, material);
    mesh.frustumCulled = false; mesh.renderOrder = 10; mesh.matrixAutoUpdate = false;
    scene.add(mesh); api.mesh = mesh; api.widthM = widthM;
    Object.assign(samples, { S, src, side, st, mask, cls, depth, maxDepth, pos: lifted, stripStart, nStrips, nIdx });
    return api;
  };

  api.setNight = (data, nFrames) => {
    if (data.length !== nFrames * S) throw new Error(`streets_r has ${data.length} bytes, expected ${nFrames * S}`);
    u8 = data; nF = nFrames;
    const R = Math.ceil(S / TEXW), H = R * nFrames, buf = new Uint8Array(TEXW * H).fill(SENTINEL);
    for (let f = 0; f < nFrames; f++) buf.set(data.subarray(f * S, (f + 1) * S), f * R * TEXW);
    if (tex) tex.dispose();
    tex = new THREE.DataTexture(buf, TEXW, H, THREE.RedFormat, THREE.UnsignedByteType);
    tex.minFilter = tex.magFilter = THREE.NearestFilter; tex.generateMipmaps = false; tex.flipY = false;
    tex.unpackAlignment = 1; tex.needsUpdate = true;
    uniforms.uWet.value = tex; uniforms.uRows.value = R;
    api.setFrame(uniforms.uFa.value + uniforms.uU.value);
  };

  api.setFrame = (f) => {
    f = Math.min(Math.max(f, 0), Math.max(nF - 1, 0));
    const a = Math.floor(f); uniforms.uFa.value = a; uniforms.uFb.value = Math.min(a + 1, Math.max(nF - 1, 0)); uniforms.uU.value = f - a;
  };

  /* CPU copy of the displayed wetness (same sentinel rules as the shader); -1 = missing */
  api.fillWet = (out, f) => {
    if (!u8) { out.fill(-1); return out; }
    f = Math.min(Math.max(f, 0), nF - 1);
    const a = Math.floor(f), b = Math.min(a + 1, nF - 1), u = f - a, A = a * S, B = b * S;
    for (let k = 0; k < S; k++) {
      const w0 = u8[A + k], w1 = u8[B + k];
      out[k] = w0 === SENTINEL ? (w1 === SENTINEL ? -1 : w1) : w1 === SENTINEL ? w0 : w0 + (w1 - w0) * u;
    }
    return out;
  };

  api.setRoute = (bit, hex) => { uniforms.uRouteBit.value = bit; selected = bit >= 0; if (hex != null) uniforms.uRouteCol.value.set(hex); api.setLod(dist); };
  api.setRouteColors = (hexes) => { hexes.forEach((hx, i) => { if (i < 16) uniforms.uCols.value[i].set(hx); }); };
  api.setSideVisible = (v) => { sideVis = !!v; uniforms.uSideVis.value = sideVis ? 1 : 0; };
  api.setSideDepth = (d) => { uniforms.uSideDepth.value = d; api.setSideVisible(d > 0); }; // 0 = off, 1..3 = blocks from each route intersection
  api.setLod = (d) => {
    dist = d;
    uniforms.uWidth.value = Math.min(8, Math.max(1, d / 450));
    const t = Math.min(1, Math.max(0, (24000 - d) / 6000)); // district streets stay drawn at every useful zoom; only the whole-region view thins them out
    uniforms.uSideFade.value = t * t * (3 - 2 * t);
  };
  api.setVisible = (v) => { if (mesh) mesh.visible = v; };
  api.dispose = () => {
    if (mesh) scene.remove(mesh);
    if (geom) geom.dispose(); if (tex) tex.dispose(); blank.dispose(); material.dispose();
    mesh = geom = tex = null;
  };
  return api;
}
