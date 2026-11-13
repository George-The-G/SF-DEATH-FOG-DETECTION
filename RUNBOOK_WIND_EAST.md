# Runbook: east wind directions (babysitter)

Goal: compute six more wind directions on the GPU, ONE AT A TIME, in this order: **22.5, 45, 67.5, 112.5, 135, 157.5**.
(90 is already done and validated; 0 is the same as 360 and exists.) Each direction = wind field + 4 fog bands, then a validation.

Paths (PowerShell, every command starts with these two lines):
```powershell
$WT='C:\Users\George\AppData\Local\Temp\wt_winde'; $PY='C:\Users\George\source\repos\SF-DEATH-FOG-DETECTION\.venv\Scripts\python.exe'
$RUNS='C:\Users\George\deathfog-data\library\runs'; cd $WT
```
File code for the direction: 22.5 -> `0225`, 45 -> `0450`, 67.5 -> `0675`, 112.5 -> `1125`, 135 -> `1350`, 157.5 -> `1575`.

## 1. Before starting a direction
```powershell
& $PY -m microclimate winddir --status          # last line must read: gpu.lock: free
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader
```
Start only if `gpu.lock: free` AND memory.used < 2000 MiB (the desktop uses about 500 MiB). Otherwise do not start; wait 5 minutes and look again;
after 30 minutes of "busy" report it. Never start two directions at once (the runner also refuses: exit code 3).

## 2. Start one direction in the background (example 22.5)
```powershell
Start-Process -FilePath $PY -ArgumentList '-m','microclimate','winddir','--dir','22.5' -WorkingDirectory $WT -WindowStyle Hidden -RedirectStandardOutput "$RUNS\dir_0225.out" -RedirectStandardError "$RUNS\dir_0225.err"
```
Then wait 60 s and run the status command (section 3): it must say `state=running`.

## 3. See progress (look every 5 minutes, not more often)
```powershell
& $PY -m microclimate winddir --status --dir 22.5     # state, start, pid, last 3 log lines, lock
Get-Content "$RUNS\dir_0225.log" -Tail 5              # the log
```
Timeline of one run: about 5 min setup (no step lines yet, normal), then `step N/40000` lines every 2000 steps, then the wind field is
written, then `fog band 0..3 written` lines, then `=== DONE`. Status file: `$RUNS\dir_0225.json`.

## 4. How long
Test run, 90 (36.6 M cells): 788 s wall (13 min), GPU memory peak 8.4 GB. The six remaining frames are bigger (oblique, 50-60 M cells):
expect **25-30 min each** (earlier rotated runs of this kind: 1400-1500 s) and up to **13-14 GB** GPU memory. Whole set: about 3 hours.
Worry only if `step` lines stop advancing for 15 minutes or the process is gone.

## 5. Done looks like
Status says `state=done`, the log's last line is `=== DONE direction ...`, and these exist in `C:\Users\George\deathfog-data\library`:
`wind_<code>.npz/.json`, `fog_<code>_0..3.npz/.json`. Then validate (about 1-2 min, run it BEFORE starting the next direction):
```powershell
& $PY -m microclimate winddir --dir 22.5 --validate     # last line: VALIDATION direction 22.5: PASS (or FAIL)
```
PASS = all checks a-h pass. On FAIL: do not re-run the solver; copy the line, `& $PY -c "import json;print(json.load(open(r'$RUNS\dir_0225.json'))['validation'])"` and the log tail into your report, then continue with the next direction.

## 6. Failed looks like
`state=failed`, or `state=running` with the note `(process gone: interrupted ...)`, or exit with `=== FAILED`. Look at `Get-Content "$RUNS\dir_0225.log" -Tail 30` and `$RUNS\dir_0225.err`.
- First failure: run the SAME start command (section 2) once more. It cleans up and restarts by itself (a finished wind field and finished fog bands are reused; an unfinished wind run restarts from step 0: the solver has no checkpoints).
- Second failure of the same direction: STOP. Do not start anything else. Report the direction, both log tails and the .err file.
- "GPU lock held by running pid N" (exit 3) means a run is active: do not start anything, just keep watching it.
- If the log says `NaN or |u| > 0.5`, that is a numerical blow-up: the re-run will probably repeat it; follow the same rule (one re-run, then stop and report).

