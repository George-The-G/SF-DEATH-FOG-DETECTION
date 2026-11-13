/* Data layers (promoted to the public page; also the beta preview channel): weather stations, GOES satellite low cloud, sunshine, water depth and the regrouped settings.
   Loaded by app.js with import() only when the public live output carries the promoted settings or the page is opened with ?beta=1; otherwise nobody downloads it. X.beta tells the two apart (Beta tag and wording only on ?beta=1).
   Every data file is fetched on demand (when its layer is switched on or a street / route needs it); the first paint does not grow.
   Missing data never breaks anything: a layer whose files are absent is shown greyed in settings with a one-line reason.
   Design: the approved "Viewer data layers" mockup. Context (scene, camera, current night, helpers) comes from app.js through init(ctx). */

const $ = (id) => document.getElementById(id);
let X = null;                                   // app context (see betaCtx() in app.js)
const S = { color: 'slick', light: true, stations: false, sat: false };   // default is the wet/slick colouring, 'sun' only when chosen
const A = {};                                   // availability of the layers for this run
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const h = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
const F2 = (c) => c * 9 / 5 + 32;
const MPH = 2.23694;
const X_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

export const slick = () => !!A.film;            // labels say "slick" (film model) instead of "wet" (index)

/* ------------------------------------------------------------------ init */
export async function init(ctx) {
  X = ctx;
  await new Promise((res) => {
    if ($('bx-css')) return res();
    const l = document.createElement('link'); l.id = 'bx-css'; l.rel = 'stylesheet'; l.href = new URL('beta_ui.css', import.meta.url).href;
    l.onload = l.onerror = () => res(); document.head.appendChild(l);
  });
  document.body.classList.add('beta');
  const idx = X.idx, st = X.status;
  A.live = !!X.LV;
  A.film = !!(idx && idx.film && idx.film.files && idx.film.files.depth && idx.film.files.dry && idx.film.files.cause);
  A.tsurf = !!(idx && idx.tsurf && idx.tsurf.files);
  A.sun = !!(idx && idx.sun && idx.sun.files && idx.sun.sun_el && idx.sun.sun_az);
  A.sat = !!(idx && idx.sat_score && idx.sat_score.satellite && idx.sat_score.satellite.files && idx.fog_grid);
  A.satHours = A.sat ? (idx.sat_score.satellite.hours_with_data | 0) : 0;
  A.stFile = (st && st.stations && st.stations.file) || null;
  A.stNow = A.stFile && st.stations.newest ? Object.keys(st.stations.newest).length : 0;
  // default colouring is wet / slick (the original dry-wet legend); Sunshine is chosen in the Slippery | Sunshine control (night and failed loads fall back to slick, see colorMode / loadSun)
  A.any = A.film || A.sun || A.sat || !!A.stFile;
  window.__betaOn = true; window.__bxPromo = !X.beta; window.__betaLabel = (X.LV && X.LV.betaLabel) || '';
  buildSettings();
  buildTag();
  if (A.any) { buildReadout(); buildChipLine(); lightInit(); X.refresh(); }
  if (A.film || A.sun) { const lg = $('legend'); lg.onclick = () => { if ($('sheet').hidden) openSettings('bx-g-color'); else X.setSheet(false); }; }
  new ResizeObserver(() => { const d = $('dock'); if (d) document.documentElement.style.setProperty('--bx-dock-h', Math.round(d.getBoundingClientRect().height) + 'px'); measure(); }).observe($('dock'));
  new ResizeObserver(measure).observe($('top'));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCard(); });
  syncColorUi();
  if (S.color === 'sun') loadSun().then(() => { colorMode(X.curF, true); });
}

export function openSettings(id) {
  X.setSheet(true);
  requestAnimationFrame(() => {
    const g = $(id), sh = $('sheet'); if (!g || !sh) return;
    sh.scrollTop = Math.max(0, g.getBoundingClientRect().top - sh.getBoundingClientRect().top + sh.scrollTop - 6);
  });
}

