// node tests_js/test_bustimeline.mjs   (no dependencies)
import assert from 'node:assert/strict';
import { PathTrack } from '../site/v3/busfilter.js';
import { sceneToLL } from '../site/v3/busgeo.js';
import { TimelineTrack, BusReplay } from '../site/v3/bustimeline.js';
const path = new PathTrack([[100, 0, 0], [100, 0, 1000], [100, 0, 2000]]); // a straight street 2 km long, s = z
const T0 = 1.78e9, near = (a, b, e, m) => assert.ok(Math.abs(a - b) <= e, `${m}: ${a} vs ${b}`);
const rep = (tr, t, s, deg) => tr.report(100, s, T0 + t, T0 + t, deg); // a fix on the street at distance s

// 1. a bus at 3 m/s reported every 60 s: positions between reports are on the line, and the answer does not depend on the order of the questions (reverse replay)
{
  const tr = new TimelineTrack(path, 'a'); for (let i = 0; i <= 10; i++) rep(tr, i * 60, 3 * i * 60);
  const fwd = [], bwd = [];
  for (let t = 30; t <= 570; t += 7.5) fwd.push(tr.step(T0 + t).z);
  for (let t = 570; t >= 30; t -= 7.5) bwd.unshift(tr.step(T0 + t).z);
  assert.deepEqual(fwd, bwd, 'same position forward and backward');
  fwd.forEach((z, i) => near(z, 3 * (30 + i * 7.5), 0.5, 'on the line at 3 m/s'));
  assert.equal(tr.step(T0 + 100).est, false, 'between GPS reports is not flagged as an estimate');
  assert.equal(tr.step(T0 - 5), null, 'before the first report: nothing');
}
// 2. a stop: reports at the same place for 2 minutes keep the bus still there and never go backward
{
  const tr = new TimelineTrack(path, 'b', { stopsS: [500] }); [[0, 380], [60, 500], [120, 500], [180, 500], [240, 620]].forEach(([t, s]) => rep(tr, t, s));
  let prev = -1; for (let t = 0; t <= 240; t += 2) { const z = tr.step(T0 + t).z; assert.ok(z >= prev - 1e-6, 'monotone'); prev = z; }
  near(tr.step(T0 + 100).z, 500, 1, 'dwells at the stop'); assert.equal(tr.step(T0 + 100).dwelling, true);
}
// 3. coasting after the last report fades out and stops being drawn
{
  const tr = new TimelineTrack(path, 'c'); rep(tr, 0, 100); rep(tr, 60, 580);
  assert.equal(tr.step(T0 + 100).est, false, 'coasting within 60 s still looks like data'); assert.equal(tr.step(T0 + 100).alpha, 1);
  assert.ok(tr.step(T0 + 130).est, 'coasting longer is an estimate'); assert.ok(tr.step(T0 + 200).alpha < 0.5); assert.equal(tr.step(T0 + 260), null);
}
// 4. a scheduled trip fills a gap in the GPS log, shifted by how late the bus was; GPS that arrives later replaces the estimate
const ll = (s) => { const g = sceneToLL(100, s); return [g.lat, g.lon]; };
const trip = (id, t0, sBase = 0) => ({ id, p: [0, 250, 500, 750, 1000, 1250, 1500].map((s, i) => [T0 + t0 + i * 60, ...ll(s + sBase)]) }); // 250 m a minute, uphill = larger s
{
  const R = new BusReplay(path); R.setSchedule([trip('t1', 0)]);
  assert.equal(R.ghosts.size, 1, 'an unclaimed trip shows as a scheduled (ghost) bus');
  const g = [...R.ghosts.values()][0]; near(g.step(T0 + 90).z, 375, 8, 'ghost follows the schedule'); assert.equal(g.step(T0 + 90).est, true);
  // the real bus runs 120 s late: fixes at the 0 m and 250 m points, then a gap (nobody was watching), then fixes at 1250 and 1500
  const b = R.track('veh1'); rep(b, 120, 0, 180); rep(b, 180, 250, 180); R.sync();
  assert.equal(R.ghosts.size, 0, 'the real bus claims the trip and the ghost goes away'); assert.equal(b.trip.id, 't1');
  assert.ok(b.step(T0 + 400).est, 'after the last report the schedule carries the bus');
  near(b.step(T0 + 120 + 6 * 60).z, 1500, 12, 'estimate is the schedule shifted by the 120 s delay');
}
{
  const R = new BusReplay(path); R.setSchedule([trip('t1', 0)]);
  const b = R.track('veh1'); rep(b, 120, 0, 180); rep(b, 180, 250, 180); rep(b, 600, 1250, 180); rep(b, 660, 1500, 180); R.sync();
  const mid = b.step(T0 + 390); assert.equal(mid.est, true, 'inside the gap the position is an estimate');
  assert.ok(mid.z > 250 && mid.z < 1250, 'and it is between the surrounding reports');
  // now the GPS for the gap arrives (logged late): the estimate is replaced
  for (const [t, s] of [[240, 500], [300, 750], [360, 1000], [420, 1000 + 125]]) rep(b, t, s, 180);
  const after = b.step(T0 + 300); assert.equal(after.est, false, 'real GPS replaced the estimate'); near(after.z, 750, 0.5, 'exact position from the log');
}
// 5. direction: a bus going downhill is not claimed by an uphill trip; arrivals at the bottom stop come from both GPS and schedule
{
  const R = new BusReplay(path); R.setSchedule([trip('up', 0)]);
  const d = R.track('down'); rep(d, 0, 1500, 0); rep(d, 60, 1250, 0); R.sync();
  assert.equal(d.trip, null, 'wrong direction is not matched'); assert.equal(R.ghosts.size, 1);
  const a = R.arrivalsAt(1000, T0); assert.equal(a.length, 1); near(a[0], T0 + 240, 1, 'scheduled arrival at s = 1000 m');
  const u = R.track('upv'); rep(u, 30, 0, 180); rep(u, 90, 250, 180); R.sync();
  assert.equal(u.trip.id, 'up', 'uphill bus claims the uphill trip');
}
console.log('bustimeline tests passed');
