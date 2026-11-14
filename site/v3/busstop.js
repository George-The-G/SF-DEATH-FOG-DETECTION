/* Bus-stop shelter (low-poly covered bench with a line sign) and its pinned arrivals popup. Beta only, loaded by buses.js.
   - The shelter is a three.js group; an invisible, generous sphere is the click target, and a soft ring pulses until the popup is first opened.
   - The popup is an HTML card anchored to a 3-D point above the roof and re-placed every frame, so it stays attached as the camera moves.
     It only closes with its X. Several can be open at once. */
const CSS = `
.bspop{position:fixed;left:0;top:0;z-index:30;width:min(248px,calc(100vw - 20px));box-sizing:border-box;background:rgba(16,24,34,.96);color:#e8eef4;border:1px solid #2a3745;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.55);padding:10px 12px 9px;font:13px/1.35 Inter,system-ui,sans-serif;will-change:transform;touch-action:manipulation}
.bspop.off{display:none}.bspop.dim{opacity:.55}
.bspop .x{position:absolute;right:2px;top:2px;width:36px;height:36px;border:0;background:transparent;color:#93a3b3;font-size:22px;line-height:1;cursor:pointer;border-radius:8px}
.bspop .x:hover,.bspop .x:focus-visible{color:#fff;background:#2a3745;outline:none}
.bspop .ln{display:flex;align-items:center;gap:8px;padding-right:30px}
.bspop .bd{font:700 13px Inter,system-ui,sans-serif;background:var(--c,#4fd8ee);color:#0b1118;border-radius:5px;padding:1px 7px}
.bspop .dr{font-size:11.5px;color:#93a3b3;margin-top:2px}
.bspop .rw{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;align-items:baseline;margin:8px 0 4px}
.bspop .rw span{color:#93a3b3;font-size:12px}.bspop .rw b{font:600 26px Inter,system-ui,sans-serif;font-variant-numeric:tabular-nums}.bspop .rw .b2{font-size:18px}
.bspop .st{font-size:11px;color:#6fdc9b}.bspop .st.stale{color:#f0b24a}.bspop .st.down{color:#ff8a80}.bspop .st.demo{color:#f0b24a}
.bspop .cr{font-size:10.5px;color:#93a3b3;margin-top:6px;border-top:1px solid #2a3745;padding-top:5px}.bspop .cr a{color:#93a3b3}
.bsleader{position:fixed;left:0;top:0;width:2px;z-index:29;background:#4fd8ee;opacity:.8;transform-origin:top left;pointer-events:none;display:none}
@media (prefers-reduced-motion:reduce){.bspop{will-change:auto}}`;
let cssDone = false;
function addCss() { if (cssDone) return; cssDone = true; const s = document.createElement('style'); s.textContent = CSS; document.head.appendChild(s); }

