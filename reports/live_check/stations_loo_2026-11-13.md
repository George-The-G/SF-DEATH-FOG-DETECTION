# Station anchors, leave-one-station-out (2026-10-14 to 2026-11-12, 697 h)

raw = RRFS forecast at the station; city = today's citywide correction; anchor = guide 03 fit without that station. Fort Point wind city column is in-sample.

Wind fit with all in-grid anchors: 697 of 697 h fitted (609 regional, 88 citywide); hours flagged offshore (library cannot represent the direction): 363; station-hours dropped by the outlier check: 83.

## Air temperature: mean absolute error (C), bias in brackets

| station | hours | raw model | today's citywide | anchor fit |
|---|---|---|---|---|
| FTPC1 | 638 | 0.92 (-0.45) | 0.85 (+0.34) | 1.24 (+0.99) |
| PXOC1 | 638 | 2.35 (-1.67) | 1.72 (-0.94) | 1.23 (-0.32) |
| PXSC1 | 638 | 2.55 (-2.00) | 1.87 (-1.27) | 1.37 (-0.67) |
| OBXC1 | 638 | 1.20 (-1.11) | 0.97 (-0.38) | 1.40 (+0.28) |
| OKXC1 | 638 | 2.03 (-1.00) | 1.64 (-0.27) | 1.13 (+0.40) |
| AAMC1 | 638 | 2.02 (-1.62) | 1.45 (-0.89) | 0.88 (-0.26) |
| LNDC1 | 638 | 2.21 (-2.05) | 1.57 (-1.32) | 1.02 (-0.71) |
| SFOC1 | 636 | 2.12 (-2.00) | 1.75 (-1.59) | 0.99 (-0.61) |
| OAMC1 | 631 | 2.08 (-1.70) | 1.44 (-0.97) | 1.05 (-0.30) |
| KSFO | 638 | 1.69 (-1.05) | 1.29 (-0.39) | 0.94 (+0.36) |
| KOAK | 638 | 1.11 (-0.61) | 0.97 (+0.14) | 1.30 (+0.82) |
| KHAF | 635 | 1.95 (+0.50) | 2.44 (+1.54) | 2.85 (+1.98) |
| ONOC1 (hill) | 634 | 2.93 (-2.61) | 2.40 (-1.88) | 1.76 (-1.13) |
| OKSC1 (hill) | 633 | 2.39 (-2.01) | 1.91 (-1.28) | 1.23 (-0.33) |
| HSPC1 (hill) | 630 | 2.16 (-1.99) | 1.85 (-1.26) | 1.50 (-0.54) |
| MDEC1 (hill) | 635 | 2.56 (-2.45) | 2.09 (-1.71) | 1.33 (+0.06) |

## Dew point: mean absolute error (C), bias in brackets

| station | hours | raw model | today's citywide | anchor fit |
|---|---|---|---|---|
| PXSC1 | 638 | 2.72 (-2.30) | 2.81 (-2.52) | 2.51 (-2.08) |
| OBXC1 | 638 | 1.45 (-1.17) | 1.57 (-1.39) | 1.19 (-0.82) |
| SFOC1 | 636 | 1.90 (+1.33) | 2.17 (+1.45) | 2.37 (+1.93) |
| OAMC1 | 631 | 2.10 (-1.91) | 2.29 (-2.14) | 1.91 (-1.65) |
| KSFO | 638 | 1.39 (-0.30) | 1.43 (-0.70) | 1.32 (+0.15) |
| KOAK | 638 | 1.20 (+0.08) | 1.31 (-0.19) | 1.29 (+0.58) |
| KHAF | 635 | 1.68 (-0.25) | 1.85 (-0.64) | 1.69 (+0.21) |
| ONOC1 (hill) | 634 | 1.83 (+0.03) | 1.98 (-0.19) | 1.72 (+0.48) |
| OKSC1 (hill) | 633 | 2.00 (-1.09) | 2.22 (-1.31) | 1.77 (-0.80) |
| HSPC1 (hill) | 630 | 1.55 (+0.51) | 1.51 (+0.28) | 1.62 (+1.01) |
| MDEC1 (hill) | 635 | 2.37 (+0.58) | 2.55 (+0.36) | 2.52 (+1.41) |

## Wind: mean vector error (m/s), speed error in brackets

| station | hours | raw model | today's citywide | anchor fit |
|---|---|---|---|---|
| FTPC1 | 638 | 2.41 (1.42) | 2.83 (0.62) in-sample | 2.04 (1.58) |
| PXOC1 | 638 | 1.85 (1.05) | 2.34 (1.03) | 1.92 (1.39) |
| OKXC1 | 638 | 1.74 (0.99) | 1.85 (1.12) | n/a |
| AAMC1 | 638 | 1.52 (0.87) | 2.10 (1.06) | n/a |
| LNDC1 | 638 | 1.39 (0.84) | 1.75 (0.96) | n/a |
| KSFO | 627 | 2.02 (1.43) | 2.63 (1.71) | n/a |
| KOAK | 633 | 1.54 (1.01) | 2.24 (1.29) | n/a |
| KHAF | 635 | 2.07 (1.57) | 2.37 (1.62) | n/a |
| ONOC1 (hill) | 634 | 2.39 (2.39) | 1.99 (1.99) | n/a |
| OKSC1 (hill) | 633 | 1.85 (1.74) | 1.82 (1.54) | n/a |
| HSPC1 (hill) | 630 | 1.93 (1.15) | 2.60 (1.33) | n/a |
| MDEC1 (hill) | 635 | 2.33 (1.30) | 3.07 (1.78) | n/a |
| GGBC1 | 631 | 5.02 (1.49) | 4.65 (1.26) | 3.90 (1.50) |
| OMHC1 | 638 | 1.61 (0.95) | 2.20 (1.19) | n/a |
