/* Fog menu (beta): a small cloud button just above the bottom card opens a panel to play with the fog look live.
   Settings are saved in this browser only (localStorage 'foglab.v2'); Reset returns to the defaults in fog2.js DEFAULTS.
   The Advanced section holds technique, quality and speed-up switches, a cost measurement and "Copy settings" (JSON). */
import { DEFAULTS } from './fog2.js';

export const KEY = 'foglab.v2';
const PRESETS = {
  'Default': { ...DEFAULTS },
  'Flat marine layer': { ...DEFAULTS, follow: 0, bump: 0.35, soft: 0.6 },
  'Hugs the hills': { ...DEFAULTS, follow: 0.85, topMax: 300 },
  'Classic': { ...DEFAULTS, mode: 'march', follow: 1, smooth: 3, topMin: 80, topMax: 300, soft: 0.35, detail: 1, warp: 1, sun: 1, shadow: 1 },
};

/* [key, label, kind, min, max, step, hint]; a one-element row is a heading */
const LOOK = [
  ['Fog top'],
  ['follow', 'Shape', 'range', 0, 1, 0.05, '0 = flat layer at one altitude (hills poke out), 1 = same depth over hills'],
  ['smooth', 'Time smoothing (h)', 'range', 0, 6, 0.5, 'Blends the hourly fog top so it changes gradually'],
  ['topSrc', 'Height from', 'select', [['data', 'Forecast'], ['fixed', 'Fixed']]],
  ['topFixed', 'Fixed height (m)', 'range', 40, 600, 10],
  ['topMin', 'Lowest top (m)', 'range', 40, 300, 10],
  ['topMax', 'Highest top (m)', 'range', 100, 900, 10],
  ['Shape'],
  ['soft', 'Softness', 'range', 0.05, 1, 0.05],
  ['bump', 'Lumpy top', 'range', 0, 1, 0.05],
  ['detail', 'Fine detail', 'range', 0, 1, 0.05],
  ['warp', 'Billows', 'range', 0, 1.5, 0.05],
  ['Wind'],
  ['flow', 'Detail follows wind', 'check', null, null, null, 'Fine billows drift along a wind field that bends around hills'],
  ['flowSpeed', 'Detail drift speed', 'range', 0, 0.05, 0.002, 'Fraction of the real wind speed, in simulation time'],
  ['advect', 'Shape drift', 'range', 0, 0.2, 0.01, 'Larger shapes drift with the mean wind as the time moves; never while paused'],
  ['eddy', 'Eddies', 'range', 0, 1, 0.05, 'Adds swirls to the wind field behind hills'],
  ['Light'],
  ['sun', 'Sunlight', 'range', 0, 2, 0.05],
  ['night', 'Night brightness', 'range', 0.3, 2.5, 0.05],
  ['shadow', 'Self-shadow', 'range', 0, 1, 0.05],
];
const ADVANCED = [
  ['mode', 'Technique', 'select', [['voxel', 'Voxel volume'], ['march', 'Ray march'], ['sheets', 'Sheets (old)']]],
  ['steps', 'Steps (0 = auto)', 'range', 0, 48, 1],
  ['res', 'Resolution', 'select', [[0, 'Auto'], [0.5, '50%'], [0.75, '75%'], [1, '100%']]],
  ['vres', 'Voxel size', 'select', [[0, 'Auto'], [1, '80 m'], [2, '40 m']], null, null, 'Auto = 80 m on phones, 40 m on computers'],
  ['vlayers', 'Voxel layers', 'select', [[0, 'Auto'], [16, '16'], [24, '24'], [32, '32']], null, null, 'Auto = 16 on phones, 24 on computers'],
  ['skip', 'Skip empty space', 'check', null, null, null, 'Rays jump over clear air'],
  ['accum', 'Still refinement', 'select', [[0, 'Off'], [4, '4 passes'], [6, '6 passes'], [8, '8 passes']], null, null, 'When nothing moves, average a few passes, then reuse the result'],
  ['motion', 'Cheaper while moving', 'check', null, null, null, 'Lower resolution and fewer steps while the camera moves'],
  ['motionSteps', 'Steps while moving', 'range', 0.3, 1, 0.05],
  ['taa', 'Trace 1 in 16 while moving', 'check', null, null, null, 'While moving, trace one pixel in 16 each frame and reuse the rest from earlier frames'],
  ['dither', 'Dither', 'range', 0, 1, 0.05],
  ['blur', 'Blur', 'check'],
  ['auto', 'Auto quality drop', 'check', null, null, null, 'Lowers quality by itself when the frame rate falls'],
];
const NUMERIC_CHECK = new Set(['blur', 'skip', 'motion', 'flow', 'taa']);

