/* MOCK bus source, used until the real 511 Worker is connected. It speaks the exact feed contract (busfeed.js): lat/lon positions that
   were recorded a few seconds ago and only change once a minute per bus, and arrival predictions at the bottom stop. So the viewer
   exercises the same path (lat/lon conversion, filter, popup) it will use with real data; swapping to the Worker changes no drawing code.
   Scenario of a day at the stop, one cycle per bus: a bus comes DOWN the skate route's street to the bottom stop, waits there for
   skaters (about 25 s), then drives back UP. Cycles start every `headway` seconds (default 8 min, shorter than a real 15-20 min so
   there is usually something to see). Arrival predictions are what a real predictor would say: the true arrival plus some error.
   Scenarios (?busmock=name, or the small selector in the page):
     normal  as above            down   the source throws (network failure)     notok  source answers ok:false
     stale   frozen positions and old times, flagged stale                      empty  no buses, no predictions (service ended)
     jumpy   35 m GPS error and dropped reports (stress test for the filter) */
import { sceneToLL } from './busgeo.js';

export const SCENARIOS = ['normal', 'jumpy', 'stale', 'empty', 'notok', 'down'];
const hash = (s) => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0) / 4294967296; };
const gauss = (key) => Math.sqrt(-2 * Math.log(hash(key + 'a') + 1e-9)) * Math.cos(2 * Math.PI * hash(key + 'b'));

/* districts: [{ id, line, poly: [[x,y,z]...] top to bottom in scene metres, headway?: seconds, offset?: seconds }] */
export function createMockFeed({ districts, scenario = 'normal', now = () => Date.now() / 1000 }) {
  const D = districts.map((d) => {
    const P = d.poly, cum = [0]; for (let i = 1; i < P.length; i++) cum.push(cum[i - 1] + Math.hypot(P[i][0] - P[i - 1][0], P[i][2] - P[i - 1][2]));
    const L = cum[cum.length - 1], vDown = 6.5, vUp = 5, dwell = 25;
    return { id: d.id, line: d.line, P, cum, L, tDown: L / vDown, dwell, tUp: L / vUp, H: d.headway || 480, off: d.offset || 0 };
  });
  const at = (o, dist) => { // position and heading at arclength dist from the top
    const { P, cum } = o; dist = Math.min(o.L, Math.max(0, dist)); let i = 1; while (i < cum.length - 1 && cum[i] < dist) i++;
    const f = (dist - cum[i - 1]) / ((cum[i] - cum[i - 1]) || 1), dx = P[i][0] - P[i - 1][0], dz = P[i][2] - P[i - 1][2];
    return { x: P[i - 1][0] + dx * f, z: P[i - 1][2] + dz * f, dx, dz };
  };
  // where is the bus of the cycle that started at s, at time t? null if not on the street
  const pos = (o, s, t) => {
    const u = t - s; if (u < 0) return null;
    if (u < o.tDown) { const d = o.L * (u / o.tDown); const p = at(o, d); return { ...p, dir: 1 }; }
    if (u < o.tDown + o.dwell) { const p = at(o, o.L); return { ...p, dir: 1 }; }
    const u2 = u - o.tDown - o.dwell; if (u2 > o.tUp) return null;
    const p = at(o, o.L * (1 - u2 / o.tUp)); return { ...p, dir: -1 };
  };
  const out = { scenario, name: 'mock', setScenario(s) { this.scenario = s; }, SCENARIOS };
  out.get = async () => {
    const sc = out.scenario, t = now();
    if (sc === 'down') throw new Error('mock: network down');
    if (sc === 'notok') return { ok: false, ts: t, stale: false, vehicles: [], arrivals: {} };
    const vehicles = [], arrivals = {};
    for (const o of D) {
      const k1 = Math.floor((t - o.off) / o.H), nexts = [];
      for (let k = k1 - 1; k <= k1 + 3; k++) { // cycles that may be on the street or coming
        const s = k * o.H + o.off, id = o.id + '-' + k;
        const noise = sc === 'jumpy' ? 35 : 6;
        const rep = (v) => { // reports arrive on a per-bus once-a-minute rhythm, recorded 3 to 10 s before they are fetched
          const ph = hash(id) * 60, tr = Math.floor((t - 4 - ph) / 60) * 60 + ph;
          if (sc === 'stale') return Math.min(tr, t - 400);
          return tr;
        };
        const tr = rep(), p = pos(o, s, tr);
        if (p && sc !== 'empty') {
          if (!(sc === 'jumpy' && hash(id + Math.floor(tr / 60)) < 0.2)) { // jumpy: one report in five is missing
            const q = sceneToLL(p.x + gauss(id + tr + 'x') * noise, p.z + gauss(id + tr + 'z') * noise);
            vehicles.push({ id, district: o.id, line: o.line, lat: q.lat, lon: q.lon, bearing: Math.round((Math.atan2(p.dir * p.dx, -p.dir * p.dz) * 180 / Math.PI + 360) % 360), t: tr });
          }
        }
        const arr = s + o.tDown + (sc === 'stale' ? 0 : gauss(id + Math.floor(t / 60) + 'e') * 15); // prediction = truth + error
        if (arr > t - 15) nexts.push(arr);
      }
      arrivals[o.id] = { t: sc === 'stale' ? t - 400 : t, next: sc === 'empty' ? [] : nexts.sort((a, b) => a - b).slice(0, 3) };
    }
    return { ok: true, ts: t, stale: sc === 'stale', vehicles, arrivals };
  };
  return out;
}
