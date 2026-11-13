/* Live physical sky (beta, ?beta=1&sky=1): sun, moon with phase, ~5000 stars, twilight colours, for San Francisco at the time the viewer is showing.
   Plan: Claude doc "Realistic cloud and sky rendering plan" (Stage 0 measure HUD, Stage 1 sky). Nothing here runs unless app.js imports it behind ?beta=1.
   Cost design: one fullscreen triangle at the far plane (sun, moon, gradient: a handful of ALU ops per pixel), one Points draw for the stars (5k vertices),
   ephemeris recomputed only when the shown time moves by 30 s or more. Scene axes: x east, y up, z = -north (same as the rest of the viewer). */

const R = Math.PI / 180;
const LAT = 37.7749, LON = -122.4194;

/* ------------------------------------------------------------------ ephemeris (low-precision Meeus: sun ~0.01 deg, moon ~0.3 deg + topocentric parallax) */
const norm = (x) => ((x % 360) + 360) % 360;
function eqToHor(ra, dec, lst) { // radians; returns altitude and azimuth (from north, clockwise) in degrees
  const H = lst - ra, sl = Math.sin(LAT * R), cl = Math.cos(LAT * R);
  const alt = Math.asin(sl * Math.sin(dec) + cl * Math.cos(dec) * Math.cos(H));
  const az = Math.atan2(-Math.cos(dec) * Math.sin(H), Math.sin(dec) * cl - Math.cos(dec) * Math.cos(H) * sl);
  return { alt: alt / R, az: norm(az / R) };
}
const dirFromAltAz = (alt, az) => { const a = alt * R, z = az * R; return [Math.sin(z) * Math.cos(a), Math.sin(a), -Math.cos(z) * Math.cos(a)]; };
const mul3 = (A, B) => { const o = new Array(9); for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) o[r * 3 + c] = A[r * 3] * B[c] + A[r * 3 + 1] * B[3 + c] + A[r * 3 + 2] * B[6 + c]; return o; };
const Rz = (a) => [Math.cos(a), -Math.sin(a), 0, Math.sin(a), Math.cos(a), 0, 0, 0, 1];
const Ry = (a) => [Math.cos(a), 0, Math.sin(a), 0, 1, 0, -Math.sin(a), 0, Math.cos(a)];

