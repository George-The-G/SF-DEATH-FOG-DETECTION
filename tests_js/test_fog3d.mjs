// node tests_js/test_fog3d.mjs   (no dependencies): pure helpers of the beta 3D fog data (site/v3/fog3dview.js)
import assert from 'node:assert/strict';
import { atlasOffset, colMax, volumeHeight, usable, createLru } from '../site/v3/fog3dview.js';

const info = { files: ['a.png'], nz: 24, dz_m: 25, cols: 6, rows: 4, w: 214, h: 203 };
// level 7 sits at column 1, row 1
assert.deepEqual(atlasOffset(7, info), [214, 203]);
assert.deepEqual(atlasOffset(0, info), [0, 0]);
assert.deepEqual(atlasOffset(23, info), [5 * 214, 3 * 203]);

// colMax: the max over levels per column, read from the right tile, row 0 = north
const small = { nz: 4, cols: 2, rows: 2, w: 3, h: 2, dz_m: 25 };
const W = small.cols * small.w, H = small.rows * small.h, px = new Uint8Array(W * H);
const set = (k, x, y, v) => { const [ox, oy] = atlasOffset(k, small); px[(oy + y) * W + ox + x] = v; };
set(0, 1, 0, 40); set(2, 1, 0, 90); set(3, 2, 1, 7); set(1, 0, 1, 255);
const cm = colMax(px, small);
assert.equal(cm.length, 6);
assert.equal(cm[0 * 3 + 1], 90);   // row 0, column 1: max(40, 90)
assert.equal(cm[1 * 3 + 2], 7);
assert.equal(cm[1 * 3 + 0], 255);
assert.equal(cm[0], 0);

// volume height rule: (max(topLayer, 3) + 2) * dz * EX, capped at (nz + 1) * dz * EX
assert.equal(volumeHeight(10, info, 1), 12 * 25);
assert.equal(volumeHeight(0, info, 1), 5 * 25);          // never below 3 + 2 levels
assert.equal(volumeHeight(-1, info, 2), 5 * 25 * 2);
assert.equal(volumeHeight(23, info, 1), 25 * 25);        // 25 = nz + 1, the cap
assert.equal(volumeHeight(99, info, 1), 25 * 25);

// usable: index entry vs the sprite grid
assert.ok(usable(info, { w: 214, h: 203 }));
assert.ok(!usable(info, { w: 200, h: 203 }));
assert.ok(!usable(null, { w: 214, h: 203 }));
assert.ok(!usable({ ...info, files: [] }, { w: 214, h: 203 }));
assert.ok(!usable({ ...info, nz: 25 }, { w: 214, h: 203 }));

// cache eviction: keeps 3, evicts the least recently used that is not protected, calls onEvict
const gone = [], c = createLru(3, (v, k) => gone.push(k));
c.set(1, 'a'); c.set(2, 'b'); c.set(3, 'c');
c.get(1);                      // 2 is now the oldest
c.set(4, 'd');
assert.deepEqual(gone, [2]);
assert.deepEqual(c.keys(), [3, 1, 4]);
c.set(5, 'e', [3]);            // 3 is protected: 1 goes
assert.deepEqual(gone, [2, 1]);
assert.equal(c.size, 3);
console.log('test_fog3d ok');