const CLOUD = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="M7.5 19h10a4.5 4.5 0 0 0 .6-8.96A6 6 0 0 0 6.6 8.1 5.5 5.5 0 0 0 7.5 19Z"/></svg>';

export function mountFogLab({ fog, densEl, extraControls }) {
  if (!fog || !fog.setParams) return;
  const css = document.createElement('style');
  css.textContent = `
  #fogbtn{position:fixed;right:12px;z-index:61;width:36px;height:36px;border-radius:50%;display:grid;place-items:center;cursor:pointer;
    color:#e6ebf2;background:rgba(14,18,26,.82);border:1px solid rgba(255,255,255,.16);box-shadow:0 2px 10px rgba(0,0,0,.35);-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)}
  #fogbtn[aria-expanded=true]{background:#2b6cb0;border-color:#5a9be0}
  #foglab{position:fixed;right:12px;z-index:62;width:280px;max-width:calc(100vw - 24px);overflow:auto;display:none;
    background:rgba(14,18,26,.94);color:#e6ebf2;font:12px/1.35 system-ui,-apple-system,sans-serif;border:1px solid rgba(255,255,255,.14);border-radius:12px;
    box-shadow:0 6px 24px rgba(0,0,0,.45);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);padding:4px 12px 12px}
  #foglab.open{display:block}
  #foglab .hd{display:flex;align-items:center;padding:6px 0 2px}#foglab .hd b{flex:1;font-size:13px}
  #foglab button{font:inherit;color:inherit;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.16);border-radius:6px;padding:4px 8px;cursor:pointer}
  #foglab button:hover{background:rgba(255,255,255,.16)}
  #foglab .x{border:0;background:none;font-size:16px;padding:2px 6px}
  #foglab h4{margin:10px 0 3px;font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:#8fa3bf;font-weight:600}
  #foglab label{display:grid;grid-template-columns:1fr 96px 34px;align-items:center;gap:6px;margin:3px 0}
  #foglab label.sel{grid-template-columns:1fr 130px}
  #foglab input[type=range]{width:96px;margin:0}
  #foglab select{font:inherit;background:#1d2533;color:inherit;border:1px solid rgba(255,255,255,.18);border-radius:5px;padding:2px 4px;width:130px}
  #foglab .v{text-align:right;font-variant-numeric:tabular-nums;color:#b9c6d8}
  #foglab .dens{display:grid;grid-template-columns:1fr 96px 34px;align-items:center;gap:6px;margin:6px 0 2px;font-size:inherit;color:inherit;min-width:0}
  #foglab .dens label{display:block;margin:0}
  #foglab .dens input{width:96px;min-width:0;height:auto;margin:0;flex:none}
  #foglab .presets{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0 2px}
  #foglab .presets button.on{background:#2b6cb0;border-color:#5a9be0}
  #foglab details{margin-top:10px;border-top:1px solid rgba(255,255,255,.08);padding-top:6px}
  #foglab summary{cursor:pointer;color:#8fa3bf;font-size:11px;text-transform:uppercase;letter-spacing:.06em;font-weight:600}
  #foglab .read{font:11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;color:#b9c6d8;background:rgba(255,255,255,.05);border-radius:6px;padding:6px 8px;margin-top:6px;white-space:pre-wrap}
  #foglab .row{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}`;
  document.head.appendChild(css);

  const btn = document.createElement('button'); btn.id = 'fogbtn'; btn.type = 'button'; btn.innerHTML = CLOUD;
  btn.setAttribute('aria-label', 'Fog settings'); btn.title = 'Fog settings'; btn.setAttribute('aria-expanded', 'false'); btn.setAttribute('aria-controls', 'foglab');
  const el = document.createElement('div'); el.id = 'foglab'; el.setAttribute('role', 'dialog'); el.setAttribute('aria-label', 'Fog settings');
  el.innerHTML = `<div class="hd"><b>Fog</b><button class="x" data-a="close" aria-label="Close">×</button></div>
    <div class="presets"></div><div class="look"></div>
    <details><summary>Advanced</summary><div class="adv"></div>
      <div class="read" aria-live="off"></div>
      <div class="row"><button data-a="bench" title="Times the fog drawing alone (a few seconds)">Measure cost</button><button data-a="copy">Copy settings</button></div>
    </details>
    <div class="row"><button data-a="reset">Reset to default</button></div>`;
  document.body.appendChild(btn); document.body.appendChild(el);
  if (densEl && densEl.parentElement) { // the density slider lives at the top of this menu now (same element, same listener)
    const row = densEl.parentElement; row.querySelector('label').textContent = 'Fog density'; el.querySelector('.hd').after(row);
    const dv = document.createElement('span'); dv.className = 'v'; row.appendChild(dv);
    const showD = () => { dv.textContent = densEl.value + '%'; }; densEl.addEventListener('input', showD); showD(); setInterval(showD, 1000);
  }
  const pr = el.querySelector('.presets'), read = el.querySelector('.read'), inputs = {};
  let benchTxt = '';

  // keep the button just above the bottom card (its height changes with layout and phone size)
  const foot = document.querySelector('footer'), dock = document.getElementById('dock');
  function place() { // beta playback UI (body.bx-sp): follow the whole dock, so rows added above the bar (stations, cloud match) push the buttons up
    const anchor = document.body.classList.contains('bx-sp') && dock ? dock : foot;
    const top = anchor ? anchor.getBoundingClientRect().top : innerHeight - 12;
    const bottom = Math.max(12, innerHeight - top + 10);
    btn.style.bottom = bottom + 'px'; el.style.bottom = (bottom + 44) + 'px'; el.style.maxHeight = Math.max(160, innerHeight - bottom - 64) + 'px';
  }
  btn.style.visibility = fog.ready ? '' : 'hidden';
  const waitReady = setInterval(() => { if (fog.ready) { btn.style.visibility = ''; clearInterval(waitReady); } }, 500);
  place(); addEventListener('resize', place); if (window.ResizeObserver) { const ro = new ResizeObserver(place); if (foot) ro.observe(foot); if (dock) ro.observe(dock); } setInterval(place, 1500);

  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(fog.params)); } catch (e) { /* storage blocked: settings last until reload */ } };
  const fmt = (v, step) => (step >= 1 ? String(Math.round(v)) : (+v).toFixed(step < 0.01 ? 3 : step < 0.1 ? 2 : 1));
  function set(patch) { fog.setParams(patch); save(); sync(); }

  Object.keys(PRESETS).forEach((name) => {
    const b = document.createElement('button'); b.textContent = name; b.dataset.p = name; b.onclick = () => set(PRESETS[name]); pr.appendChild(b);
  });
  function addControls(list, host) {
    for (const c of list) {
      const [k, label, kind, a, b, step, hint] = c;
      if (c.length === 1) { const h = document.createElement('h4'); h.textContent = k; host.appendChild(h); continue; }
      const row = document.createElement('label'); if (hint) row.title = hint;
      const name = document.createElement('span'); name.textContent = label; row.appendChild(name);
      let inp;
      if (kind === 'select') {
        row.className = 'sel'; inp = document.createElement('select');
        a.forEach(([v, t]) => { const o = document.createElement('option'); o.value = String(v); o.textContent = t; inp.appendChild(o); });
        inp.onchange = () => set({ [k]: typeof a[0][0] === 'number' ? +inp.value : inp.value });
        row.appendChild(inp);
      } else if (kind === 'check') {
        row.className = 'sel'; inp = document.createElement('input'); inp.type = 'checkbox';
        inp.onchange = () => set({ [k]: NUMERIC_CHECK.has(k) ? (inp.checked ? 1 : 0) : inp.checked });
        row.appendChild(inp);
      } else {
        inp = document.createElement('input'); inp.type = 'range'; inp.min = a; inp.max = b; inp.step = step;
        const out = document.createElement('span'); out.className = 'v';
        inp.oninput = () => { out.textContent = fmt(inp.value, step); fog.setParams({ [k]: +inp.value }); save(); markPreset(); };
        row.appendChild(inp); row.appendChild(out); inp._out = out; inp._step = step;
      }
      inputs[k] = inp; host.appendChild(row);
    }
  }
  addControls(LOOK, el.querySelector('.look'));
  addControls(ADVANCED.concat(extraControls || []), el.querySelector('.adv'));

  function markPreset() {
    const p = fog.params;
    pr.querySelectorAll('button').forEach((b) => { const q = PRESETS[b.dataset.p]; b.classList.toggle('on', Object.keys(q).every((k) => q[k] === p[k])); });
  }
  function sync() {
    const p = fog.params;
    for (const k in inputs) {
      const i = inputs[k]; if (!(k in p)) continue;
      if (i.type === 'checkbox') i.checked = !!p[k];
      else if (i.tagName === 'SELECT') i.value = String(p[k]);
      else { i.value = p[k]; i._out.textContent = fmt(p[k], i._step); }
    }
    markPreset();
  }
  sync();

  function toggle(open) {
    el.classList.toggle('open', open); btn.setAttribute('aria-expanded', String(open)); if (open) { place(); sync(); }
  }
  btn.addEventListener('click', () => toggle(!el.classList.contains('open')));
  addEventListener('keydown', (e) => { if (e.key === 'Escape' && el.classList.contains('open')) { toggle(false); btn.focus(); } });

  el.addEventListener('click', (e) => {
    const t = e.target.closest('[data-a]'); if (!t) return; const a = t.dataset.a;
    if (a === 'close') toggle(false);
    if (a === 'reset') { set({ ...DEFAULTS }); if (densEl) { densEl.value = 33; densEl.dispatchEvent(new Event('input', { bubbles: true })); } }
    if (a === 'bench' && fog.bench) { // fog drawing alone, GPU-synchronised: full quality, then the moving-camera version
      t.disabled = true; benchTxt = '\nmeasuring...';
      setTimeout(() => {
        const b = fog.bench(8), m = fog.params.mode === 'voxel' ? fog.bench(8, { motion: true, bake: false }) : null;
        benchTxt = b ? `\nfog ${b.ms.toFixed(1)} ms (${b.size[0]}x${b.size[1]}, ${b.steps} steps)` + (m ? `, moving ${m.ms.toFixed(1)} ms` : '') + (b.bakeMs != null ? `, hour bake ${b.bakeMs.toFixed(1)} ms` : '') : '\nnot available';
        t.disabled = false;
      }, 50);
    }
    if (a === 'copy') {
      const txt = JSON.stringify({ ...fog.params, density: densEl ? +densEl.value : null });
      const done = () => { t.textContent = 'Copied'; setTimeout(() => { t.textContent = 'Copy settings'; }, 1500); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(done, () => prompt('Settings', txt));
      else prompt('Settings', txt);
    }
  });

  // readout (Advanced): frame time over the last 60 frames, technique and state, last measurement
  setInterval(() => {
    if (!el.classList.contains('open')) return;
    const s = fog.stats ? fog.stats() : { med: 0, p95: 0 };
    read.textContent = `frame ${s.med.toFixed(1)} ms median, ${s.p95.toFixed(1)} ms p95 (~${s.med ? Math.round(1000 / s.med) : '--'} fps)\n${fog.info()}${benchTxt}`;
  }, 500);
}