/* ------------------------------------------------------------------ settings: Playback, Colour and light, Map, Observations, Data on this night */
function buildSettings() {
  const sheet = $('sheet'), idx = X.idx;
  const two = sheet.querySelector(':scope > .two.sec'), rng = $('rngrow');
  const layers = [...sheet.querySelectorAll(':scope > .sec')].find((e) => e.querySelector('.tgs'));
  const play = h('<section class="bx-grp" id="bx-g-play"><div class="bx-gh">Playback</div></section>');
  if (two) play.appendChild(two);
  if (rng) play.appendChild(rng);
  const map = h('<section class="bx-grp" id="bx-g-map"><div class="bx-gh">Map</div></section>');
  if (layers) { const t = layers.querySelector('h4'); if (t) t.remove(); map.appendChild(layers); }
  const parts = [];
  const label = (X.LV && X.LV.betaLabel) || '';
  if (X.beta) parts.push(h(`<div class="bx-note"><span class="bx-tag-s">Beta</span><span>Beta preview: stations, satellite cloud, sunshine and water depth are on test and may change.${label ? ' Settings: ' + esc(label) + '.' : ''} Not the public forecast.</span></div>`));
  else parts.push(h(`<div class="bx-note"><span>Stations, satellite cloud and sunshine are shown beside the model. Sunshine and water are modelled, not measured, and not a forecast guarantee.${label ? ' Model: ' + esc(label) + '.' : ''}</span></div>`));
  parts.push(play);
  { // always shown on the beta channel: layers with no data in this run are greyed with the reason
    const g = h(`<section class="bx-grp" id="bx-g-color"><div class="bx-gh">Colour and light<em id="bx-clnote"></em></div>
      <div class="bx-crow"><div class="seg bx-seg" id="bx-color" role="group" aria-label="Street colour">
        <button type="button" data-c="slick">${A.film ? 'Slippery' : 'Wet'}</button>
        <button type="button" data-c="sun"${A.sun ? '' : ' disabled'}>${A.sun ? 'Sunshine' : 'Sunshine · not in this run'}</button></div>
      </div>
        <p class="bx-hint">On a touch screen, press and hold a street for its details. A short tap does nothing.</p>
      <div class="tgs bx-one"><label class="${A.sun ? '' : 'na'}"><input type="checkbox" id="bx-light"${A.sun ? ' checked' : ' disabled'}><span><i class="bx-tt"><b>Light the scene by the sun</b><small>${A.sun ? 'Day and night follow the selected hour' : 'No sun data in this run'}</small></i></span></label></div></section>`);
    parts.push(g);
  }
  parts.push(map);
  {
    const stSub = A.stFile ? `${A.stNow || ''} ${A.stNow ? 'reporting' : 'at real spots'}`.trim() : 'No station file in this run';
    const satSub = !A.sat ? 'Not in this run' : A.satHours > 0 ? `GOES-18 · ${A.satHours} h of scans` : 'No satellite scans in this window';
    const satOk = A.sat && A.satHours > 0;
    parts.push(h(`<section class="bx-grp" id="bx-g-obs"><div class="bx-gh">Observations</div><div class="tgs">
      <label class="bx-tall${A.stFile ? '' : ' na'}"><input type="checkbox" id="bx-stn"${A.stFile ? '' : ' disabled'}><span><i class="bx-tt"><b>Stations</b><small>${esc(stSub)}</small></i></span></label>
      <label class="bx-tall${satOk ? '' : ' na'}"><input type="checkbox" id="bx-sat"${satOk ? '' : ' disabled'}><span><i class="bx-tt"><b>Satellite low cloud</b><small>${esc(satSub)}</small></i></span></label></div></section>`));
    const ob = idx && idx.observed_until_frame != null ? X.ptFmt(idx.observed_until_iso, { weekday: 'short', hour: '2-digit', minute: '2-digit' }).replace(',', '') : null;
    const row = (cls, t, s) => `<div class="bx-av"><i class="${cls}"></i><span class="t">${t}</span><small>${esc(s)}</small></div>`;
    parts.push(h(`<section class="bx-grp" id="bx-g-data"><div class="bx-gh">Data on this night</div><div class="bx-avail">
      ${row(A.film ? 'y' : 'n', 'Slippery and water depth', A.film ? 'model' : 'wetness index only')}
      ${row(A.stFile ? 'y' : 'n', 'Weather stations', A.stFile ? (A.stNow ? A.stNow + ' reporting' : 'file present') : 'not in this run')}
      ${row(satOk ? 'y' : 'n', 'Satellite low cloud', satOk ? A.satHours + ' h of scans' : A.sat ? 'none in window' : 'not in this run')}
      ${row(A.sun ? 'y' : 'n', 'Sunshine', A.sun ? 'model + GOES sunlight' : 'not in this run')}
      ${row('p', A.live ? 'Live forecast' : 'Past night', A.live ? (ob ? 'observed to ' + ob : 'forecast only') : 'replay')}</div></section>`));
  }
  sheet.append(...parts);
  const col = $('bx-color');
  if (col) col.addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b || b.disabled) return;
    S.color = b.dataset.c; syncColorUi();
    if (S.color === 'sun') loadSun().then(() => { colorMode(X.curF, true); });
    else colorMode(X.curF, true);
  });
  const lt = $('bx-light'); if (lt) lt.addEventListener('change', () => { S.light = lt.checked; lightApply(X.curF, true); });
  const sb = $('bx-stn'); if (sb) sb.addEventListener('change', () => toggleStations(sb.checked));
  const sa = $('bx-sat'); if (sa) sa.addEventListener('change', () => toggleSat(sa.checked));
}
function syncColorUi() {
  const col = $('bx-color'); if (!col) return;
  [...col.children].forEach((b) => b.classList.toggle('on', b.dataset.c === S.color));
  const n = $('bx-clnote'); if (n) n.textContent = S.color === 'sun' ? (sunErr ? 'could not load' : sunArr ? (modeOn ? 'W/m² on the street' : 'night: showing slick') : 'loading…') : '';
}

/* beta tag at the start of the status line: opens About (its first section is the beta note) */
function buildTag() {
  if (!X.beta) return;                           // the public page has no Beta tag
  const l = $('st3l'); if (!l || l.parentElement.classList.contains('bx-stl')) return;
  const w = document.createElement('span'); w.className = 'bx-stl'; l.before(w);
  const b = h('<button type="button" class="bx-tag" aria-label="Beta preview: about the new data layers">Beta</button>');
  w.append(b, l); b.addEventListener('click', () => { if (window.openAbout) window.openAbout(); });
}

/* ------------------------------------------------------------------ colour mode (slick | sunshine), legend, night fallback */
let sunArr = null, sunP = null, sunErr = false, modeOn = false, ramp0 = null;
const SUNRAMP = 'linear-gradient(90deg,#24304d 0%,#5b4f7e 22%,#b5674a 48%,#f0a23a 72%,#ffe08a 100%)';
function elAt(f) {
  const a = A.sun ? X.idx.sun.sun_el : null; if (!a) return -90;
  const i = Math.min(a.length - 1, Math.max(0, Math.floor(f))), j = Math.min(a.length - 1, i + 1), u = f - i;
  return a[i] + (a[j] - a[i]) * u;
}
function loadSun() {
  if (sunArr) return Promise.resolve(true);
  if (sunP) return sunP;
  const idx = X.idx, LV = X.LV;
  sunP = (async () => {
    const nF = idx.n_frames, ST = LV.ST, all = new Uint8Array(nF * ST);
    const bufs = await Promise.all(idx.sun.files.map((f) => X.getBuf(X.base + f + LV.V)));
    let o = 0; for (const b of bufs) { all.set(new Uint8Array(b), o); o += b.byteLength; }
    if (o !== nF * ST) throw new Error(`sun files have ${o} bytes, expected ${nF * ST}`);
    X.rib.setAlt(all, nF, 0, ST); X.rib2.setAlt(all, nF, LV.S1, ST);
    sunArr = all; sunErr = false; syncColorUi(); return true;
  })().catch((e) => { console.warn('sunshine data', e); sunErr = true; sunP = null; S.color = 'slick'; syncColorUi(); return false; });
  syncColorUi();
  return sunP;
}
function colorMode(f, force) {
  const want = S.color === 'sun' && !!sunArr && elAt(f) > 0;
  if (!force && want === modeOn) return;
  modeOn = want;
  X.rib.setMode(want ? 1 : 0); if (X.rib2) X.rib2.setMode(want ? 1 : 0);
  legend(); syncColorUi();
}
function legend() {
  const rp = $('ramp'), lg = $('legend'), tk = lg.querySelector('.ticks'); if (!rp || !tk) return;
  if (ramp0 == null) ramp0 = rp.style.background;
  if (modeOn) {
    rp.style.background = SUNRAMP; tk.innerHTML = '<span>0</span><span>900 W/m²</span>';
    lg.setAttribute('aria-label', 'Legend: sunshine on the street, 0 to 900 watts per square metre. Open settings');
  } else {
    rp.style.background = ramp0; tk.innerHTML = A.film ? '<span>dry</span><span>slick</span>' : '<span>dry</span><span>wet</span>';
    lg.setAttribute('aria-label', A.film ? 'Legend: slick, 0 dry to 100 slick. Open settings' : 'Legend: wetness, 0 dry to 100 wet. Open settings');
  }
}

