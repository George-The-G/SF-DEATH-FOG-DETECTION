/* Pure helpers of the beta 3D fog data (guide 04); no DOM, no three.js, so node can test them (tests_js/test_fog3d.mjs).
   Atlas layout (index.json "fog3d"): nz levels of dz m above sea level, `cols` x `rows` tiles of w x h pixels, level k at column k % cols, row floor(k / cols),
   row 0 of a tile = north. */

/* pixel offset of level k inside the atlas */
export function atlasOffset(k, info) {
  const c = k % info.cols, r = Math.floor(k / info.cols);
  return [c * info.w, r * info.h];
}

/* colMax[h * w] = the largest value over all levels of every column, from the atlas pixels (one channel, width W = cols * w) */
export function colMax(px, info) {
  const { w, h, nz, cols } = info, W = cols * w, out = new Uint8Array(w * h);
  for (let k = 0; k < nz; k++) {
    const [ox, oy] = atlasOffset(k, info);
    for (let y = 0; y < h; y++) {
      const src = (oy + y) * W + ox, dst = y * w;
      for (let x = 0; x < w; x++) { const v = px[src + x]; if (v > out[dst + x]) out[dst + x] = v; }
    }
  }
  return out;
}

/* volume height in scene units: (max(topLayer, 3) + 2) levels, at most nz + 1 levels */
export function volumeHeight(topLayer, info, EX) {
  return Math.min(Math.max(topLayer, 3) + 2, info.nz + 1) * info.dz_m * EX;
}

/* data usable at all: the index entry has the layout the shader expects */
export function usable(info, grid) {
  return !!(info && info.files && info.files.length && info.cols > 0 && info.rows > 0 && info.nz > 0 && info.nz <= info.cols * info.rows
    && grid && info.w === grid.w && info.h === grid.h);
}

/* an hour the data says has no fog at all (index.json fog3d.files[k] is null): an explicit empty entry, so the viewer draws NO fog for it instead of falling back to the flat 2D map
   (which knows no height and would stand fog from the sky to the ground). The shared texture is never disposed. */
export function emptyEntry(k, info, tex) {
  return { k, tex, colMax: new Uint8Array(info.w * info.h), topLayer: 3, empty: true };
}

/* tiny LRU for decoded hours: get(k) -> entry | undefined (marks it used); set(k, v) evicts the least recently used beyond max and calls onEvict(v) */
export function createLru(max, onEvict) {
  const m = new Map();
  return {
    get(k) { const v = m.get(k); if (v !== undefined) { m.delete(k); m.set(k, v); } return v; },
    has(k) { return m.has(k); },
    set(k, v, keep) {
      if (m.has(k)) m.delete(k);
      m.set(k, v);
      for (const key of m.keys()) { if (m.size <= max) break; if (key === k || (keep && keep.includes(key))) continue; const old = m.get(key); m.delete(key); if (onEvict) onEvict(old, key); }
    },
    get size() { return m.size; },
    keys() { return [...m.keys()]; },
  };
}
