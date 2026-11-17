// node tests_js/test_macro.mjs   (no dependencies): pure parts of the macro view (site/v3/macrocalc.js, guide 09 part B)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseWide, wideHeight, seamWeight, inHole, slabSegment, utm10ToLonLat, resampleMacro, macroBracket } from '../site/v3/macrocalc.js';

// header parse on the real file: size, grid, Mount Tamalpais
const b = readFileSync(new URL('../site/v3/data/terrain_wide.bin', import.meta.url));
const W = parseWide(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
assert.equal(W.nx, 312); assert.equal(W.ny, 308); assert.equal(W.cell, 400); assert.equal(W.zs, 0.5); assert.equal(W.h.length, 312 * 308);
const [tamE, tamN] = [535463, 4197404];   // 37.9235 N, -122.5965 E in UTM 10N
const tr = Math.floor((W.y0 - tamN) / W.cell), tc = Math.floor((tamE - W.x0) / W.cell);
let mx = 0; for (let r = tr - 1; r <= tr + 1; r++) for (let c = tc - 1; c <= tc + 1; c++) mx = Math.max(mx, wideHeight(W, r, c));
assert.ok(mx > 600 && mx < 800, 'Mount Tamalpais ' + mx);
assert.equal(wideHeight(W, Math.floor((W.y0 - 4175000) / W.cell), Math.floor((505000 - W.x0) / W.cell)), 0); // open ocean west of the Golden Gate
assert.throws(() => parseWide(new ArrayBuffer(10)));
const bad = b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); new Uint8Array(bad)[0] = 0x58;
assert.throws(() => parseWide(bad));

// UTM inverse
const [lo, la] = utm10ToLonLat(551081, 4180455); // downtown San Francisco, roughly
assert.ok(Math.abs(lo + 122.42) < 0.01 && Math.abs(la - 37.77) < 0.01, [lo, la].join());
const [lo2, la2] = utm10ToLonLat(500000, 4000000); assert.ok(Math.abs(lo2 + 123) < 1e-9 && Math.abs(la2 - 36.1) < 0.05);

// seam: 1 outside, 0 two km inside, monotone between
const R = [0, -16000, 17000, 0];
assert.equal(seamWeight(-500, -8000, R), 1); assert.equal(seamWeight(0, -8000, R), 1);
assert.equal(seamWeight(8000, -8000, R), 0); assert.equal(seamWeight(2000, -8000, R), 0);
let prev = 1; for (let d = 0; d <= 2000; d += 100) { const w = seamWeight(d, -8000, R); assert.ok(w <= prev + 1e-12); prev = w; }
assert.ok(Math.abs(seamWeight(1000, -8000, R) - 0.5) < 1e-9);
// hole: shrunk by one wide cell
assert.ok(inHole(8000, -8000, R, 400)); assert.ok(!inHole(300, -8000, R, 400)); assert.ok(!inHole(-100, -8000, R, 400));

// slab
assert.deepEqual(slabSegment(500, -1, 100, 300), [200, 400]);          // from above, going down
assert.deepEqual(slabSegment(0, 1, 100, 300), [100, 300]);             // from below
assert.deepEqual(slabSegment(200, 0, 100, 300), [0, Infinity]);        // horizontal, inside
assert.equal(slabSegment(50, 0, 100, 300), null);                      // horizontal, outside
assert.equal(slabSegment(500, 1, 100, 300), null);                     // slab behind the camera
assert.deepEqual(slabSegment(200, 1, 100, 300), [0, 100]);             // camera inside
assert.equal(slabSegment(0, 1, 300, 100), null);

// resample: a constant frame stays constant inside the box and is empty outside; a gradient in longitude is kept
const w = 5, h = 4, box = [-123.0, 37.0, -122.0, 38.0], f = new Uint8Array(w * h * 4);
for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const o = (y * w + x) * 4; f[o] = x * 60; f[o + 1] = 200; f[o + 2] = 7; f[o + 3] = 255; }
const T = { x0: 500000, y0: 4210000, cell: 5000, nx: 20, ny: 20 }; // lon about -123.1 .. -122.0
const o = resampleMacro(f, w, h, box, T);
let inside = 0, outside = 0, monotone = true;
for (let r = 0; r < T.ny; r++) { let last = -1; for (let c = 0; c < T.nx; c++) { const k = (r * T.nx + c) * 4; if (o[k + 3]) { inside++; assert.equal(o[k + 1], 200); assert.equal(o[k + 2], 7); if (o[k] < last) monotone = false; last = o[k]; } else outside++; } }
assert.ok(inside > 20 && outside > 20 && monotone);

// smoothing: one hard cloud pixel (R 255, top 35 -> B 35) is spread over its neighbours, and the top is not diluted by the empty pixels around it
const sw = 9, sh = 9, sf = new Uint8Array(sw * sh * 4); sf[(4 * sw + 4) * 4] = 255; sf[(4 * sw + 4) * 4 + 1] = 200; sf[(4 * sw + 4) * 4 + 2] = 35;
const sb = [-122.9, 37.6, -122.5, 38.0], tt = { x0: 515000, y0: 4215000, cell: 400, nx: 80, ny: 80 };
const so = resampleMacro(sf, sw, sh, sb, tt);
let rmax = 0, cells = 0, topBad = false;
for (let k = 0; k < tt.nx * tt.ny; k++) { if (!so[k * 4 + 3]) continue; if (so[k * 4] > rmax) rmax = so[k * 4]; if (so[k * 4] > 8) { cells++; if (Math.abs(so[k * 4 + 2] - 35) > 1) topBad = true; } }
assert.ok(rmax < 140 && rmax > 40, 'peak is spread: ' + rmax); assert.ok(cells > 20, 'covers more than one source pixel: ' + cells); assert.ok(!topBad, 'top stays 35 where there is cloud');

// bracket
const idx = { schema: 1, frames: [{ t: '2026-11-16T10:00:00Z', file: 'a.png' }, { t: '2026-11-16T10:05:00Z', file: 'b.png' }, { t: '2026-11-16T10:10:00Z', file: 'c.png' }] };
const t = (s) => Date.parse('2026-11-16T' + s + 'Z');
assert.equal(macroBracket(idx, t('09:59:00')), null);
assert.equal(macroBracket(idx, t('10:30:00')), null);
let m = macroBracket(idx, t('10:02:30')); assert.equal(m.a.file, 'a.png'); assert.equal(m.b.file, 'b.png'); assert.ok(Math.abs(m.mix - 0.5) < 1e-9);
m = macroBracket(idx, t('10:12:00')); assert.equal(m.a.file, 'c.png'); assert.equal(m.mix, 0);
assert.equal(macroBracket(null, 0), null); assert.equal(macroBracket({ schema: 2, frames: [] }, 0), null);
console.log('test_macro: ok');