/* ------------------------------------------------------------------ scene light follows the sun (elevation and azimuth from index.json sun) */
let orig = null, litOn = false;
function lightInit() {
  const { hemi, moon, amb } = X.lights, T = X.THREE;
  orig = { bg: X.scene.background.clone(), mp: moon.position.clone(), mc: moon.color.clone(), mi: moon.intensity, hs: hemi.color.clone(), hg: hemi.groundColor.clone(), hi: hemi.intensity, ac: amb.color.clone(), ai: amb.intensity };
  orig.sky = new T.Color('#6f97c8'); orig.dusk = new T.Color('#2d3556'); orig.warm = new T.Color(0xffe3b8); orig.white = new T.Color(0xfff4de);
  orig.hsD = new T.Color(0xb4cdee); orig.hgD = new T.Color(0x77766b); orig.acD = new T.Color(0x59627a); orig.v = new T.Vector3();
  legend();
}
function lightApply(f, force) {
  if (!orig) return;
  const { hemi, moon, amb } = X.lights, sc = X.scene;
  if (!A.sun || !S.light) {
    if (litOn || force) { sc.background.copy(orig.bg); moon.position.copy(orig.mp); moon.color.copy(orig.mc); moon.intensity = orig.mi; hemi.color.copy(orig.hs); hemi.groundColor.copy(orig.hg); hemi.intensity = orig.hi; amb.color.copy(orig.ac); amb.intensity = orig.ai; litOn = false; }
    return;
  }
  litOn = true;
  const sun = X.idx.sun, n = sun.sun_el.length, i = Math.min(n - 1, Math.max(0, Math.floor(f))), j = Math.min(n - 1, i + 1), u = f - i, rad = Math.PI / 180;
  const vec = (k) => { const e = sun.sun_el[k] * rad, a = sun.sun_az[k] * rad; return [Math.cos(e) * Math.sin(a), Math.sin(e), -Math.cos(e) * Math.cos(a)]; }; // x east, y up, z = -north
  const p = vec(i), q = vec(j), d = orig.v.set(p[0] + (q[0] - p[0]) * u, p[1] + (q[1] - p[1]) * u, p[2] + (q[2] - p[2]) * u).normalize();
  const el = elAt(f), day = smooth(-3, 10, el), warm = 1 - smooth(4, 28, el), tw = Math.max(0, 1 - Math.abs(el + 2) / 9);
  sc.background.copy(orig.bg).lerp(orig.sky, day).lerp(orig.dusk, tw * 0.45 * (1 - 0.6 * day));
  moon.position.copy(orig.mp).lerp(orig.v.clone().multiplyScalar(12000).add(X.CITY.target), day);
  moon.color.copy(orig.mc).lerp(orig.white, day).lerp(orig.warm, day * warm * 0.8);
  moon.intensity = orig.mi + (2.7 * Math.max(0.35, Math.sin(Math.max(el, 0) * rad) * 1.2) - orig.mi) * day;
  hemi.color.copy(orig.hs).lerp(orig.hsD, day); hemi.groundColor.copy(orig.hg).lerp(orig.hgD, day); hemi.intensity = orig.hi + (1.7 - orig.hi) * day;
  amb.color.copy(orig.ac).lerp(orig.acD, day); amb.intensity = orig.ai;
}
function clockIcon(f) {
  const c = $('clock') && $('clock').parentElement; if (!c) return;
  if (!A.sun) { delete c.dataset.sky; return; }
  const v = elAt(f) > 0 ? 'sun' : 'moon'; if (c.dataset.sky !== v) c.dataset.sky = v;
}

/* ------------------------------------------------------------------ per-frame hook (called by app.js applyTime) */
let lastCardKey = '';
export function frame(f) {
  if (!X) return;
  if (A.sun) { colorMode(f); lightApply(f); clockIcon(f); }
  if (S.sat) satFrame(f);
  if (card && !card.hidden) cardUpdate();
}

