# Live skate-shuttle buses on the 3-D map: plan (no code written)

Repo: George-The-G/SF-DEATH-FOG-DETECTION, branch v3-dev. Date: 2026-11-13.
What I could and could not check: this container cannot reach 511.org, SFMTA feeds or NextBus (proxy returns 403), so nothing was tested live. Facts below come from reading the official 511.org and sfmta.com pages (via a page-reader) and from the repo. Anything unverified is marked.

## 1. The two districts and the likely shuttle line

The repo (routes.toml) has exactly two districts.

| District | Run (top to bottom) | Likely shuttle | Why |
|---|---|---|---|
| Potrero Hill | 20th & Kansas down Kansas to the bus stop "Rhode Island & 15th" (930 m, 76 m drop) | **19 Polk** | Muni's route text for the 19 goes "Kansas, 23rd, De Haro, 16th, Rhode Island, Division" and it has a stop at Rhode Island & 15th. Active, every 15 min weekdays, 20 min weekends, 5 am to 10 pm. |
| Sunset | Five runs on Quintara/12th-9th Ave, all finishing at 9th Ave & Judah | **66 Quintara** | Its terminal is 9th Ave & Judah, and it runs Lawton, 16th Ave, Quintara. Active, every 20 min, weekdays 6 am to 10 pm, weekends 8 am to 10 pm. |

Not chosen: 10 Townsend (also stops at Rhode Island & 15th but is suspended), 22 Fillmore and 55 Dogpatch (they pass nearby but not on your run), 48 (only low Rhode Island/Kansas, 23rd to 26th). The 33 Ashbury/18th page could not be read, so I have not ruled it out.

Stop IDs: I do not know them and will not guess. They come from Muni's schedule file (stops.txt) once we can download it. Plan is to look up: 19 Polk at Rhode Island & 15th (bottom) and at De Haro near 20th (top); 66 Quintara at 9th Ave & Judah (bottom) and at Quintara near 12th to 16th Ave (top). Please confirm: (a) for Potrero, the skaters ride the 19 from Rhode Island & 15th back up De Haro, and get off near 20th; (b) for Sunset, which Quintara stop is "the top" (the 66 turns at 16th Ave & Quintara, so the nearest stop to your starts at 10th to 14th may be a short walk).

Only the line matters for display: both directions of that route, so we show buses going up and coming back down, tinted by direction.

## 2. Where the data can come from

| Option | Cost | Limits | Verdict |
|---|---|---|---|
| **511 SF Bay API**, GTFS-Realtime VehiclePositions, `api.511.org/transit/vehiclepositions?api_key=KEY&agency=<Muni id>` | Free, key by email form at 511.org/open-data/token | Default 60 requests per hour per key; increases by emailing 511 | Best official source. Update speed is not stated on the pages I read (to test). Muni's agency code (probably "SF") is found with the operators call. |
| SFMTA direct GTFS-RT | n/a | I found none; SFMTA's own page only offers the static schedule download | Not available as far as I can tell |
| NextBus / UmoIQ | Unknown | Old public XML feed status is unverified; SFMTA's 2022 page only says NextBus signs still work | Do not rely on it. Check later as a backup only. |

**Terms of use is the big risk.** 511's Terms of Use (page dated June 2022) say personal, non-commercial use, no automated access, and no "re-distribute, re-publish, re-transmit, display publicly" without prior written authorisation from MTC, which they say covers the developer APIs and can be granted by written agreement. There is also a 2026 Data Agreement PDF that I could not read, and it likely governs the API. So showing live buses publicly is probably allowed only under that agreement. Step zero is for you to get a key and read that agreement (or email transitdata@511.org and ask). Until then, build behind ?beta=1 only.

## 3. Keeping the key secret and the data live

Your site is static (GitHub Pages) and the key is a URL parameter, so the browser can never call 511 directly or the key leaks. Three ways:

1. **Build-time fetch (GitHub Actions)**: the existing scheduled job grabs positions and publishes a small `buses.json`. Simple and key stays in a repo secret, but the job runs about every 30 to 60 minutes, so buses would be badly stale. Fine only for a "typical bus" demo, not live.
2. **Small edge function (recommended)**: a free Cloudflare Worker (or similar) holds the key, fetches 511 for the two lines, caches the answer for ~30 s, and returns a tiny JSON with CORS. 511's 60/hour limit means one fetch per minute max, so the cache is essential: every visitor reads the cache, so 1,000 viewers still cost 60 calls/hour. Needs a free Cloudflare account and one secret; not GitHub-only, which is a change from your "no server" rule.
3. **GitHub Actions on a fast schedule**: not workable; GitHub schedules are minimum 5 minutes and often late, and committing every minute is unwise.

