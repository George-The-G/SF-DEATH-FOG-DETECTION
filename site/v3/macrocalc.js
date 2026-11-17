/* Pure parts of the macro view (guide 09 part B, beta): no DOM, no three.js, so node can test them (tests_js/test_macro.mjs).
   Scene axes: x = east metres from meta.x0, z = -(north metres from meta.y0), y = up (times EX). UTM zone 10N, WGS84. */

/* data/terrain_wide.bin (microclimate/wide.py): header '<4sHHddff' = TWD1, nx, ny, x0 (west edge, m), y0 (NORTH edge, m), cell (m), z scale (m per step), then uint16 heights, row 0 = north */
export function parseWide(buf) {
  const dv = new DataView(buf);
  if (buf.byteLength < 32 || dv.getUint32(0, true) !== 0x31445754) throw new Error('not a TWD1 file');
  const nx = dv.getUint16(4, true), ny = dv.getUint16(6, true), x0 = dv.getFloat64(8, true), y0 = dv.getFloat64(16, true), cell = dv.getFloat32(24, true), zs = dv.getFloat32(28, true);
  if (!nx || !ny || buf.byteLength < 32 + nx * ny * 2) throw new Error('TWD1 file is truncated');
  return { nx, ny, x0, y0, cell, zs, h: new Uint16Array(buf.slice(32, 32 + nx * ny * 2)) };
}
/* height (m) of the wide cell at row r, column c */
export const wideHeight = (W, r, c) => W.h[r * W.nx + c] * W.zs;

/* weight of the macro layer at a point: 1 outside the city grid, falling to 0 at `fadeM` inside its edge. rect = [xmin, zmin, xmax, zmax] in scene metres */
export function seamWeight(x, z, rect, fadeM = 2000) {
  const d = Math.min(x - rect[0], rect[2] - x, z - rect[1], rect[3] - z); // > 0 inside, distance to the nearest edge
  if (d <= 0) return 1;
  if (d >= fadeM) return 0;
  const t = d / fadeM;
  return 1 - t * t * (3 - 2 * t);
}

/* is the wide vertex inside the detailed terrain's footprint shrunk by one wide cell (so it is dropped and the detailed terrain covers it) */
export function inHole(x, z, rect, cell) {
  return x > rect[0] + cell && x < rect[2] - cell && z > rect[1] + cell && z < rect[3] - cell;
}

/* analytic slab: the ray origin height oy, direction y component dy, slab between base and top (m, scene units). Returns [t0, t1] along the ray (t >= 0), or null */
export function slabSegment(oy, dy, base, top) {
  if (!(top > base)) return null;
  if (Math.abs(dy) < 1e-6) return oy >= base && oy <= top ? [0, Infinity] : null;
  let a = (base - oy) / dy, b = (top - oy) / dy;
  if (a > b) { const t = a; a = b; b = t; }
  if (b < 0) return null;
  return [Math.max(a, 0), b];
}

/* UTM zone 10N (northing in the northern hemisphere) -> [lon, lat] degrees (Krueger series, WGS84; good to a millimetre) */
export function utm10ToLonLat(e, n) {
  const a = 6378137, f = 1 / 298.257223563, k0 = 0.9996, lon0 = -123 * Math.PI / 180;
  const n1 = f / (2 - f), A = a / (1 + n1) * (1 + n1 * n1 / 4 + n1 ** 4 / 64);
  const xi = n / (k0 * A), eta = (e - 500000) / (k0 * A);
  const b1 = n1 / 2 - 2 * n1 ** 2 / 3 + 37 * n1 ** 3 / 96, b2 = n1 ** 2 / 48 + n1 ** 3 / 15, b3 = 17 * n1 ** 3 / 480;
  let xp = xi, ep = eta;
  [[1, b1], [2, b2], [3, b3]].forEach(([j, b]) => { xp -= b * Math.sin(2 * j * xi) * Math.cosh(2 * j * eta); ep -= b * Math.cos(2 * j * xi) * Math.sinh(2 * j * eta); });
  const chi = Math.asin(Math.sin(xp) / Math.cosh(ep));
  const e2 = f * (2 - f), d1 = e2 / 2 + 5 * e2 ** 2 / 24 + e2 ** 3 / 12 + 13 * e2 ** 4 / 360, d2 = 7 * e2 ** 2 / 48 + 29 * e2 ** 3 / 240 + 811 * e2 ** 4 / 11520, d3 = 7 * e2 ** 3 / 120 + 81 * e2 ** 4 / 1120;
  const lat = chi + d1 * Math.sin(2 * chi) + d2 * Math.sin(4 * chi) + d3 * Math.sin(6 * chi);
  const lon = lon0 + Math.atan2(Math.sinh(ep), Math.cos(xp));
  return [lon * 180 / Math.PI, lat * 180 / Math.PI];
}