export function ephemeris(ms) {
  const J = ms / 86400000 + 2440587.5, T = (J - 2451545) / 36525, n = J - 2451545;
  const gmst = norm(280.46061837 + 360.98564736629 * n + 0.000387933 * T * T);
  const lst = norm(gmst + LON) * R, eps = (23.439291 - 0.0130042 * T) * R;
  // sun
  const L0 = 280.46646 + 36000.76983 * T, M = (357.52911 + 35999.05029 * T) * R;
  const C = (1.914602 - 0.004817 * T) * Math.sin(M) + (0.019993 - 0.000101 * T) * Math.sin(2 * M) + 0.000289 * Math.sin(3 * M);
  const sunLon = norm(L0 + C - 0.00569), sl = sunLon * R;
  const sra = Math.atan2(Math.cos(eps) * Math.sin(sl), Math.cos(sl)), sdec = Math.asin(Math.sin(eps) * Math.sin(sl));
  const sun = eqToHor(sra, sdec, lst);
  // moon
  const Lm = 218.3164477 + 481267.88123421 * T, D = (297.8501921 + 445267.1114034 * T) * R, Ms = (357.5291092 + 35999.0502909 * T) * R,
    Mm = (134.9633964 + 477198.8675055 * T) * R, F = (93.272095 + 483202.0175233 * T) * R, s = Math.sin;
  const lon = Lm + 6.288774 * s(Mm) + 1.274027 * s(2 * D - Mm) + 0.658314 * s(2 * D) + 0.213618 * s(2 * Mm) - 0.185116 * s(Ms) - 0.114332 * s(2 * F) + 0.058793 * s(2 * D - 2 * Mm) + 0.057066 * s(2 * D - Ms - Mm) + 0.053322 * s(2 * D + Mm) + 0.045758 * s(2 * D - Ms) - 0.040923 * s(Ms - Mm) - 0.03472 * s(D) - 0.030383 * s(Ms + Mm);
  const lat = 5.128122 * s(F) + 0.280602 * s(Mm + F) + 0.277693 * s(Mm - F) + 0.173237 * s(2 * D - F) + 0.055413 * s(2 * D - Mm + F) + 0.046271 * s(2 * D - Mm - F) + 0.032573 * s(2 * D + F);
  const ml = norm(lon) * R, mb = lat * R;
  const mra = Math.atan2(s(ml) * Math.cos(eps) - Math.tan(mb) * s(eps), Math.cos(ml)), mdec = Math.asin(s(mb) * Math.cos(eps) + Math.cos(mb) * s(eps) * s(ml));
  const moon = eqToHor(mra, mdec, lst); moon.alt -= 0.95 * Math.cos(moon.alt * R); // topocentric parallax (~57')
  const elong = Math.acos(Math.cos(mb) * Math.cos(ml - sl)), illum = (1 - Math.cos(elong)) / 2, waxing = norm(lon - sunLon) < 180;
  // equatorial-of-date -> world, with precession from J2000 for the catalogue stars (about 0.4 deg by 2026)
  const zeta = 2306.2181 * T / 3600 * R, z = (2306.2181 * T + 0.30188 * T * T) / 3600 * R, theta = 2004.3109 * T / 3600 * R;
  const P = mul3(Rz(-z), mul3(Ry(theta), Rz(-zeta)));
  const cL = Math.cos(lst), sL = Math.sin(lst), sp = Math.sin(LAT * R), cp = Math.cos(LAT * R);
  const loc = (X, Y, Z) => [cL * Y - sL * X, Z * sp + (cL * X + sL * Y) * cp, -(Z * cp - (cL * X + sL * Y) * sp)]; // east, up, -north
  const e = loc(1, 0, 0), u = loc(0, 1, 0), w = loc(0, 0, 1);
  const M3 = [e[0], u[0], w[0], e[1], u[1], w[1], e[2], u[2], w[2]]; // row-major, world = M3 * equatorial
  return { sun, moon, sunDir: dirFromAltAz(sun.alt, sun.az), moonDir: dirFromAltAz(moon.alt, moon.az), elong, illum, waxing, lstH: lst / R / 15, eq2w: mul3(M3, P) };
}

/* ------------------------------------------------------------------ sky colour model: zenith and horizon colour by sun elevation (day, golden hour, sunset, civil, nautical, astronomical twilight) */
const KEYS = [[-18, [.001, .002, .008], [.012, .018, .045]], [-12, [.004, .008, .03], [.07, .09, .2]], [-6, [.016, .032, .1], [.5, .29, .38]], [-1, [.09, .16, .38], [1, .52, .28]], [4, [.18, .33, .62], [.98, .72, .52]], [20, [.17, .38, .82], [.6, .75, .93]], [60, [.14, .34, .8], [.55, .72, .92]]];
function skyKeys(el) {
  if (el <= KEYS[0][0]) return [KEYS[0][1], KEYS[0][2]];
  for (let i = 1; i < KEYS.length; i++) if (el <= KEYS[i][0]) {
    const t = (el - KEYS[i - 1][0]) / (KEYS[i][0] - KEYS[i - 1][0]), m = (a, b) => a.map((v, j) => v + (b[j] - v) * t);
    return [m(KEYS[i - 1][1], KEYS[i][1]), m(KEYS[i - 1][2], KEYS[i][2])];
  }
  return [KEYS[6][1], KEYS[6][2]];
}
function sunColour(el) { // transmitted sunlight colour from air mass: white high up, deep orange near the horizon
  const a = Math.max(el, 0.5) * R, am = 1 / (Math.sin(a) + 0.15 * Math.pow(Math.max(el, 0) + 3.885, -1.253)), f = Math.min(1, Math.max(0, (el + 2) / 6));
  return [Math.exp(-am * 0.04), Math.exp(-am * 0.09), Math.exp(-am * 0.22)].map((v) => v * f);
}

const SKY_VS = `
  varying vec2 vNdc;
  void main(){ vNdc = position.xy; gl_Position = vec4(position.xy, .999, 1.); }`;

