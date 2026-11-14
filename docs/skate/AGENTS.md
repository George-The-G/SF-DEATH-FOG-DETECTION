# Agent brief: skate mode

Read [README.md](README.md) first (what it is, files, rules). Track work in [FEATURES.md](FEATURES.md).

* It is a side feature. Keep skating code in `site/v3/skate.js` and `skate.css`. Keep the hook in `app.js` small.
* `site/` is gitignored: use `git add -f` for new files there.
* Never change wetness, fog, sky, bus or data-build code from a skate task. Skating only reads shared state (`G`, `wetNow`, `terrainH`, building data).
* Test: `node site/v3/tools/skate_test.mjs` (needs only Node). For a browser check, serve `site/v3`, map the three.js CDN to a local copy of three 0.160.0 (the container cannot reach the CDN), and map `/live_beta/` to `/live/`. Software rendering is very slow: wait for the Take control button to enable, use long screenshot timeouts.
* UI changes (buttons, layout) need George's confirmation first. Keep wording plain: George is not a web developer.
* Ship with the normal v3 steps (merge to `v3-dev`, run `collect.yml`, check the run). Mobile browsers cache modules for minutes: say "close and reopen the tab".
* Put new ideas and bugs in FEATURES.md.