## 7. Pause / stop safely
To pause BETWEEN directions simply do not start the next one. Never kill a running solver unless the coordinator tells you to (then: `Stop-Process -Id <pid from status>`; the next start recovers by itself).
Do not delete `gpu.lock`, `.part` or `.tmp` files by hand; the runner handles stale ones.

## 8. After each direction, report one line
direction, state, wall seconds (`dir_<code>.json` -> `wall_seconds`), GPU peak (`gpu_mem_peak_mb`), validation PASS/FAIL, free disk (`Get-PSDrive C`).
Each direction adds about 175 MB (wind about 90 MB + fog about 90 MB + png); 137 GB were free before the six.

## 9. Do not touch
- Worktree `C:\Users\George\AppData\Local\Temp\wt_v3dev` (live pipeline, other agent) and the main checkout `C:\Users\George\source\repos\SF-DEATH-FOG-DETECTION` (only its `.venv\Scripts\python.exe` is used).
- Any existing file in `library\` (wind_/fog_ for 180-360, `wind_dirs.json`, `live_static.npz`, `*.bak.*`, `*.unstable.*`). The runner refuses to overwrite them.
- Do not run `winddirs` (plural), do not edit code, do not install packages, do not use `--force`.
- Do not run other GPU work while a direction runs.
- Do not wire the new fields into the live pipeline (section 10 is a later step).

## 10. Note for the coordinator: wiring 16 directions into the live pipeline (NOT part of this run)
After all seven new directions (22.5, 45, 67.5, 90, 112.5, 135, 157.5) are done and validated:
1. Manifest: `validate --dir` writes `library\wind_dirs_east.json`, not `wind_dirs.json` (which `dirlib.available` reads). Merge the validated list into `wind_dirs.json` (new file, do not overwrite the 270-360 entries); `available()` then returns 16 directions (22.5 ... 360, 270 always) when `wind_*.npz` and `fog_*_0..3.npz` exist.
2. `dirlib.weights` has no wrap-around: wind from 0-22.5 deg would be served by the 22.5 field only and 337.5-360 by 360 only, with no blend across north. Add a wrap (treat 360 as 0 at the low end, or add a 360+22.5 pair) and revisit `MAX_GAP_DEG`/`gap_deg` (still 22.5 spacing, so unchanged in value) plus the tests `tests_mc/test_dirlib.py`.
3. Rebuild `live_static.npz` with `python -m microclimate city static` (city.build_static reads `dirlib.available(lib)`): the `tracer` table grows from [9, N] to [16, N] and `dirs` to 16 entries; `live_extra.npz` is unaffected. Check also the places that index by direction: `microclimate/live/build.py` (`S["dirs"]`, station wind loader `_stations.load_station_wind(..., S["dirs"])`), `live/compare.py`, `wetness.simulate(tracer_dirs, dirs_deg)` and `wetness.onshore_weight` (it gives zero fog weight to offshore directions, so the new east fields may change nothing for fog; they matter for the wind ratio / residence of the street, check that the wind part of the table is also direction-aware, which currently is only the tracer).
4. Release upload: `live_static.npz` is fetched by `refresh.sh` from the release (size grows by about 7/9); upload the new asset, keep the old one until the live build has run green once with the new file.
5. Tests: `tests_mc/test_dirlib.py`, `test_live_core.py` (direction counts), replay byte-identity (`DIR_LIBRARY` default OFF), then a live `build` run and `compare --anchors` before and after, per the existing live check.
6. Known limit of the east fields: the grid ends in the middle of the Bay (east edge x = 557.2 km, all water, terrain 0 m). For an east wind the inflow is clean open water: Oakland / the East Bay hills upwind are NOT represented (filler is sea level), so the east fields have no East-Bay-hills wake. Real east winds (offshore) cross the East Bay land first.
