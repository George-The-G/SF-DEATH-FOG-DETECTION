/* Street ribbons: continuous, terrain-lifted, gradient-shaded lane ribbons: every district street (cls 1, underneath) and the routes (cls 0, on top:
   dashed when not selected, one thick solid-centre line when selected).
   Data: data/ribbons.bin (RIB1, written by tools/make_ribbons.py) + per-night streets_r.u8 (uint8 [n_frames, S], 0-100, 255 = missing).
   One BufferGeometry mesh, one draw call. Wetness lives in a frame-major R8 texture; the vertex shader fetches the two bracketing
   frames and interpolates, the fragment shader applies the colour ramp to the interpolated value (never mixes colours).
   Scrubbing = three uniform writes. */

const TEXW = 4096;
const SENTINEL = 255;

const VERT = `
  attribute vec2 aOff;      // normal offset in xz (metres at width scale 1): nrm * sign * width/2 * class factor
  attribute vec4 aInfo;     // sample id, across (-1..1), class (0 route, 1 district street), 1
  attribute float aMask;    // route_mask bits
  attribute vec2 aRt;       // arc length in metres along the strip, lane side (0 left, 1 right)
  uniform sampler2D uWet;
  uniform vec3 uCols[16];   // route colours by route index (bit number in the mask)
  uniform float uRows, uFa, uFb, uU, uWidth, uRouteBit, uDepthK, uThin, uLane, uSelW;
  varying float vWet, vMiss, vAcross, vCls, vSel, vArc;
  varying vec3 vRtCol;
  void main(){
    int s = int(aInfo.x + .5), R = int(uRows + .5);
    int ix = s & 4095, iy = s >> 12;
    float w0 = texelFetch(uWet, ivec2(ix, int(uFa + .5) * R + iy), 0).r * 255.;
    float w1 = texelFetch(uWet, ivec2(ix, int(uFb + .5) * R + iy), 0).r * 255.;
    bool m0 = w0 > 254.5, m1 = w1 > 254.5;
    vMiss = (m0 && m1) ? 1. : 0.;
    vWet = m0 ? w1 : (m1 ? w0 : mix(w0, w1, uU));
    vAcross = aInfo.y; vCls = aInfo.z;
    float sel = (uRouteBit >= 0. && mod(floor(aMask / exp2(uRouteBit)), 2.) > .5) ? 1. : 0.;
    vSel = sel;
    // route colour of the lowest route in the mask (the selected route's own colour is used when it is selected)
    vRtCol = uCols[0]; bool found = false;
    for (int i = 0; i < 16; i++) if (!found && mod(floor(aMask / exp2(float(i))), 2.) > .5) { vRtCol = uCols[i]; found = true; }
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

/* Beta preview only (createRibbons({ alt: true })): a second frame-major texture (sunshine, u8 = W/m2 / 10) and a uMode uniform (0 = wetness / slick ramp, 1 = sun ramp).
   The public page builds the shaders above unchanged. */
const SUN_GLSL = `
  vec3 sunramp(float v){ float t = clamp(v / 90., 0., 1.);
    vec3 a = vec3(.1412,.1882,.3020), b = vec3(.3569,.3098,.4941), c = vec3(.7098,.4039,.2902), d = vec3(.9412,.6353,.2275), e = vec3(1.,.8784,.5412);
    if (t < .22) return mix(a, b, t / .22); if (t < .48) return mix(b, c, (t - .22) / .26); if (t < .72) return mix(c, d, (t - .48) / .24); return mix(d, e, (t - .72) / .28); }
