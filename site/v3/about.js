// "How this simulation works" pop-up. Self-contained ES module, no dependencies.
// Wire-up: <script type="module" src="about.js"></script>  (auto-initialises; also exports initAbout and sets window.openAbout).
// The write-up itself lives in about.html (an HTML fragment) next to this file and is fetched on first open.

const CSS = `
.ab-trigger{cursor:pointer;-webkit-tap-highlight-color:transparent}
.ab-trigger:focus-visible{outline:2px solid var(--accent,#5ef2ff);outline-offset:3px;border-radius:8px}
.ab-info{display:inline-grid;place-items:center;width:16px;height:16px;margin-left:2px;border:1px solid var(--accent,#5ef2ff);border-radius:50%;
  color:var(--accent,#5ef2ff);font:600 11px/1 var(--sans,system-ui,sans-serif);flex:none;opacity:.9}
.ab-root{position:fixed;inset:0;z-index:2147483000;display:flex;align-items:flex-end;justify-content:center;
  padding:0 max(8px,env(safe-area-inset-left,0px)) max(8px,env(safe-area-inset-bottom,0px)) max(8px,env(safe-area-inset-right,0px));
  background:rgba(2,5,12,.62);touch-action:none;overscroll-behavior:contain;animation:ab-fade .15s ease-out}
.ab-card{position:relative;display:flex;flex-direction:column;width:100%;max-width:560px;height:60vh;height:60dvh;max-height:calc(100dvh - 24px - env(safe-area-inset-top,0px));
  background:var(--panel,rgba(12,19,36,.98));color:var(--text,#e6ecf5);border:1px solid var(--accent,#5ef2ff);border-radius:20px 20px 16px 16px;
  font:14px/1.45 var(--sans,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif);box-shadow:0 12px 40px rgba(0,0,0,.55);overflow:hidden;touch-action:auto;animation:ab-up .18s ease-out}
.ab-head{flex:none;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:2px 6px 2px 16px;min-height:48px;
  background:var(--band,#0b1020);border-bottom:1px solid var(--line,rgba(140,170,210,.26))}
.ab-head h2{margin:0;font-size:15px;font-weight:650;letter-spacing:.02em}
.ab-close{flex:none;width:44px;height:44px;display:grid;place-items:center;padding:0;border:1px solid var(--line,rgba(140,170,210,.26));border-radius:14px;
  background:rgba(255,255,255,.06);color:inherit;font-size:22px;line-height:1;cursor:pointer}
.ab-close:focus-visible,.ab-body:focus-visible{outline:2px solid var(--accent,#5ef2ff);outline-offset:-2px}
.ab-body{flex:1 1 auto;min-height:0;overflow-y:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain;touch-action:pan-y;
  padding:6px 16px calc(14px + env(safe-area-inset-bottom,0px));scrollbar-color:rgba(140,170,210,.4) transparent}
.ab-body h3{margin:16px 0 6px;font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--accent,#5ef2ff);font-weight:650}
.ab-body p{margin:0 0 8px}
.ab-body ol,.ab-body ul{margin:0;padding-left:20px}
.ab-body li{margin:0 0 7px;padding-left:2px}
.ab-body li::marker{color:var(--dim,#9db0c9)}
.ab-body b{font-weight:650}
.ab-body a{color:var(--accent,#5ef2ff);text-underline-offset:2px;overflow-wrap:anywhere}
.ab-body code{font-family:var(--mono,ui-monospace,Consolas,monospace);font-size:12.5px;color:var(--dim,#9db0c9)}
.ab-formula{font-family:var(--mono,ui-monospace,Consolas,monospace);font-size:12.5px;padding:8px 10px;border:1px solid var(--line,rgba(140,170,210,.26));border-radius:10px;background:rgba(255,255,255,.04)}
.ab-tblw{overflow-x:auto;margin:0 0 8px}
.ab-tbl{width:100%;border-collapse:collapse;font-size:12.5px;line-height:1.35}
.ab-tbl th,.ab-tbl td{text-align:left;vertical-align:top;padding:5px 6px 5px 0;border-bottom:1px solid var(--line,rgba(140,170,210,.26))}
.ab-tbl th{color:var(--dim,#9db0c9);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.ab-tbl td:first-child{white-space:nowrap;font-weight:650}
.ab-src{line-height:1.7}
.ab-date{color:var(--dim,#9db0c9);font-size:12px;margin-top:12px!important}
.ab-msg{color:var(--dim,#9db0c9)}
@media (min-width:700px){
  .ab-root{align-items:center;padding:16px}
  .ab-card{height:auto;max-height:70vh;max-height:70dvh;border-radius:18px}
}
@media (prefers-reduced-motion:reduce){.ab-root,.ab-card{animation:none}}
@keyframes ab-fade{from{opacity:0}to{opacity:1}}
@keyframes ab-up{from{transform:translateY(14px);opacity:.6}to{transform:none;opacity:1}}
`;

const FOCUSABLE = 'a[href],button:not([disabled]),[tabindex]:not([tabindex="-1"])';
const SELECTORS = ['#brand', '.brand', '#title', 'header .title'];
let state = { opts: {}, trigger: null, root: null, html: null, prevFocus: null, prevOverflow: null, warned: false, styled: false };

function injectCss() {
  if (state.styled) return;
  const s = document.createElement('style');
  s.id = 'about-css';
  s.textContent = CSS;
  document.head.appendChild(s);
  state.styled = true;
}

