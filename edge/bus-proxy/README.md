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

## First run: check the real answer
Open the Worker address plus `/api/buses` from the beta page's origin only (other origins get "forbidden" by design). Because the 511 field names here come from 511's documentation and could not be tested from our build environment, if `vehicles` stays empty ask for a check: the fix is in `parseVehicles` / `parseArrivals` in `worker.js` (about 10 lines).

## Safety
- The key exists only as the Worker secret `API_KEY_511`. It is not in the code, config, logs, error messages or responses (a test checks this).
- Only `https://george-the-g.github.io` (set in `ALLOWED_ORIGINS`) may call it.
- 511 is called only for VehicleMonitoring and StopMonitoring of the configured lines/stops. Cache: vehicles 60 s, arrivals 180 s, so 100 viewers cost the same as 1. If 511 fails the last good answer is served for up to 3 more minutes, then `ok:false` and the page shows "unavailable".
- Leaked key? Request a new one at 511.org/open-data/token and run step 3 again.
- 511's rules: show "Data provided by 511.org" next to the data (the popup does), send 511 a screenshot within 30 days of launch, and ask 511 for a higher rate limit (default 60 calls/hour).

## Tests
`node tests_js/test_worker.mjs` (mocked 511, no network) and `node tests_js/test_bus.mjs`.
