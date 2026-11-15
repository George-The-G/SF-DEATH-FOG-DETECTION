// Guards two deploy failures: (1) every relative import of buses.js must be a git-tracked file (site/ is gitignored, so new files need git add -f);
// (2) the committed bus_schedule.json must yield scheduled buses on both district routes (stops sit up to ~100 m off the skate polyline).
import fs from 'fs'; import { execSync } from 'child_process'; import assert from 'assert';
import { BusReplay } from '../site/v3/bustimeline.js'; import { PathTrack } from '../site/v3/busfilter.js';
const tracked = new Set(execSync('git ls-files site/v3', { encoding: 'utf8' }).split('\n'));
const seen = new Set(), walk = (f) => { if (seen.has(f)) return; seen.add(f); assert(tracked.has('site/v3/' + f), 'not tracked: ' + f);
  for (const m of fs.readFileSync('site/v3/' + f, 'utf8').matchAll(/from '\.\/([\w.]+\.js)'/g)) walk(m[1]); };
walk('buses.js');
const rj = JSON.parse(fs.readFileSync('site/v3/data/routes.json')), cfg = JSON.parse(fs.readFileSync('site/v3/data/bus_config.json'));
const sch = JSON.parse(fs.readFileSync('site/v3/data/bus_schedule.json'));
for (const d of cfg.districts) { const R = rj.routes.find((r) => r.id === d.route), rp = new BusReplay(new PathTrack(R.poly)); rp.setSchedule(sch.lines[d.line].trips);
  assert(rp.ghosts.size > 0.9 * sch.lines[d.line].trips.length, 'line ' + d.line + ': only ' + rp.ghosts.size + ' scheduled buses on the route'); }
console.log('busdeploy tests passed');
