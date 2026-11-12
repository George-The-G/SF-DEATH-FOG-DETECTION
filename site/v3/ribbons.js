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
  uniform sampler2D uWet;
  uniform float uRows, uFa, uFb, uU, uWidth, uRouteBit, uDepthK;
  varying float vWet, vMiss, vAcross, vCls, vFade, vSel;
  void main(){
    int s = int(aInfo.x + .5), R = int(uRows + .5);
    int ix = s & 4095, iy = s >> 12;
    float w0 = texelFetch(uWet, ivec2(ix, int(uFa + .5) * R + iy), 0).r * 255.;
    float w1 = texelFetch(uWet, ivec2(ix, int(uFb + .5) * R + iy), 0).r * 255.;
    bool m0 = w0 > 254.5, m1 = w1 > 254.5;
    vMiss = (m0 && m1) ? 1. : 0.;
    vWet = m0 ? w1 : (m1 ? w0 : mix(w0, w1, uU));
    vAcross = aInfo.y; vCls = aInfo.z; vFade = aInfo.w;
    vSel = (uRouteBit >= 0. && mod(floor(aMask / exp2(uRouteBit)), 2.) > .5) ? 1. : 0.;
    vec3 p = position + vec3(aOff.x, 0., aOff.y) * uWidth;
    p.y += 0.35 * (uWidth - 1.);
    gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(p, 1.);
    // small view-dependent depth bias (metres along the view ray, converted to clip-space depth)
    gl_Position.z -= uDepthK * (1. + 0.003 * gl_Position.w) / gl_Position.w;
  }`;

const frag = (glslRamp) => `
  precision highp float;
  varying float vWet, vMiss, vAcross, vCls, vFade, vSel;
  uniform float uSideFade, uSideVis;
  uniform vec3 uRouteCol;
  ${glslRamp}
  void main(){
    vec3 c = vMiss > .5 ? vec3(.25, .28, .34) : ramp(vWet);
    float ea = abs(vAcross), a;
    if (vCls < .5) { c = min(c * 1.07, vec3(1.)); a = 1.; }
    else { c *= .94; a = .8 * vFade * uSideFade * uSideVis; }
    c = mix(c, uRouteCol, smoothstep(.6, .8, ea) * .88 * vSel);
    a *= 1. - .35 * smoothstep(.8, 1., ea);
    if (a < .02) discard;
    gl_FragColor = vec4(c, a);
  }`;

export function createRibbons({ THREE, scene, EX, terrainH, glslRamp, isTouch }) {
  const uniforms = {
    uWet: { value: null }, uRows: { value: 1 }, uFa: { value: 0 }, uFb: { value: 0 }, uU: { value: 0 },
    uWidth: { value: 1 }, uRouteBit: { value: -1 }, uRouteCol: { value: new THREE.Color(0x5ef2ff) },
    uSideFade: { value: 1 }, uSideVis: { value: 1 }, uDepthK: { value: 16 },
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
    widthM = dv.getFloat32(16, true);
    if (meta && meta.S != null && meta.S !== S) throw new Error(`ribbons meta S=${meta.S} != bin S=${S}`);
    let o = 24;
    const take = (T, n) => { const a = new T(buffer, o, n); o += Math.ceil(n * T.BYTES_PER_ELEMENT / 4) * 4; return a; };
    const pos = take(Float32Array, S * 3), nrm = take(Float32Array, S * 2), src = take(Uint32Array, S), st = take(Uint16Array, S);
    const mask = take(Uint16Array, S), side = take(Uint8Array, S), cls = take(Uint8Array, S), fade = take(Uint8Array, S);
    const stripStart = take(Uint32Array, nStrips + 1); take(Float32Array, nStrips * 4);
    const idx = take(Uint32Array, nIdx);

    const V = S * 2, half = widthM / 2;
    const P = new Float32Array(V * 3), OFF = new Float32Array(V * 2), INFO = new Float32Array(V * 4), MASK = new Float32Array(V);
    const lifted = new Float32Array(S * 3);
    const h = terrainH ? (x, z) => terrainH.at(x, z) : () => -1e9;
    for (let k = 0; k < S; k++) {
      const x = pos[k * 3], z = pos[k * 3 + 2], nx = nrm[k * 2], nz = nrm[k * 2 + 1];
      const wf = cls[k] ? 0.75 : 1, hx = nx * half * wf, hz = nz * half * wf;
      let y = Math.max(pos[k * 3 + 1] * EX, h(x, z), h(x + hx, z + hz), h(x - hx, z - hz)) + 1.0 + (cls[k] ? 0 : 0.3);
      lifted[k * 3] = x; lifted[k * 3 + 1] = y; lifted[k * 3 + 2] = z;
      for (let sg = 0; sg < 2; sg++) {
        const v = 2 * k + sg, sign = sg ? 1 : -1;
        P[v * 3] = x; P[v * 3 + 1] = y; P[v * 3 + 2] = z;
        OFF[v * 2] = nx * sign * half * wf; OFF[v * 2 + 1] = nz * sign * half * wf;
        INFO[v * 4] = k; INFO[v * 4 + 1] = sign; INFO[v * 4 + 2] = cls[k]; INFO[v * 4 + 3] = fade[k] / 255;
        MASK[v] = mask[k];
      }
    }
    geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(P, 3));
    geom.setAttribute('aOff', new THREE.BufferAttribute(OFF, 2));
    geom.setAttribute('aInfo', new THREE.BufferAttribute(INFO, 4));
    geom.setAttribute('aMask', new THREE.BufferAttribute(MASK, 1));
    geom.setIndex(new THREE.BufferAttribute(idx, 1));
    mesh = new THREE.Mesh(geom, material);
    mesh.frustumCulled = false; mesh.renderOrder = 10; mesh.matrixAutoUpdate = false;
    scene.add(mesh); api.mesh = mesh; api.widthM = widthM;
    Object.assign(samples, { S, src, side, st, mask, cls, pos: lifted, stripStart, nStrips, nIdx });
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
  api.setSideVisible = (v) => { sideVis = !!v; uniforms.uSideVis.value = sideVis ? 1 : 0; };
  api.setLod = (d) => {
    dist = d;
    uniforms.uWidth.value = Math.min(8, Math.max(1, d / 450));
    const t = Math.min(1, Math.max(0, (7000 - d) / 3500));
    uniforms.uSideFade.value = selected && d < 12000 ? 1 : t * t * (3 - 2 * t);
  };
  api.setVisible = (v) => { if (mesh) mesh.visible = v; };
  api.dispose = () => {
    if (mesh) scene.remove(mesh);
    if (geom) geom.dispose(); if (tex) tex.dispose(); blank.dispose(); material.dispose();
    mesh = geom = tex = null;
  };
  return api;
}
