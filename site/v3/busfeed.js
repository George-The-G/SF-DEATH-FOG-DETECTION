/* THE FEED CONTRACT. Everything that supplies buses to the viewer (the mock in busmock.js today, the Cloudflare Worker that wraps the
   511 API later) returns exactly this JSON. buses.js only ever reads this shape, so swapping the source changes no drawing code.

   {
     "ok": true,                      // false = the source has nothing usable; the page shows "unavailable"
     "ts": 1783000000.0,              // epoch seconds when the source produced this
     "stale": false,                  // true if the source is serving an older saved copy
     "vehicles": [                    // only buses on the shuttle lines; may be empty
       { "id": "7001",                // stable vehicle id
         "district": "potrero",       // which district (preferred) ...
         "line": "19",                // ... or the Muni line number
         "lat": 37.7675, "lon": -122.4030,
         "bearing": 181,              // degrees, optional
         "t": 1782999980.0 }          // epoch seconds the position was RECORDED (not fetched)
     ],
     "arrivals": {                    // predicted arrivals at each district's bottom stop
       "potrero": { "t": 1783000000.0, "next": [1783000240.0, 1783000840.0] }   // epoch seconds, ascending
     }
   }
*/
export function validateFeed(f) {
  const bad = (m) => { throw new Error('feed contract: ' + m); };
  if (!f || typeof f !== 'object') bad('not an object');
  if (typeof f.ok !== 'boolean') bad('ok must be boolean');
  if (typeof f.ts !== 'number') bad('ts must be a number');
  if (!Array.isArray(f.vehicles)) bad('vehicles must be an array');
  for (const v of f.vehicles) {
    if (v.id == null) bad('vehicle without id');
    if (!(typeof v.district === 'string' || v.line != null)) bad('vehicle needs district or line');
    if (!(v.lat > 36 && v.lat < 39 && v.lon > -124 && v.lon < -121)) bad('vehicle lat/lon outside the Bay Area: ' + v.lat + ',' + v.lon);
    if (typeof v.t !== 'number') bad('vehicle t must be a number');
  }
  if (typeof f.arrivals !== 'object' || f.arrivals === null) bad('arrivals must be an object');
  for (const [k, a] of Object.entries(f.arrivals)) {
    if (!Array.isArray(a.next) || a.next.some((t) => typeof t !== 'number')) bad('arrivals.' + k + '.next must be numbers');
    if (a.next.some((t, i) => i && t < a.next[i - 1])) bad('arrivals.' + k + '.next must ascend');
    if (typeof a.t !== 'number') bad('arrivals.' + k + '.t must be a number');
  }
  return f;
}
/* a source is anything with get(nowSeconds) -> Promise<feed>. Real: the Worker over HTTP. Mock: busmock.js. */
export function httpSource(url, getJSON) { return { name: 'http', get: async () => validateFeed(await getJSON(url, { cache: 'no-store' })) }; }
