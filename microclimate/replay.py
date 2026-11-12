"""M4: replay real nights through the wetness model and write the viewer files to site/v3/replay/.

Layout per night <id> (id = local date of the 18:00 start): streets.u8 (uint8 [n_frames, N] row-major, W rounded to 0..100),
weather.json, fog.png. fog.png is a grayscale sprite sheet: tiles of 214 x 203 pixels (80 m cells = 4 x 4 block mean of the
20 m band-0 tracer taken in the first fluid cell above terrain + mean building height), 8 tiles per row, one tile per
30 min (33 tiles, tile k = k*30 min after 18:00). INSIDE a tile row 0 = NORTH edge, column 0 = WEST edge
(x = 540100 + 80*col, y_north = 4171500 + 20*811 - 80*row, i.e. the y axis is flipped relative to the grid arrays).
Pixel = 255 * s(t - lag(x)) * tracer, with lag(x) = distance from the west edge / mean night wind, so the front drifts in.
"""
import json
from datetime import timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import weather, wetness

LAT, LON = 37.76, -122.40
FOG_W, FOG_H, FOG_COLS, FOG_CELL = 214, 203, 8, 80
DAWN_STEP_H = 15.0   # 09:00 local (frame 90) = "dawn / morning" frame used by validation


def load_inputs(data_dir):
    zone = Path(data_dir) / "grids" / "zone"
    pts = pd.read_csv(zone / "potrero_points.csv")
    feat = pd.read_csv(zone / "street_features.csv")
    sur = pd.read_csv(zone / "street_surface.csv")
    fog_b = feat[["fog_b0", "fog_b1", "fog_b2", "fog_b3"]].to_numpy(np.float32)
    P = dict(
        x=pts["x"].to_numpy(float), y=pts["y"].to_numpy(float), z=pts["z"].to_numpy(float),
        bearing=pts["bearing"].to_numpy(float), way=pts["way"].to_numpy(), seg=pts["seg"].to_numpy(), side=pts["side"].to_numpy(),
        svf=np.load(zone / "street_svf.npy").astype(np.float32),
        horizon=np.load(zone / "street_horizon.npy").astype(np.float32),
        porous=sur["porous_factor"].to_numpy(np.float32),
        wind_ratio=feat["wind_ratio"].to_numpy(np.float32), rel_wind=wetness.refined_rel(feat["refine_corr"].to_numpy(float)),
        fog_b=fog_b, tracer=wetness.pick_tracer(fog_b, pts["z"].to_numpy(float)),
        street=pts["street"].to_numpy(), on_route=pts["on_route"].to_numpy(), lat=pts["lat"].to_numpy(), lon=pts["lon"].to_numpy(),
    )
    return P


def get_nights(cfg, data_dir, until=None):
    cache = Path(data_dir) / "raw" / f"weather_{LAT}_{LON}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    data = weather.fetch(LAT, LON, cache)
    info = {}
    r = cfg["replay"]
    picks = weather.pick_nights(data, r["fog_nights"], r["dry_nights"], until=until, info=info)
    picks = sorted([p for p in picks if p[1] == "fog"], key=lambda p: p[0], reverse=True) + \
        sorted([p for p in picks if p[1] == "dry"], key=lambda p: p[0], reverse=True)
    out = []
    for start, kind in picks:
        ser = weather.night_series(data, start, r["step_min"])
        out.append(dict(start=start, kind=kind, tier=info.get(start.isoformat(), 0), ser=ser, id=start.astimezone(weather.PT).date().isoformat()))
    return out


def run_night(P, night, step_min=10, **kw):
    return wetness.simulate(P, night["ser"], step_min, **kw)


def night_meta(night):
    ser = night["ser"]
    s = wetness.fog_inflow(ser["temperature_2m"], ser["dew_point_2m"], ser["cloud_cover_low"], ser["wind_dir"])
    th = np.radians(ser["wind_dir"])
    wgt = s if s.sum() > 0 else np.ones_like(s)
    mdir = (np.degrees(np.arctan2((wgt * np.sin(th)).sum(), (wgt * np.cos(th)).sum())) + 360) % 360
    dev = abs((mdir - 270 + 180) % 360 - 180)
    ons = wetness.onshore_weight(ser["wind_dir"])
    return dict(mean_dir=float(mdir), dev_from_270=float(dev), approx_direction=bool(dev > 22.5),
                offshore_pct=float(100 * (ons == 0).mean()), mean_wind_ms=float(np.mean(ser["wind_speed_10m"])),
                mean_inflow=float(s.mean()), tier=night["tier"])


def city_tracer_80m(data_dir):
    """Band-0 tracer at street level (first fluid cell above terrain + mean building height) in 80 m blocks.
    Returns [FOG_H, FOG_W], row 0 = north, column 0 = west."""
    from .street import first_fluid_k
    D = Path(data_dir)
    c = np.load(D / "library" / "fog_2700_0.npz")["c"]
    solid = np.load(D / "grids" / "solid_20m.npz")["solid"]
    k = first_fluid_k(solid)
    nx, ny = k.shape
    a = np.take_along_axis(c, np.clip(k, 0, c.shape[2] - 1)[:, :, None], axis=2)[:, :, 0]  # [nx, ny]
    a = a.T[::-1]                                                                           # [ny, nx], row 0 = north
    pad_y, pad_x = FOG_H * 4 - ny, FOG_W * 4 - nx
    a = np.pad(a, ((0, pad_y), (0, pad_x)), mode="edge")
    return a.reshape(FOG_H, 4, FOG_W, 4).mean(axis=(1, 3)).astype(np.float32)


