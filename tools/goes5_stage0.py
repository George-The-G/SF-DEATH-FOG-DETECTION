"""Stage 0 check of multi-scale guide 07: does the satellite show sub-hourly change over the city and at the coast-side fog edge?
  python tools/goes5_stage0.py fetch  --start 2026-11-10T08:00 --end 2026-11-12T08:00 --store DIR      pull past scans (AWS archive) into a goes5 store (not the live store)
  python tools/goes5_stage0.py analyse --store DIR --out DIR [--hourly-tiles DIR]                       numbers + picture strip
All times UTC. Reads public NOAA data only; no GPU."""
import argparse
import datetime as dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microclimate.live import config as C, goes, goes5  # noqa: E402

UTC = dt.timezone.utc
CITY_BOX = (-122.52, 37.70, -122.35, 37.83)          # the 20 m domain's lon/lat extent (config LAT_RANGE / LON_RANGE)
EDGE_LAT = 37.76                                     # west-east line through Ocean Beach
EDGE_LON0, EDGE_LON1 = -123.00, -122.30              # offshore start, inland end
OB_LON = -122.51


def parse(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


def fetch(a):
    store, cache = Path(a.store), Path(a.store).parent / (Path(a.store).name + "_raw")
    t0, t1 = parse(a.start), parse(a.end)
    scans = goes5.list_scans(t0, t1)
    print(f"{len(scans)} scans listed", flush=True)

    def one(s):
        ts, ka, kh = s
        for attempt in range(2):
            try:
                fa, fh = goes.download(ka, cache), goes.download(kh, cache)
                try:
                    return ts, ka, kh, goes5.crop_scan(fa, fh)
                finally:
                    for f in (fa, fh):
                        Path(f).unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                err = e
        return ts, ka, kh, err

    grid_p = store / "grid.json"
    n = bad = 0
    t = time.time()
    with ThreadPoolExecutor(6) as ex:
        for ts, ka, kh, r in ex.map(one, scans):
            if isinstance(r, Exception):
                bad += 1
                print("failed", ts, repr(r)[:120], flush=True)
                continue
            cr, acm, ht = r
            if not grid_p.exists():
                goes5._atomic_bytes(grid_p, json.dumps(goes5.make_grid(cr, goes.BOX)).encode())
            p = goes5.hour_path(store, ts)
            goes5.save_hour(p, goes5.merge_scan(goes5.load_hour(p), int(ts.timestamp()), acm, ht, f"{ka.split('/')[-1]}|{kh.split('/')[-1]}"))
            n += 1
            if n % 50 == 0:
                print(n, "done", round(time.time() - t), "s", flush=True)
    print(f"done: {n} stored, {bad} failed, {round(time.time() - t)} s, store {goes5.store_bytes(store)} bytes in {len(goes5.hour_files(store))} hour files")


def series(store):
    """Per scan: time, city-box mean p (conf > 0 pixels), valid share, edge position (km east of Ocean Beach, NaN when none) and the p profile along the line."""
    crop = goes5.Crop5(store)
    lon = np.arange(EDGE_LON0, EDGE_LON1 + 1e-9, 0.01)
    lat = np.full_like(lon, EDGE_LAT)
    lon2, lat2 = lon[None, :], lat[None, :]
    cl1, cl2 = goes5.macro_axes(goes.BOX, C.GOES5_MACRO_RES)
    cl, ca = np.meshgrid(cl1, cl2)
    out = []
    for i in range(len(crop)):
        p, c, top, _ = goes.low_cloud(crop, i)
        sp, sc, _ = goes5.sample_macro(crop, i, p, c, top, cl, ca)
        m = (cl >= CITY_BOX[0]) & (cl <= CITY_BOX[2]) & (ca >= CITY_BOX[1]) & (ca <= CITY_BOX[3])
        ok = m & (sc > 0)
        city = float(sp[ok].mean()) if ok.any() else float("nan")
        lp, lc, _ = goes5.sample_macro(crop, i, p, c, top, lon2, lat2)
        lp, lc = lp[0], lc[0]
        prof = np.where(lc > 0, lp, np.nan)
        edge = float("nan")
        if np.isfinite(prof[0]) and prof[0] >= 0.5:               # offshore end is fogged: edge = eastmost point of the contiguous fogged run from the west
            below = np.nonzero(~(prof >= 0.5))[0]
            k = (below[0] - 1) if len(below) else len(prof) - 1
            edge = (lon[k] - OB_LON) * 111.32 * np.cos(np.radians(EDGE_LAT))
        out.append(dict(i=i, t=crop.time(i), city=city, edge=edge, prof=prof, valid=float((lc > 0).mean())))
    return crop, out


def analyse(a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    crop, s = series(a.store)
    t = np.array([x["t"] for x in s])
    city = np.array([x["city"] for x in s])
    edge = np.array([x["edge"] for x in s])
    step_min = np.diff([x.timestamp() for x in t]) / 60.0
    res = dict(n_scans=len(s), first=t[0].isoformat(), last=t[-1].isoformat(), median_step_min=float(np.median(step_min)), gaps_over_7min=int((step_min > 7).sum()))
    d = np.abs(np.diff(city))[(step_min < 7)[: len(city) - 1]]
    res["city_p_mean_abs_change_per_5min"] = float(np.nanmean(d))
    res["city_p_p95_abs_change_per_5min"] = float(np.nanpercentile(d, 95))
    # what a linear blend of the hourly scans (the one nearest each hour) misses
    hourly = [i for i, x in enumerate(t) if x.minute < 5]
    ti = np.array([x.timestamp() for x in t])
    if len(hourly) > 2:
        interp = np.interp(ti, ti[hourly], city[hourly])
        res["city_p_rms_vs_hourly_linear"] = float(np.sqrt(np.nanmean((city - interp) ** 2)))
        res["city_p_max_vs_hourly_linear"] = float(np.nanmax(np.abs(city - interp)))
        res["city_p_std"] = float(np.nanstd(city))
    # edge motion inside each UTC hour
    hrs = {}
    for x in s:
        hrs.setdefault(x["t"].replace(minute=0, second=0), []).append(x)
    net, rng, moved2 = [], [], 0
    nh = 0
    for h, xs in sorted(hrs.items()):
        e = np.array([x["edge"] for x in xs])
        if np.isfinite(e).sum() < 3:
            continue
        nh += 1
        ee = e[np.isfinite(e)]
        net.append(float(ee[-1] - ee[0]))
        rng.append(float(ee.max() - ee.min()))
        moved2 += int(ee.max() - ee.min() > 2.0)
    res["edge"] = dict(hours_with_edge=nh, hours_total=len(hrs), median_abs_net_km_per_hour=float(np.median(np.abs(net))) if net else None,
                       p90_abs_net_km_per_hour=float(np.percentile(np.abs(net), 90)) if net else None,
                       median_range_km_inside_hour=float(np.median(rng)) if rng else None,
                       hours_edge_range_over_2km=moved2, share_hours_over_2km=(moved2 / nh) if nh else None)
    # one-pixel flicker: share of consecutive 5-min steps where the edge jumps by more than 2 km
    de = np.abs(np.diff(edge))
    okd = np.isfinite(de) & (step_min < 7)[: len(de)]
    res["edge"]["steps_over_2km"] = int((de[okd] > 2.0).sum())
    res["edge"]["steps_with_edge"] = int(okd.sum())
    (out / "stage0.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))
    # series plot
    fig, ax = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
    ax[0].plot(t, city, ".-", ms=2, lw=0.6, label="5-minute")
    ax[0].plot(np.array(t)[hourly], city[hourly], "o-", ms=3, lw=1, label="hourly scans")
    ax[0].set_ylabel("city box mean p"); ax[0].legend()
    ax[1].plot(t, edge, ".-", ms=2, lw=0.6)
    ax[1].set_ylabel("fog edge, km east of Ocean Beach"); ax[1].grid(alpha=.3)
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(out / "series.png", dpi=110); plt.close(fig)
    # strip: 12 consecutive frames around the largest 1 h edge movement (or the largest city change)
    if net:
        start_h = sorted(hrs)[int(np.argmax(rng))] if rng and len(rng) == len(hrs) else None
    j0 = int(np.nanargmax(np.abs(np.convolve(np.nan_to_num(np.gradient(city)), np.ones(12), "valid")))) if len(city) > 12 else 0
    lon1, lat1 = goes5.macro_axes(goes.BOX, C.GOES5_MACRO_RES)
    lon, lat = np.meshgrid(lon1, lat1)
    fig, axs = plt.subplots(2, 6, figsize=(15, 5.6))
    for k, axk in enumerate(axs.ravel()):
        i = min(j0 + k, len(crop) - 1)
        p, c, top, _ = goes.low_cloud(crop, i)
        sp, sc, _ = goes5.sample_macro(crop, i, p, c, top, lon, lat)
        img = np.where(sc > 0, sp, np.nan)
        axk.imshow(img, vmin=0, vmax=1, cmap="Blues", extent=[lon1[0], lon1[-1], lat1[-1], lat1[0]], interpolation="nearest")
        axk.axhline(EDGE_LAT, color="r", lw=0.4)
        axk.plot([CITY_BOX[0], CITY_BOX[2], CITY_BOX[2], CITY_BOX[0], CITY_BOX[0]], [CITY_BOX[1], CITY_BOX[1], CITY_BOX[3], CITY_BOX[3], CITY_BOX[1]], "k", lw=0.6)
        axk.set_title(crop.time(i).strftime("%d %H:%MZ"), fontsize=8); axk.set_xticks([]); axk.set_yticks([])
    fig.suptitle("GOES-18 low-cloud probability p (parallax corrected, grey = no confidence); red line = edge transect, box = city", fontsize=9)
    fig.tight_layout(); fig.savefig(out / "strip12.png", dpi=110); plt.close(fig)
    # optional: the model's hourly fog tiles for the same hours
    if a.hourly_tiles:
        model_vs_sat(a, crop, s)


def model_vs_sat(a, crop, s):
    """a.hourly_tiles: folder with tiles.npz written from the published fog png frames (t_iso [n], city_mean [n] = mean opacity over the city box). Prints the comparison."""
    z = np.load(Path(a.hourly_tiles) / "tiles.npz", allow_pickle=True)
    for t, m in zip(z["t_iso"], z["city_mean"]):
        tt = parse(str(t)[:19])
        sat = [x["city"] for x in s if abs((x["t"] - tt).total_seconds()) <= 1800]
        print(str(t), "model", round(float(m), 3), "sat 5-min range", (round(float(np.nanmin(sat)), 3), round(float(np.nanmax(sat)), 3)) if sat else None)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("--start", required=True); f.add_argument("--end", required=True); f.add_argument("--store", required=True)
    g = sub.add_parser("analyse"); g.add_argument("--store", required=True); g.add_argument("--out", required=True); g.add_argument("--hourly-tiles")
    a = ap.parse_args()
    fetch(a) if a.cmd == "fetch" else analyse(a)