/* ------------------------------------------------------------------ water depth, dries in, cause, road temperature: on-demand day files */
const aux = new Map();
const frameDay = (f) => { const r = X.idx.frame_ranges; for (let d = 0; d < r.length; d++) if (f >= r[d][0] && f < r[d][1]) return d; return r.length - 1; };
function auxFile(kind, d) {
  const idx = X.idx;
  return kind === 'tsurf' ? idx.tsurf.files[d] : idx.film.files[kind][d];
}
function auxArr(kind, d) { // Uint8Array of that day (frames_in_day x ST) or null while loading / failed; starts the load once
  const key = kind + d; let e = aux.get(key);
  if (!e) {
    e = { u8: null, err: false }; aux.set(key, e);
    const r = X.idx.frame_ranges[d], want = (r[1] - r[0]) * X.LV.ST;
    X.getBuf(X.base + auxFile(kind, d) + X.LV.V).then((b) => {
      if (b.byteLength !== want) throw new Error(`${auxFile(kind, d)} has ${b.byteLength} bytes, expected ${want}`);
      e.u8 = new Uint8Array(b); X.refresh();
    }).catch((err) => { e.err = true; console.warn('beta data', err); });
  }
  return e.u8;
}
const auxBad = (d) => ['depth', 'dry', 'cause'].some((k) => { const e = aux.get(k + d); return !!(e && e.err); });
function auxVal(kind, f, k) { // value of sample k at the nearest hourly frame, undefined while not loaded
  const i = Math.round(f), d = frameDay(i), u8 = auxArr(kind, d); if (!u8) return undefined;
  return u8[(i - X.idx.frame_ranges[d][0]) * X.LV.ST + k];
}
function depthMm(u) {
  const s = X.idx.film.depth_scale, ref = s.ref_mm, mx = s.max_mm;
  return u <= 0 ? 0 : ref * (Math.exp(u / 255 * Math.log(1 + mx / ref)) - 1);
}
const dryMm = () => X.idx.film.dry_mm || 0.005;
function fmtMm(F) { return F < 0.005 ? '0' : F < 0.01 ? '<0.01' : F.toFixed(2); }
const CAUSE_TXT = { none: '', fog: 'fog', dew: 'dew', drizzle: 'drizzle', rain: 'rain' };
const causeName = (c) => CAUSE_TXT[(X.idx.film.cause_codes || [])[c]] || '';
function driesTxt(h) { return h === 255 ? 'not within the window' : h <= 1 ? 'under 1 h' : `about ${h} h`; }
function rain24(i) {
  const p = X.weather && X.weather.precip_mm; if (!p) return null;
  let s = 0; for (let k = Math.max(0, i - 23); k <= i; k++) s += p[k] || 0; return s;
}
function roadAir(f, k) {
  const i = Math.round(f), w = X.weather; let air = w && w.temp_c ? w.temp_c[i] : null, road = null;
  if (A.tsurf) { const u = auxVal('tsurf', f, k); if (u !== undefined && u !== 255) { const m = /u8 \/ ([\d.]+) - ([\d.]+)/.exec(X.idx.tsurf.formula || ''); road = u / (m ? +m[1] : 4) - (m ? +m[2] : 10); } }
  return { road, air };
}

/* street card (the app's tooltip): slick, water, dries in, cause, road and air temperature, rain 24 h; sunshine mode: W/m2 */
export function tip(k, v, when) {
  const html = tipHtml(k, v, when); $('tip').classList.toggle('bx-tc', !!html); return html;
}
function tipHtml(k, v, when) {
  if (!X || !X.LV || !(v >= 0)) return null;
  const G = X.G, name = esc(G.names[G.st[k]] || ''), f = X.curF, i = Math.round(f);
  const route = X.selRoute >= 0 && ((G.mask[k] >> X.ROUTES()[X.selRoute].def.index) & 1);
  const tail = `${when}${route ? ' · on route' : G.cls[k] ? ' · district street' : ''} · modelled`;
  if (modeOn && sunArr) {
    const ST = X.LV.ST, a = Math.floor(f), b = Math.min(a + 1, X.idx.n_frames - 1), u = f - a;
    const w = (sunArr[a * ST + k] * (1 - u) + sunArr[b * ST + k] * u) * (X.idx.sun.unit_wm2 || 10);
    const lc = X.weather && X.weather.cloud_low_pct ? X.weather.cloud_low_pct[i] : null;
    return `<div class="bx-tipc"><div class="big"><b style="color:var(--sun)">${Math.round(w)}</b><span>W/m² · ${name}</span></div><dl><dt>Sun</dt><dd>${Math.round(elAt(f))}° above the horizon</dd>${lc == null ? '' : `<dt>Low cloud</dt><dd>${Math.round(lc)}%</dd>`}</dl><div class="wh">${tail}</div></div>`;
  }
  if (!A.film) return null;
  const dep = auxVal('depth', f, k), dry = auxVal('dry', f, k), cau = auxVal('cause', f, k);
  const ra = roadAir(f, k), r24 = rain24(i), Fm = dep === undefined ? null : depthMm(dep);
  const wet = Fm != null && Fm >= dryMm();
  let rows = '';
  rows += `<dt>Water</dt><dd>${Fm == null ? (auxBad(frameDay(i)) ? 'not available' : 'loading…') : fmtMm(Fm) + ' mm' + (wet ? ' film' : '')}</dd>`;
  if (Fm != null) rows += `<dt>Dries in</dt><dd>${wet ? driesTxt(dry) : 'dry now'}</dd>`;
  if (Fm != null && wet && cau !== undefined && causeName(cau)) rows += `<dt>Cause</dt><dd>${causeName(cau)}</dd>`;
  if (ra.road != null || ra.air != null) rows += `<dt>${ra.road != null ? 'Road' : 'Air'}</dt><dd>${ra.road != null ? Math.round(F2(ra.road)) + '°F' : ''}${ra.road != null && ra.air != null ? ' · air ' : ''}${ra.air != null ? Math.round(F2(ra.air)) + '°F' : ''}</dd>`;
  if (r24 != null) rows += `<dt>Rain 24 h</dt><dd>${r24 < 0.05 ? '0' : r24.toFixed(1)} mm</dd>`;
  return `<div class="bx-tipc"><div class="big"><b style="color:${X.rgbCss(X.rampColor(v))}">${Math.round(v)}</b><span>slick · ${name}</span></div><dl>${rows}</dl><div class="wh">${tail}</div></div>`;
}
export function tipReset() { const t = $('tip'); if (t) t.classList.remove('bx-tc'); }