const SKY_FS = `
  precision highp float;
  varying vec2 vNdc;
  uniform mat4 uInvProj; uniform mat3 uCamRot;
  uniform vec3 uSun, uSunCol, uMoon, uZen, uHor;
  uniform float uMoonE, uSunEl, uIllum, uSize, uExpo, uGamma;
  float hash(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
  float vn(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3. - 2. * f);
    return mix(mix(hash(i), hash(i + vec2(1., 0.)), f.x), mix(hash(i + vec2(0., 1.)), hash(i + vec2(1., 1.)), f.x), f.y); }
  vec3 aces(vec3 x){ return clamp((x * (2.51 * x + .03)) / (x * (2.43 * x + .59) + .14), 0., 1.); }
  void main(){
    vec4 v = uInvProj * vec4(vNdc, 1., 1.);
    vec3 rd = normalize(uCamRot * (v.xyz / v.w));
    float h = max(rd.y, 0.), t = pow(1. - h, 3.5);
    vec3 col = mix(uZen, uHor, t);
    if (rd.y < 0.) col = uHor * mix(1., .55, smoothstep(0., .35, -rd.y)); // below the horizon: haze, only visible past the terrain edge
    float cs = max(dot(rd, uSun), 0.);
    float glow = pow(cs, 8.) * .25 + pow(cs, 64.) * .45 + pow(cs, 900.) * 1.2;
    col += uSunCol * glow * (.5 + .8 * (1. - clamp(uSunEl / 30., 0., 1.))) * (uSunEl > -8. ? 1. : 0.);
    float night = clamp(-(uSunEl + 5.) / 10., 0., 1.);
    float angS = acos(clamp(dot(rd, uSun), -1., 1.)), rs = .00465 * uSize;
    col += uSunCol * (1. - smoothstep(rs * .92, rs * 1.02, angS)) * 60. * (uSunEl > -1. ? 1. : 0.);
    vec3 mr = normalize(cross(uMoon, vec3(0., 1., 0.))), mu = cross(mr, uMoon);
    float rm = .0045 * uSize, am = acos(clamp(dot(rd, uMoon), -1., 1.));
    col += vec3(.7, .78, 1.) * exp(-am / (rm * 14.)) * .015 * uIllum * night * smoothstep(-.02, .05, rd.y);
    if (am < rm * 1.08) {
      vec3 d = rd - uMoon * dot(rd, uMoon); vec2 pq = vec2(dot(d, mr), dot(d, mu)) / rm; float r2 = dot(pq, pq);
      float cov = 1. - smoothstep(.96, 1., sqrt(r2));
      if (cov > 0.) {
        vec3 n = vec3(pq, sqrt(max(1. - r2, 0.)));
        vec3 ms = normalize(uSun - uMoon * dot(uSun, uMoon)); vec2 st = vec2(dot(ms, mr), dot(ms, mu));
        vec3 L = vec3(st * sin(uMoonE), -cos(uMoonE));
        float lit = smoothstep(-.02, .06, dot(n, L));
        float maria = .62 + .38 * smoothstep(.35, .65, vn(pq * 3.1 + 4.) * .6 + vn(pq * 7.3) * .4);
        vec3 m = vec3(.92, .9, .84) * maria * (.9 + .1 * vn(pq * 22.)) * (lit + .035 * (1. - lit)); // faint earthshine on the dark limb
        col = mix(col, m * (1. - .35 * clamp(uSunEl / 30., 0., 1.)), cov * .98);
      }
    }
    col = aces(col * uExpo);
    gl_FragColor = vec4(pow(col, vec3(uGamma)), 1.); // uGamma 1/2.2 straight to the canvas, 1.0 into a linear render target (fog2.js)
  }`;

const STAR_VS = `
  uniform mat3 uM; uniform float uVis, uDpr, uSize;
  attribute float aMag; attribute float aBv;
  varying vec3 vCol; varying float vA;
  void main(){
    vec3 d = uM * position;
    vec4 vv = viewMatrix * vec4(d, 0.);
    gl_Position = projectionMatrix * vec4(vv.xyz, 1.); gl_Position.z = gl_Position.w * .999;
    float br = clamp(pow(10., -.32 * (aMag - 1.)), .28, 2.);
    gl_PointSize = uDpr * clamp(1.4 + (4. - aMag) * .55, 1.5, 4.6) * uSize;
    float ext = .35 + .65 * smoothstep(0., .3, d.y);      // atmospheric extinction near the horizon
    vA = br * ext * uVis * smoothstep(-.01, .06, d.y);
    float k = clamp((aBv + .3) / 2., 0., 1.);
    vCol = k < .35 ? mix(vec3(.7, .8, 1.), vec3(1., .97, .92), k / .35) : mix(vec3(1., .97, .92), vec3(1., .68, .42), (k - .35) / .65);
  }`;
