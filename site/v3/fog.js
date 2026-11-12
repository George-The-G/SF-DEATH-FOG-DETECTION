/* Fog layer: stacked, terrain-following, noise-modulated translucent sheets driven by the replay fog sprite sheet.
   Visual stretch: density = sqrt(px / PX_REF), the same square-root idea as the wetness model (FOG_EXP 0.5). */
const HEIGHTS_M = [10, 38, 75, 118, 165, 220]; // metres above ground (multiplied by the viewer's vertical exaggeration)
const PX_REF = 16;       // sprite value (0..255) that maps to full density after the sqrt stretch
const LAYER_ALPHA = [0.13, 0.13, 0.12, 0.11, 0.10, 0.09]; // per-layer max alpha (column of 6 ~ 0.55)

const VERT = `
  uniform float uOff; varying vec2 vUv; varying vec3 vW; varying float vDist;
  void main(){
    vUv = uv; vec3 p = position; p.y += uOff; vW = p;
    vec4 mv = modelViewMatrix * vec4(p, 1.); vDist = length(mv.xyz);
    gl_Position = projectionMatrix * mv;
  }`;

const FRAG = `
  precision highp float;
  varying vec2 vUv; varying vec3 vW; varying float vDist;
  uniform sampler2D uTex; uniform float uFrame, uNT, uNorthUp, uDens, uTime, uVis;
  uniform vec4 uTiles; uniform vec2 uTexel;
  uniform float uThr, uMaxA, uMix, uSeed, uSpeed;
  float tile(float k, vec2 uv){
    k = clamp(k, 0., uNT - 1.); float r = floor(k / uTiles.x), c = k - r * uTiles.x;
    float s = uNorthUp > .5 ? 1. - uv.y : uv.y;
    vec2 tp = clamp(vec2(uv.x * uTiles.z, s * uTiles.w), vec2(.5), uTiles.zw - .5); // stay inside the tile: no bleed into neighbours
    return texture2D(uTex, (vec2(c * uTiles.z, r * uTiles.w) + tp) * uTexel).r;
  }
  float hash(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
  float vnoise(vec2 p){
    vec2 i = floor(p), f = fract(p); f = f * f * (3. - 2. * f);
    return mix(mix(hash(i), hash(i + vec2(1., 0.)), f.x), mix(hash(i + vec2(0., 1.)), hash(i + vec2(1., 1.)), f.x), f.y);
  }
  void main(){
    float a = floor(uFrame), t = uFrame - a;
    float px = mix(tile(a, vUv), tile(a + 1., vUv), t) * 255.;
    float d = clamp(sqrt(px / ${PX_REF.toFixed(1)}) * uDens, 0., 1.);
    // drifting cloud texture (world space, scrolls east-south-east like the onshore flow)
    vec2 q = vW.xz / 1100. + vec2(uSeed * 7.3, uSeed * 3.1) + vec2(1., .35) * (uTime * uSpeed / 1100.);
    float n = .58 * vnoise(q) + .30 * vnoise(q * 2.3 + 11.7) + NOISE3_EXPR
    float dd = clamp(d * (.35 + 1.3 * n), 0., 1.);
    float al = smoothstep(uThr, uThr + .30, dd) * uMaxA;
    float e = smoothstep(0., .12, min(min(vUv.x, 1. - vUv.x), min(vUv.y, 1. - vUv.y)));
    float nf = smoothstep(uVis * .15, uVis, vDist);               // fade near the camera: keeps streets legible
    vec3 col = mix(vec3(.60, .72, .90), vec3(.86, .92, 1.), uMix) * (.9 + .2 * n);
    gl_FragColor = vec4(col, al * e * nf);
  }`;

