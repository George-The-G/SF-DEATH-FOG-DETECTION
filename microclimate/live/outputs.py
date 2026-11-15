"""Render data of the live run (PLAN_LIVE_DATA.md section 5). Files written to <out>:

  index.json            replay-night schema 2 fields + live fields (see write())
  day0.u8 day1.u8 day2.u8   wetness 0..100 per ribbon sample, frame-major [frames_in_file, S] uint8 (255 = missing); S = S_district + S_city columns:
                        the shipped data/ribbons.bin samples first (19,444), then the ribbons_city.bin samples
  fog0.png fog1.png fog2.png  hourly fog tiles of that day (214 x 203 px per tile, 8 tiles per row, row 0 = north, column 0 = west)
  weather.json          hourly coast series (replay weather.json fields)
  ribbons_city.bin / ribbons_city_meta.json   copied from the static folder (built offline by `python -m microclimate.live ribbons`)
  status.json           written by build.py
"""
import gzip
import json
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from .. import dirlib, replay, weather, wetness
from . import config as C

PT = ZoneInfo("America/Los_Angeles")
N_DAYS = 3


def smooth_strips(a, strip_start, passes=2, edge=25.0):
    """Edge-preserving [1,2,1]/4 smoothing along every ribbon strip, per frame (same filter as reslice_replay.smooth). a: uint8 [F, S]."""
    out = a.copy()
    for i in range(len(strip_start) - 1):
        s0, s1 = int(strip_start[i]), int(strip_start[i + 1])
        if s1 - s0 < 2:
            continue
        v = a[:, s0:s1].astype(np.float32)
        miss = a[:, s0:s1] == 255
        for _ in range(passes):
            num, den = 2.0 * v, np.full_like(v, 2.0)
            for sh in (1, -1):
                nb = np.roll(v, sh, axis=1)
                ok = np.roll(~miss, sh, axis=1)
                ok[:, 0 if sh == 1 else -1] = False
                ok &= np.abs(nb - v) <= edge
                num += np.where(ok, nb, 0.0)
                den += ok
            v = np.where(miss, 255.0, num / den)
        out[:, s0:s1] = np.where(miss, 255, np.clip(np.rint(v), 0, 100)).astype(np.uint8)
    return out


def ribbon_cols(a, Rb, how="mean"):
    """Per-street-point array a [F, N] -> ribbon sample columns [F, S_district + S_city] like the day files: district samples as they are, city samples from
    their left / right street points (how: 'mean', 'max' or 'left'). Integer input stays integer."""
    SA, SB = len(Rb["A_src"]), len(Rb["B_srcL"])
    a = np.asarray(a)
    out = np.empty((a.shape[0], SA + SB), a.dtype)
    out[:, :SA] = a[:, Rb["A_src"]]
    L, R = a[:, Rb["B_srcL"]], a[:, Rb["B_srcR"]]
    if how == "max":
        out[:, SA:] = np.maximum(L, R)
    elif how == "left":
        out[:, SA:] = L
    else:
        m = 0.5 * (L.astype(np.float32) + R.astype(np.float32))
        out[:, SA:] = np.rint(m) if np.issubdtype(a.dtype, np.integer) else m
    return out