export function createStop({ THREE, scene, canvas, camera, id, line, name, dirText, color, position, yaw, getScale, demo }) {
  addCss();
  const g = new THREE.Group(); g.position.copy(position); g.rotation.y = yaw; scene.add(g);
  const m = (c, e = .25) => new THREE.MeshLambertMaterial({ color: c, emissive: new THREE.Color(c).multiplyScalar(e) });
  const box = (w, h, d, mat, x, y, z) => { const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); b.position.set(x, y, z); return b; };
  const body = new THREE.Group(); g.add(body);
  const roof = m(0x2b3a4a, .15), post = m(0x8895a3, .2), bench = m(0xc98b3a, .3), sign = m(0x1560c0, .5), pole = m(0x9aa6b2, .2);
  const glass = new THREE.MeshLambertMaterial({ color: 0x9fd8e6, transparent: true, opacity: .35 });
  body.add(box(3.2, .14, 1.7, roof, 0, 2.3, 0));
  for (const [x, z] of [[-1.5, .75], [1.5, .75], [-1.5, -.75], [1.5, -.75]]) body.add(box(.1, 2.3, .1, post, x, 1.15, z));
  body.add(box(3, 1.7, .05, glass, 0, 1.3, -.78)); body.add(box(.05, 1.7, 1.4, glass, -1.5, 1.3, 0));
  body.add(box(2.2, .1, .5, bench, 0, .5, -.45)); body.add(box(2.2, .5, .08, bench, 0, .8, -.7));
  body.add(box(.1, 3.2, .1, pole, 2.3, 1.6, .7)); body.add(box(.6, .6, .08, sign, 2.3, 3, .7));
  const ring = new THREE.Mesh(new THREE.RingGeometry(3.2, 3.7, 48), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .8, side: THREE.DoubleSide, depthWrite: false }));
  ring.rotation.x = -Math.PI / 2; ring.position.y = .2; body.add(ring);
  const hit = new THREE.Mesh(new THREE.SphereGeometry(6, 12, 8), new THREE.MeshBasicMaterial({ visible: false })); hit.position.y = 2; body.add(hit);

  const pop = document.createElement('div'); pop.className = 'bspop off'; pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', 'Next buses at ' + name);
  pop.style.setProperty('--c', '#' + new THREE.Color(color).getHexString());
  pop.innerHTML = `<button class="x" aria-label="Close bus times">&times;</button><div class="ln"><span class="bd"></span><b class="nm"></b></div><div class="dr"></div>
    <div class="rw"><span>Next bus</span><b class="n1">--</b><span>Then</span><b class="n2 b2">--</b></div><div class="st"></div>
    <div class="cr">Data provided by <a href="https://511.org" target="_blank" rel="noopener">511.org</a></div>`;
  const q = (s) => pop.querySelector(s);
  q('.bd').textContent = line; q('.nm').textContent = name; q('.dr').textContent = dirText;
  document.body.appendChild(pop);
  const leader = document.createElement('div'); leader.className = 'bsleader'; document.body.appendChild(leader);
  let open = false, pulsed = false, arr = null, t0 = performance.now();
  const setOpen = (v) => { open = v; pop.classList.toggle('off', !v); if (!v) leader.style.display = 'none'; if (v) pulsed = true; ring.visible = !pulsed; };
  q('.x').addEventListener('click', (e) => { e.stopPropagation(); setOpen(false); });
  pop.addEventListener('pointerdown', (e) => e.stopPropagation());

  const ray = new THREE.Raycaster(), mv = new THREE.Vector2(); let dn = null;
  canvas.addEventListener('pointerdown', (e) => { dn = { x: e.clientX, y: e.clientY, t: performance.now() }; }, true);
  canvas.addEventListener('pointerup', (e) => {
    if (!dn) return; const moved = Math.hypot(e.clientX - dn.x, e.clientY - dn.y), dt = performance.now() - dn.t; dn = null;
    if (moved > 8 || dt > 600) return;
    const r = canvas.getBoundingClientRect(); mv.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(mv, camera); if (ray.intersectObject(hit, false).length) setOpen(true);
  }, true);

  const fmt = (s) => (s < 60 ? 'due' : Math.round(s / 60) + ' min');
  const v = new THREE.Vector3();
  return {
    id, group: g, isOpen: () => open, open: () => setOpen(true), close: () => setOpen(false),
    /* a = { next: [epoch s...], t: when the feed produced it (epoch s) } or null; status: 'live' | 'down' */
    setArrivals(a, status) { arr = a; this.status = status; },
    update(now) { // now: epoch seconds
      const sc = getScale(); body.scale.setScalar(1.5 * sc);
      const tt = (performance.now() - t0) / 1000; ring.scale.setScalar(1 + .08 * Math.sin(tt * 3)); ring.material.opacity = .45 + .35 * Math.sin(tt * 3);
      if (!open) return;
      const st = q('.st'), n1 = q('.n1'), n2 = q('.n2');
      const list = arr ? arr.next.filter((t) => t > now - 20).slice(0, 2) : [];
      if (this.status === 'down' || !arr) { n1.textContent = '--'; n2.textContent = '--'; st.textContent = 'Live times unavailable'; st.className = 'st down'; }
      else {
        n1.textContent = list[0] != null ? fmt(list[0] - now) : '--'; n2.textContent = list[1] != null ? fmt(list[1] - now) : '--';
        const age = now - arr.t;
        if (demo) { st.textContent = 'Demo times, not live'; st.className = 'st demo'; }
        else { st.textContent = 'Live, updated ' + Math.round(Math.max(0, age)) + ' s ago'; st.className = 'st' + (age > 300 ? ' stale' : ''); }
        if (!list.length) { st.textContent = 'No upcoming buses listed'; st.className = 'st stale'; }
      }
      v.set(0, 5.4 * 1.5 * sc, 0); body.localToWorld(v); const wp = v.clone(); v.project(camera);
      const W = innerWidth, H = innerHeight, pw = pop.offsetWidth, ph = pop.offsetHeight, x = (v.x * .5 + .5) * W, y = (1 - (v.y * .5 + .5)) * H;
      const behind = v.z > 1; pop.classList.toggle('dim', behind);
      const px = Math.min(W - pw - 6, Math.max(6, x - pw / 2)), py = Math.min(H - ph - 6, Math.max(6, y - ph - 14));
      pop.style.transform = `translate(${px}px,${py}px)`;
      const x0 = Math.min(px + pw - 10, Math.max(px + 10, x)), y0 = py + ph, len = Math.hypot(x - x0, y - y0);
      if (behind || len < 6) leader.style.display = 'none';
      else { leader.style.display = 'block'; leader.style.left = x0 + 'px'; leader.style.top = y0 + 'px'; leader.style.height = len + 'px'; leader.style.transform = `rotate(${Math.atan2(-(x - x0), y - y0)}rad)`; }
    },
    dispose() { scene.remove(g); pop.remove(); leader.remove(); },
  };
}
