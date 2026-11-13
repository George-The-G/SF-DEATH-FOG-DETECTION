"""Live whole-city death fog: constants (URLs, station ids, lattice, nudging weights). Everything tunable lives here
(PLAN_LIVE_DATA.md 3.1-3.3). Nothing is calibrated; the nudging weights are first guesses to be tuned with `python -m microclimate.live check`."""
import os

UA = {"User-Agent": "sf-death-fog (github.com/George-The-G/SF-DEATH-FOG-DETECTION)"}
TIMEOUT_S = 40
RETRIES = 4                  # attempts per source call (transient errors only: timeouts, resets, 5xx, 429)
RETRY_WAITS_S = (2.0, 6.0, 12.0)   # waits between attempts (<= 20 s of sleeping in total per call)
RETRY_AFTER_MAX_S = 20.0     # cap on a server Retry-After
RETRY_BUDGET_S = 170.0       # no new attempt if it could start after this many seconds into the call
MODEL_VERSION = "live-1"
CARRY_STATE = False                   # True: a window starts from the saved anchor state (changes what the page shows: needs the user's approval); False: always dry start. Anchors are saved either way.
ARCHIVE = True                        # write the per-run archive file when the caller gives an archive folder (refresh.sh pushes it to branch live-archive); DEATHFOG_ARCHIVE=false also turns it off
ARCHIVE_EVERY_H = 1                   # at most one archive file per this many UTC hours on branch live-archive (the first run of the slot is kept; 0 = keep every run)
PHYSICS = ()                          # physics switches (wetness.KNOWN_PHYSICS) the scheduled job runs with; empty = the public model (guide 01)
# Beta preview channel (site/v3/live_beta/, opened with /v3/?beta=1): a second build of the same fetched inputs with these settings. It is applied ONLY by `build --beta`
# (build.run(beta=True)); the public build never reads it. Staging order (user's decision 2026-11-12): stage 1 = station anchors + clear-sky sun, stage 2 = carry_state,
# stage 3 = film + pavement. A stage goes to the public settings above only after the user has looked at the preview and approved it.
BETA = dict(physics=("sun_clear",), nudge_anchors=True, carry_state=False, wind_east=True)
# WIND_EAST (guide 14): the east wind-direction fields (22.5 .. 135 deg; 157.5 is an accepted gap) feed the STREET WIND only: station-anchor wind fit and
# the direction weights. Needs live_east.npz next to the static table; the fog tracer weight for east / offshore wind stays zero. False = the public model, bit for bit.
# The public build reads this value; the beta build reads BETA["wind_east"] only. build.run sets it for the duration of one build (like NUDGE_ANCHORS).
WIND_EAST = False
EAST_FILE = "live_east.npz"           # release asset of live-static-v1 (wind vectors at the stations for the 15 library directions); optional
# Settings the PUBLIC build runs with when it is started with `build --promoted` (live_ci/refresh.sh does that when DEATHFOG_LIVE_PROMOTE is true; the job falls back to the old settings,
# C.PHYSICS / C.NUDGE_ANCHORS / C.CARRY_STATE, if such a build fails). Approved 2026-11-13: clear-sky sun (GOES sunlight) + station anchors, exactly the stage 1 beta. Nothing else:
# no carried state, film, pavement or east wind (wind_east stays False here on purpose; the east wind is under test on the beta channel). Beta (C.BETA) moves on to the next stage on its own; this dict changes only with an explicit approval.
PROMOTED = dict(physics=("sun_clear",), nudge_anchors=True, carry_state=False, wind_east=False)
BETA_DIR = "live_beta"                # published folder of the beta channel, next to live/

# ---- RRFS through Open-Meteo (HRRR on Open-Meteo is discontinued 2026-11-30: do not use it) ----
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"
RRFS_MODELS = ["ncep_rrfs_conus", "ncep_rrfs_north_america"]      # first that answers is used (hourly, 84 h; north_america has no gaps at the end)
SURFACE_VARS = ["temperature_2m", "dew_point_2m", "wind_speed_10m", "wind_direction_10m", "cloud_cover_low", "cloud_cover", "precipitation",
                "shortwave_radiation", "visibility"]
LEVELS_HPA = (1000, 975, 950, 925, 900, 850)
PROFILE_VARS = ([f"temperature_{l}hPa" for l in LEVELS_HPA] + [f"geopotential_height_{l}hPa" for l in LEVELS_HPA]
                + [f"relative_humidity_{l}hPa" for l in LEVELS_HPA[:5]])