def write_extras(out, diag, Rb, ranges, idx, local):
    """On-demand files of the switches that are on (never part of the first paint): sun{d}.u8 (sun_clear), depth{d}.u8 dry{d}.u8 cause{d}.u8 (film).
    Returns (index fields, file names)."""
    fields, names = {}, []

    def put(pattern, cols):
        fl = []
        for d, (a, b) in enumerate(ranges):
            fn = pattern.format(d=d)
            (out / fn).write_bytes(np.ascontiguousarray(cols[a:b]).tobytes())
            fl.append(fn)
        names.extend(fl)
        return fl
    if diag.get("sun_u8") is not None:
        fields["sun"] = {"files": put(C.SUN_FILE, ribbon_cols(diag["sun_u8"], Rb, "mean")), "unit_wm2": 10, "max": 250, "layout": "same as frame_files: uint8 [frames, S], round(S / 10 W/m2), 0 = dark or night",
                         "sun_el": np.round(diag["sun_el"][idx], 2).tolist(), "sun_az": np.round(diag["sun_az"][idx], 2).tolist(), "k_mean": np.round(diag["sun_k"], 3).tolist()}
    if diag.get("ts_u8") is not None:
        fields["tsurf"] = {"files": put(C.TSURF_FILE, ribbon_cols(diag["ts_u8"], Rb, "mean")), "formula": "ts_degC = u8 / %g - %g" % (wetness.TS_U8_PER_K, wetness.TS_U8_OFFSET_C),
                           "layout": "same as frame_files: uint8 [frames, S]", "unverified": "textbook heat balance, no road-temperature measurements"}
    if diag.get("film_mm") is not None:
        depth = wetness.depth_u8(ribbon_cols(np.asarray(diag["film_mm"], np.float32), Rb, "mean"))
        fields["film"] = {"slick_mm": wetness.FILM_SLICK, "dry_mm": wetness.DRY_MM, "note": "day files hold slip = 100 * (1 - exp(-F / slick_mm))",
                          "depth_scale": {"ref_mm": wetness.DEPTH_REF_MM, "max_mm": wetness.DEPTH_MAX_MM, "formula": "u8 = round(255 * ln(1 + F / ref_mm) / ln(1 + max_mm / ref_mm))"},
                          "files": {"depth": put(C.DEPTH_FILE, depth), "dry": put(C.DRY_FILE, ribbon_cols(diag["dry_in_h"], Rb, "max")),
                                    "cause": put(C.CAUSE_FILE, ribbon_cols(diag["cause"], Rb, "left"))},
                          "dry_unit_h": 1, "dry_none": 255, "cause_codes": ["none", "fog", "dew", "drizzle", "rain"]}
    return fields, names


FOG3D_BUDGET_BYTES = 12_000_000     # target for all 3D files; over it, only frames from FOG3D_PAST_H before now to FOG3D_FUTURE_H after keep a file (the viewer falls back to 2D elsewhere)
FOG3D_PAST_H, FOG3D_FUTURE_H = 6, 30


last_fogbase = None   # the FogBase of the latest write() (guide 08 reuses it for the 5-minute 3D files); None when fog_base is off


def write_fog3d(out, fog3d, S, F, amps, Wts, spf, t_hours, now, log=print, base=None, dbg_frame=None):
    """fog3d = (T, info) from live.fog3d.load. Writes fog3d_<fff>.png for frames with any fog aloft. Returns (index field, file names)."""
    import datetime as dt
    from . import fog3d as f3
    T, _ = fog3d
    dirs = np.asarray(S["dirs"], float)
    top = np.asarray(F["fog_top"], float)
    nF = len(amps)
    files, tops, sizes = [None] * nF, [-1] * nF, [0] * nF
    base_m, lift_m, dbg = [None] * nF, [None] * nF, None
    for f in range(nF):
        a = f3.band_weights(top[min(f * spf, len(top) - 1)], 0.0)
        rows = f3.dir_rows(dirs, Wts[f], T["dirs"])
        if not rows or not np.any(a > 0):
            continue
        w3 = None
        if base is not None:   # guide 05: cloud floor (and urban lift) as a weight on every level
            w3, bst = base.frame(f, amps[f], top[min(f * spf, len(top) - 1)])
            base_m[f], lift_m[f] = bst["base_m"], bst["lift_m"]
            if f == dbg_frame:
                dbg = bst["base"]
        u8 = f3.encode(f3.volume(T, rows, a, amps[f], weight3d=w3))
        tl = f3.top_layer(u8)
        if tl < 0:
            continue
        fn = f"fog3d_{f:03d}.png"
        f3.atlas(u8).save(out / fn, optimize=True)
        files[f], tops[f], sizes[f] = fn, tl, (out / fn).stat().st_size
    total = sum(sizes)
    windowed = None
    if total > FOG3D_BUDGET_BYTES:
        lo, hi = now - dt.timedelta(hours=FOG3D_PAST_H), now + dt.timedelta(hours=FOG3D_FUTURE_H)
        for f in range(nF):
            if files[f] and not (lo <= t_hours[f] <= hi):
                (out / files[f]).unlink()
                total -= sizes[f]
                files[f], tops[f] = None, -1
        windowed = [FOG3D_PAST_H, FOG3D_FUTURE_H]
    log(f"fog3d: {sum(1 for x in files if x)} of {nF} frames, {total} bytes" + (f" (over {FOG3D_BUDGET_BYTES}: windowed to -{FOG3D_PAST_H} h .. +{FOG3D_FUTURE_H} h)" if windowed else ""))
    field = {"files": files, "nz": f3.NZ, "dz_m": f3.DZ, "z0_m": 0, "cols": f3.COLS, "rows": f3.ROWS, "w": replay.FOG_W, "h": replay.FOG_H, "top_layer": tops, "ref": 16,
             "bytes": total}
    if windowed:
        field["windowed_h"] = windowed
    names = [x for x in files if x]
    if base is not None:
        field["base_m"] = [None if v is None else round(v, 1) for v in base_m]
        field["lift_m"] = [None if v is None else round(v, 1) for v in lift_m]
        if dbg is not None:   # beta only: the base field of the newest observed frame, 4 m per grey level
            from PIL import Image
            Image.fromarray(np.clip(np.rint(dbg / 4.0), 0, 255).astype(np.uint8), "L").save(out / "fog_base_dbg.png", optimize=True)
            names.append("fog_base_dbg.png")
    return field, names