/* route chip, fourth line: water, dries in, cause (route samples at the selected hour) */
function buildChipLine() {
  const tx = $('rbtn') && $('rbtn').querySelector('.tx'); if (!tx || $('bx-water')) return;
  tx.insertAdjacentHTML('beforeend', '<span class="wt" id="bx-water" hidden></span>');
}
export function chip(R) {
  const el = $('bx-water'); if (!el) return;
  if (!R || !A.film || !X.LV || !X.night) { el.hidden = true; document.body.classList.remove('bx-water'); return; }
  const f = X.curF, i = Math.round(f), d = frameDay(i);
  const dp = auxArr('depth', d), dr = auxArr('dry', d), ca = auxArr('cause', d);
  document.body.classList.add('bx-water'); el.hidden = false;
  if (!dp || !dr || !ca) { el.textContent = auxBad(d) ? 'Water · not available' : 'Water · loading…'; return; }
  const ks = R.bxKs || (R.bxKs = X.liveRouteCols(R)), row = (i - X.idx.frame_ranges[d][0]) * X.LV.ST, wn = X.wetNow, lim = dryMm();
  let sum = 0, n = 0, nw = 0; const dh = new Uint16Array(256), cc = new Uint16Array(8);
  for (const k of ks) {
    if (!(wn[k] >= 0)) continue;
    const Fm = depthMm(dp[row + k]); sum += Fm; n++;
    if (Fm >= lim) { nw++; dh[dr[row + k]]++; const c = ca[row + k]; if (c < 8) cc[c]++; }
  }
  if (!n) { el.hidden = true; document.body.classList.remove('bx-water'); return; }
  const mean = sum / n;
  if (nw < n * 0.05) { el.innerHTML = 'Water <b>0 mm</b> · surface dry'; return; }
  let acc = 0, med = 255; for (let h2 = 0; h2 < 256; h2++) { acc += dh[h2]; if (acc >= nw / 2) { med = h2; break; } }
  const cs = [...cc].map((c, c2) => [c, c2]).filter(([c, c2]) => c2 > 0 && c >= nw * 0.25).sort((a, b) => b[0] - a[0]).slice(0, 2).map(([, c2]) => causeName(c2)).filter(Boolean);
  el.innerHTML = `Water <b>${fmtMm(mean)} mm</b> · dries in <b>${med === 255 ? 'not soon' : med <= 1 ? '<1 h' : '~' + med + ' h'}</b>${cs.length ? ' · ' + cs.join(' + ') : ''}`;
}

/* flyover sub-line: "slick 84 · 0.05 mm here" */
export function flyHere(R, s, w) {
  let t = `slick ${w}`;
  if (A.film && X.LV) {
    const i = Math.round(X.curF), d = frameDay(i), dp = auxArr('depth', d);
    if (dp) {
      const bins = X.routeBins(R), b0 = Math.floor(s / 20), row = (i - X.idx.frame_ranges[d][0]) * X.LV.ST; let sum = 0, n = 0;
      for (let b = b0 - 1; b <= b0 + 1; b++) for (const k of bins.get(b) || []) if (X.wetNow[k] >= 0) { sum += depthMm(dp[row + k]); n++; }
      if (n) t += ` · ${fmtMm(sum / n)} mm here`;
    }
  }
  return t;
}

/* ------------------------------------------------------------------ readout row in the dock (only while Stations or Satellite is on) */
let r0 = null, scoreDoc = null, scoreP = null;
function buildReadout() {
  if (r0) return;
  r0 = h('<div id="bx-r0" hidden></div>');
  $('controls').before(r0);
  r0.addEventListener('click', () => openSettings('bx-g-obs'));
  readout();
}
function meanErrF() { // mean 24 h air-temperature error of the reporting stations, in deg F
  let s = 0, n = 0; for (const p of SP) if (p.errT != null) { s += p.errT; n++; }
  return n ? s / n * 1.8 : null;
}
function readout() {
  if (!r0) return;
  let html = '';
  if (S.stations) {
    const rep = SP.filter((p) => p.rep).length, e = meanErrF();
    html += `<button type="button" class="bx-oc" aria-label="Stations reporting and mean 24 hour air temperature error: open settings" title="Stations reporting · mean 24 h air error"><i class="bx-k stn"></i><span>Stations</span> <b>${D ? rep : '…'}</b>${e != null ? ` <b>· ±${e.toFixed(1)}°F</b>` : ''}</button>`;
  }
  if (S.sat) {
    const hd = scoreDoc && scoreDoc.headline;
    html += `<button type="button" class="bx-oc" aria-label="Satellite cloud match: open settings"><i class="bx-k sat"></i>${hd ? `<span>Cloud match 24 h</span> <b>${hd.match_pct_24h}%</b>` : scoreDoc ? '<span>Cloud match: no scorable hours</span>' : '<span>Cloud match 24 h</span> <b>…</b>'}</button>`;
  }
  r0.innerHTML = html; r0.hidden = !html;
}