PROFILE_POINT = (37.76, -122.45)     # one point is enough for the marine-layer profile
# lattice over the 20 m domain (EPSG:32610 x0=540100, y0=4171500, 17.1 x 16.2 km): 6 x 5 points at ~3 km
LAT_RANGE, N_LAT = (37.700, 37.830), 5
LON_RANGE, N_LON = (-122.520, -122.350), 6
COAST_X_MAX = 545000.0               # lattice points west of this x (m, UTM 10N) form the coast / upwind series s(t)

# ---- observations ----
COOPS_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
COOPS_STATION = "9414290"
COOPS_LL = (37.8063, -122.4659)      # Fort Point / Presidio (San Francisco)
METAR_URL = "https://aviationweather.gov/api/data/metar"
METAR_IDS = ("KHAF", "KSFO", "KOAK")
METAR_LL = {"KHAF": (37.5134, -122.5011), "KSFO": (37.6196, -122.3656), "KOAK": (37.7213, -122.2208)}
NWS_URL = "https://api.weather.gov/stations/{id}/observations"
NWS_STATIONS = {"SFOC1": (37.77056, -122.42694)}    # San Francisco downtown (T, Td, RH only)
SOUNDING_URL = "https://www.spc.noaa.gov/exper/soundings/{yy}{mm}{dd}{hh}_OBS/OAK.txt"
GOES_BUCKET = "https://noaa-goes18.s3.amazonaws.com/"
GOES_MASK = "ABI-L2-ACMC"            # clear sky mask (ACM 0 clear, 1 probably clear, 2 probably cloudy, 3 cloudy), 2 km
GOES_HEIGHT = "ABI-L2-ACHAC"         # cloud top height HT (m), 10 km
GOES_CACHE_H = 40                    # hours of GOES history kept in the local cache directory

# ---- nudging (3.3) ----
HISTORY_H = 6                        # window starts this long before the 18:00 PT start
WINDOW_H = 72
STEP_MIN = 10
OUT_STEP_MIN = 60
DAY_FRAMES = 26                      # hourly frames per day file
ROLL_H = 3                           # rolling median for the wind / temperature corrections
WIND_RATIO_CLIP = (0.5, 2.0)
WIND_DIR_CLIP_DEG = 45.0
WIND_E_FOLD_H = 6.0                  # observation corrections fade into the forecast with this e-folding time
TEMP_CLIP_C = 4.0
TEMP_E_FOLD_H = 6.0
FOG_E_FOLD_H = 3.0
W_MODEL = 0.5                        # s_used = W_MODEL * s_model + W_OBS * s_obs where both exist
W_OBS = 0.5
KHAF_VIS_MI = 3.0                    # visibility <= this (statute miles) counts as fog at the coast ...
KHAF_CEIL_FT = 1000.0                # ... as does a BKN/OVC/VV ceiling at or below this
LOW_TOP_M = 1000.0                   # GOES: cloudy pixel with cloud top below this = low cloud / fog
BOX_NEAR_KM, BOX_FAR_KM, SECTOR_HALF_DEG = 2.0, 15.0, 22.5   # upwind sector (off the coast), wind-from direction +- 22.5 deg
COAST_REF = (37.760, -122.510)       # Ocean Beach: the sector is laid out from here
CLEAR_MAX_OPACITY = 0.30             # fog sheet cells where GOES says clear (observed hours) get at most this opacity
FOG_TOP_ABOVE_CEILING_M = 100.0
SOUNDING_VALID_H = 6.0               # a sounding overrides the RRFS fog top for this long after its valid time, then fades out over the next 6 h
INV_RISE_C = 1.0                     # inversion: temperature rises by at least this within INV_DEPTH_M above its base
INV_DEPTH_M = 300.0