def tile_lonlat():
    from pyproj import Transformer
    t = Transformer.from_crs(32610, 4326, always_xy=True)
    xs = wetness.X_WEST + replay.FOG_CELL * (np.arange(replay.FOG_W) + 0.5)
    ys = 4171500.0 + 20 * 811 - replay.FOG_CELL * (np.arange(replay.FOG_H) + 0.5)
    X, Y = np.meshgrid(xs, ys)
    lon, lat = t.transform(X, Y)
    return xs, ys, lon, lat


def fog_frames(S, diag, F, goes_hours, t_hours, step_per_frame, return_amp=False):
    """Hourly fog tiles uint8 [nF, H, W]: 255 * s(t - lag) * direction-blended 80 m tracer (weights of the HOUR's wind direction), times the GOES
    clear-sky factor for observed hours (cells where GOES sees no low cloud keep at most CLEAR_MAX_OPACITY).
    return_amp=True (guide 03, fog3d): returns (tiles, amps, Wts) with amp = lagged source * GOES factor [H, W] float32 per frame and the direction weights of each frame;
    the tiles are computed exactly as without it."""
    from PIL import Image  # noqa: F401
    tr80, dirs = S["tr80"], np.asarray(S["dirs"], float)
    xs, ys, lon, lat = tile_lonlat()
    ser, s = F["ser"], F["s_series"]
    n = len(s)
    wdir, wspd = np.asarray(ser["wind_dir"], float), np.asarray(ser["wind_speed_10m"], float)
    blk = int(round(3 * 60 / C.STEP_MIN))
    nF = (n + step_per_frame - 1) // step_per_frame
    out = np.zeros((nF, replay.FOG_H, replay.FOG_W), np.uint8)
    lagc = {}
    amps, Wts = [], []
    for f in range(nF):
        i = f * step_per_frame
        b = i // blk
        if b not in lagc:
            sl = slice(b * blk, min(n, (b + 1) * blk))
            mb = wetness.mean_flow_dir(s[sl], wdir[sl])
            lagc[b] = wetness.lag_seconds_dir(xs[None, :], ys[:, None], mb, float(np.mean(wspd[sl]))) / (C.STEP_MIN * 60.0)
        Wt = dirlib.weights(wdir[i], dirs)[0]
        trb = np.tensordot(Wt, tr80, axes=1)
        la = wetness.lagged(s, i - lagc[b])
        tile = 255.0 * la * trb
        fac = None
        lc = goes_hours.get(t_hours[f]) if goes_hours else None
        if lc is not None:
            low = lc.sample(lon, lat, "low")
            cl = lc.sample(lon, lat, "cloudy")
            v = np.maximum(low, 0.5 * cl)
            fac = np.where(np.isfinite(v), C.CLEAR_MAX_OPACITY + (1 - C.CLEAR_MAX_OPACITY) * v, 1.0)
            tile = tile * fac
        if return_amp:
            amps.append(np.asarray(la * (1.0 if fac is None else fac), np.float32) * np.ones((replay.FOG_H, replay.FOG_W), np.float32))
            Wts.append(np.asarray(Wt, float))
        out[f] = np.clip(np.rint(tile), 0, 255).astype(np.uint8)
    return (out, amps, Wts) if return_amp else out


