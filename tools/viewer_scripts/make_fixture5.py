"""Test fixture for guide 08 part B: the guide 04 fixture (live_beta of wt_sim04: real hourly tiles, weather, fog3d files) copied into wt_sim0809 plus a SYNTHETIC fog5/ folder.
Synthetic: each 5-minute frame is the blend of the two real hourly tiles around it, drifted east by 1 cell per 5 minutes inside the hour, so the 5-minute frames differ visibly from a plain hourly blend.
This tests the viewer plumbing (index, sheets, key frame line, blends, label kinds), not the model. 3D files use the guide 03 code path (volume / encode / atlas) like fog5.write.
Usage: make_fixture5.py [first_hour] [last_hour]   (data hours since start_iso; default 8 and 12)"""
import datetime as dt
import hashlib
import io
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

WT = Path(r"C:\Users\George\AppData\Local\Temp\wt_sim0809")
sys.path.insert(0, str(WT))
from microclimate import city, dirlib  # noqa: E402
from microclimate.live import fog3d as F3  # noqa: E402

SRC = Path(r"C:\Users\George\AppData\Local\Temp\wt_sim04\site\v3\live_beta")
LB = WT / "site" / "v3" / "live_beta"
if not LB.exists():
    shutil.copytree(SRC, LB)
H0 = int(sys.argv[1]) if len(sys.argv) > 1 else 8
H1 = int(sys.argv[2]) if len(sys.argv) > 2 else 12
idx = json.loads((LB / "index.json").read_text())
wx = json.loads((LB / "weather.json").read_text())
g = idx["fog_grid"]
tiles = []
for fn, cnt in zip(idx["fog_files"], g["tiles_per_file"]):
    a = np.asarray(Image.open(LB / fn))
    for k in range(cnt):
        r, c = divmod(k, g["cols"])
        tiles.append(a[r * g["h"]:(r + 1) * g["h"], c * g["w"]:(c + 1) * g["w"]].astype(np.float32))
S = city.load_static(Path(r"C:/Users/George/source/repos/SF-DEATH-FOG-DETECTION/data\live\live_static.npz"))
S = {"tr80": S["tr80"], "dirs": S["dirs"]}
T3, info = F3.load(Path(r"C:/Users/George/source/repos/SF-DEATH-FOG-DETECTION/data\live\live_tracer3d.npz"), S["dirs"])
dirs = np.asarray(S["dirs"], float)
wd = np.asarray(wx["wind_dir"], float)
top = np.asarray(wx["fog_top_m"], float)
t0 = dt.datetime.fromisoformat(idx["start_iso"]).astimezone(dt.timezone.utc)
out = LB / "fog5"
shutil.rmtree(out, ignore_errors=True)
out.mkdir()
W, Hh = g["w"], g["h"]
newest = H0 + (H1 - H0) * 0.6  # the newest scan, in data hours; later frames are forecast


def png(arr):
    bio = io.BytesIO(); Image.fromarray(arr, "L").save(bio, format="PNG", optimize=True); return bio.getvalue()


def hsh(b):
    return hashlib.sha1(b).hexdigest()[:8]


frames, A5 = [], []
n5 = (H1 - H0) * 12 + 1
for i in range(n5):
    hk = H0 + i / 12
    k0 = int(np.floor(hk)); w = hk - k0; k1 = min(k0 + 1, len(tiles) - 1)
    A = tiles[k0] * (1 - w) + tiles[k1] * w
    A = np.roll(A, int(round(w * 12)), axis=1)  # 1 cell east per 5 minutes inside the hour
    A5.append(np.clip(A, 0, 255).astype(np.uint8))
    kind = "forecast" if hk > newest else ("interpolated" if i % 7 == 3 else "observed")
    t = t0 + dt.timedelta(hours=hk)
    frames.append(dict(t=t.isoformat(), kind=kind, conf=0.6, tile=None, vol=None, slot=0))
hours = {}
for i, f in enumerate(frames):
    hours.setdefault(int(dt.datetime.fromisoformat(f["t"]).timestamp() // 3600), []).append(i)
total = 0
for h, ks in sorted(hours.items()):
    sheet = np.zeros((2 * Hh, 8 * W), np.uint8)
    for j, i in enumerate(ks):
        r, c = divmod(j, 8); sheet[r * Hh:(r + 1) * Hh, c * W:(c + 1) * W] = A5[i]
    data = png(sheet); name = f"tile_{dt.datetime.fromtimestamp(h * 3600, tz=dt.timezone.utc):%Y%m%dT%H}_{hsh(data)}.png"
    (out / name).write_bytes(data); total += len(data)
    for j, i in enumerate(ks):
        frames[i]["tile"] = name; frames[i]["slot"] = j
nvol = 0
for i, f in enumerate(frames):
    hk = H0 + i / 12
    if not (H0 + 0.5 <= hk <= H1 - 0.5):  # volumes only for part of the window, like the last 3 h and next 1 h
        continue
    ti = int(round(hk))
    a = F3.band_weights(top[ti], 0.0)
    Wt = dirlib.weights(wd[ti], dirs)[0]
    rows = F3.dir_rows(dirs, Wt, T3["dirs"])
    if not rows or not np.any(a > 0):
        continue
    trb = np.tensordot(Wt, S["tr80"], axes=1)
    amp = np.where(trb > 0.05, np.clip(A5[i].astype(np.float32) / 255.0 / np.maximum(trb, 0.05), 0, 1), 0).astype(np.float32)
    u8 = F3.encode(F3.volume(T3, rows, a, amp, weight3d=None))
    if F3.top_layer(u8) < 0:
        continue
    data = png(np.asarray(F3.atlas(u8))); name = f"vol_{dt.datetime.fromisoformat(f['t']):%Y%m%dT%H%M}_{hsh(data)}.png"
    (out / name).write_bytes(data); f["vol"] = name; f["top"] = int(F3.top_layer(u8)); total += len(data); nvol += 1
now = t0 + dt.timedelta(hours=newest)
doc = dict(schema=1, step_min=5, generated_iso=now.isoformat(), nz=F3.NZ, dz_m=F3.DZ, cols=F3.COLS, rows=F3.ROWS, w=W, h=Hh, frames=frames)
(out / "index.json").write_text(json.dumps(doc, separators=(",", ":")))
idx["fog5"] = dict(index="fog5/index.json")
(LB / "index.json").write_text(json.dumps(idx, indent=1))
print("fog5 frames", len(frames), "vols", nvol, "bytes", total, "window hours", H0, H1, "kinds", {k: sum(f["kind"] == k for f in frames) for k in ("observed", "interpolated", "forecast")})
