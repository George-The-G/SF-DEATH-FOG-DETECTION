# Fog top fusion (guide 06): gate results, 2026-11-15

Scripts: `tools/top_fusion_gate/` (fetch_history.py, leave_one_out.py, compare_retro.py). Data: Open-Meteo historical-forecast archive, `ncep_rrfs_conus`, 1-14 Nov 2026 (the live endpoint
api.open-meteo.com was unreachable from the PC during this work, connection reset); SPC Oakland soundings; aviationweather METAR (the API gave only the last ~3 days, so ceilings exist for
12-14 Nov only).

## Leave-one-sounding-out (`leave_one_sounding_out.json`)
17 soundings, 7 without an inversion, 10 scored. Both methods run WITHOUT the scored sounding (old = model profile + ceiling floor; new = fusion of model + ceilings), METARs cut at the valid time.
Error = fog top at the valid time minus that sounding's inversion base (m).
- MAE old 474 m, new 380 m (n = 10). Without the 2317 m base of 13 Nov 00Z (an elevated, non-marine inversion that both miss): old 277 m, new 175 m (n = 9).
- Bias old -152 m, new -178 m. Only 2 of the 10 had a ceiling in the 6 h before (12 Nov 12Z, 13 Nov 00Z); both methods are wrong there (model top near the 900 m cap at 12 Nov 12Z).
- Share of steps at the floor (elev + 50 m) or the 900 m model cap, mean over the 10 windows: old 22.8 %, new 3.0 %.
- No GOES top source in this test (no scans in the archive run).
Not a strong test: 10 points, 3 of the 10 with errors above 500 m for the old method; the withheld-sounding setup tests the smoothing and ceiling terms only.

## Street wetness compare (`compare_20261112T120000Z.md`)
One window (night of 12 Nov, public physics = none, real RRFS archive + METAR ceilings + the 12 Nov 12Z sounding, 6 h of model padded at the end, no GOES): mean W 36.45 -> 36.50 (+0.05 of 100), p90 unchanged,
largest change at any point and frame 9.98 (mean per-point abs change under 0.8); fog term total +13.5 (65.3 -> 78.8). Only one night; George should look before BETA["top_fusion"] is turned on.
