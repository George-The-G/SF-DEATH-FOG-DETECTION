# Plan: viewer UI redesign (live preview first)

Status: BUILT in the ?live=1 preview (commit 1ea8e6d, deployed 2026-11-12), awaiting the user second confirmation. Mockup approved ("Looks good"). Build into the `?live=1` preview only, then show the user and get a second confirmation before anything else ships. Going live (PLAN_LIVE_DATA.md section 6) remains a separate approval.

Reference mockup: `docs/mockups/ui_redesign_v1.html` (open in a browser; every screen is drawn at 390x844 on real captures). Artifact copy: https://claude.ai/artifact/C7Tt6LrV7zUQgpZ1hF7cv7. Route names and numbers in it are examples.

## 0. Ground rules for the implementer
- Branch `v3-dev`, worktree `C:\Users\George\AppData\Local\Temp\wt_v3dev`. No PR to main, never force-push. LF, UTF-8 no BOM.
- All new chrome sits behind a body class `ui2`, set in `app.js` when the URL has `live=1` (also accept `ui=2` for testing on replay data). The page without these parameters must stay pixel-identical to today's (Playwright compare of header and bottom controls, as in the last preview check).
- Keep the existing icons from `icons.js` unchanged (`ICON_SKATE_PLAY`, `ICON_FOG_PLAY` and the pause icons). Only their containers change.
- Keep the existing system font stack. No new fonts, no new libraries.
- Commit trailer as usual. Deploy with `gh workflow run collect.yml` from `C:\Users\George\source\repos\SF-DEATH-FOG-DETECTION-deploy`. Kill only your own processes.

## 1. Design tokens (style.css, scoped to `body.ui2`)
| Token | Value | Use |
|---|---|---|
| `--r-s` | 4px | rail, tags, checkboxes, ramp |
| `--r-c` | 10px | every control: route chip, play/skate buttons, icon buttons, toggles, selects, banners, tooltips |
| `--r-p` | 12px | panels: dock, route list, About sheet (top corners only) |
| `--glass` | rgba(9,15,28,.80) + `backdrop-filter: blur(10px)` | all panels and controls |
| `--glass-hi` | rgba(14,22,40,.92) | About sheet, tooltips |
| `--line` | rgba(140,170,210,.22) | 1px borders |
| `--text` / `--soft` | #e6ecf5 / #9db0c9 | primary / secondary text |
| `--cy` / `--cy-ink` | #5ef2ff / #7fe3f0 | anything pressable, played part of the time rail |
| `--warn` | #f2c230 | data-time tick, "5 m sim" note |
| `--amber` / `--blue` | #ff9f43 / #5aa8ff | delayed / forecast-only notices only |

No `border-radius: 999px` anywhere under `ui2`. Spacing: 8px outer margin, 6px between top controls, 8px between dock rows. Tap targets at least 32px; play buttons at least 44px tall. Clock and all numbers use `font-variant-numeric: tabular-nums`.

Wetness ramp for profiles and numbers: reuse the viewer's existing `RAMP` from `app.js` (the mockup used an approximation).

## 2. Layout targets (acceptance)
Measured with Playwright at 390x844, 3-D share = (dock top - 8) - (top row bottom + 8), divided by 844:
- Idle whole city: at least 74% (mockup 75%; today 61%).
- Route selected, flyover, fog playing: at least 73%.
- With a data notice showing: at least 68%.
- Route list, settings, About are temporary overlays and are exempt.
- Desktop 1440x900: top cluster 440px wide in the top-left; dock 720px wide, centred at the bottom; 3-D at least 85% of window area.
- No horizontal overflow at 360px width.

## 3. Top row (skate routes)
Remove the header's outer frame under `ui2`. The row is two floating controls: route chip (flex 1) and the skate button.