`;
const altVert = (v) => v.replace('uniform sampler2D uWet;', 'uniform sampler2D uWet, uSun; uniform float uMode;')
  .replace('float w0 = texelFetch(uWet, ivec2(ix, int(uFa + .5) * R + iy), 0).r * 255.;', 'float w0 = (uMode > .5 ? texelFetch(uSun, ivec2(ix, int(uFa + .5) * R + iy), 0).r : texelFetch(uWet, ivec2(ix, int(uFa + .5) * R + iy), 0).r) * 255.;')
  .replace('float w1 = texelFetch(uWet, ivec2(ix, int(uFb + .5) * R + iy), 0).r * 255.;', 'float w1 = (uMode > .5 ? texelFetch(uSun, ivec2(ix, int(uFb + .5) * R + iy), 0).r : texelFetch(uWet, ivec2(ix, int(uFb + .5) * R + iy), 0).r) * 255.;');
const altFrag = (f) => f.replace('uniform float uDistFade, uDistVis, uDash, uWidth;', 'uniform float uDistFade, uDistVis, uDash, uWidth, uMode;')
  .replace('void main(){', SUN_GLSL + 'void main(){')
  .replace('vec3 c = vMiss > .5 ? vec3(.25, .28, .34) : ramp(vWet);', 'vec3 c = vMiss > .5 ? vec3(.25, .28, .34) : (uMode > .5 ? sunramp(vWet) : ramp(vWet));');

export function createRibbons({ THREE, scene, EX, terrainH, glslRamp, isTouch, alt = false }) {
  const uniforms = {
    uWet: { value: null }, uRows: { value: 1 }, uFa: { value: 0 }, uFb: { value: 0 }, uU: { value: 0 },
    uWidth: { value: 1 }, uRouteBit: { value: -1 }, uRouteCol: { value: new THREE.Color(0x5ef2ff) },
    uDistFade: { value: 1 }, uDistVis: { value: 1 }, uDepthK: { value: 16 }, uSelW: { value: 2.5 },
    uCols: { value: Array.from({ length: 16 }, () => new THREE.Color(0x5ef2ff)) }, uThin: { value: 0.5 }, uLane: { value: 5 }, uDash: { value: 30 },
  };
  if (alt) { uniforms.uSun = { value: null }; uniforms.uMode = { value: 0 }; }
  const material = new THREE.ShaderMaterial({
    uniforms, vertexShader: alt ? altVert(VERT) : VERT, fragmentShader: alt ? altFrag(frag(glslRamp)) : frag(glslRamp),
    transparent: true, depthWrite: false, depthTest: true, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  });
  // placeholder 1x1 texture until a night is set
  const blank = new THREE.DataTexture(new Uint8Array([SENTINEL]), 1, 1, THREE.RedFormat, THREE.UnsignedByteType);
  blank.needsUpdate = true; uniforms.uWet.value = blank; if (alt) uniforms.uSun.value = blank;

  let mesh = null, geom = null, tex = null, S = 0, nF = 0, u8 = null, widthM = 6.5, c0 = 0, stw = 0;
  const samples = { S: 0, src: null, side: null, st: null, mask: null, cls: null, pos: null };
  const api = { samples, mesh: null, widthM, uniforms, material };
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
    const idx = take(Uint32Array, nIdx);

    const V = S * 2, half = widthM / 2;
    const P = new Float32Array(V * 3), OFF = new Float32Array(V * 2), INFO = new Float32Array(V * 4), MASK = new Float32Array(V);
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
        INFO[v * 4] = k; INFO[v * 4 + 1] = sign; INFO[v * 4 + 2] = cls[k]; INFO[v * 4 + 3] = 1;
        MASK[v] = mask[k];
      }
    }
    geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(P, 3));
    geom.setAttribute('aOff', new THREE.BufferAttribute(OFF, 2));
    geom.setAttribute('aInfo', new THREE.BufferAttribute(INFO, 4));
    geom.setAttribute('aMask', new THREE.BufferAttribute(MASK, 1));
    geom.setAttribute('aRt', new THREE.BufferAttribute(RT, 2));
    geom.setIndex(new THREE.BufferAttribute(idx, 1));
    mesh = new THREE.Mesh(geom, material);
    mesh.frustumCulled = false; mesh.renderOrder = 10; mesh.matrixAutoUpdate = false;
    scene.add(mesh); api.mesh = mesh; api.widthM = widthM;
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
  let texAlt = null;
  api.setAlt = (data, nFrames, col0 = 0, stride = S) => {
    if (!alt) throw new Error('ribbons built without alt');
    if (data.length !== nFrames * stride) throw new Error(`alt data has ${data.length} bytes, expected ${nFrames * stride}`);
    const R = Math.ceil(S / TEXW), H = R * nFrames, buf = new Uint8Array(TEXW * H).fill(SENTINEL);
    for (let f = 0; f < nFrames; f++) buf.set(data.subarray(f * stride + col0, f * stride + col0 + S), f * R * TEXW);
    if (texAlt) texAlt.dispose();
    texAlt = new THREE.DataTexture(buf, TEXW, H, THREE.RedFormat, THREE.UnsignedByteType);
    texAlt.minFilter = texAlt.magFilter = THREE.NearestFilter; texAlt.generateMipmaps = false; texAlt.flipY = false; texAlt.unpackAlignment = 1; texAlt.needsUpdate = true;
    uniforms.uSun.value = texAlt;
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
    uniforms.uWidth.value = Math.min(8, Math.max(1, d / 450));
    const t = Math.min(1, Math.max(0, (24000 - d) / 6000)); // district streets stay drawn at every useful zoom; only the whole-region view thins them out
    uniforms.uDistFade.value = t * t * (3 - 2 * t);
  };
  api.setVisible = (v) => { if (mesh) mesh.visible = v; };
  api.dispose = () => {
    if (mesh) scene.remove(mesh);
    if (geom) geom.dispose(); if (tex) tex.dispose(); if (texAlt) texAlt.dispose(); blank.dispose(); material.dispose();
    mesh = geom = tex = null;
  };
  return api;
}
