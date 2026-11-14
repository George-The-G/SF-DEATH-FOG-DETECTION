/* Fog lab (?beta=1&lab=1): a panel for comparing fog techniques and settings live. Loaded only on that URL.
   Settings are saved in this browser (localStorage 'foglab.v1') so a reload keeps them; "Copy settings" puts them on the clipboard as JSON
   so a chosen look can be made the beta default in fog2.js DEFAULTS. */
import { DEFAULTS } from './fog2.js';

const KEY = 'foglab.v1';
const PRESETS = {
  'Beta today': { ...DEFAULTS, auto: false },
  'Voxel, flat top': { ...DEFAULTS, mode: 'voxel', follow: 0.15, smooth: 3, topMin: 120, topMax: 320, soft: 0.45, bump: 0.35, detail: 0.6, warp: 1, advect: 0.04, auto: false },
  'Voxel, follows hills': { ...DEFAULTS, mode: 'voxel', follow: 0.85, smooth: 3, topMin: 90, topMax: 260, soft: 0.45, bump: 0.3, detail: 0.6, warp: 1, advect: 0.04, auto: false },
  'March, smoothed': { ...DEFAULTS, smooth: 3, follow: 0.5, auto: false },
};

/* [key, label, kind, min, max, step, hint] */
const CONTROLS = [
  ['Technique'],
  ['mode', 'Technique', 'select', [['march', 'Ray march (beta today)'], ['voxel', 'Voxel volume'], ['sheets', 'Sheets (old)']], null, null, 'How the fog is drawn'],
  ['Fog top'],
  ['follow', 'Top shape', 'range', 0, 1, 0.05, '0 = flat inversion (hills poke out), 1 = same depth over hills'],
  ['topSrc', 'Top from', 'select', [['data', 'Hourly data'], ['fixed', 'Fixed height']]],
  ['topFixed', 'Fixed top (m)', 'range', 40, 600, 10],
  ['smooth', 'Time smoothing (h)', 'range', 0, 6, 0.5, '0 = old behaviour: the top jumps at each half hour'],
  ['topMin', 'Top min (m)', 'range', 40, 300, 10],
  ['topMax', 'Top max (m)', 'range', 100, 900, 10],
  ['Shape'],
  ['soft', 'Edge softness', 'range', 0.05, 1, 0.05],
  ['bump', 'Lumpy top (voxel)', 'range', 0, 1, 0.05],
  ['detail', 'Fine detail', 'range', 0, 1, 0.05],
  ['warp', 'Billow warp', 'range', 0, 1.5, 0.05],
  ['advect', 'Wind drift (voxel)', 'range', 0, 0.2, 0.01, 'Noise drifts with the mean wind as the time moves; never while paused'],
  ['Light'],
  ['sun', 'Sunlight', 'range', 0, 2, 0.05],
  ['night', 'Night brightness', 'range', 0.3, 2.5, 0.05],
  ['shadow', 'Self-shadow (voxel)', 'range', 0, 1, 0.05],
  ['Quality'],
  ['steps', 'Steps (0 = auto)', 'range', 0, 48, 1],
  ['res', 'Resolution', 'select', [[0, 'Auto'], [0.5, '50%'], [0.75, '75%'], [1, '100%']]],
  ['vres', 'Voxel size', 'select', [[1, '80 m'], [2, '40 m']]],
  ['vlayers', 'Voxel layers', 'select', [[16, '16'], [24, '24'], [32, '32']]],
  ['dither', 'Dither', 'range', 0, 1, 0.05],
  ['blur', 'Blur', 'check'],
  ['auto', 'Auto quality drop', 'check', null, null, null, 'Off in the lab so the look does not change while you compare'],
];

