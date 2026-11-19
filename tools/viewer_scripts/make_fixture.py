"""Test fixture for guide 04: the published beta live folder (downloaded) plus SYNTHETIC-DERIVED 3D fog files built from its real 2D tiles.
amp = tile / (255 * tracer) (clipped), direction weights from the real hourly wind, top from the real fog_top_m series; everything else is the guide-03 code path."""
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

WT = Path(r"C:\Users\George\AppData\Local\Temp\wt_sim04")
sys.path.insert(0, str(WT))
from microclimate import city, dirlib  # noqa: E402
from microclimate.live import fog3d as F3, outputs  # noqa: E402

LB = WT / "site" / "v3" / "live_beta"
LB.mkdir(parents=True, exist_ok=True)
B = "https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/v3/live_beta/"
idx = json.loads(urllib.request.urlopen(B + "index.json?x=1").read())
names = ["index.json", "status.json", "weather.json", "ribbons_city.bin", "ribbons_city_meta.json"] + idx["frame_files"] + idx["fog_files"]
for n in names:
    if not (LB / n).exists() or n in ("index.json", "status.json"):
        (LB / n).write_bytes(urllib.request.urlopen(B + n + "?x=1").read())
idx = json.loads((LB / "index.json").read_text())
wx = json.loads((LB / "weather.json").read_text())
g = idx["fog_grid"]
tiles = []
for fn, rows, cnt in zip(idx["fog_files"], g["rows"], g["tiles_per_file"]):
    a = np.asarray(Image.open(LB / fn))
    for k in range(cnt):
        r, c = divmod(k, g["cols"])
        tiles.append(a[r * g["h"]:(r + 1) * g["h"], c * g["w"]:(c + 1) * g["w"]].astype(np.float32))
n = len(tiles)
S = city.load_static(Path(r"C:/Users/George/source/repos/SF-DEATH-FOG-DETECTION/data\live\live_static.npz"))
S = {"tr80": S["tr80"], "dirs": S["dirs"]}
Tz, info = F3.load(Path(r"C:/Users/George/source/repos/SF-DEATH-FOG-DETECTION/data\live\live_tracer3d.npz"), S["dirs"])
wd = np.asarray(wx["wind_dir"], float)
amps, Wts = [], []
for f in range(n):
    Wt = dirlib.weights(wd[f], np.asarray(S["dirs"], float))[0]
    trb = np.tensordot(Wt, S["tr80"], axes=1)
    amp = np.where(trb > 0.05, np.clip(tiles[f] / 255.0 / np.maximum(trb, 0.05), 0, 1), 0).astype(np.float32)
    amps.append(amp)
    Wts.append(Wt)
top = np.asarray(wx["fog_top_m"], float)
Fo = {"fog_top": top}
t0 = dt.datetime.fromisoformat(idx["start_iso"]).astimezone(dt.timezone.utc)
th = [t0 + dt.timedelta(hours=k) for k in range(n)]
field, files = outputs.write_fog3d(LB, (Tz, info), S, Fo, amps, Wts, 1, th, th[n // 2])
idx["fog3d"] = field
(LB / "index.json").write_text(json.dumps(idx, indent=1))
print("frames", n, "3D files", len(files), "bytes", field["bytes"], "top_layer sample", field["top_layer"][:30])