/* ------------------------------------------------------------------ stations: pins at real positions, stem = sensor height, dot colour = 24 h error */
let D = null, SP = [], sel = null, pinsEl = null, edgeEl = null, card = null, stP = null;
const KIND = { coops: 'NOAA CO-OPS station', iem: 'Mesonet / RAWS station', nws: 'NWS station', metar: 'Airport report (METAR)', ndbc: 'NDBC buoy' };
function errClass(p) { // dot colour from the 24 h error of the model shown (air temperature in C, else wind speed in m/s)
  const col = D.nudge_anchors ? 1 : 2, pick = (a) => (a ? (a[col] != null ? a[col] : a[0]) : null);
  const t = pick(p.r.mae_24h && p.r.mae_24h.T), w = pick(p.r.mae_24h && p.r.mae_24h.wspd);
  p.errT = t;
  if (t != null) return t <= 1.0 ? 'g' : t <= 2.0 ? 'f' : 'b';
  if (w != null) return w <= 1.5 ? 'g' : w <= 3 ? 'f' : 'b';
  return 'u';
}
function loadStations() {
  if (D) return Promise.resolve(true);
  if (stP) return stP;
  stP = X.getJSON(X.base + A.stFile + X.LV.V).then((j) => {
    D = j; const m = X.meta, T = X.terrainH, EX = X.EX;
    SP = Object.entries(j.stations || {}).map(([id, r]) => {
      const x = r.x - m.x0, z = -(r.y - m.y0), inGrid = x >= 0 && x <= m.nx * m.cell_m && -z >= 0 && -z <= m.ny * m.cell_m;
      const gy = inGrid && T ? T.at(x, z) : 0, sens = inGrid && T ? Math.min(90, Math.max(0, r.z - gy / EX)) : (r.kind === 'ndbc' ? 4 : 10);
      const p = { id, r, x, z, gy, rep: !!r.reporting, stem: Math.round(Math.min(46, 8 + sens * 0.55)), sens: Math.round(sens), el: null };
      p.cls = p.rep ? errClass(p) : 'u'; return p;
    });
    buildPins(); readout(); return true;
  }).catch((e) => { console.warn('stations', e); stP = null; toastNote('Stations could not be loaded'); const b = $('bx-stn'); if (b) { b.checked = false; } S.stations = false; readout(); return false; });
  return stP;
}
function toastNote(t) { const n = $('bx-clnote'); if (n) n.textContent = t; }
function toggleStations(on) {
  S.stations = on;
  if (!on) { closeCard(); if (pinsEl) pinsEl.hidden = true; if (edgeEl) edgeEl.hidden = true; readout(); return; }
  readout();
  loadStations().then((ok) => { if (ok && S.stations) { pinsEl.hidden = false; } });
}
function buildPins() {
  if (pinsEl) return;
  pinsEl = h('<div id="bx-pins" role="group" aria-label="Weather stations"></div>'); $('stage').appendChild(pinsEl);
  edgeEl = h('<button type="button" class="bx-edge" hidden></button>'); $('stage').appendChild(edgeEl);
  edgeEl.addEventListener('click', () => X.flyTo(X.CITY.pos, X.CITY.target, 2200));
  for (const p of SP) {
    const b = h(`<button type="button" class="bx-pin ${p.cls}" style="--h:${p.stem}px" aria-label="${esc(p.r.name)}, station"><i class="bx-stem"></i><i class="bx-dot"></i><em class="bx-plab">${esc(p.r.name)}</em></button>`);
    b.addEventListener('click', (e) => { e.stopPropagation(); selectStation(p.id); });
    pinsEl.appendChild(b); p.el = b;
  }
}
let bandTop = 60, bandBot = 700, vw = innerWidth, vh = innerHeight;
function measure() {
  const t = $('top'), d = $('dock'); if (!t || !d) return;
  bandTop = t.getBoundingClientRect().bottom + 4; bandBot = d.getBoundingClientRect().top - 4; vw = innerWidth; vh = innerHeight;
}
let nOff = -1, edgeFrame = 0;
export function render() {
  if (!X || !S.stations || !SP.length || !pinsEl) return;
  const cam = X.camera, T = X.THREE; cam.updateMatrixWorld();
  if ((edgeFrame++ & 15) === 0) measure();
  const v = (render.v || (render.v = new T.Vector3()));
  let off = 0;
  for (const p of SP) {
    v.set(p.x, p.gy, p.z).project(cam);
    const sx = (v.x * 0.5 + 0.5) * vw, sy = (1 - (v.y * 0.5 + 0.5)) * vh, vis = v.z < 1 && sx > 8 && sx < vw - 8 && sy - p.stem > bandTop && sy < bandBot;
    if (vis !== p.vis) { p.vis = vis; p.el.style.display = vis ? '' : 'none'; }
    if (vis) { p.el.style.transform = `translate(${sx.toFixed(1)}px,${sy.toFixed(1)}px)`; p.el.style.zIndex = Math.round(sy); } else off++;
  }
  if (off !== nOff) { nOff = off; edgeEl.hidden = !off; edgeEl.textContent = `+${off} off screen ›`; edgeEl.setAttribute('aria-label', `${off} stations off screen. Show the whole city`); }
}

