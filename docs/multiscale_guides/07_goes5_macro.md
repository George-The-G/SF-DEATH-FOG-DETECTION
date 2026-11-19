# Guide 07: 5-minute GOES crops and macro cloud frames (switch `goes5`)

Read `00_README.md` first, then `replay/goes-crop-format.md` (the storage contract, binding) and `replay/goes-anchor-plan.md` sections 3.1-3.2. Independent of guides 01-06. Action side, plus one small JSON and PNG output. No viewer work here.

## Goal
Ingest every GOES-18 scan (every 5 minutes) instead of one per hour, keep a rolling 48 h of small crops, and publish wide-area cloud frames (about 123 x 122 km) for the macro view. Today's hourly use of GOES must not change.

## Background (from the code)
- `live/goes.py`: `nearest_keys(t)` finds the ACM and ACHA scan nearest an hour; `download` fetches whole files (ACM about 3.8 MB, ACHA about 0.34 MB); `LowCloud` crops box `[-123.3, 37.2, -121.9, 38.3]` and flags low cloud (`ht < LOW_TOP_M`).
- State between runs lives in files the job carries over (see how `dsr_cache.json` and `state_*.npz` are carried in `live_ci/refresh.sh`).
- The cron is every 10 minutes with a 10-minute job timeout; runs can start late.

## Steps

1. **Switch** `C.GOES5 = False`, `BETA["goes5"] = True` after the gate. Off: no new downloads, no new files.
2. **Crop store** exactly as `goes-crop-format.md` (schema 1): `grid.json` once; one `npz` per UTC hour with `t_scan`, `acm` (uint8, 254 bad quality, 255 missing), `ht` (uint16 metres, 0 = none), `src`. New module `microclimate/live/goes5.py`:
   - `list_scans(t_from, t_to)`: list keys of both products for the hours touched (reuse `_list_keys`, `_scan_start`), pair ACM and ACHA by scan start (same start within 60 s).
   - `ingest(store_dir, now, deadline_s)`: scans newer than the newest stored `t_scan`, oldest first, at most `GOES5_MAX_SCANS_PER_RUN = 6`, stopping at `GOES5_DEADLINE_S = 60`. On a cold start take only the last 60 minutes. Each scan: download both files to the existing cache, crop with the same index math as `LowCloud.__init__` (factor that math into a shared function so both use one implementation; `LowCloud` output must stay bit-identical), append, write the hour file atomically (write to a temp name, then rename).
   - Idempotent merge by `t_scan`; delete hour files older than 48 h; delete the raw downloads of this guide right after cropping (the hourly path keeps its own cache).
   - Read ACM quality: if the file has a `DQF` variable, set `acm = 254` where it flags bad. Check the variable name in a real file first and report what you found.
   - Any failure is logged in `status.json` and never fails the build.
3. **Where the store lives.** Carry `state/goes5/` between runs the same way the other state is carried. Check its size against that mechanism (about 2 MB for 48 h, estimate) and report the real size.
4. **One derivation function** `goes.low_cloud(crop, i, version=1) -> (p, c, top_m)` as specified in `goes-crop-format.md`: probability `p` from the class and height, confidence `c` (0 for missing or bad, 0 where high cloud covers the neighbourhood, 0 inside the terminator window of sun elevation -6..+6 degrees, 1 by day, 0.7 by night), and the height. Sun elevation from the existing sun code (`microclimate/sun.py`). Return the rule version. Parallax is applied when sampling (step 5), never stored.
5. **Parallax when sampling.** For a pixel with cloud top `h` (use `ht` when valid, else the fused fog top, else 300 m), the true ground position is shifted toward the satellite by `h * tan(zenith)`. GOES-18 is at longitude -137.0 (read `longitude_of_projection_origin` from `grid.json`, do not hard-code). Compute the view zenith and azimuth at the crop centre once (spherical geometry is enough at this size; put the formula in a docstring and test it against one published value: zenith about 46 degrees at San Francisco, estimate to be confirmed by the computation). Shift in metres, converted to degrees of lon/lat.
6. **Macro frames.** Regular lon/lat grid over the box at 0.01 degrees (141 x 111). For each stored scan in the last 6 h: sample `p`, `c`, `top_m` with parallax, nearest-neighbour. Write `macro/<yyyymmddThhmm>.png`, RGBA 8-bit: R = `255 p`, G = `255 c`, B = `min(top_m / 8, 255)`, A = 255. Write `macro/index.json`: `{"schema": 1, "box": [...], "w": 141, "h": 111, "row0": "north", "rule_version": 1, "frames": [{"t": iso, "file": name}, ...]}`. Only frames whose file content changed are rewritten; frames older than 6 h are deleted from the output. These are RGBA PNGs with data in the colour channels: write them with Pillow, no palette, no optimisation that alters values; a test must read one back and compare bytes.
7. **Leave the hourly path alone.** `goes.hourly`, `cloud_score` and `fog_frames` keep using nearest-hour scans. Guide 08 is where 5-minute data starts to drive the fog.

## Stage 0 check (do this before step 6, it decides how much guide 08 is worth)
With the store filled for two foggy days (on the PC, pull past scans; the AWS archive holds past dates), compute per 5-minute step the change in low-cloud probability over the city box and the motion of the coast-side fog edge (position along a west-east line through Ocean Beach where `p` crosses 0.5). Report: how far the edge moves per hour, how often it moves more than one pixel (2 km) inside an hour, and a small picture strip of 12 consecutive frames. Compare with the model's hourly tiles for the same hours. If the satellite shows no sub-hourly change the model misses, say so plainly.

## Tests (`tests_mc/test_goes5.py`, no network: build tiny fake netCDF files with h5py like `tests_mc/test_goes_box.py` does)
- Pairing by scan start; unpaired scans are skipped.
- Idempotent merge; 48 h pruning; atomic write leaves no partial file when an exception is raised mid-write.
- The shared crop function gives the same indices as the old `LowCloud` code on the fixture.
- `low_cloud`: class and height table; confidence 0 at the terminator, for 254 and 255, and under high cloud; 0.7 at night.
- Parallax: a 500 m top shifts the sample by the expected distance and toward the south-west; a 0 m top does not shift.
- Macro PNG round trip is exact.
- Switch off: no `macro/` folder, no `goes5` state, download count unchanged (mock the fetch and count calls).

## Gate
- Added run time under 60 s typical, never past the deadline; store size reported.
- 24 h on the beta channel without a failed build and with at most 2 missing scans per hour on average (report the gaps).
- The Stage 0 numbers and picture strip sent to George.

## Pitfalls
- ACM `y` decreases with row; `LowCloud` handles this, reuse it.
- Scan start in the key is `sYYYYDDDHHMMSSs` (day of year); `_scan_start` parses it.
- Do not read whole files into the store or commit them anywhere.
- GitHub may run two jobs close together after a delay: the idempotent merge covers it, and the `concurrency` group in `collect.yml` already prevents overlap.
