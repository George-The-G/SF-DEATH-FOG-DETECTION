"""Live whole-city death fog: constants (URLs, station ids, lattice, nudging weights). Everything tunable lives here
(PLAN_LIVE_DATA.md 3.1-3.3). Nothing is calibrated; the nudging weights are first guesses to be tuned with `python -m microclimate.live check`."""
import os

UA = {"User-Agent": "sf-death-fog (github.com/George-The-G/SF-DEATH-FOG-DETECTION)"}
TIMEOUT_S = 40
RETRIES = 3
MODEL_VERSION = "live-1"

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
ARCHIVE_DIR = os.environ.get("DEATHFOG_ARCHIVE_DIR") or None   # folder holding run_<yyyymmddThhmm>.npz (guide 02 part B); None = no archive yet
CLOUDSCORE_PREV_ENV = "DEATHFOG_CLOUDSCORE_PREV"               # path of the previous rolling cloud_score.json (set by live_ci/refresh.sh)

# ---- paths ----
STATIC_DEFAULT = os.path.join(os.path.expanduser("~"), "deathfog-data", "live", "live_static.npz")


def blocked(name):
    """Test hook: DEATHFOG_LIVE_BLOCK=metar,goes,coops,sounding,nws,rrfs makes those fetches fail as if the network were blocked."""
    return name in {x.strip() for x in os.environ.get("DEATHFOG_LIVE_BLOCK", "").split(",") if x.strip()}
