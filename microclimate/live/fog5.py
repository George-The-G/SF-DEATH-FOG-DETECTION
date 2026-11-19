"""Guide 08 Part A output (switch config.FOG_TRAVEL / BETA["fog_travel"]; needs goes5 and fog3d): the fog5/ folder.

fog5/index.json  {"schema": 1, "step_min": 5, "frames": [{"t": iso, "kind": "observed|interpolated|forecast", "conf": mean c, "tile": file, "vol": file or null}]}
fog5/tile_<yyyymmddThh>_<hash8>.png   the 12 five-minute tiles of one hour, round(255 * A * trb), same tile layout as fog{d}.png (214 x 203, 8 per row)
fog5/vol_<yyyymmddThhmm>_<hash8>.png  the 3D atlas (guide 03 format) of the frames from 3 h before now to 1 h after
Names carry the first 8 hex of the sha1 of the content, so cached copies are never stale; index.json is the only fixed name. The hourly files are not touched.
Wind here is uniform (see travel.py); the prior is the hourly model amp evaluated at 5-minute resolution, without the GOES factor."""
import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np

from .. import dirlib, replay, wetness
from . import config as C
from . import fog3d as f3
from . import travel as T

VOL_PAST_H, VOL_FWD_H = 3.0, 1.0
FOG5_BUDGET_BYTES = 15_000_000


def _hash(b):
    return hashlib.sha1(b).hexdigest()[:8]


def _png_bytes(arr_u8):
    import io

    from PIL import Image
    bio = io.BytesIO()
    Image.fromarray(arr_u8, "L").save(bio, format="PNG", optimize=True)
    return bio.getvalue()


def _interp_series(series, i):
    s = np.asarray(series, float)
    return float(np.interp(i, np.arange(len(s)), s))


def _interp_dir(deg, i):
    d = np.radians(np.asarray(deg, float))
    return float(np.degrees(np.arctan2(_interp_series(np.sin(d), i), _interp_series(np.cos(d), i))) % 360.0)