/* station card: observed vs model at the selected hour, 24 h error, type and height, dew-point sparkline */
function hourIndex() { // index into stations.json hours of the selected frame; exact = the hour is inside the observed part
  const t = Date.parse(X.idx.start_iso) + X.curF * X.idx.step_min * 60000, t0 = Date.parse(D.hours_iso[0]), K = D.hours_iso.length;
  const raw = Math.round((t - t0) / 3600000);
  return { k: Math.min(K - 1, Math.max(0, raw)), exact: raw >= 0 && raw < K };
}
function modelSeries(r, v) {
  const order = D.nudge_anchors ? ['model_after', 'model_city', 'model_before'] : ['model_city', 'model_before'];
  for (const m of order) if (r[m] && r[m][v]) return r[m][v];
  return null;
}
const num = (a, k) => (a && a[k] != null ? a[k] : null);
function closeCard() { if (card) { card.hidden = true; document.body.classList.remove('bx-card-open'); } if (sel) { const p = SP.find((q) => q.id === sel); if (p && p.el) { p.el.classList.remove('sel'); } sel = null; } lastCardKey = ''; }
function selectStation(id) {
  sel = id; SP.forEach((p) => p.el && p.el.classList.toggle('sel', p.id === id));
  X.hideTip();
  if (!card) {
    card = h('<div id="bx-card" class="bx-card" role="dialog" aria-label="Weather station" hidden></div>'); document.body.appendChild(card);
    card.addEventListener('click', (e) => { if (e.target.closest('.x')) { const p = SP.find((q) => q.id === sel); closeCard(); if (p && p.el) p.el.focus(); } });
  }
  lastCardKey = ''; card.hidden = false; document.body.classList.add('bx-card-open'); cardUpdate(true);
}
function cardUpdate(focusClose) {
  const p = SP.find((q) => q.id === sel); if (!p || !D) return;
  const { k, exact } = hourIndex(), key = sel + '|' + k + '|' + exact; if (key === lastCardKey) return; lastCardKey = key;
  const r = p.r, ob = r.obs || {}, f = X.curF;
  const hourTxt = X.ptFmt(D.hours_iso[k], { weekday: 'short', hour: '2-digit', minute: '2-digit' }).replace(',', '');
  const sub = `${KIND[r.kind] || r.kind}${r.hill ? ' · hill' : ''} · sensor about ${Math.round(r.z)} m above sea level`;
  let body = '';
  if (!p.rep) body = '<div class="none">No report from this station in the observed hours.</div>';
  else {
    const rows = [];
    const T = (a, b) => (a == null ? null : [Math.round(F2(a)) + '°F', b == null ? '' : Math.round(F2(b)) + '°F', b == null ? '' : sg(F2(b) - F2(a), 1) + '°']);
    const sg = (v, nd) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(nd);
    const addT = (label, v, key2) => { const o = num(ob[v], k), m = num(modelSeries(r, v), k); if (o != null) { const t = T(o, m); rows.push([label, t[0], t[1], t[2]]); } };
    addT('Air', 'T', 'T'); addT('Dew', 'Td', 'Td');
    const ow = num(ob.wspd, k), mw = num(modelSeries(r, 'wspd'), k), od = num(ob.wdir, k), md = num(modelSeries(r, 'wdir'), k);
    if (ow != null) rows.push(['Wind', `${od != null ? X.compass(od) + ' ' : ''}${Math.round(ow * MPH)}`, mw == null ? '' : `${md != null ? X.compass(md) + ' ' : ''}${Math.round(mw * MPH)}`, mw == null ? '' : sg((mw - ow) * MPH, 0) + ' mph']);
    const vk = num(ob.vis_km, k); if (vk != null) rows.push(['Visibility', `${vk >= 10 ? '10+' : vk.toFixed(1)} km`, '', '']);
    const col = D.nudge_anchors ? 1 : 2, er = r.mae_24h || {}, ec = (a) => (a ? (a[col] != null ? a[col] : a[0]) : null);
    const chips = [];
    if (ec(er.T) != null) chips.push(`air <b>±${(ec(er.T) * 1.8).toFixed(1)}°F</b>`);
    if (ec(er.Td) != null) chips.push(`dew <b>±${(ec(er.Td) * 1.8).toFixed(1)}°F</b>`);
    if (ec(er.wspd) != null) chips.push(`wind <b>±${(ec(er.wspd) * MPH).toFixed(1)} mph</b>`);
    const sv = ob.Td ? 'Td' : ob.T ? 'T' : null;
    let spark = '';
    if (sv) {
      const o = ob[sv], m = modelSeries(r, sv), a0 = Math.max(0, k - 23), pts = (arr) => { const out = []; for (let j = a0; j <= k; j++) if (arr && arr[j] != null) out.push([j - a0, arr[j]]); return out; };
      const po = pts(o), pm = pts(m), all = po.concat(pm);
      if (po.length > 1) {
        const lo = Math.min(...all.map((q) => q[1])), hi = Math.max(...all.map((q) => q[1])), span = Math.max(0.5, hi - lo), nx = Math.max(1, k - a0);
        const path = (q) => q.map((c, j) => (j ? 'L' : 'M') + (c[0] / nx * 330).toFixed(1) + ' ' + (32 - (c[1] - lo) / span * 28).toFixed(1)).join(' ');
        spark = `<div class="st">${sv === 'Td' ? 'Dew point' : 'Air temperature'}, last ${k - a0 + 1} h · solid observed · dashed model</div><svg class="spark" viewBox="0 0 330 36" preserveAspectRatio="none" aria-hidden="true"><path d="${path(po)}" fill="none" stroke="#e6ecf5" stroke-width="1.6" vector-effect="non-scaling-stroke"/>${pm.length > 1 ? `<path d="${path(pm)}" fill="none" stroke="#7fe3f0" stroke-width="1.6" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>` : ''}</svg>`;
      }
    }
    body = `<div class="bx-ct"><span class="h">${esc(hourTxt)}${exact ? '' : ' (latest)'}</span><span class="h r">observed</span><span class="h r">model</span><span class="h r">diff</span>${rows.map((q) => `<span class="k2">${q[0]}</span><span class="r">${q[1]}</span><span class="r">${q[2]}</span><span class="r dl">${q[3]}</span>`).join('')}</div>`
      + (exact ? '' : `<div class="st">The selected hour is after the last observation: showing the latest observed hour.</div>`)
      + (chips.length ? `<div class="bx-err"><span>24 h error</span>${chips.map((c) => `<span class="e">${c}</span>`).join('')}</div>` : '') + spark;
  }
  card.innerHTML = `<div class="hd"><span class="tx"><span class="nm">${esc(r.name)}</span><span class="st">${esc(sub)}</span></span><button type="button" class="x" aria-label="Close station card">${X_ICON}</button></div>${body}`;
  if (focusClose) { const x = card.querySelector('.x'); if (x && document.activeElement !== x && !X.isTouch) x.focus({ preventScroll: true }); }
}

/* ------------------------------------------------------------------ satellite low cloud: violet dashed outline with light hatching, past hours only */
let satMesh = null, satTex = null, satP = null, satFrames = null;
function loadImg(u) { return new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error('image ' + u)); i.src = u; }); }
const SAT_FRAG = `
  precision highp float;
  varying vec2 vUv;
  uniform sampler2D uTex; uniform float uFrame, uNT, uNorthUp; uniform vec4 uTiles; uniform vec2 uTexel;
  float fetchV(float k, vec2 tp){
    k = clamp(k, 0., uNT - 1.); float r = floor(k / uTiles.x), c = k - r * uTiles.x;
    vec2 q = clamp(floor(tp) + .5, vec2(.5), uTiles.zw - .5);
    return texture2D(uTex, (vec2(c * uTiles.z, r * uTiles.w) + q) * uTexel).r * 255.;
  }
  float low(float v){ return (v < 251. && v >= 125.) ? 1. : 0.; }
  void main(){
    float s = uNorthUp > .5 ? 1. - vUv.y : vUv.y;
    vec2 tp = vec2(vUv.x * uTiles.z, s * uTiles.w), dx = dFdx(tp) * 1.6, dy = dFdy(tp) * 1.6;
    float c0 = low(fetchV(uFrame, tp));
    float e = max(max(abs(low(fetchV(uFrame, tp + dx)) - c0), abs(low(fetchV(uFrame, tp - dx)) - c0)), max(abs(low(fetchV(uFrame, tp + dy)) - c0), abs(low(fetchV(uFrame, tp - dy)) - c0)));
    float dash = step(.4, fract((gl_FragCoord.x + gl_FragCoord.y) / 11.));
    float hatch = step(.76, fract((gl_FragCoord.x - gl_FragCoord.y) / 6.));
    float a = max(e * dash * .95, c0 * (hatch * .42 + .05));
    if (a < .01) discard;
    gl_FragColor = vec4(.72, .61, 1., a);
  }`;