def fog_sheet(tr80, ser, step_min=10, frame_min=30):
    from PIL import Image
    n = len(ser["temperature_2m"])
    s = wetness.fog_inflow(ser["temperature_2m"], ser["dew_point_2m"], ser["cloud_cover_low"], ser["wind_dir"])
    wmean = float(np.mean(ser["wind_speed_10m"]))
    xs = wetness.X_WEST + FOG_CELL * (np.arange(FOG_W) + 0.5)
    lag = wetness.lag_seconds(xs, wmean) / (step_min * 60.0)
    nt = (n - 1) * step_min // frame_min + 1
    rows = (nt + FOG_COLS - 1) // FOG_COLS
    sheet = np.zeros((rows * FOG_H, FOG_COLS * FOG_W), np.uint8)
    for k in range(nt):
        i = k * frame_min / step_min
        tile = 255.0 * wetness.lagged(s, i - lag)[None, :] * tr80
        r, c = divmod(k, FOG_COLS)
        sheet[r * FOG_H:(r + 1) * FOG_H, c * FOG_W:(c + 1) * FOG_W] = np.clip(np.rint(tile), 0, 255).astype(np.uint8)
    return Image.fromarray(sheet, "L"), rows, nt


def write(cfg, data_dir, out_dir, until=None):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    P = load_inputs(data_dir)
    nights = get_nights(cfg, data_dir, until)
    tr80 = city_tracer_80m(data_dir)
    step_min = cfg["replay"]["step_min"]
    frame_min = cfg["replay"]["fog_frame_min"]
    N = len(P["x"])
    entries = []
    for nt in nights:
        d = nt["id"]
        (out / d).mkdir(exist_ok=True)
        W, diag = run_night(P, nt, step_min)
        n = W.shape[0]
        (out / d / "streets.u8").write_bytes(np.rint(W).astype(np.uint8).tobytes())
        img, rows, ntiles = fog_sheet(tr80, nt["ser"], step_min, frame_min)
        img.save(out / d / "fog.png", optimize=True)
        local = [x.astimezone(weather.PT) for x in nt["ser"]["t_utc"]]
        ser = nt["ser"]
        wj = {"t": [x.isoformat() for x in local], "temp_c": np.round(ser["temperature_2m"], 2).tolist(),
              "dewpoint_c": np.round(ser["dew_point_2m"], 2).tolist(), "wind_ms": np.round(ser["wind_speed_10m"], 2).tolist(),
              "wind_dir": np.round(ser["wind_dir"], 1).tolist(), "cloud_low_pct": np.round(ser["cloud_cover_low"], 1).tolist(),
              "sun_el": np.round(diag["sun_el"], 2).tolist(), "precip_mm": np.round(ser["precipitation"], 2).tolist(),
              "shortwave_wm2": np.round(ser["shortwave_radiation"], 1).tolist()}
        (out / d / "weather.json").write_text(json.dumps(wj, separators=(",", ":")))
        meta = night_meta(nt)
        d0 = local[0]
        entries.append({
            "id": d, "label": f"{d0:%a} {d0.day} {d0:%b}, {nt['kind']} night", "kind": nt["kind"], "step_min": step_min,
            "n_frames": n, "start_iso": local[0].isoformat(), "streets": f"{d}/streets.u8", "fog": f"{d}/fog.png",
            "fog_grid": {"row0": "north", "w": FOG_W, "h": FOG_H, "cols": FOG_COLS, "rows": rows, "frame_min": frame_min, "x0": 540100, "y0": 4171500,
                         "cell_m": FOG_CELL},
            "weather": f"{d}/weather.json", "approx_direction": meta["approx_direction"],
            "info": {k: round(v, 2) if isinstance(v, float) else v for k, v in meta.items()},
            "w_dawn_mean": round(float(W[int(DAWN_STEP_H * 60 / step_min)].mean()), 1),
            "w_dawn_p95": round(float(np.percentile(W[int(DAWN_STEP_H * 60 / step_min)], 95)), 1),
        })
        print(d, nt["kind"], "tier", nt["tier"], "W09 mean/p95", entries[-1]["w_dawn_mean"], entries[-1]["w_dawn_p95"],
              "approx" if meta["approx_direction"] else "", flush=True)
    index = {"zone": {"name": "Potrero Hill", "n_points": int(N)}, "model": "wetness proxy, hand-set constants (MVP)",
             "constants": wetness.CONSTANTS, "fog_sheet": "tile row 0 = north, column 0 = west; pixel = 255*s(t-lag)*tracer(band 0)",
             "nights": entries}
    (out / "index.json").write_text(json.dumps(index, indent=1))
    tot = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print("replay bytes", tot)
    return index