export function mountFogLab({ fog, getSky, densEl }) {
  if (!fog || !fog.setParams) return;
  let stored = null; try { stored = localStorage.getItem(KEY); } catch (e) { /* storage blocked */ }
  if (!stored) fog.setParams({ auto: false }); // the lab starts with the beta look but without the automatic quality drop
  const css = document.createElement('style');
  css.textContent = `
  #foglab{position:fixed;top:8px;right:8px;z-index:60;width:286px;max-width:calc(100vw - 16px);max-height:calc(100vh - 16px);overflow:auto;
    background:rgba(14,18,26,.92);color:#e6ebf2;font:12px/1.35 system-ui,-apple-system,sans-serif;border:1px solid rgba(255,255,255,.14);border-radius:10px;
    box-shadow:0 6px 24px rgba(0,0,0,.4);-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)}
  #foglab.min .fl-body{display:none}
  #foglab header{display:flex;align-items:center;gap:6px;padding:8px 10px;position:sticky;top:0;background:rgba(14,18,26,.97);border-bottom:1px solid rgba(255,255,255,.08)}
  #foglab header b{flex:1;font-size:13px}
  #foglab button{font:inherit;color:inherit;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.16);border-radius:6px;padding:4px 8px;cursor:pointer}
  #foglab button:hover{background:rgba(255,255,255,.16)}
  #foglab .fl-body{padding:4px 10px 10px}
  #foglab h4{margin:10px 0 4px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#8fa3bf}
  #foglab label{display:grid;grid-template-columns:1fr 92px 34px;align-items:center;gap:6px;margin:3px 0}
  #foglab label.sel{grid-template-columns:1fr 126px}
  #foglab input[type=range]{width:92px;margin:0}
  #foglab select{font:inherit;background:#1d2533;color:inherit;border:1px solid rgba(255,255,255,.18);border-radius:5px;padding:2px 4px;width:126px}
  #foglab .v{text-align:right;font-variant-numeric:tabular-nums;color:#b9c6d8}
  #foglab .presets{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0}
  #foglab .read{font:11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;color:#b9c6d8;background:rgba(255,255,255,.05);border-radius:6px;padding:6px 8px;margin-top:6px;white-space:pre-wrap}
  #foglab .row{display:flex;gap:6px;margin-top:8px}
  @media (max-width:600px){#foglab{top:auto;bottom:8px;max-height:46vh}}`;
  document.head.appendChild(css);

  const el = document.createElement('div'); el.id = 'foglab';
  el.innerHTML = `<header><b>Fog lab</b><button data-a="min" title="Collapse">–</button></header><div class="fl-body">
    <div class="presets"></div><div class="ctl"></div>
    <div class="read" aria-live="off"></div>
    <div class="row"><button data-a="copy">Copy settings</button><button data-a="reset">Reset</button></div></div>`;
  document.body.appendChild(el);
  const pr = el.querySelector('.presets'), ctl = el.querySelector('.ctl'), read = el.querySelector('.read');
  const inputs = {};

  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(fog.params)); } catch (e) { /* storage blocked: settings last until reload */ } };
  const fmt = (v, step) => (step >= 1 ? String(Math.round(v)) : (+v).toFixed(step < 0.1 ? 2 : 1));
  function set(patch) { fog.setParams(patch); save(); sync(); }

  Object.keys(PRESETS).forEach((name) => {
    const b = document.createElement('button'); b.textContent = name; b.onclick = () => set(PRESETS[name]); pr.appendChild(b);
  });
  for (const c of CONTROLS) {
    const [k, label, kind, a, b, step, hint] = c;
    if (c.length === 1) { const h = document.createElement('h4'); h.textContent = k; ctl.appendChild(h); continue; }
    const row = document.createElement('label'); if (hint) row.title = hint;
    const name = document.createElement('span'); name.textContent = label; row.appendChild(name);
    let inp;
    if (kind === 'select') {
      row.className = 'sel'; inp = document.createElement('select');
      a.forEach(([v, t]) => { const o = document.createElement('option'); o.value = String(v); o.textContent = t; inp.appendChild(o); });
      inp.onchange = () => { const v = inp.value, num = typeof a[0][0] === 'number'; set({ [k]: num ? +v : v }); };
      row.appendChild(inp);
    } else if (kind === 'check') {
      row.className = 'sel'; inp = document.createElement('input'); inp.type = 'checkbox';
      inp.onchange = () => set({ [k]: k === 'blur' ? (inp.checked ? 1 : 0) : inp.checked });
      row.appendChild(inp);
    } else {
      inp = document.createElement('input'); inp.type = 'range'; inp.min = a; inp.max = b; inp.step = step;
      const out = document.createElement('span'); out.className = 'v';
      inp.oninput = () => { out.textContent = fmt(inp.value, step); fog.setParams({ [k]: +inp.value }); save(); };
      row.appendChild(inp); row.appendChild(out); inp._out = out; inp._step = step;
    }
    inputs[k] = inp; ctl.appendChild(row);
  }
  function sync() {
    const p = fog.params;
    for (const k in inputs) {
      const i = inputs[k];
      if (i.type === 'checkbox') i.checked = !!p[k];
      else if (i.tagName === 'SELECT') i.value = String(p[k]);
      else { i.value = p[k]; i._out.textContent = fmt(p[k], i._step); }
    }
  }
  sync();

  el.addEventListener('click', (e) => {
    const a = e.target && e.target.dataset ? e.target.dataset.a : null; if (!a) return;
    if (a === 'min') { el.classList.toggle('min'); e.target.textContent = el.classList.contains('min') ? '+' : '–'; }
    if (a === 'reset') set({ ...DEFAULTS, auto: false });
    if (a === 'copy') {
      const txt = JSON.stringify({ ...fog.params, density: densEl ? +densEl.value : null });
      const done = () => { e.target.textContent = 'Copied'; setTimeout(() => { e.target.textContent = 'Copy settings'; }, 1500); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(done, () => prompt('Settings', txt));
      else prompt('Settings', txt);
    }
  });

  // readout: frame time over the last 60 frames, technique, fog top now
  setInterval(() => {
    const s = fog.stats ? fog.stats() : { med: 0, p95: 0 };
    read.textContent = `frame ${s.med.toFixed(1)} ms median, ${s.p95.toFixed(1)} ms p95 (~${s.med ? Math.round(1000 / s.med) : '--'} fps)\n${fog.info()}`;
  }, 500);
}
