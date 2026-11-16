// node tests_js/test_fogtravel.mjs   (no dependencies): pure helpers of the 5-minute fog frames (site/v3/fogtravel.js, guide 08 part B)
import assert from 'node:assert/strict';
import { parseIndex, buildLine, bracket, strideFor, kindAt, swapIsInvisible, isNewer } from '../site/v3/fogtravel.js';

const t0 = Date.parse('2026-11-15T00:00:00Z');
const iso = (hk) => new Date(t0 + hk * 3600000).toISOString();
// window 10:00 .. 12:00 (data hours 10..12), 25 frames; observed to 11:00, then forecast
const frames = [];
for (let i = 0; i <= 24; i++) {
  const hk = 10 + i / 12;
  frames.push({ t: iso(hk), kind: hk <= 11 ? (i % 5 === 3 ? 'interpolated' : 'observed') : 'forecast', conf: 0.5, tile: `tile_${Math.floor(hk)}_ab.png`, slot: Math.round((hk - Math.floor(hk)) * 12) % 12, vol: i % 2 ? null : `vol_${i}.png`, top: 7 });
}
const idx = { schema: 1, step_min: 5, nz: 24, dz_m: 25, cols: 6, rows: 4, w: 214, h: 203, generated_iso: '2026-11-15T10:30:00Z', frames };
const P = parseIndex(idx, t0);
assert.equal(P.frames.length, 25);
assert.equal(P.stepMin, 5);
assert.equal(P.info.w, 214);
assert.ok(Math.abs(P.frames[1].hk - (10 + 1 / 12)) < 1e-9);
// bad input
assert.equal(parseIndex(null, t0), null);
assert.equal(parseIndex({ schema: 2, frames, step_min: 5 }, t0), null);
assert.equal(parseIndex({ schema: 1, step_min: 5, frames: [{ t: 'nope', tile: 'a', slot: 0 }] }, t0), null);
assert.equal(parseIndex({ schema: 1, step_min: 5, frames: [{ t: iso(1), tile: 'a', slot: -1 }] }, t0), null);

// line: hourly 0..23, window 10..12 replaced by 25 five-minute frames
const hourly = { n: 24, files: null, top_layer: null };
const L = buildLine(hourly, P, 1);
assert.equal(L.length, 24 - 3 + 25); // hours 10, 11, 12 are inside the window
for (let i = 1; i < L.length; i++) assert.ok(L[i].hk > L[i - 1].hk, 'sorted, strictly increasing');
assert.equal(L.filter((e) => e.src === 'f').length, 25);
assert.deepEqual(L.filter((e) => e.src === 'h').map((e) => e.k).slice(8, 12), [8, 9, 13, 14]);

// bracket: hourly side, the join, the window, the end
let b = bracket(L, 3.25);
assert.equal(b.a.id, 3); assert.equal(b.b.id, 4); assert.ok(Math.abs(b.mix - 0.25) < 1e-9);
b = bracket(L, 9.5); // between hourly 9 and the first fog5 frame at 10: a plain two-point blend, no jump at the join
assert.equal(b.a.id, 9); assert.equal(b.b.src, 'f'); assert.ok(Math.abs(b.mix - 0.5) < 1e-9);
b = bracket(L, 10); assert.equal(b.b.src, 'f'); assert.ok(b.mix === 0 || b.mix === 1);
b = bracket(L, 10 + 2.5 / 12); assert.equal(b.a.src, 'f'); assert.equal(b.b.src, 'f'); assert.ok(Math.abs(b.mix - 0.5) < 1e-9);
b = bracket(L, 12.5); assert.equal(b.a.src, 'f'); assert.equal(b.b.id, 13); assert.ok(Math.abs(b.mix - 0.5 / 1) < 0.51);
assert.deepEqual([bracket(L, -5).a.id, bracket(L, 99).b.id], [0, 23]);
// continuity of the weight across the join: crossing a key frame, the weight of the old one goes to 0 as the weight of the new one goes to 1
const e1 = bracket(L, 10 - 1e-9), e2 = bracket(L, 10 + 1e-9);
assert.ok(Math.abs((e1.b.src === 'f' ? e1.mix : 1 - e1.mix) - (e2.a.src === 'f' ? 1 - e2.mix : e2.mix)) < 1e-6);
// smoothstep easing keeps the end points
b = bracket(L, 3.5, 1); assert.ok(Math.abs(b.mix - 0.5) < 1e-9);
b = bracket(L, 3.1, 1); assert.ok(b.mix < 0.1);

// stride 3: every third frame plus the last one
const L3 = buildLine(hourly, P, 3);
assert.equal(L3.filter((e) => e.src === 'f').length, 9); // indices 0,3,...,24
assert.equal(L3.filter((e) => e.src === 'f').at(-1).hk, P.frames.at(-1).hk);
assert.equal(strideFor(0.1), 1);          // 6 min of data per second: 1.2 frames/s
assert.equal(strideFor(1 / 6), 1);        // 1 h per 10 s = 2 frames/s: still every frame
assert.equal(strideFor(0.25), 3);
assert.equal(strideFor(-0.5), 3);

// hourly 3D files and top layers carry over; fog5 frames carry their own
const H3 = buildLine({ n: 24, files: Array.from({ length: 24 }, (_, k) => `fog3d_${k}.png`), top_layer: Array.from({ length: 24 }, (_, k) => k) }, P, 1);
assert.equal(H3.find((e) => e.id === 5).vol, 'fog3d_5.png'); assert.equal(H3.find((e) => e.id === 5).top, 5);
assert.equal(H3.find((e) => e.src === 'f').vol, 'vol_0.png');

// no fog5: the line is the hourly frames, brackets are as before
const L0 = buildLine(hourly, null, 1);
assert.equal(L0.length, 24); assert.equal(bracket(L0, 7.4).a.id, 7);

// labels
assert.equal(kindAt(L, 5), null);              // outside the window: today's labels
assert.equal(kindAt(L, 10 + 0.01), 'observed');
assert.equal(kindAt(L, 10 + 3.5 / 12), 'interpolated');
assert.equal(kindAt(L, 11.5), 'forecast');

// refresh: a new index with identical frames is invisible; a changed frame on screen is not; a change away from the screen is
const shown = bracket(L, 11.5);
assert.ok(swapIsInvisible(shown, L, 11.5));
const idx2 = JSON.parse(JSON.stringify(idx)); idx2.generated_iso = '2026-11-15T10:35:00Z';
idx2.frames.forEach((f, i) => { if (i >= 20) f.tile = 'tile_new.png'; }); // the last frames change
const L2 = buildLine(hourly, parseIndex(idx2, t0), 1);
assert.ok(swapIsInvisible(bracket(L, 10.5), L2, 10.5), 'early frames unchanged: swap now');
assert.ok(!swapIsInvisible(bracket(L, 11.8), L2, 11.8), 'a changed frame is on screen: wait');
assert.ok(isNewer(P, parseIndex(idx2, t0)));
assert.ok(!isNewer(P, P));
assert.ok(isNewer(null, P));

console.log('test_fogtravel: ok');