const STAR_FS = `
  precision highp float; varying vec3 vCol; varying float vA; uniform float uLin;
  void main(){ float r = length(gl_PointCoord - .5) * 2.; float a = exp(-r * r * 3.) * (1. - smoothstep(.85, 1., r)); vec3 c = vCol * a * vA; gl_FragColor = vec4(uLin > .5 ? pow(c, vec3(2.2)) : c, 1.); }`;

/* ------------------------------------------------------------------ perf HUD (Stage 0): rolling frame time, draw calls, device */
function makeHud(THREE, renderer) {
  const el = document.createElement('div');
  el.setAttribute('aria-hidden', 'true');
  el.style.cssText = 'position:fixed;right:8px;top:calc(env(safe-area-inset-top,0px) + 72px);z-index:30;padding:6px 8px;border-radius:8px;background:rgba(9,15,28,.8);color:#9db0c9;font:11px/1.35 ui-monospace,Menlo,monospace;pointer-events:none;white-space:pre;font-variant-numeric:tabular-nums';
  document.body.appendChild(el);
  const gl = renderer.getContext(), dbg = gl.getExtension('WEBGL_debug_renderer_info'), gpu = dbg ? String(gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)).slice(0, 38) : 'gpu n/a';
  const buf = [], v2 = new THREE.Vector2(); let last = 0, shown = 0;
  return {
    tick(now, extra) {
      if (last) { buf.push(now - last); if (buf.length > 120) buf.shift(); }
      last = now;
      if (now - shown < 500 || buf.length < 10) return; shown = now;
      const s = [...buf].sort((a, b) => a - b), med = s[s.length >> 1], p95 = s[Math.min(s.length - 1, Math.floor(s.length * 0.95))], i = renderer.info.render, sz = renderer.getDrawingBufferSize(v2);
      el.textContent = `frame ${med.toFixed(1)} ms med  ${p95.toFixed(1)} p95  (${(1000 / med).toFixed(0)} fps)\n${i.calls} draws  ${(i.triangles / 1000).toFixed(0)}k tris  ${sz.x}x${sz.y}  dpr ${renderer.getPixelRatio()}\n${gpu}${extra ? '\n' + extra : ''}`;
    },
  };
}

