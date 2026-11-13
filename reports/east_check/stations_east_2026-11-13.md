# East wind fields on vs off: in-grid wind anchors, leave-one-station-out (2026-10-14 to 2026-11-12, 697 h)

Error of the anchor fit's wind at the held-out in-grid station; speed MAE in m/s, vector MAE in the last two columns. raw = RRFS at the station (same in both). East hours = observed wind from 0..180 deg.

| station | hours | east hours | raw speed, east h | OFF speed (all h) | ON speed (all h) | OFF speed, east h | ON speed, east h | OFF speed, other h | ON speed, other h | OFF vector (all h) | ON vector (all h) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| FTPC1 | 638 | 240 | 1.15 (n=234) | 1.55 (n=697) | 1.57 (n=697) | 0.83 (n=240) | 0.88 (n=240) | 1.93 (n=457) | 1.94 (n=457) | 2.02 | 1.99 |
| PXOC1 | 638 | 210 | 1.10 (n=201) | 1.38 (n=697) | 1.40 (n=697) | 1.22 (n=210) | 1.25 (n=210) | 1.45 (n=487) | 1.47 (n=487) | 1.92 | 1.95 |
| GGBC1 | 632 | 393 | 1.57 (n=348) | 1.50 (n=691) | 1.47 (n=691) | 1.75 (n=393) | 1.75 (n=393) | 1.17 (n=298) | 1.11 (n=298) | 3.88 | 4.05 |

Wind fit OFF: {'hours_fitted': 697, 'regional': 610, 'citywide': 87, 'offshore_flagged': 364, 'qc_dropped': 83, 'hours': 697}

Wind fit ON: {'hours_fitted': 697, 'regional': 610, 'citywide': 87, 'offshore_flagged': 364, 'qc_dropped': 83, 'hours': 697}