def sheet(tiles):
    from PIL import Image
    nt = len(tiles)
    rows = (nt + replay.FOG_COLS - 1) // replay.FOG_COLS
    img = np.zeros((rows * replay.FOG_H, replay.FOG_COLS * replay.FOG_W), np.uint8)
    for k in range(nt):
        r, c = divmod(k, replay.FOG_COLS)
        img[r * replay.FOG_H:(r + 1) * replay.FOG_H, c * replay.FOG_W:(c + 1) * replay.FOG_W] = tiles[k]
    return Image.fromarray(img, "L"), rows


def write(out, W, diag, F, S, Rb, t0, start, now, goes_hours, static_dir, log=print, tiles_cb=None, fog3d=None, fogbase=None):
    import datetime as dt
    out = Path(out)
    if Rb is None:
        raise RuntimeError("live_ribbons.npz missing (python -m microclimate.live ribbons)")
    nF = W.shape[0]
    spf = C.OUT_STEP_MIN // C.STEP_MIN
    SA, SB = len(Rb["A_src"]), len(Rb["B_srcL"])
    q = np.rint(np.clip(W, 0, 100))
    cols = np.empty((nF, SA + SB), np.uint8)
    cols[:, :SA] = smooth_strips(q[:, Rb["A_src"]].astype(np.uint8), Rb["A_strips"])
    cols[:, SA:] = smooth_strips(np.rint(0.5 * (W[:, Rb["B_srcL"]] + W[:, Rb["B_srcR"]])).clip(0, 100).astype(np.uint8), Rb["B_strips"])
    parts = np.array_split(np.arange(nF), N_DAYS)
    ranges = [[int(p[0]), int(p[-1]) + 1] for p in parts]
    t_hours = [t0 + dt.timedelta(hours=k) for k in range(nF)]
    if fog3d is not None:
        tiles, amps, Wts = fog_frames(S, diag, F, goes_hours, t_hours, spf, return_amp=True)
    else:
        tiles = fog_frames(S, diag, F, goes_hours, t_hours, spf)
    if tiles_cb is not None:
        tiles_cb(tiles, t_hours)        # read-only hand-over for the cloud score (cloud_score.Hook)
    files = {}
    frame_files, fog_files, fog_rows = [], [], []
    for d, (a, b) in enumerate(ranges):
        fn = f"day{d}.u8"
        (out / fn).write_bytes(cols[a:b].tobytes())
        frame_files.append(fn)
        img, rows = sheet(tiles[a:b])
        img.save(out / f"fog{d}.png", optimize=True)
        fog_files.append(f"fog{d}.png")
        fog_rows.append(rows)
    ser = F["ser"]
    idx = np.arange(nF) * spf
    local = [t.astimezone(PT) for t in t_hours]
    extra_fields, extra_files = write_extras(out, diag, Rb, ranges, idx, local)
    if fog3d is not None:
        fbo, fb_info = None, None
        if fogbase is not None:   # guide 05: never fatal, any problem means no cloud floor
            try:
                from . import fogbase as _fb, forcing as _fc
                xs_, ys_, _, _ = tile_lonlat()
                fbo, fb_info = _fb.prepare(F, diag, fog3d[0], (replay.FOG_H, replay.FOG_W), (xs_, ys_), spf, fogbase.get("urban", False), _fc._xy_to_ll, nF)
            except Exception as e:  # noqa: BLE001
                fbo, fb_info = None, dict(applied=False, reason=repr(e)[:160])
        obs_u = F.get("obs_until")
        dbg_f = max([f for f in range(nF) if obs_u is None or t_hours[f] <= obs_u] or [nF - 1])
        globals()["last_fogbase"] = fbo
        f3, f3files = write_fog3d(out, fog3d, S, F, amps, Wts, spf, t_hours, now, log, base=fbo, dbg_frame=dbg_f)
        extra_fields["fog3d"] = f3
        extra_files = list(extra_files) + f3files
    wj = {"t": [x.isoformat() for x in local], "temp_c": np.round(np.asarray(ser["temperature_2m"])[idx], 2).tolist(),
          "dewpoint_c": np.round(np.asarray(ser["dew_point_2m"])[idx], 2).tolist(), "wind_ms": np.round(np.asarray(ser["wind_speed_10m"])[idx], 2).tolist(),
          "wind_dir": np.round(np.asarray(ser["wind_dir"])[idx], 1).tolist(), "cloud_low_pct": np.round(np.asarray(ser["cloud_cover_low"])[idx], 1).tolist(),
          "sun_el": np.round(diag["sun_el"][idx], 2).tolist(), "precip_mm": np.round(np.asarray(ser["precipitation"])[idx], 2).tolist(),
          "shortwave_wm2": np.round(np.asarray(ser["shortwave_radiation"])[idx], 1).tolist(),
          "fog_source": np.round(F["s_series"][idx], 3).tolist(), "fog_top_m": np.round(np.asarray(F["fog_top"])[idx], 0).tolist()}
    (out / "weather.json").write_text(json.dumps(wj, separators=(",", ":")))
    for fn in ("ribbons_city.bin", "ribbons_city_meta.json"):
        src = Path(static_dir) / fn
        if src.exists() and (not (out / fn).exists() or (out / fn).stat().st_size != src.stat().st_size):
            shutil.copyfile(src, out / fn)
    obs_until = F["info"]["obs_until"]
    k_obs = None if obs_until is None else int((dt.datetime.fromisoformat(obs_until) - t0).total_seconds() // 3600)
    index = {
        "schema": 2, "live": True, "id": "live", "label": f"Live (beta): tonight + 2 days (updated {now.astimezone(PT):%a %H:%M} PT)", "kind": "live",
        "step_min": C.OUT_STEP_MIN, "n_frames": nF, "start_iso": local[0].isoformat(), "end_iso": local[-1].isoformat(),
        "start_18pt_iso": start.astimezone(PT).isoformat(), "frame_files": frame_files, "frame_ranges": ranges,
        "frame_layout": "uint8 [frames, ribbons_n + ribbons_city_n], frame-major, 255 = missing; columns = data/ribbons.bin samples then ribbons_city.bin samples",
        "fog_files": fog_files, "fog": fog_files[0],
        "fog_grid": {"row0": "north", "w": replay.FOG_W, "h": replay.FOG_H, "cols": replay.FOG_COLS, "rows": fog_rows, "frame_min": C.OUT_STEP_MIN,
                     "x0": 540100, "y0": 4171500, "cell_m": replay.FOG_CELL, "tiles_per_file": [b - a for a, b in ranges]},
        "weather": "weather.json", "ribbons": "../data/ribbons.bin", "ribbons_n": SA, "ribbons_city": "ribbons_city.bin",
        "ribbons_city_meta": "ribbons_city_meta.json", "ribbons_city_n": SB, "streets_r": frame_files[0],
        "observed_until_iso": None if obs_until is None else dt.datetime.fromisoformat(obs_until).astimezone(PT).isoformat(), "observed_until_frame": k_obs,
        "data_as_of": now.isoformat(), "status": "status.json", "approx_direction": False,
        "model": "wetness proxy, hand-set constants (not calibrated): an index, not a measurement", "constants": wetness.CONSTANTS,
        "info": {"mean_flow_dir": round(float(diag["mean_flow_dir"]), 1), "block_flow_dirs": [round(float(x), 1) for x in diag["block_dirs"]],
                 "dirs_used": diag["dirs_used"], "points": int(W.shape[1]), "w_mean_last_frame": round(float(W[-1].mean()), 2)},
    }
    index.update(extra_fields)   # only the switches that are on add keys: sun (sun_clear), film (film)
    (out / "index.json").write_text(json.dumps(index, indent=1))
    meta = {}
    for fn in ["index.json", "weather.json", "ribbons_city.bin", "ribbons_city_meta.json"] + frame_files + fog_files + extra_files:
        p = out / fn
        if p.exists():
            raw = p.read_bytes()
            meta[fn] = {"bytes": len(raw), "gz": len(gzip.compress(raw, 9))}
    meta["first_paint_gz_extra"] = sum(meta[f]["gz"] for f in ("index.json", "day0.u8", "fog0.png", "ribbons_city.bin", "ribbons_city_meta.json", "weather.json") if f in meta)
    meta["S_district"], meta["S_city"] = SA, SB
    if fog3d is not None:
        meta["fog3d_bytes"] = extra_fields["fog3d"]["bytes"]
        meta["fog3d_base"] = fb_info
    log("outputs: " + json.dumps({k: v for k, v in meta.items() if not isinstance(v, dict)}))
    return meta
