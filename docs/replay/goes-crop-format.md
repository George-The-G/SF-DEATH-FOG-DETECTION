# Shared GOES-18 5-minute crop format (schema 1)

Owner: replay thread (ingest, layout, 48 h store). Readers: replay (predict-correct) and validation (scoring). Draft 2026-11-14; changes bump `schema`.

## Rule: store raw, derive in one place
The crop store keeps **raw ACM classes and ACHA heights only**. Everything derived (low-cloud probability p, confidence c, parallax shift) comes from ONE function, `microclimate/live/goes.py::low_cloud(crop, t, version=1)`, which both replay and scoring import. Then the two can never disagree on what "satellite fog" means, and a better rule later does not invalidate stored data.

## Layout (data branch, rolling 48 h)
- `state/goes5/grid.json`, written once: `schema`, satellite `goes18`, product keys (`ABI-L2-ACMC`, `ABI-L2-ACHAC`), lon/lat box `[-123.3, 37.2, -121.9, 38.3]` (the box `goes.LowCloud` already uses), crop row/col offsets in the ACM 2 km CONUS fixed grid, shape `[H, W]`, fixed-grid x/y (radians) of the crop axes, and satellite longitude/height for parallax.
- `state/goes5/<YYYY-MM-DD>/<HH>.npz`, one file per UTC hour holding up to 12 scans:
  - `t_scan` int64 [n]: scan start, unix seconds UTC
  - `acm` uint8 [n, H, W]: ACM class 0 clear, 1 probably clear, 2 probably cloudy, 3 cloudy; 254 = retrieved but flagged bad quality (ACM DQF), 255 = missing / outside scan
  - `ht` uint16 [n, H, W]: ACHA cloud-top height in metres, nearest-resampled from 10 km to the ACM grid; 0 = no retrieval
  - `src` (str array [n]): the source object keys, for audit
- Writes are idempotent: re-running an hour merges by `t_scan`. Hours older than 48 h are deleted. Validation's long-term copy (monthly release packs) stores the same npz files unchanged.

## Derivation, version 1 (`low_cloud`)
`low_cloud` returns its rule version alongside p and c; scoring records that version in every scorecard row so old scores stay comparable.
- low = class in {2, 3} and 0 < ht < `LOW_TOP_M` (1000 m). p = {0: 0, 1: 0.25, 2: 0.75, 3: 1}[class] where the top is low or no height is retrieved; 0 where the top is high.
- c = 0 where acm >= 254, or where the share of cloudy pixels with ht >= LOW_TOP_M is large (high cloud hides the layer: same rule as `CLOUD_HIGH_MAX`), or within the terminator window (sun elevation from -6 to +6 degrees at the pixel; to tune). Elsewhere c = 1 by day, 0.7 by night (to tune).
- Parallax: shift = ht_or_fogtop x tan(view zenith) toward the satellite. Applied when sampling, never baked into the stored arrays.
- Sizes (estimate): per scan about 4 KB acm + 8 KB ht raw, a few KB compressed; 48 h is about 2 MB.