Route chip (`#rbtn`):
- 52px tall with no route; 66px with a route selected. Radius `--r-c`.
- Swatch 11x11 radius 3px (dashed circle outline for Whole city).
- Line 1 (15px, 600): route name, or "Whole city".
- Line 2 (11.5px, soft): existing `routeStats()` text (length, drop, climb), or "Pick a route to fly it".
- Line 3, route selected only (11.5px): "Wet **62** avg · **88** worst · peaks Fri 4 AM". Worst number coloured by the ramp. This replaces the separate route-wetness strip built in commit 33aadc9 (remove that strip under `ui2`).
- Wetness profile: 4px strip along the chip's bottom edge, clipped by the chip radius. Compute from the selected route's ribbon samples in route order (by `s`), split into 24 equal-length bins, mean wetness per bin at the current frame, each bin a hard-stop gradient segment in the ramp colour. Update when the frame changes (throttle to at most every 200 ms while playing).

Skate button (`#flyhdr`): 60px wide, height equal to the chip (52 or 66). Cyan outline and 10% cyan fill. Disabled (no route): opacity .42, `--line` border, soft colour.

Route list (`#rpop`/`#rlist`): panel under the top row (top 66px), radius `--r-p`, padding 6px.
- First item "Whole city" with sub-line "All streets".
- Group headers per district ("Potrero Hill", "Golden Gate Heights"), 11.5px soft.
- Each route row: swatch, name (14px, 550) with the existing stats line under it (kept exactly as today), a 64x4 mini profile (same 24-bin method), and the current mean wetness as a number (13px, 600, ramp colour, right-aligned, 36px column).
- Current selection gets a 8% cyan background.