function findTrigger() {
  for (const sel of SELECTORS) {
    const el = document.querySelector(sel);
    if (el) return el;
  }
  // last resort: the smallest element whose own text is "Death Fog"
  let best = null;
  for (const el of document.querySelectorAll('header *, body *')) {
    if (el.children.length > 3) continue;
    const t = (el.textContent || '').trim().replace(/\s+/g, ' ');
    if (/^death fog( 3d)?$/i.test(t) && !el.closest('.ab-root')) { best = el; break; }
  }
  return best;
}

function bindTrigger(el) {
  if (!el || el.dataset.aboutBound) return;
  el.dataset.aboutBound = '1';
  state.trigger = el;
  el.classList.add('ab-trigger');
  el.setAttribute('role', 'button');
  el.setAttribute('tabindex', '0');
  el.setAttribute('aria-haspopup', 'dialog');
  el.setAttribute('aria-label', 'Death Fog: how this simulation works');
  if (!el.querySelector('.ab-info')) {
    const i = document.createElement('span');
    i.className = 'ab-info';
    i.setAttribute('aria-hidden', 'true');
    i.textContent = 'i';
    el.appendChild(i);
  }
  el.addEventListener('click', (e) => { e.preventDefault(); openAbout(); });
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openAbout(); }
  });
}

async function loadHtml() {
  if (state.html != null) return state.html;
  try {
    const r = await fetch(new URL('about.html', import.meta.url));
    if (!r.ok) throw new Error('HTTP ' + r.status);
    state.html = await r.text();
  } catch (err) {
    console.warn('[about] could not load about.html', err);
    return '<p class="ab-msg">The write-up could not be loaded right now. In short: this is a proof-of-concept 20 m simulation of overnight street wetness from fog, dew, rain, sun and shade. It is not a forecast. Details are in MVP_REPORT.md at <a href="https://github.com/George-The-G/SF-DEATH-FOG-DETECTION" target="_blank" rel="noopener noreferrer">github.com/George-The-G/SF-DEATH-FOG-DETECTION</a>.</p>';
  }
  return state.html;
}

export function isOpen() { return !!state.root; }

export async function openAbout() {
  if (state.root) return;
  injectCss();
  state.prevFocus = document.activeElement;
  const root = document.createElement('div');
  root.className = 'ab-root';
  root.innerHTML = '<div class="ab-card" role="dialog" aria-modal="true" aria-labelledby="ab-title">' +
    '<div class="ab-head"><h2 id="ab-title">How this works</h2><button type="button" class="ab-close" aria-label="Close">&times;</button></div>' +
    '<div class="ab-body" tabindex="0"><p class="ab-msg">Loading...</p></div></div>';
  state.root = root;
  const card = root.firstChild, body = root.querySelector('.ab-body'), close = root.querySelector('.ab-close');

  // keep gestures away from the 3-D scene behind
  for (const t of ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mouseup', 'touchstart', 'touchend', 'wheel', 'click', 'dblclick', 'contextmenu']) {
    root.addEventListener(t, (e) => e.stopPropagation());
  }
  root.addEventListener('wheel', (e) => { if (!card.contains(e.target)) e.preventDefault(); }, { passive: false });
  root.addEventListener('touchmove', (e) => { e.stopPropagation(); if (!body.contains(e.target)) e.preventDefault(); }, { passive: false });
  root.addEventListener('click', (e) => { if (e.target === root) closeAbout(); });
  close.addEventListener('click', closeAbout);
  root.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeAbout(); return; }
    if (e.key !== 'Tab') return;
    const f = [...card.querySelectorAll(FOCUSABLE)].filter((n) => n.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && (document.activeElement === first || !card.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });

  state.prevOverflow = [document.documentElement.style.overflow, document.body.style.overflow];
  document.documentElement.style.overflow = 'hidden';
  document.body.style.overflow = 'hidden';
  document.body.appendChild(root);
  close.focus({ preventScroll: true });
  if (state.trigger) state.trigger.setAttribute('aria-expanded', 'true');

  const html = await loadHtml();
  if (state.root === root) { body.innerHTML = html; body.scrollTop = 0; }
}

export function closeAbout() {
  const root = state.root;
  if (!root) return;
  root.remove();
  state.root = null;
  if (state.prevOverflow) {
    document.documentElement.style.overflow = state.prevOverflow[0];
    document.body.style.overflow = state.prevOverflow[1];
  }
  if (state.trigger) state.trigger.setAttribute('aria-expanded', 'false');
  const back = state.trigger || state.prevFocus;
  if (back && back.focus) back.focus({ preventScroll: true });
}

export function initAbout(opts = {}) {
  state.opts = opts;
  window.openAbout = openAbout;
  window.closeAbout = closeAbout;
  injectCss();
  const el = opts.trigger || findTrigger();
  if (el) { bindTrigger(el); return true; }
  // markup may still be rewritten by the app: watch for it
  const mo = new MutationObserver(() => {
    const t = findTrigger();
    if (t) { mo.disconnect(); clearTimeout(timer); bindTrigger(t); }
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });
  const timer = setTimeout(() => {
    mo.disconnect();
    if (!state.trigger && !state.warned) {
      state.warned = true;
      console.warn('[about] "Death Fog" trigger not found; call window.openAbout() to open the pop-up.');
    }
  }, 8000);
  return false;
}

// Re-bind if the app replaces the header markup after we attached.
function watchRebind() {
  const mo = new MutationObserver(() => {
    if (state.trigger && !state.trigger.isConnected) {
      state.trigger = null;
      const t = findTrigger();
      if (t) bindTrigger(t);
    }
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });
}

function boot() { initAbout(); watchRebind(); }
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
else boot();
