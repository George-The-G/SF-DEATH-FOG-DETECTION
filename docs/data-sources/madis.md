# Data source review for SF death-fog

Status: MADIS docs read, but this container could not reach MADIS, api.weather.gov or Synoptic, so nothing was tested live.
"Verified" = read on NOAA's own pages (some last updated 2017 to 2025). "Unverified" = from memory.

## What the model needs
Fog and wet streets come from: onshore wind (direction and speed), temperature minus dew point (spread), humidity,
calm air, no sun, and fog depth (cloud base / inversion height). Local detail is the weak spot: airports are flat and
on the bay shore, so they miss hills and neighbourhood differences.

## A. Already in the repo
| Source | What it is | Value | Safe to use? | Pros | Cons |
|---|---|---|---|---|---|
| Open-Meteo (NOAA RRFS) | Model forecast, ~3 km | Core: full picture and the future | CC BY 4.0, credit it | Free, complete, forecast hours | Model fog timing often off by hours |
| NWS API (KSFO, SFOC1) | Station obs | Real temp/dew point/wind | US gov, public. Needs User-Agent | No key | Mostly hourly, few stations |
| NOAA CO-OPS 9414290 | Tide-station wind + air temp | Waterfront wind | Public | Reliable | One point |
| Aviation Weather Center | METAR (KSFO, KOAK, KHAF) | Visibility and cloud ceiling (best fog evidence) | Public | Only source of visibility/ceiling | Airports only |
| SPC Oakland sounding | Balloon profile | Inversion height = fog top | Public | Direct physical signal | Twice a day, Oakland |
| GOES-18 on AWS | Satellite cloud mask/top | Upwind and overhead low cloud | NOAA open data | Sees fog offshore | Cloud-top, not street level |
| IEM archive | Airport history | 30-day baseline | Public archive | Free | Archive only |
| PurpleAir (off) | Neighbourhood sensors | Local temp/RH | Their terms; paid key (secret) | Dense | Reads warm/dry, needs calibration, costs points |

## B. MADIS (needs your account; apply via the form you filled in)
Access: Text/XML viewer, login by email after approval (~1 week, verified).
Licence: private applicants get "full distribution" (public) data only. Restricted providers must not be shown
on public pages, including your data branch DB. Verified rules: restrictions page.

You ticked: METAR, Maritime, Integrated Mesonet. Good choice.

| Dataset | What it is | Value | Safe to use publicly? | Pros | Cons |
|---|---|---|---|---|---|
| Integrated Mesonet: CWOP | ~13k citizen stations (verified count; SF subset unknown) | Highest: block-scale temp, dew, wind | Yes, category 3 | Dense, 5-15 min | Siting, warm bias, humidity drift, QC needed |
| Integrated Mesonet: RAWS | Fire-weather stations | Hillsides/ridges, some solar | Yes (cat. 3) | Well maintained | Few near city |
| Integrated Mesonet: PG&E | Utility stations | Local temp/wind | Yes (cat. 3) | Decent siting | Sparse in city, unverified coverage |
| Integrated Mesonet: Caltrans (CADOT) | Highway stations | Road-adjacent | Yes (cat. 3) | Near pavement | 44 stations statewide |
| Integrated Mesonet: HADS | Hydro stations | Marginal | Yes (cat. 3) | | Mostly rain/river |
| Integrated Mesonet: Earth Networks (AWS) | Commercial network | Dense in cities | NO (cat. 2) | | Not for you |
| Synoptic/Davis WeatherLink inside MADIS | Home stations | | NO (cat. 4, NOAA only) | | Not for you |
| METAR | Airport reports | Visibility, ceiling cross-check | Yes | Already have most via AWC | Duplicates AWC |
| Maritime | Buoys and ships | Upwind wind/temp, fog warning | Yes (cat. 3, verify per buoy) | Upwind of the city | Sparse, offshore |
| 1-minute ASOS (not ticked) | Airport minute data | Faster fog onset | Experimental | Fast | Only KSFO/KOAK |

Variables (verified): TD, RH, T, DD (wind dir), FF (speed), FFGUST, ALTSE (pressure). Solar, visibility and cloud only on some stations.
Latency (verified): mesonet ~8-45 min, best data ~2 h later.

## C. Not ticked, and why
Radiosonde (have SPC), Profilers, Satellite wind/sounding/radiance (have GOES), Aircraft (sparse, airline-restricted),
Radiometer / Hydro Surface / National Mesonet (restricted for private), Snow, CRN, USHCN-M, NEPP, RSAS grids (13 km, worse than RRFS).

## D. Outside MADIS (unverified)
- Direct CWOP over APRS-IS: no account, TCP socket, radius filter. Best fast win; needs a new parser. Terms unverified.
- Synoptic Data API: free token (secret), terms for public display unverified.
- Weather Underground / Netatmo / Ambient / WeatherLink: keys, redistribution limits likely.
- Own sensors: `plugins/example_local_csv.py`.

## E. Rules for any new source
1. Never commit keys; use GitHub secrets and environment variables (like PURPLEAIR_API_KEY).
2. Public-page rule: only category-3 / explicitly open data goes into the public database or site.
3. QC citizen data: median per zone, drop stations that disagree with KSFO beyond a margin.
4. Add wind direction (DD) to Reading/WeatherState; it is not carried today.
5. Credit MADIS and providers on the page.

## F. Open questions
- Does MADIS restrict logins by IP (GitHub Actions has rotating IPs)? Put it in Remarks.
- Which CWOP/PG&E/RAWS stations are actually near SF? Answer after first real response.
- Is each provider's public status still current? Restrictions page dates from 2017.

## Recommendation
1. Submit the MADIS form as ticked. 2. Meanwhile add nearer NWS stations to config. 3. When the login arrives, build the MADIS
source (wind direction, provider whitelist, QC). 4. Consider direct CWOP later.