Flyover running: the top row becomes three controls.
- Progress panel (flex 1, 52px): route name; sub-line "0.33 of 0.78 mi · wet 84 here" (wetness at the skater's position); speed button 38x32 (existing `#flyspeed` behaviour); a 3px cyan progress line along the bottom edge (replaces `#flyprog`).
- Pause (`#flypause`): 60px button with the existing pause icon.
- Exit: 74px button, X icon plus "Exit".

## 4. Bottom dock (fog, weather, time)
One panel `#dock` replaces the three stacked cards (`#weather`, `#legend`, `#timebar`). Left/right/bottom 8px on phone; 720px centred on desktop. Padding 8px 10px 7px, rows separated by 8px.

Row 1 (32px):
- Weather on one line (12.5px): `66° dew 57° · SW 11 mph · ☁47% · rain 0`. Labels and separators in soft colour; small cloud icon before low-cloud %. Ellipsis if it overflows. Values come from the selected hour (live weather.json, or replay series).
- Legend: 52px-wide ramp (5px tall, radius `--r-s`) with "dry" and "icy fog" under it (9.5px). Tapping it opens settings (as `#legend` does today).
- Settings icon button 34x32 (existing slider icon); cyan "on" state while settings are open.
- Remove under `ui2`: `#wxbtn` expand behaviour and `#wxgrid`, the separate weather source line, the full-width legend card.

Row 2 (44px):
- Fog play button (`#play`): 60x44, existing fog-play/pause icons.
- Time track (flex 1), custom-drawn over the existing `<input id="slider">` (keep the input for keyboard and screen readers: same position, opacity 0, on top):
  - Day labels at the top of the track at each local midnight ("Thu", "Fri", "Sat"; drop a label if it would overlap the next), 10px soft, with a 1px left tick.
  - Rail 6px at top 24px, radius `--r-s`. Forecast part: dotted (3px dash, 3px gap, soft 42%). Observed part, from 0 to `observed_until_frame`: solid soft 55%. Played part, from 0 to the thumb: solid cyan.
  - Data-time tick: 2x16px `--warn` at `observed_until_frame`.
  - Thumb: 16x16, radius 5px, white, 3px cyan halo.
  - Remove the "observed" / "forecast" words and the `#livebar` text line under `ui2`.
- Clock (82px, right-aligned): time 19px 650; second line 10.5px "Thu · observed" or "Fri · forecast" (forecast in `--cy-ink`).

Row 3 (14px, 10.5px soft):
- Left: "As of Thu 10:52 · Fort Point, KSFO" for observed hours; "Forecast hour · NWS RRFS model" for forecast hours. While the data is a static snapshot, append " · preview snapshot" (keep the earlier decision: no "updated N min ago" until `scheduled: true`).
- Right: info icon plus "5 m sim · not a forecast" in `--warn`. Tapping either side opens About (replaces the top `#poc` pill, which is hidden under `ui2`).

Notices: the delayed (amber) and forecast-only (blue) notices become slim strips (radius `--r-c`, 12px text, a dot icon) at the top of the dock, inside it. Same triggers and `livetest` parameters as now.

Settings: open upward inside the dock (above row 1) instead of the separate sheet, on phone and desktop. Contents: grab bar; a row with Night select ("Live" plus a "beta" tag first, then the past nights) and Speed segmented control (1x/4x/16x); Layers as a 2x2 grid of toggles (Fog, Buildings, City streets, District streets) 36px tall, radius `--r-c`, checkbox 14px radius `--r-s`; Fog density slider. Reuse the existing inputs (`#night`, `#speed`, `#fogtoggle`, `#bldtoggle`, `#citytoggle`, `#disttoggle`, `#fogdens`); only move and restyle them. Hide `#sheettab` under `ui2`.

## 5. About sheet
Bottom sheet from top 170px (phone), radius `--r-p` on the top corners, `--glass-hi`, dimmed backdrop rgba(3,6,12,.45), grab bar, 34x34 close button top-right.
- Heading "How this works".
- "Live data" section first: one sentence; bullet list (Forecast: NWS RRFS via Open-Meteo; Fog cover: NOAA GOES-18 satellite; Surface: airport reports from KSFO, KOAK and KHAF; Wind: NOAA tide station at Fort Point); accuracy box (yellow border, radius `--r-c`): "Wetness is modelled, not measured, and has not been checked against real wet-road reports yet. Use it as a guide."
- Then "The simulation" with the existing text. In live mode, reword the "replay of nine recent nights" sentence to describe tonight plus two days; keep the replay wording for past nights.

## 6. Order of work
1. Tokens and `ui2` switch; hide old chrome under `ui2`.
2. Dock rows 1-3 and the custom track.
3. Top row: chip lines, profile, skate button, route list rows.
4. Flyover top row.
5. Settings inside the dock; notices.
6. About sheet.
7. Desktop layout.
8. Checks (section 7), deploy to the preview, phone screenshots of: idle, route list, route selected, flyover, fog playing in forecast hours, settings, delayed notice, About, desktop.

## 7. Checks before showing the user
- Layout targets in section 2 measured and reported.
- Page without `live=1` / `ui=2`: header and bottom controls pixel-identical to the current deploy.
- `python -m pytest` passes; no console errors on the preview.
- Keyboard: every control reachable with visible focus; slider still works with arrow keys.
- `prefers-reduced-motion`: no animated transitions on panels.
- Real values only: no example route names from the mockup in the build.

## 8. Not in scope
Observed-line timing bug, first-paint size, rain-term saturation, go-live (PLAN_LIVE_DATA.md section 6). These stay on the open list.

## 9. Build result (2026-11-12)
Built: sections 1-7 (also ?ui=2 on replay data). Measured 3-D share at 390x844: idle 75.5%, route selected 73.8%, flyover 75.5%, fog playing 73.8%, delayed notice 69.0%; desktop 1440x900 about 90.9%. Normal page unchanged; pytest 124 passed; keyboard (slider arrows, Esc closes list/settings/About) checked.
Not built / open: settings-open state is an overlay (45.5%, exempt); real-phone performance unmeasured; observed-line timing, first-paint size, rain saturation, go-live as in section 8.