# ---- satellite cloud score (guide 04; all first guesses) ----
CLOUD_SCORE_FILE = "cloud_score.json"
SAT_FILE = "sat{d}.png"              # observed low-cloud tiles, same layout as fog{d}.png
CLOUD_YES = 0.5                      # a fraction at or above this counts as cloud / fog present
CLOUD_HIGH_MAX = 0.30                # box-hour not scorable when more than this share of the valid pixels is cloud that is not low (high cloud hides the fog)
CLOUD_VALID_MIN = 0.50               # ... or when fewer than this share of the pixels has a cloud mask
CLOUD_KEEP_DAYS = 14                 # rolling history kept in cloud_score.json (local PT dates)
CLOUD_MAX_BYTES = 100_000            # size limit of cloud_score.json
CLOUD_LEADS_H = (1, 3, 6, 12, 24)    # lead times scored from the run archive
CLOUD_LEAD_TOL_H = 0.75              # an archive frame counts for a lead time when its lead is within this many hours
UPWIND_BOX = (-122.80, 37.68, -122.62, 37.84)   # ocean box lon0, lat0, lon1, lat1: about 10-25 km west of Ocean Beach
BAY_BOX = (-122.37, 37.74, -122.31, 37.78)      # central bay between San Francisco and Oakland
CLOUD_ARCHIVE_DIR = os.environ.get("DEATHFOG_CLOUD_ARCHIVE_DIR") or None   # folder with recent run_<yyyymmddThhmm>.npz files (a checkout of branch live-archive, guide 02 part B) read for lead times; None = no archive yet
CLOUDSCORE_PREV_ENV = "DEATHFOG_CLOUDSCORE_PREV"               # path of the previous rolling cloud_score.json (set by live_ci/refresh.sh)

# ---- sunlight (guide 06; first guesses) ----
EXTRA_FILE = "live_extra.npz"        # bearing, slope, surface class per street point; looked up next to the static table (release live-static-v1 extra asset)
SAT_K = (1.0, 0.9, 0.6, 0.35)        # cloudiness factor of a GOES clear sky mask class: clear, probably clear, probably cloudy, cloudy
K_SAT_WEIGHT = 0.5                   # share of (k_sat - k_model) added to the model's k in observed hours (fades out with FOG_E_FOLD_H)
SUN_DSR = True                       # sun_clear runs: k_sat from the GOES downward shortwave product (DSR / clear-sky GHI) where it exists, the mask classes elsewhere. False: mask classes only.
GOES_DSR_PRODUCTS = ("ABI-L2-DSRC", "ABI-L2-DSRF")   # tried in this order; CONUS (DSRC) is empty on goes18 and goes19 (2026-11), full disk (DSRF) is read with range requests
DSR_BOX = (-122.65, 37.62, -122.25, 37.92)           # lon0, lat0, lon1, lat1 of the city subset kept per hour (about 12 x 12 pixels of 2 km)
DSR_BLOCK_BYTES = 32 * 1024          # range request block size for the netCDF4 / HDF5 file
DSR_CACHE_FILE = "dsr_cache.json"    # rolling cache of the per-hour city subsets, published in the output folder and carried between runs like the state files
DSR_DEADLINE_S = 90.0                # new hours are fetched until this many seconds have passed; the rest comes with the next run (the cache is kept)
DSR_REFRESH_H = 3                    # the newest hours are fetched again each run (their scan may not have been published at the last run)
DSR_MAX_SCAN_OFFSET_MIN = 30         # a scan counts for an hour when its start is within this many minutes of it
DSR_MIN_GHI = 50.0                   # no k from DSR where the clear-sky GHI at the scan time is below this (W/m2): low sun, the ratio is not meaningful
DSR_BOX_SCALE = 1.5                  # a lattice cell takes the DSR pixels inside 1.5 x its spacing (DSR is 2 km, the lattice 3.3 km: a plain cell box can hold no pixel centre)
SUN_FILE = "sun{d}.u8"               # on-demand sunlight files, same layout as day{d}.u8: round(100 * S / 1000) capped at 250 (written only when sun_clear is on)
DEPTH_FILE = "depth{d}.u8"           # water depth files (guide 05): round(255 * log1p(F / 0.01) / log1p(1 / 0.01)); written only when film is on
DRY_FILE = "dry{d}.u8"               # hours until the street is dry again, 255 = not within the window
PAV_SPINUP_DAYS = 3                  # status flag "pavement spin-up" stays until the window starts from an anchor that carries the road temperature of this many days
TSURF_FILE = "tsurf{d}.u8"           # road surface temperature (guide 07, written only when pavement is on): round((ts + 10) * 4), 0.25 K steps, -10 to 53.75 degC
CAUSE_FILE = "cause{d}.u8"           # main source of the water in the last 3 h: 0 none, 1 fog, 2 dew, 3 drizzle, 4 rain

# ---- stations as anchors (guide 03) ----
NUDGE_ANCHORS = False                # True: the station fit drives the per-point wind (regional inflow + direction weights) and the street temperature / dew point
                                     # (sea-level offset + lapse adjustment). False: today's model, bit-identical; stations are still fetched, fitted and written to stations.json.
