/* Pure helpers of the 5-minute fog frames (guide 08 part B, lab switch `travel`); no DOM, no three.js, so node can test them (tests_js/test_fogtravel.mjs).
   Time is "data hours": hours since the fog grid's first frame (the same unit as fog2's setFrame). The shown time is blended between two key frames.
   Key frames: the hourly frames outside the fog5 window plus every fog5 frame inside it, so the blend never mixes two frames that are not neighbours in time.
   fog5/index.json: { schema, step_min, nz, dz_m, cols, rows, w, h, frames: [{ t: iso, kind, conf, tile, slot, vol, top? }] } (microclimate/live/fog5.py). */

/* the kind of the frames (observed | interpolated | forecast) as the label wants it */
export const KINDS = ['observed', 'interpolated', 'forecast'];

/* parse an index into frames with their time in data hours; frames with a bad time or no tile are dropped; sorted by time */
export function parseIndex(idx, t0ms) {
  if (!idx || idx.schema !== 1 || !Array.isArray(idx.frames) || !(idx.step_min > 0)) return null;
  const frames = [];
  for (const f of idx.frames) {
    const ms = Date.parse(f && f.t), hk = (ms - t0ms) / 3600000;
    if (!f || !Number.isFinite(hk) || !f.tile || !Number.isInteger(f.slot) || f.slot < 0) continue;
    frames.push({ hk, kind: KINDS.includes(f.kind) ? f.kind : 'observed', tile: f.tile, slot: f.slot, vol: f.vol || null, top: Number.isInteger(f.top) ? f.top : null });
  }
  frames.sort((a, b) => a.hk - b.hk);
  return frames.length ? { frames, stepMin: idx.step_min, info: { nz: idx.nz, dz_m: idx.dz_m, cols: idx.cols, rows: idx.rows, w: idx.w, h: idx.h }, generated: idx.generated_iso || '' } : null;
}

/* the key frame list. hourly = { n, files, top_layer } of the hourly data (files may be null); frames from parseIndex; stride 1 = every 5-minute frame, 3 = every third (15 min).
   key frame: { id, hk, src: 'h' | 'f', k (hourly), tile, slot, vol, top, kind }; id is a number for hourly frames and a string for fog5 frames (names carry a content hash, so equal ids = equal content) */
export function buildLine(hourly, parsed, stride = 1) {
  const line = [];
  let lo = Infinity, hi = -Infinity;
  if (parsed) {
    const fr = parsed.frames, s = Math.max(1, stride | 0);
    fr.forEach((f, i) => {
      if (s > 1 && i % s !== 0 && i !== fr.length - 1) return;
      line.push({ id: `${f.tile}#${f.slot}|${f.vol || ''}`, hk: f.hk, src: 'f', k: -1, tile: f.tile, slot: f.slot, vol: f.vol, top: f.top, kind: f.kind });
    });
    lo = fr[0].hk; hi = fr[fr.length - 1].hk;
  }
  for (let k = 0; k < hourly.n; k++) {
    if (k >= lo - 1e-6 && k <= hi + 1e-6) continue;
    line.push({ id: k, hk: k, src: 'h', k, tile: null, slot: -1, vol: hourly.files ? hourly.files[k] || null : null, top: hourly.top_layer && hourly.top_layer[k] != null ? hourly.top_layer[k] : null, kind: 'observed' });
  }
  line.sort((a, b) => a.hk - b.hk);
  return line;
}

/* the two key frames around hk and the blend weight of the second (0..1); ends clamp. ease: 0 = linear; 1 = smoothstep (only if a pulse shows, guide 08 B.2) */
export function bracket(line, hk, ease = 0) {
  const n = line.length;
  if (!n) return null;
  if (hk <= line[0].hk) return { a: line[0], b: line[0], mix: 0, i: 0 };
  if (hk >= line[n - 1].hk) return { a: line[n - 1], b: line[n - 1], mix: 0, i: n - 1 };
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (line[m].hk <= hk) lo = m; else hi = m; }
  let w = (hk - line[lo].hk) / (line[hi].hk - line[lo].hk);
  if (ease > 0) w = w + ease * (w * w * (3 - 2 * w) - w);
  return { a: line[lo], b: line[hi], mix: w, i: lo };
}

/* 5-minute frames shown per wall second at this playback rate (hours of data time per second): above 2 (one frame per 0.5 s) use every third frame */
export function strideFor(hoursPerSec, stepMin = 5) {
  return Math.abs(hoursPerSec) * 60 / stepMin > 2 ? 3 : 1;
}

/* the kind of the data at hk for the label: the kind of the nearer key frame; null when hk is outside the fog5 window (today's labels apply there) */
export function kindAt(line, hk) {
  const br = bracket(line, hk);
  if (!br || br.a.src !== 'f' || br.b.src !== 'f') return null;
  return (br.mix > 0.5 ? br.b : br.a).kind;
}

/* a refreshed index may replace the old one without a visible change when the two key frames on screen have the same ids in the new line (names carry content hashes) */
export function swapIsInvisible(shown, newLine, hk) {
  if (!shown || !shown.a) return true;
  const br = bracket(newLine, hk);
  return !!br && br.a.id === shown.a.id && br.b.id === shown.b.id;
}

/* the index.json of a newer run: a different generated_iso */
export function isNewer(oldParsed, newParsed) {
  return !!newParsed && (!oldParsed || (newParsed.generated || '') > (oldParsed.generated || ''));
}
