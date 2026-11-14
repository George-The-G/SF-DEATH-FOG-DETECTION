# Bus proxy (Cloudflare Worker)

A tiny always-on program that sits between the beta map page and 511. It holds your 511 key as a secret, asks 511 for the two shuttle lines
(19 Polk, 66 Quintara) at most about once a minute, saves the answer, and hands it to the page. The page never sees the key.

## One-time setup (about 15 minutes)
1. Make a free Cloudflare account at cloudflare.com (no payment card needed for the free Workers plan).
2. On your computer, in this repo's `edge/bus-proxy` folder: `npx wrangler login`, then `npx wrangler deploy`. It prints the Worker's address (`https://bus-proxy.<you>.workers.dev`).
3. Store the key: `npx wrangler secret put API_KEY_511`. Paste the key when asked (it is hidden). Do not paste it into chat, a file, a commit or an issue.
4. Find the stop codes of the two bottom stops: `python3 find_stops.py "Rhode Island" "15th"` and `python3 find_stops.py "Judah" "9th"` (key typed hidden). Pick the stop on the side where the shuttle (19 or 66) picks skaters up for the uphill ride. Put the codes in `wrangler.toml` under `DISTRICTS`, and redeploy.
5. In the repo, set `"api"` in `site/v3/data/bus_config.json` to `https://bus-proxy.<you>.workers.dev/api/buses` (this is not secret).
6. Open the beta page with `?beta=1&buses=1`. The banner "DEMO data" disappears and the popup says "Live".

## Setup check
Open `https://bus-proxy.<you>.workers.dev/api/health` in any browser tab. It shows whether the secret is set, whether 511 answered (`upstream: ok` or `511 HTTP 401`), how many vehicles 511 returned, the line ids it uses and how many matched ours. No key and no positions are shown.

## First run: check the real answer
Open the Worker address plus `/api/buses` from the beta page's origin only (other origins get "forbidden" by design). Because the 511 field names here come from 511's documentation and could not be tested from our build environment, if `vehicles` stays empty ask for a check: the fix is in `parseVehicles` / `parseArrivals` in `worker.js` (about 10 lines).

## Mock now, real later (no design change)
The viewer only reads one JSON shape, the feed contract documented at the top of `site/v3/busfeed.js`. Two things produce it:
- `site/v3/busmock.js` (used while `"api"` in `bus_config.json` is empty): a simulated bus day with the same fields as the real feed.
  Try it with `?beta=1&buses=1`; a small "Mock buses" selector (bottom left) switches scenarios: normal, jumpy (bad GPS), stale, empty (no service), notok, down.
- This Worker, once `"api"` points at it. `validateFeed` checks both, and a test runs the Worker's output through the same check.
So going live is one line in `bus_config.json` (`"api"`), nothing else changes. `?busmock=normal` forces the mock even when `"api"` is set, for comparison.

## Safety
- The key exists only as the Worker secret `API_KEY_511`. It is not in the code, config, logs, error messages or responses (a test checks this).
- Only `https://george-the-g.github.io` (set in `ALLOWED_ORIGINS`) may call it.
- 511 is called only for VehicleMonitoring and StopMonitoring of the configured lines/stops. Cache: vehicles 60 s, arrivals 180 s, so 100 viewers cost the same as 1. If 511 fails the last good answer is served for up to 3 more minutes, then `ok:false` and the page shows "unavailable".
- Leaked key? Request a new one at 511.org/open-data/token and run step 3 again.
- 511's rules: show "Data provided by 511.org" next to the data (the popup does), send 511 a screenshot within 30 days of launch, and ask 511 for a higher rate limit (default 60 calls/hour).

## Tests
`node tests_js/test_worker.mjs` (mocked 511, no network) and `node tests_js/test_bus.mjs`.

## GPS log and reverse replay (beta, optional)
The beta page (`?beta=1&buses=1`) lets you play the simulation clock backward and scrub, and buses move back along where they really were. That needs a log of every GPS report, kept by this Worker in a free Cloudflare **D1** database. Without it the page still works: it logs the reports it sees while open and fills the rest from the schedule.
1. `npx wrangler d1 create bus-log` (prints a `database_id`), then `npx wrangler d1 execute bus-log --remote --file=schema.sql`.
2. In `wrangler.toml`, uncomment the `[[d1_databases]]` block and paste the id. `npx wrangler deploy`.
3. The Worker now stores each new 511 position once (key = vehicle id + recorded time) and `GET /api/history?line=19&since=<epoch>&bbox=lat0,lon0,lat1,lon1` returns the last 78 h as `{vehicles:[{id, f:[[t,lat,lon,bearing],...]}]}`. Same origin rule as `/api/buses`. Rows older than `LOG_DAYS` (default 7) are deleted once an hour.
4. Gaps: the Worker calls 511 only when someone has the page open (cached 60 s), so quiet hours have no GPS and the page uses the schedule there. For a gapless log, set `LOG_CRON = "1"` and uncomment the `[triggers]` cron. Each run is one 511 call, so `*/2` uses 30 of your 60 calls per hour; the page's own requests reuse the cache. Ask 511 for a higher limit first if you want `* * * * *`.

**Size and limits (estimates until we see real 511 data).** Unfiltered, the two lines have roughly 15 to 20 buses reporting about once a minute: around 25,000 rows a day, about 1.5 MB a day, so about 10 MB for 7 days (D1 free tier: 5 GB storage, 100,000 row writes a day; we use about a quarter of the writes). The page asks only for a box around each district, so one 78 h download is on the order of a few hundred KB compressed. GitHub alone cannot do this: Actions runs at best every 5 minutes (often late), so a per-minute log needs the Worker.

## Scheduled trips (estimates for the future and for gaps)
`python3 tools/make_bus_schedule.py --gtfs <Muni GTFS zip or URL>` writes `site/v3/data/bus_schedule.json` (scheduled passing times near each district, 14 days by default, a few hundred KB). Commit it to refresh. The page loads it if present; without it buses are GPS only.