STATION_FETCH_DEADLINE_S = 14.0      # all extra station requests run in parallel and are abandoned after this long
STATION_TIMEOUT_S = 8.0              # one request (no retries)
STATION_MAX_AGE_MIN = 90             # a station-hour is used only when its newest report in that hour is not older than the hour
STATION_WIND_FILE = "station_wind.npz"          # simulated vectors at the in-grid wind stations (python -m microclimate.live stationwind); optional release asset
STATIONS_FILE = "stations.json"
IEM_OBHISTORY = "https://mesonet.agron.iastate.edu/api/1/obhistory.json"
NDBC_URL = "https://www.ndbc.noaa.gov/data/realtime2/{id}.txt"
NWS_OBS_URL = "https://api.weather.gov/stations/{id}/observations"
# id -> dict(kind: coops|ndbc|iem|nws|metar, src: id at the source, name, lat, lon, elev_m (ground, or deck / water surface), sensor_m (wind sensor height above that),
#            vars: what it carries, hill: True for the RAWS stations (data points at their real height; wind aloft and inversion, never street wind)).
# The "Recommended list" of reports/live_check/stations_2026-11-12.md; ground elevations from that report, sensor heights 10 m when unknown (first guess).
STATIONS = {
    "FTPC1": dict(kind="coops", src="9414290", name="Fort Point (CO-OPS)", lat=37.8060, lon=-122.4660, elev_m=3.0, sensor_m=10.0, vars=("T", "wind")),
    "GGBC1": dict(kind="iem", src="GGBC1", name="Golden Gate Bridge centre", lat=37.8187, lon=-122.4784, elev_m=67.0, sensor_m=0.0, vars=("wind",)),   # IEM lists -95 m: wrong; deck about 67 m
    "PXOC1": dict(kind="coops", src="9414311", name="San Francisco Pier 1", lat=37.7980, lon=-122.3930, elev_m=3.0, sensor_m=10.0, vars=("T", "wind")),
    "PXSC1": dict(kind="coops", src="9414296", name="Pier 17 (visibility)", lat=37.8030, lon=-122.3970, elev_m=3.0, sensor_m=10.0, vars=("T", "Td", "vis")),
    "OBXC1": dict(kind="coops", src="9414797", name="Oakland Berth 38", lat=37.8040, lon=-122.3410, elev_m=3.0, sensor_m=10.0, vars=("T", "Td", "vis")),
    "OKXC1": dict(kind="coops", src="9414776", name="Oakland Berth 34", lat=37.8110, lon=-122.3330, elev_m=3.0, sensor_m=10.0, vars=("T", "wind")),
    "OMHC1": dict(kind="coops", src="9414769", name="Oakland Middle Harbor", lat=37.8010, lon=-122.3300, elev_m=3.0, sensor_m=10.0, vars=("wind",)),
    "AAMC1": dict(kind="coops", src="9414750", name="Alameda", lat=37.7720, lon=-122.3000, elev_m=2.0, sensor_m=10.0, vars=("T", "wind")),
    "LNDC1": dict(kind="coops", src="9414763", name="Oakland Berth 67", lat=37.7950, lon=-122.2810, elev_m=3.0, sensor_m=10.0, vars=("T", "wind")),
    "SFOC1": dict(kind="nws", src="SFOC1", name="San Francisco downtown", lat=37.7706, lon=-122.4269, elev_m=46.0, sensor_m=0.0, vars=("T", "Td")),
    "OAMC1": dict(kind="iem", src="OAMC1", name="Oakland Museum", lat=37.7981, lon=-122.2634, elev_m=9.0, sensor_m=0.0, vars=("T", "Td")),
    "D3169": dict(kind="nws", src="D3169", name="DW3169 Oakland", lat=37.8321, lon=-122.2500, elev_m=29.0, sensor_m=0.0, vars=("T", "Td", "wind")),
    "KSFO": dict(kind="metar", src="KSFO", name="San Francisco Intl", lat=37.6196, lon=-122.3656, elev_m=3.0, sensor_m=10.0, vars=("T", "Td", "wind", "vis")),
    "KOAK": dict(kind="metar", src="KOAK", name="Oakland Intl", lat=37.7178, lon=-122.2330, elev_m=3.0, sensor_m=10.0, vars=("T", "Td", "wind", "vis")),
    "KHAF": dict(kind="metar", src="KHAF", name="Half Moon Bay", lat=37.5136, lon=-122.4996, elev_m=20.0, sensor_m=10.0, vars=("T", "Td", "wind", "vis")),
    "ONOC1": dict(kind="iem", src="ONOC1", name="Oakland North RAWS", lat=37.8653, lon=-122.2208, elev_m=409.0, sensor_m=6.0, vars=("T", "Td", "wind"), hill=True),
    "OKSC1": dict(kind="iem", src="OKSC1", name="Oakland South RAWS", lat=37.7881, lon=-122.1436, elev_m=323.0, sensor_m=6.0, vars=("T", "Td", "wind"), hill=True),
    "HSPC1": dict(kind="iem", src="HSPC1", name="Spring Valley RAWS", lat=37.5625, lon=-122.4364, elev_m=314.0, sensor_m=6.0, vars=("T", "Td", "wind"), hill=True),
    "MDEC1": dict(kind="iem", src="MDEC1", name="Middle Peak", lat=37.9278, lon=-122.5872, elev_m=587.0, sensor_m=6.0, vars=("T", "Td", "wind"), hill=True),
    "46026": dict(kind="ndbc", src="46026", name="Buoy 46026 (offshore)", lat=37.750, lon=-122.838, elev_m=0.0, sensor_m=5.0, vars=("wind", "sst")),
    "46237": dict(kind="ndbc", src="46237", name="Buoy 46237 (SF Bar)", lat=37.788, lon=-122.634, elev_m=0.0, sensor_m=5.0, vars=("T", "sst")),
}
GGB_DECK_M = 67.0                    # Golden Gate Bridge deck above sea level