export function createFog({ THREE, scene, EX, isTouch, getMeta, getTerrainH, toast }) {
  let layers = [], tex = null, geo = null, shared = null, want = true, dens = 1, last = '';
  const noise3 = isTouch ? '0.12 * 0.5;' : '.12 * vnoise(q * 5.3 + 3.9);';
  const frag = FRAG.replace('NOISE3_EXPR', noise3);

  function clear() {
    layers.forEach((m) => { scene.remove(m); m.material.dispose(); }); layers = [];
    if (geo) geo.dispose(); geo = null; if (tex) tex.dispose(); tex = null; shared = null;
  }

  function build(png, g) {
    last = '';
    clear();
    return new Promise((resolve) => {
      new THREE.TextureLoader().load(png, (t) => {
        t.minFilter = t.magFilter = THREE.LinearFilter; t.generateMipmaps = false; t.flipY = false; t.colorSpace = THREE.NoColorSpace;
        t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping; tex = t;
        const meta = getMeta(), terrainH = getTerrainH();
        const wm = g.w * g.cell_m, hm = g.h * g.cell_m, ox = g.x0 - meta.x0, oz = -(g.y0 - meta.y0);
        geo = new THREE.PlaneGeometry(wm, hm, Math.round(g.w / 2), Math.round(g.h / 2));
        geo.rotateX(-Math.PI / 2); geo.translate(ox + wm / 2, 0, oz - hm / 2);
        const p = geo.attributes.position, cx = wm / (g.w / 2) * .6; // sample the neighbourhood: sheets must clear ridges between vertices
        for (let k = 0; k < p.count; k++) {
          let h = 0;
          if (terrainH) {
            const x = p.getX(k), z = p.getZ(k);
            h = Math.max(terrainH.at(x, z), terrainH.at(x + cx, z), terrainH.at(x - cx, z), terrainH.at(x, z + cx), terrainH.at(x, z - cx), 0);
          }
          p.setY(k, h);
        }
        shared = {
          uTex: { value: t }, uFrame: { value: 0 }, uTiles: { value: new THREE.Vector4(g.cols, g.rows, g.w, g.h) }, uNT: { value: g.cols * g.rows },
          uNorthUp: { value: g.row0 === 'north' ? 1 : 0 }, uTexel: { value: new THREE.Vector2(1 / t.image.width, 1 / t.image.height) },
          uDens: { value: dens }, uTime: { value: 0 }, uVis: { value: 1100 },
        };
        HEIGHTS_M.forEach((hm_, i) => {
          const mat = new THREE.ShaderMaterial({
            uniforms: { ...shared, uOff: { value: hm_ * EX }, uThr: { value: i * 0.1 }, uMaxA: { value: LAYER_ALPHA[i] }, uMix: { value: i / (HEIGHTS_M.length - 1) }, uSeed: { value: i + 1 }, uSpeed: { value: 7 + 2.5 * i } },
            vertexShader: VERT, fragmentShader: frag, transparent: true, depthWrite: false, side: THREE.DoubleSide,
          });
          const m = new THREE.Mesh(geo, mat); m.renderOrder = 1 + i; m.frustumCulled = false; m.visible = want; m.userData.h = hm_ * EX;
          scene.add(m); layers.push(m);
        });
        resolve(true);
      }, undefined, () => { toast('Fog layer unavailable'); resolve(false); });
    });
  }

  return {
    build, clear,
    get ready() { return layers.length > 0; },
    setVisible(v) { want = v; layers.forEach((m) => (m.visible = v)); },
    setDensity(v) { dens = v; if (shared) shared.uDens.value = v; },
    setFrame(f) { if (shared) shared.uFrame.value = f; },
    /* per frame: clock for the drift, sort sheets back-to-front, pull the near fade in/out with zoom */
    update(camera, dist, tSec) {
      if (!shared) return;
      shared.uTime.value = tSec;
      shared.uVis.value = Math.min(1100, Math.max(260, dist * 0.45));
      const above = camera.position.y > 120 * EX, key = above ? 'a' : 'b';
      if (key !== last) { last = key; layers.forEach((m, i) => (m.renderOrder = above ? 1 + i : 1 + layers.length - i)); }
    },
  };
}