/* macro frame (RGBA, w x h, row 0 = north, lon/lat box [lon0, lat0, lon1, lat1] inclusive of both edges) -> RGBA on a UTM grid (row 0 = north), bilinear.
   Cells outside the box are 0,0,0,0. target = { x0 (west edge), y0 (north edge), cell, nx, ny }. R = probability, G = confidence, B = top / 8 m. */
export function resampleMacro(rgba, w, h, box, target) {
  const out = new Uint8Array(target.nx * target.ny * 4);
  const dx = (box[2] - box[0]) / (w - 1), dy = (box[3] - box[1]) / (h - 1);
  for (let r = 0; r < target.ny; r++) for (let c = 0; c < target.nx; c++) {
    const [lon, lat] = utm10ToLonLat(target.x0 + (c + 0.5) * target.cell, target.y0 - (r + 0.5) * target.cell);
    const fx = (lon - box[0]) / dx, fy = (box[3] - lat) / dy;
    if (fx < 0 || fy < 0 || fx > w - 1 || fy > h - 1) continue;
    const x0 = Math.min(Math.floor(fx), w - 2), y0 = Math.min(Math.floor(fy), h - 2), tx = fx - x0, ty = fy - y0, o = (r * target.nx + c) * 4;
    for (let k = 0; k < 3; k++) {
      const v00 = rgba[(y0 * w + x0) * 4 + k], v10 = rgba[(y0 * w + x0 + 1) * 4 + k], v01 = rgba[((y0 + 1) * w + x0) * 4 + k], v11 = rgba[((y0 + 1) * w + x0 + 1) * 4 + k];
      out[o + k] = Math.round((v00 * (1 - tx) + v10 * tx) * (1 - ty) + (v01 * (1 - tx) + v11 * tx) * ty);
    }
    out[o + 3] = 255;
  }
  return out;
}

/* macro/index.json {schema 1, frames: [{t, file}]} -> the two frames around time tMs and the blend weight of the second; null outside the window (before the first frame,
   or more than `slackMs` after the newest one) */
export function macroBracket(index, tMs, slackMs = 15 * 60000) {
  if (!index || index.schema !== 1 || !Array.isArray(index.frames) || !index.frames.length) return null;
  const fr = index.frames.map((f) => ({ ms: Date.parse(f.t), file: f.file })).filter((f) => Number.isFinite(f.ms)).sort((a, b) => a.ms - b.ms);
  if (!fr.length || tMs < fr[0].ms || tMs > fr[fr.length - 1].ms + slackMs) return null;
  if (tMs >= fr[fr.length - 1].ms) return { a: fr[fr.length - 1], b: fr[fr.length - 1], mix: 0 };
  let i = 0; while (i + 1 < fr.length && fr[i + 1].ms <= tMs) i++;
  return { a: fr[i], b: fr[i + 1], mix: (tMs - fr[i].ms) / (fr[i + 1].ms - fr[i].ms) };
}
