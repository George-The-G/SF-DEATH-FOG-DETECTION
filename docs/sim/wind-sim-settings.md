# Wind sim settings that strongly change wind and fog behavior

Recorded 2026-11-15 from the wind investigation and a literature-only research thread. Values are recommendations, not yet validated against stations. Update this file when they are tested or changed.

## Known bug (fixed on local branch `wind-cube-check`, not pushed)
- The old 16-direction library in `library/` was computed with no walls, because Warp loaded a stale kernel cache. All directions affected (near-ground mean ratio 1.006).
- Fix: `wind.run` compiles into a private empty cache folder each run. A 3 s cube regression test guards it.
- The library, tracer bands and anything tuned on the old field need recomputing once the settings below are agreed.

## Settings under test
- Inflow: log-law, u(z) = (u*/0.4) ln((z+z0)/z0), matched to the 10 m reference wind, capped above about 150 to 200 m. The old uniform inflow is likely why the city boundary layer grows too thick.
- Sea floor: free-slip as an interim. No-slip over sea at coarse cells over-damps wind 4 to 8 times (speed ratio 0.37 at 30 m, 0.15 at 3 km in; real sea stays about 0.85 to 0.9). End state is a rough-wall stress condition driven by a z0 map.
- Roughness z0 (m): sea 0.0002, open land 0.03, suburban 0.3 to 1, dense city 1 to 2. Keep z0 at or under about 1 m at 20 m cells. Ground and roofs between resolved buildings: 0.03 to 0.3, to be calibrated against stations.
- Buildings and terrain: no-slip walls (bounce-back), as now.

## Acceptance tests
- Empty sea: profile within about 5% over 10 km.
- 20-cell cube: judge stagnation and side speed-up (about 1.3 to 1.5), not wake length. Solver's low Reynolds number gives about 4 cube-height reversed flow versus about 1.6 published.
- City run, wind from 270 on the 20 m grid: 250 m median ratio near the inflow value.
- Compare with buoy 46026 and SF stations.

## Reference results (walls fixed, no-slip sea)
- Golden Gate Bridge deck 0.30 (old 1.03), Fort Point 0.13, Pier 1 0.07 of inflow. Too sheltered over water.

Reports: `C:\Users\George\AppData\Local\Temp\wt_windcube\spikes\windcube\REPORT.md`. Scratch recompute: `C:\Users\George\deathfog-data\library_walls` (270 only, stopped).