Recommendation: option 2, with option 1 kept as the fallback file ("last known schedule view") if you want to stay GitHub-only. Tell me which you prefer (open question 2).

## 4. Refresh cadence

Worker polls 511 at most once per 60 s (and only when someone asks), caches 45 s. The page asks the Worker every 30 s while the tab is visible and pauses when hidden. Buses move between updates by sliding along the route for up to ~60 s using their speed, then fade.

## 5. How buses draw in the existing 3-D viewer

The viewer already has route polylines in its own metre grid (routes.json: x east, y elevation, z minus north, origin UTM 10N 540100/4171500) and a skater model (skater.js), so I would reuse those.

- **Position**: convert lat/lon to the same grid, then **snap to the nearest point on the shuttle line's polyline** so buses sit on streets instead of drifting 10 m off. For the part of each line beyond your route (the 19 and 66 go well past it), I would draw only buses within ~400 m of the district, and snap to the street ribbon geometry already in ribbons.bin. Height from the terrain, as the skater does.
- **Marker**: a small low-poly bus box (about 12 m long, bright Muni-red or the line colour) that points along the street, plus a line badge "19" or "66" floating above. Heading comes from the feed bearing, else from the snapped segment direction. Simple 3D box so it reads from the flyover height.
- **Stale vehicles**: age = now minus the vehicle's own timestamp. Under 60 s full colour; 60 to 180 s fade to 50% with "last seen"; over 180 s hide. Never invent positions.
- **Performance**: at most a few buses per line (about 2 to 4 each), so 8 or fewer meshes, one shared geometry and material; negligible next to the 286,000 street points.
- **Tap card**: line, direction, "updated 20 s ago", and distance to the top stop.

## 6. When the feed is down

Show nothing fake. A small status chip: "Live buses: unavailable" (grey), buses fade out after 3 minutes, and the rest of the map is untouched. The Worker serves its last good answer for up to 3 minutes, then returns an explicit "stale" flag. If the key is rate-limited (HTTP 429) it backs off and keeps serving cache. Also needed: a quiet-hours message ("no buses running, next service ...") because the 66 stops around 10 pm.

## 7. Where it fits with the sky/fog work

Check done: branch claude/project-thread-gh94on (beta sky and volumetric fog, stages 0 to 2) is **already merged into v3-dev** (commit 3f64515); so there is nothing in flight to conflict with from that branch. Other open branches (ce3s0l, odepek) not inspected for overlap. To keep any future merges clean:
- Put all bus code in a **new file `site/v3/buses.js`**, loaded with a dynamic `import()` only when `?beta=1&buses=1`, exactly how sky.js and fog2.js are loaded.
- Touch `app.js` by only about three lines (the import and the per-frame hook), and avoid beta_ui.js and fog2.js entirely.
- Render buses after fog so they are not hidden by it, but they must write depth normally (fog2 raymarch is depth-aware; test one bus inside thick fog).
- Add the worker as its own folder (`edge/bus-proxy/`), separate from the build workflow.

## 8. Staged rollout (all behind ?beta=1)

0. You: request the 511 key, read the data agreement, confirm line/stop choices (above).
1. Download Muni's schedule file once, find the real stop IDs and confirm 19 and 66 geometry against your routes. Test the live call from a machine that can reach 511 and record the real update rate. (This container cannot.)
2. Edge function plus a fake/recorded feed; viewer shows buses from a replay file (no key needed to test rendering).
3. Real feed behind `?beta=1&buses=1`, a few days of watching for gaps, stale handling and wrong-direction buses.
4. If 511's agreement allows public display: add credit text, then switch on for everyone with a kill switch (a single flag in the worker).

Mockup: skipped. Doing it honestly needs the stop positions, which need the schedule file I cannot download here.

## 9. Open questions for you