/* ------------------------------------------------------------------ the sky */
export async function createSky({ THREE, scene, camera, renderer, starsUrl, sky = true, hud = false }) {
  const hudApi = hud ? makeHud(THREE, renderer) : null;
  if (!sky) { let ex = null; return { enabled: false, light: null, setTime() {}, setExtra(fn) { ex = fn; }, update(now) { hudApi.tick(now, ex ? ex() : ''); }, dispose() {} }; }
  const uni = {
    uInvProj: { value: new THREE.Matrix4() }, uCamRot: { value: new THREE.Matrix3() },
    uSun: { value: new THREE.Vector3(0, 1, 0) }, uSunCol: { value: new THREE.Vector3(1, 1, 1) }, uMoon: { value: new THREE.Vector3(0, 1, 0) },
    uZen: { value: new THREE.Vector3() }, uHor: { value: new THREE.Vector3() },
    uGamma: { value: 1 / 2.2 }, uMoonE: { value: 0 }, uSunEl: { value: 30 }, uIllum: { value: 0.5 }, uSize: { value: 1 }, uExpo: { value: 1 },
  };
  const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute([-1, -1, 0, 3, -1, 0, -1, 3, 0], 3));
  const mat = new THREE.ShaderMaterial({ uniforms: uni, vertexShader: SKY_VS, fragmentShader: SKY_FS, depthTest: false, depthWrite: false });
  const dome = new THREE.Mesh(geo, mat); dome.frustumCulled = false; dome.renderOrder = -1000; scene.add(dome);
  dome.onBeforeRender = (r) => { uni.uGamma.value = r.getRenderTarget() ? 1 : 1 / 2.2; su.uLin.value = r.getRenderTarget() ? 1 : 0; }; // linear when drawing into fog2's render target

  // stars: 5044 points from stars.bin (see tools/pack_stars.py); sky still works if the file is missing
  const su = { uLin: { value: 0 }, uM: { value: new THREE.Matrix3() }, uVis: { value: 0 }, uDpr: { value: renderer.getPixelRatio() }, uSize: { value: 1 } };
  let stars = null;
  try {
    const buf = await (await fetch(starsUrl)).arrayBuffer(), dv = new DataView(buf), n = dv.getUint32(4, true);
    const pos = new Float32Array(n * 3), mag = new Float32Array(n), bv = new Float32Array(n);
    for (let k = 0; k < n; k++) {
      const o = 8 + k * 6, ra = dv.getUint16(o, true) / 65535 * 2 * Math.PI, de = dv.getInt16(o + 2, true) / 32767 * Math.PI / 2;
      pos[k * 3] = Math.cos(de) * Math.cos(ra); pos[k * 3 + 1] = Math.cos(de) * Math.sin(ra); pos[k * 3 + 2] = Math.sin(de);
      mag[k] = dv.getUint8(o + 4) / 30 - 2; bv[k] = dv.getInt8(o + 5) / 60;
    }
    const sg = new THREE.BufferGeometry();
    sg.setAttribute('position', new THREE.BufferAttribute(pos, 3)); sg.setAttribute('aMag', new THREE.BufferAttribute(mag, 1)); sg.setAttribute('aBv', new THREE.BufferAttribute(bv, 1));
    const sm = new THREE.ShaderMaterial({ uniforms: su, vertexShader: STAR_VS, fragmentShader: STAR_FS, transparent: true, blending: THREE.AdditiveBlending, depthTest: false, depthWrite: false });
    stars = new THREE.Points(sg, sm); stars.frustumCulled = false; stars.renderOrder = -999; scene.add(stars);
  } catch (e) { console.warn('sky: stars unavailable', e); }

  let lastMs = NaN, E = null, textra = '', L = null, extraFn = null;
  function setTime(ms) {
    if (!isFinite(ms) || Math.abs(ms - lastMs) < 30000) return;
    lastMs = ms; E = ephemeris(ms);
    const [zen, hor] = skyKeys(E.sun.alt), sc = sunColour(E.sun.alt), el = E.sun.alt;
    uni.uSun.value.set(...E.sunDir); uni.uMoon.value.set(...E.moonDir); uni.uSunCol.value.set(...sc); uni.uZen.value.set(...zen); uni.uHor.value.set(...hor);
    uni.uMoonE.value = E.elong; uni.uSunEl.value = el; uni.uIllum.value = E.illum;
    uni.uExpo.value = el > 4 ? 1 : el < -16 ? 1.5 : Math.exp(Math.log(1.5) * (4 - el) / 20);
    su.uM.value.set(...E.eq2w); su.uVis.value = Math.min(1, Math.max(0, -(el + 5) / 7));
    L = { zen, hor, sunDir: E.sunDir, sunCol: sc, moonDir: E.moonDir, illum: E.illum, sunEl: el };
    textra = `sun ${el.toFixed(1)}° az ${E.sun.az.toFixed(0)}°  moon ${E.moon.alt.toFixed(1)}° ${(E.illum * 100).toFixed(0)}% lit`;
    if (stars) stars.visible = su.uVis.value > 0.001;
  }
  return {
    enabled: true, setTime,
    get info() { return E; }, get light() { return L; }, setExtra(fn) { extraFn = fn; },
    setLarge(v) { uni.uSize.value = v ? 4 : 1; su.uSize.value = 1; },
    update(now) {
      uni.uInvProj.value.copy(camera.projectionMatrixInverse); uni.uCamRot.value.setFromMatrix4(camera.matrixWorld);
      su.uDpr.value = renderer.getPixelRatio();
      if (hudApi) hudApi.tick(now, textra + (extraFn ? '\n' + extraFn() : ''));
    },
    dispose() { scene.remove(dome); geo.dispose(); mat.dispose(); if (stars) { scene.remove(stars); stars.geometry.dispose(); stars.material.dispose(); } },
  };
}