def prior_factory(F, xs, ys, t0_unix):
    """prior(t) [H, W]: lagged(s, i - lag) at fractional model step i, lag per 3-hour block of the model steps exactly as outputs.fog_frames computes it."""
    ser, s = F["ser"], F["s_series"]
    n = len(s)
    wdir, wspd = np.asarray(ser["wind_dir"], float), np.asarray(ser["wind_speed_10m"], float)
    blk = int(round(3 * 60 / C.STEP_MIN))
    lagc = {}

    def lag(b):
        if b not in lagc:
            sl = slice(b * blk, min(n, (b + 1) * blk))
            mb = wetness.mean_flow_dir(s[sl], wdir[sl])
            lagc[b] = wetness.lag_seconds_dir(xs[None, :], ys[:, None], mb, float(np.mean(wspd[sl]))) / (C.STEP_MIN * 60.0)
        return lagc[b]

    def prior(t):
        i = (t - t0_unix) / (C.STEP_MIN * 60.0)
        i = float(np.clip(i, 0, n - 1))
        return np.clip(np.asarray(wetness.lagged(s, i - lag(min(int(i) // blk, (n - 1) // blk))), np.float32), 0.0, 1.0)
    return prior


def write(out, F, S, T3, base, t0, now, scan_at, xs, ys, log=print, lid=False, hourly_amps=None):
    """Build the cycle and write fog5/. scan_at(t_unix) -> (p, c) on the tile grid or None. T3 = the guide 03 asset dict; base = fogbase.FogBase or None.
    Returns the status dict (frames, bytes, kinds)."""
    out = Path(out) / "fog5"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.iterdir():
        old.unlink()
    t0u = t0.timestamp()
    now_u = now.timestamp()
    times = T.window(now_u)
    ser = F["ser"]
    n = len(F["s_series"])
    dirs = np.asarray(S["dirs"], float)
    tr80 = S["tr80"]
    prior = prior_factory(F, xs, ys, t0u)

    def iof(t):
        return float(np.clip((t - t0u) / (C.STEP_MIN * 60.0), 0, n - 1))

    def weights(t):
        return dirlib.weights(_interp_dir(ser["wind_dir"], iof(t)), dirs)[0]

    def trb(t):
        return np.tensordot(weights(t), tr80, axes=1).astype(np.float32)

    def wind(t):
        return _interp_series(ser["wind_speed_10m"], iof(t)), _interp_dir(ser["wind_dir"], iof(t))

    hourly_ref_fn = None
    if hourly_amps is not None and len(hourly_amps) >= 2:
        spf = C.OUT_STEP_MIN / C.STEP_MIN
        nh = len(hourly_amps)

        def hourly_ref_fn(t):
            hf = float(np.clip(iof(t) / spf, 0.0, nh - 1.0))
            h0 = int(np.floor(hf))
            h1 = min(h0 + 1, nh - 1)
            u = hf - h0
            return ((1.0 - u) * np.asarray(hourly_amps[h0], np.float32) + u * np.asarray(hourly_amps[h1], np.float32)).astype(np.float32)

    A0 = prior(times[0])
    A, kinds = T.run(times, prior, trb, wind, scan_at, A0, hourly_ref_fn=hourly_ref_fn)
    conf = []
    for t in times:
        sc = scan_at(t)
        conf.append(None if sc is None else round(float(np.mean(sc[1])), 3))
    top_arr = np.asarray(F["fog_top"], float)
    spf_frames = 60.0 / C.STEP_MIN

    def top_at(t):
        return _interp_series(top_arr, iof(t))

    def weight3d_at(k, t):
        tm = top_at(t)
        w3 = None
        if base is not None:
            w3, _ = base.frame(int(round(iof(t) / spf_frames)), A[k], tm)
        if lid and T3 is not None and "z_m" in T3:
            lw = f3.lid_weight(T3["z_m"], tm, C.LID_MARGIN_M, C.LID_FADE_M)
            w3 = lw if w3 is None else w3 * lw
        return w3

    # 2D tiles: one sheet per UTC hour, up to 12 tiles (applying w3[0] floor/lid weight so 5-min tiles agree with hourly tiles)
    frames = [dict(t=dt.datetime.fromtimestamp(int(t), tz=dt.timezone.utc).isoformat(), kind=str(kinds[k]), conf=conf[k], tile=None, vol=None) for k, t in enumerate(times)]
    hours = {}
    for k, t in enumerate(times):
        hours.setdefault(int(t // 3600), []).append(k)
    total = 0
    for h, ks in sorted(hours.items()):
        sheet = np.zeros((2 * replay.FOG_H, 8 * replay.FOG_W), np.uint8)
        for j, k in enumerate(ks):
            w3 = weight3d_at(k, times[k])
            w0 = 1.0 if w3 is None else w3[0]
            tile = np.clip(np.rint(255.0 * A[k] * trb(times[k]) * w0), 0, 255).astype(np.uint8)
            r, c = divmod(j, 8)
            sheet[r * replay.FOG_H:(r + 1) * replay.FOG_H, c * replay.FOG_W:(c + 1) * replay.FOG_W] = tile
        data = _png_bytes(sheet)
        name = f"tile_{dt.datetime.fromtimestamp(h * 3600, tz=dt.timezone.utc):%Y%m%dT%H}_{_hash(data)}.png"
        (out / name).write_bytes(data)
        total += len(data)
        for j, k in enumerate(ks):
            frames[k]["tile"] = name
            frames[k]["slot"] = j
    # 3D files for the last VOL_PAST_H hours and the next VOL_FWD_H hours
    nvol = 0
    for k, t in enumerate(times):
        if not (now_u - VOL_PAST_H * 3600 <= t <= now_u + VOL_FWD_H * 3600):
            continue
        tm = top_at(t)
        a = f3.band_weights(tm, 0.0)
        rows = f3.dir_rows(dirs, weights(t), T3["dirs"])
        if not rows or not np.any(a > 0):
            continue
        w3 = weight3d_at(k, t)
        u8 = f3.encode(f3.volume(T3, rows, a, A[k], weight3d=w3))
        if f3.top_layer(u8) < 0:
            continue
        data = _png_bytes(np.asarray(f3.atlas(u8)))
        name = f"vol_{dt.datetime.fromtimestamp(int(t), tz=dt.timezone.utc):%Y%m%dT%H%M}_{_hash(data)}.png"
        (out / name).write_bytes(data)
        frames[k]["vol"] = name
        frames[k]["top"] = int(f3.top_layer(u8))
        total += len(data)
        nvol += 1
    idx = dict(schema=1, step_min=5, generated_iso=now.isoformat(), nz=f3.NZ, dz_m=f3.DZ, cols=f3.COLS, rows=f3.ROWS, w=replay.FOG_W, h=replay.FOG_H, frames=frames)
    (out / "index.json").write_text(json.dumps(idx, separators=(",", ":")))
    info = dict(applied=True, frames=len(frames), volumes=nvol, bytes=total, over_budget=total > FOG5_BUDGET_BYTES,
                kinds={k: int((kinds == k).sum()) for k in ("observed", "interpolated", "forecast")}, wind="uniform (fogflow.js wind not used yet)")
    log(f"fog5: {info}")
    return info