1. Are 19 Polk (Potrero) and 66 Quintara (Sunset) the right lines, and which stops are "top" and "bottom" for each (Sunset especially)?
2. Edge function OK (free Cloudflare account, breaks the GitHub-only rule), or must it stay GitHub-only with only a stale/typical-bus view?
3. Will you request the 511 key and read (or ask 511 about) the data agreement for public display? I should not ship this publicly until that is clear.
4. Show both directions of the line, or only buses heading uphill?
5. Is a "next bus in N min" estimate wanted, or just moving buses?


---
# Update 2026-11-13 (afternoon): usage terms read in full, plus the bus-stop shelter

## A. What the 511 usage terms actually allow (read from the Data Disseminator Agreement, 2026, and the Terms of Use)

Good news compared with my first read. There are two documents, and they point different ways:
- **Terms of Use (June 2022)**: personal, non-commercial, non-automated use; no public display or bulk feeds without MTC written authorisation.
- **Data Disseminator Agreement (2026)**: this looks like that written authorisation. You accept it by ticking "I agree" when you request a key, which makes you a "Registered Data Disseminator". It licenses you to "use, sublicense, copy, distribute, and store" the data and to "display (publicly or otherwise)" it to end users. It is aimed at anyone "providing transportation-related information to end users", and nothing says a hobby site is excluded.
- The pages do not say outright that the Agreement overrides the Terms of Use, so one email to 511 should confirm it. I recommend sending it before building, e.g.: "Hobby non-commercial site showing live Muni buses for two lines on a map; we proxy and cache your feed server-side. Is that covered by the Disseminator Agreement?"

What the Agreement requires of us, in plain terms:
- **Credit**: "data provided by 511.org" (or "powered by 511.org") with a link to 511.org, next to where the data is shown. So the popup and bus chip carry that line.
- **Be current**: make a reasonable effort to show the latest data (so no hiding a stale feed; we show "updated N s ago").
- **Do not** sell the data standalone, use the "511" name or logo in our own branding (material breach), or use Muni/SFMTA logos without their permission (so our buses are plain boxes, not Muni logos or the Muni "worm").
- **Proof of use**: send 511 a screenshot or live link within 30 days of launch; 511 may publicise that we use the data.
- **Can end**: either side can end it with 30 days' notice; 511 can start charging with 90 days' notice (currently free).
- The key itself: nothing says whether it may be in browser code, so we keep it server-side regardless.
- A server that caches and re-serves JSON to many visitors is not addressed. It is plausibly covered by the "distribute" and "sublicense" wording, but ask in the same email.

**Options this leaves us** (cheapest first):
1. Sign the Agreement, use the free key, proxy through the Worker with credit shown. Recommended, subject to 511's one-line confirmation.
2. If 511 says no to public display: keep it as a private tool (you only, key in your own browser or local file), or show the static published timetable instead (the SFMTA schedule file is separate and has its own licence page, sfmta.com/reports/gtfs-transit-data; I have not read that licence).
3. Ask 511 for a higher rate limit (see C), which is just an email.

## B. What data we can get (all from 511, same key)

- Vehicle Positions (GTFS-Realtime): where each bus is.
- Trip Updates (GTFS-Realtime): predicted arrival times at each stop. This is what "time till next bus" needs.
- Stop Monitoring (SIRI, JSON): ready-made "next buses at this stop" for one stop. Probably the simplest source for the popup. I could not confirm whether it also carries vehicle positions or how fresh it is.
- Service Alerts (optional: can show "line detoured").
- Muni's agency code and its real-time flag are looked up through the operators call once we have a key; not verified.
- Update frequency is not published, so we measure it in stage 1.

## C. The rate limit is now the main constraint

60 requests per hour per key means one call a minute in total, shared by everything. My first plan (vehicle positions every minute) would use all of it. Revised budget with the Worker, polling only while someone has the page open and caching 60 s:
- Popup arrivals: one Stop Monitoring call per bottom stop (19 Polk at Rhode Island & 15th; 66 Quintara at 9th Ave & Judah), fetched only when a stop is open (or on page load for the tiny "next bus" hint).
- Bus positions: 1 call a minute for Vehicle Positions covering Muni, shared by both lines.
- That is about 60 plus stop calls per hour while busy, over the limit. So: **email 511 for an increase to ~300 an hour (they ask for key, rate, use case, endpoints, justification)**, and until it comes, positions refresh every 90 s and stop times every 2 minutes, shared across all visitors. The Worker also goes idle with no visitors, so overnight use is zero. Two addresses are published for this (transitdata@511.org and a developer Google group); ask either.

