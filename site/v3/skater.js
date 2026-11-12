/* Low-poly procedural skateboarder for the route flyover (three.js primitives only, no external assets).
   Model units are metres (about 1.8 m tall, facing +Z, the board lies along Z). The root is placed on the street by the caller,
   oriented along the route and scaled so the figure reads at the bottom of the view.

   Wobble model (wetness w = 0..100, the interpolated street wetness at the skater's route position and time frame):
     t     = smoothstep(W0, W1, w)             W0 = 20 (dry: no wobble at all), W1 = 100
     amp   = MAX_AMP * t                       MAX_AMP = 2 x CHAR_W (character width 0.5 m) -> +-1 m in model units at w = 100
     freq  = F0 + (F1 - F0) * t   [Hz]         0.8 Hz rising to 2.2 Hz
     x(t)  = amp * (sin(phi) + 0.28 sin(2.3 phi + 1.1)) / 1.28        side-to-side offset, phi integrates 2 pi freq dt
     roll  = -LEAN * t * cos(phi) - slight lean into the motion; arms counter-swing
   smoothstep gives 0 below w = 20, a gentle start, the steepest growth around w = 60 and a soft saturation at 100. */

export const CHAR_W = 0.5;          // character width in model metres (shoulder span incl. arms)
export const MAX_AMP = 2 * CHAR_W;  // side-to-side amplitude at wetness 100
const W0 = 20, W1 = 100, F0 = 0.8, F1 = 2.2, LEAN = 0.5;
const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

export function wobbleFor(w) { // exported for tests / documentation
  const t = w == null ? 0 : smooth(W0, W1, w);
  return { t, amp: MAX_AMP * t, freq: F0 + (F1 - F0) * t };
}

export function createSkater({ THREE, reduceMotion = false }) {
  const root = new THREE.Group(); root.visible = false;
  const wob = new THREE.Group(); root.add(wob);          // side-to-side translation
  const lean = new THREE.Group(); lean.position.y = 0.05; wob.add(lean); // roll about the board (pivot near the wheels)
  const mats = [];
  const mat = (c, em = 0.35) => { const m = new THREE.MeshLambertMaterial({ color: c, emissive: new THREE.Color(c).multiplyScalar(em), transparent: true }); mats.push(m); return m; };
  const box = (w, h, d, m) => new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m);
  const up = new THREE.Vector3(0, 1, 0);
  const limb = (a, b, w, d, m) => { // box from point a to point b
    const dir = new THREE.Vector3().subVectors(b, a), len = dir.length(), g = box(w, len, d, m);
    g.position.copy(a).addScaledVector(dir, 0.5); g.quaternion.setFromUnitVectors(up, dir.normalize()); return g;
  };
  const v = (x, y, z) => new THREE.Vector3(x, y, z);
  const deck = mat(0xefe6cf), grip = mat(0x20242c, 0.1), wheel = mat(0xffffff, 0.5), pants = mat(0x2f5fb8, 0.3), shoe = mat(0xf2f2f2, 0.4),
    hood = mat(0xff7a2f, 0.45), skin = mat(0xe2a77a, 0.35), cap = mat(0x1d2b52, 0.3);

  const base = lean; // everything rolls together
  const board = box(0.25, 0.03, 0.84, deck); board.position.y = 0.1; base.add(board);
  const gtop = box(0.23, 0.006, 0.82, grip); gtop.position.y = 0.117; base.add(gtop);
  for (const sz of [-1, 1]) {
    const tail = box(0.25, 0.03, 0.12, deck); tail.position.set(0, 0.125, sz * 0.46); tail.rotation.x = -sz * 0.35; base.add(tail);
    for (const sx of [-1, 1]) {
      const w = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.045, 0.04, 10), wheel);
      w.rotation.z = Math.PI / 2; w.position.set(sx * 0.12, 0.045, sz * 0.27); base.add(w);
    }
  }
  // legs: feet across the board (front foot ahead, back foot behind), hips above the middle
  const hip = v(0, 0.98, 0);
  for (const [zf, kn] of [[0.24, 0.1], [-0.2, -0.08]]) {
    const foot = box(0.1, 0.07, 0.26, shoe); foot.position.set(0, 0.17, zf); base.add(foot);
    const knee = v(0, 0.58, zf * 0.55 + kn * 0.6);
    base.add(limb(v(0, 0.19, zf), knee, 0.13, 0.14, pants), limb(knee, hip, 0.16, 0.17, pants));
  }
  // upper body, turned sideways to the direction of travel like a rider's stance
  const upper = new THREE.Group(); upper.position.copy(hip); upper.rotation.y = -1.15; base.add(upper);
  upper.add(limb(v(0, 0, 0), v(0, 0.5, 0), 0.34, 0.2, hood));
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.115, 12, 9), skin); head.position.set(0, 0.64, 0); upper.add(head);
  const hat = new THREE.Mesh(new THREE.SphereGeometry(0.125, 12, 7, 0, Math.PI * 2, 0, Math.PI * 0.55), cap); hat.position.set(0, 0.66, 0); upper.add(hat);
  const arms = [];
  for (const s of [-1, 1]) {
    const pivot = new THREE.Group(); pivot.position.set(s * 0.2, 0.44, 0); upper.add(pivot);
    pivot.add(limb(v(0, 0, 0), v(s * 0.26, -0.3, 0.02), 0.09, 0.09, hood));
    const hand = new THREE.Mesh(new THREE.SphereGeometry(0.05, 8, 6), skin); hand.position.set(s * 0.26, -0.32, 0.02); pivot.add(hand);
    arms.push([pivot, s]);
  }
  root.traverse((o) => { if (o.isMesh) o.renderOrder = 40; });

  let phi = 0, wSm = null, last = { amp: 0, freq: F0, t: 0, x: 0, roll: 0 };
  const api = {
    group: root, get state() { return last; },
    setVisible(vis) { root.visible = vis; if (!vis) wSm = null; },
    /* pos: ground point (world), forward: unit direction of travel (3-D), scale: world metres per model metre, w: wetness 0-100 or null, dt seconds */
    update({ pos, forward, scale, w, dt }) {
      if (w != null) wSm = wSm == null ? w : wSm + (w - wSm) * (1 - Math.exp(-dt * 2.5)); // calm down jitter between route bins
      const wb = wobbleFor(wSm), k = reduceMotion ? 0.4 : 1;
      phi += Math.PI * 2 * wb.freq * (reduceMotion ? 0.5 : 1) * dt;
      const x = k * wb.amp * (Math.sin(phi) + 0.28 * Math.sin(2.3 * phi + 1.1)) / 1.28;
      const roll = -LEAN * wb.t * Math.cos(phi) * k + 0.04 * Math.sin(phi * 0.5) * wb.t;
      root.position.copy(pos); root.scale.setScalar(scale);
      root.lookAt(pos.x + forward.x, pos.y + forward.y, pos.z + forward.z);
      wob.position.x = x; lean.rotation.z = roll;
      for (const [p, s] of arms) p.rotation.z = s * (0.15 + 0.5 * wb.t * (1 + Math.cos(phi + (s > 0 ? 0 : Math.PI)) * 0.5)); // arms out wider and counter-swinging when it gets slick
      last = { ...wb, x, roll, w: wSm };
    },
  };
  return api;
}