async function satBuild() {
  const idx = X.idx, sat = idx.sat_score.satellite, g = idx.fog_grid, T = X.THREE, EX = X.EX;
  const imgs = await Promise.all(sat.files.map((f) => loadImg(X.base + f + X.LV.V)));
  const nF = idx.n_frames, rows = Math.ceil(nF / g.cols), cv = document.createElement('canvas'); cv.width = g.cols * g.w; cv.height = rows * g.h;
  const c2 = cv.getContext('2d', { willReadFrequently: true });
  idx.frame_ranges.forEach(([a, b], k) => { for (let j = 0; j < b - a; j++) { const gi = a + j; c2.drawImage(imgs[k], (j % g.cols) * g.w, Math.floor(j / g.cols) * g.h, g.w, g.h, (gi % g.cols) * g.w, Math.floor(gi / g.cols) * g.h, g.w, g.h); } });
  const px = c2.getImageData(0, 0, cv.width, cv.height).data; satFrames = new Array(nF).fill(false);
  for (let f = 0; f < nF; f++) {
    const x0 = (f % g.cols) * g.w, y0 = Math.floor(f / g.cols) * g.h; let has = false;
    for (let y = 0; y < g.h && !has; y += 2) for (let x = 0; x < g.w; x += 2) if (px[((y0 + y) * cv.width + x0 + x) * 4] !== 255) { has = true; break; }
    satFrames[f] = has;
  }
  satTex = new T.CanvasTexture(cv); satTex.minFilter = satTex.magFilter = T.NearestFilter; satTex.generateMipmaps = false; satTex.flipY = false; satTex.colorSpace = T.NoColorSpace;
  const meta = X.meta, terrainH = X.terrainH, wm = g.w * g.cell_m, hm = g.h * g.cell_m, ox = g.x0 - meta.x0, oz = -(g.y0 - meta.y0);
  const geo = new T.PlaneGeometry(wm, hm, Math.round(g.w / 2), Math.round(g.h / 2)); geo.rotateX(-Math.PI / 2); geo.translate(ox + wm / 2, 0, oz - hm / 2);
  const p = geo.attributes.position, cx = wm / (g.w / 2) * 0.6;
  for (let k = 0; k < p.count; k++) {
    let hh = 0;
    if (terrainH) { const x = p.getX(k), z = p.getZ(k); hh = Math.max(terrainH.at(x, z), terrainH.at(x + cx, z), terrainH.at(x - cx, z), terrainH.at(x, z + cx), terrainH.at(x, z - cx), 0); }
    p.setY(k, hh + 26 * EX);
  }
  const mat = new T.ShaderMaterial({
    uniforms: { uTex: { value: satTex }, uFrame: { value: 0 }, uNT: { value: g.cols * rows }, uNorthUp: { value: g.row0 === 'north' ? 1 : 0 }, uTiles: { value: new T.Vector4(g.cols, rows, g.w, g.h) }, uTexel: { value: new T.Vector2(1 / cv.width, 1 / cv.height) } },
    vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); }',
    fragmentShader: SAT_FRAG, transparent: true, depthWrite: false, side: T.DoubleSide,
  });
  satMesh = new T.Mesh(geo, mat); satMesh.renderOrder = 12; satMesh.frustumCulled = false; satMesh.visible = S.sat; X.scene.add(satMesh);
  return true;
}
function toggleSat(on) {
  S.sat = on; readout();
  if (!on) { if (satMesh) satMesh.visible = false; trkSig = ''; X.trackNow(); return; }
  if (!scoreP) scoreP = X.getJSON(X.base + X.idx.sat_score.cloud_score + X.LV.V).then((j) => { scoreDoc = j; readout(); }).catch((e) => { console.warn('cloud score', e); scoreDoc = { headline: null }; readout(); });
  if (satMesh) { satMesh.visible = true; satFrame(X.curF); trkSig = ''; X.trackNow(); return; }
  if (!satP) satP = satBuild().catch((e) => { console.warn('satellite', e); satP = null; S.sat = false; const b = $('bx-sat'); if (b) b.checked = false; toastNote('Satellite layer could not be loaded'); readout(); return false; });
  satP.then((ok) => { if (ok && S.sat) { satMesh.visible = true; satFrame(X.curF); trkSig = ''; X.trackNow(); } });
}
function satFrame(f) { if (satMesh) satMesh.material.uniforms.uFrame.value = Math.round(f); }

/* violet band under the time rail for the hours that have satellite data */
let trkSig = '';
export function track(x, min, max) {
  const tr = $('trk'); if (!tr) return;
  const on = S.sat && satFrames, sig = on ? [min, max, tr.clientWidth, satFrames.length].join('|') : 'off';
  if (sig === trkSig) return; trkSig = sig;
  tr.querySelectorAll('.bx-satseg').forEach((e) => e.remove());
  if (!on) return;
  const lo = Math.ceil(min), hi = Math.min(Math.floor(max), satFrames.length - 1);
  for (let f = lo; f <= hi; f++) {
    if (!satFrames[f]) continue; let g = f; while (g + 1 <= hi && satFrames[g + 1]) g++;
    const i = document.createElement('i'); i.className = 'bx-satseg'; const a = x(f), b = x(g); i.style.left = a + 'px'; i.style.width = Math.max(4, b - a) + 'px'; tr.appendChild(i); f = g;
  }
}