## D. The bus-stop shelter (bottom of each run)

**Model.** A small low-poly covered bench built from simple boxes, like skater.js (no outside model files): a flat roof slab on two posts, a back panel, a bench, and a little blue pole sign with the line number. About 3 m wide and 2.4 m tall in real metres, so it reads at flyover height. Make it look slightly bigger than life (about 1.5x) with a soft outline or glow so it is obviously clickable. Placed at the bottom end point of each run (already in routes.json: 15th & Rhode Island for Potrero, 9th & Judah for Sunset), nudged ~3 m to the sidewalk side and rotated to face the street. The real stop's lat/lon from the schedule file replaces my placement later.

**Clicking.** The viewer already has tap and press-and-hold handling (street card), so the shelter is added as a separate pick target with its own bigger invisible hit box (about 10 m, finger-sized on a phone) so it wins over the street under it. Hover shows a pointer cursor and a small pulse, a ring pulses gently when no popup is open so people notice it.

**Popup, pinned and sticky.** A small card (HTML) that is anchored to a point just above the shelter in 3D space and re-positioned every frame, so it moves with the shelter as you orbit and zoom and stays physically attached with a short leader line to the roof. It never closes on its own: not when you tap the map, change time, or fly to another route. Only the small X in its corner closes it. Both stops can be open at once. Contents:
- Line and direction, e.g. "19 Polk: toward the top of Potrero Hill"
- **Next bus: 4 min.  Then: 19 min.** (or "due" under 1 min, "No buses" with the reason if service has ended for the night)
- "Updated 20 s ago" in small type, going amber after 2 minutes, and a grey "Live times unavailable" if the feed is down (never showing a made-up time)
- "Data provided by 511.org" with the link
Countdown ticks down locally every second between updates so it feels live, then corrects at each refresh. Behind terrain or buildings the card stays visible but dims, so it is never lost; off screen it shows a small edge arrow chip. On phones it is narrower, larger text, and the X has a 44 px touch area.

**Predictions:** use the 511 predicted arrival (Trip Updates or Stop Monitoring), not a computed guess. Where only the schedule is available (feed down), the card can show "scheduled" times clearly marked, as an option you can choose to allow.

**Files:** `site/v3/busstop.js` (shelter mesh, picking, popup) next to `buses.js`, both loaded only by `?beta=1&buses=1`; about three added lines in app.js again.

## E. Rollout revisions
Stage 0 now also includes: sending the one-line email to 511 and asking for the rate-limit increase.
Stage 2: build the shelter and the pinned popup first using a fake feed ("4 min, 19 min"), so you can judge how it looks and feels before any key exists. This part does not need 511 at all.

## F. Open questions (updated)
1. Lines and top/bottom stops (as before): 19 Polk and 66 Quintara?
2. Cloudflare Worker OK?
3. Will you request the key (it is a form with your email and ticking the Agreement) and send 511 the one-line email? I will not publish live times publicly until 511 confirms.
4. Popup text: wording, and should "scheduled" fallback times be allowed when live data is down?
5. Which direction should the popup follow: only the uphill bus (the one skaters take back up), as I assumed above?
6. Should I build the shelter and popup on a fake feed first, as soon as you say go?

---
# Update 2026-11-14: closed beta decisions, token handling, mockup

Decisions from George: closed beta so use real-time data of the two specific buses (19 Polk for Potrero, 66 Quintara for Sunset); he has a 511 token; scheduled-time fallback allowed but kept minimal; mockups wanted.