# wind regions (3b): Gaussian weights normalised to sum to 1 everywhere (softmax of -d^2 / 2 sigma^2); first guesses
WIND_REGION_SIGMA_M = 3000.0
WIND_REGIONS = {                     # name -> (lat, lon) of the centre
    "gap_nw": (37.800, -122.462),    # Golden Gate gap, Presidio, Marina, north-west
    "west": (37.762, -122.485),      # Richmond and Sunset
    "east_downtown": (37.783, -122.405),   # east waterfront and downtown
    "south": (37.728, -122.432),     # Bernal, Excelsior, Visitacion Valley
}
WIND_REGION_NEIGHBOURS = (("gap_nw", "west"), ("gap_nw", "east_downtown"), ("west", "east_downtown"), ("west", "south"), ("east_downtown", "south"))
WIND_REGION_MIN_ANCHORS = 2          # a region with fewer in-grid anchors (weight >= WIND_ANCHOR_MIN_W) uses the citywide fit
WIND_ANCHOR_MIN_W = 0.25
WIND_OBS_SIGMA_MS = 1.0              # one station vector component (first guess)
WIND_PRIOR_SIGMA_MS = 1.5            # prior on the inflow speed: one good station counts about as much as the forecast (first guess)
WIND_PRIOR_SIGMA_DEG = 15.0
WIND_SMOOTH_SIGMA_MS = 2.5           # difference between neighbouring regions
WIND_SMOOTH_SIGMA_DEG = 25.0
WIND_QC_MS = 8.0                     # drop a station-hour whose vector is farther than this from the median of the others
WIND_U_MIN_MS, WIND_U_MAX_MS = 0.3, 25.0

# temperature / dew point (3f)
LAPSE_T_K_PER_KM = -6.5              # lattice value moved to a height with this lapse rate (guide 08 profile not built yet)
LAPSE_TD_K_PER_KM = -2.0             # dew point (first guess)
T_FIT_MIN_STATIONS = 3               # fewer: distance-weighted horizontal correction instead
T_PRIOR_OFFSET_K = 3.0               # ridge on the sea-level offset (first guess)
T_PRIOR_LAPSE_K_PER_KM = 4.0         # ridge on the lapse adjustment
T_MIN_DZ_SPREAD_KM = 0.15           # the lapse adjustment is fitted only when the reporting stations span at least this much height (first guess)
T_LAPSE_ADJ_CLIP = (-8.0, 12.0)      # K/km (inversion: positive)
T_HORIZ_L_M = 6000.0
T_HORIZ_W0 = 0.25                    # weight of "zero correction" in the horizontal fallback (pulls to zero away from stations)

# ---- paths ----
STATIC_DEFAULT = os.path.join(os.path.expanduser("~"), "deathfog-data", "live", "live_static.npz")


def blocked(name):
    """Test hook: DEATHFOG_LIVE_BLOCK=metar,goes,coops,sounding,nws,rrfs makes those fetches fail as if the network were blocked."""
    return name in {x.strip() for x in os.environ.get("DEATHFOG_LIVE_BLOCK", "").split(",") if x.strip()}