## Token handling (the key is a secret; it must never appear in chat, the repo, the page or logs)
1. **Do not paste the token into this chat, a commit, an issue or a PR.** Anything pasted there should be treated as exposed.
2. Store it once as a **Cloudflare Worker secret** (`wrangler secret put FIVE11_API_KEY`, or the dashboard's "Add secret" which hides the value). The Worker is the only thing that ever holds it.
3. The browser only talks to the Worker URL. The Worker adds `api_key=` server-side, so the token is never in page code, `status.json`, the network tab or the URL a visitor sees.
4. The Worker never echoes the upstream URL, and strips `api_key` from any error text. Logs record status codes only.
5. Nothing in the repo contains it: `.gitignore` covers `.dev.vars` (the local-test file), and I will add a secret-scan check (a CI grep for `api_key=`) so a slip fails the build.
6. **Closed beta access:** the Worker only answers requests from the beta page's origin and only when `?beta=1` was used, plus a hard cap of one upstream fetch per 60 s regardless of visitors, so strangers cannot burn the 60/hour quota.
7. If the token is ever exposed, request a new one at 511.org/open-data/token and replace the secret; nothing else changes.
8. The Worker only fetches the two operators' two lines; it cannot be pointed at other 511 endpoints.

## What the beta shows (minimal)
- Real vehicle positions of 19 Polk and 66 Quintara, only inside each district's area, snapped to the street, uphill and downhill both shown but tinted.
- The shelter and pinned popup from the mockup: next bus and the one after, from 511 predictions. If live times fail: grey "Live times unavailable", with one optional line "Scheduled: 10:42, 11:02" in smaller type, clearly marked scheduled. Nothing else (no maps of the whole line, no alerts).
- Required line: "Data provided by 511.org" with a link, in the popup.
- I would still send 511 the short confirmation email, and the 30-day proof-of-use screenshot, even for a closed beta, since the Agreement asks for it.

## Mockup
Interactive page with the real route geometry for both districts (Potrero 20th & Kansas to 15th & Rhode Island; Sunset 10th Ave run to 9th & Judah). It shows the covered-bench shelter, a pulsing ring so it reads as clickable, a demo bus moving up the street, the popup pinned to the roof with a leader line that follows as you orbit and zoom, and only the X closes it. A button simulates the feed being down. Times are fake. Shelter position is a stand-in until the real stop coordinates are known.

---
# Update 2026-11-14 (later): Cloudflare chosen, minute-by-minute animation

## Request budget (60 calls/hour = 1 per minute)
- 1 call/min is exactly the limit, and I need two kinds of data: positions (VehiclePositions, one call covers all Muni) and predicted arrivals (TripUpdates, or StopMonitoring per stop). Two calls a minute is 120/h.
- Plan: ask 511 for 300/h now (email with key withheld). Until granted: alternate calls, positions every 60 s but arrivals every 3 min from the same Worker cache, which is enough because countdowns tick locally.
- The Worker only calls 511 when a visitor asks and its cache is older than 60 s, so an idle site makes zero calls and 100 viewers cost the same as 1. Cloudflare free tier allows 100,000 Worker requests a day, plenty for a closed beta.
- Browser to Worker: one ~1 KB JSON every 30 s while the tab is visible (paused when hidden or when no popup/bus is in view).

## Smooth simulated buses
Per bus, in the browser, in one dimension: distance along the route line s (metres), not map x/y. That makes the filter tiny.
1. Each minute: snap the reported position to the route line to get s_meas, and correct for the report's age (the vehicle timestamp is usually older than the fetch).
2. Kalman filter with state [s, speed]. Predict step each animation frame (s += v dt, speed drifts toward a typical value); update step when a new report arrives, with measurement noise ~15 m (GPS plus snapping) and process noise tuned so a bus can speed up or brake. Output is smooth speed too, which gives the bus believable pace.
3. Never snap on updates. Corrections blend in over about 3 s, so buses do not jump. If the correction would move the bus backward, it slows instead (no reversing), unless it is more than ~150 m off, then it eases across over 5 s.
4. Stops: a list of stop positions along the route as s values from Muni's stop file. When the filter predicts the bus is approaching a stop with a known dwell (default 20 s, shorter at the end of the line), the speed ramps down, holds for the dwell, then accelerates. A fresh report that shows the bus already past or still far away overrides this. Dwell is a guess, labelled as simulated in the code, never claimed as data.
5. Staleness: if no report for 90 s the bus stops extrapolating and fades; after 3 min it hides.
6. Honest limits: between minutes the bus position is estimated, not measured. It will sometimes be wrong at stops and in traffic, and the popup times always come from 511 predictions, not from the animation.

## Cost / speed
Three to six moving boxes with shared geometry, one 2x2 filter each, per-frame cost is microseconds; no extra network beyond the 30 s fetch. Everything loads only with ?beta=1&buses=1.

## Not yet done
Nothing is built. The coordinator asked for a plan only, so I have not written Worker or viewer code. On your "go" the order is: (1) viewer shelter, popup, buses on a recorded fake feed; (2) Worker with your key as a secret; (3) real feed, filter tuning against a day of recorded positions.
