"""Guide 09 B test fixture: SYNTHETIC macro frames (live_beta/macro/index.json + PNGs, goes5.macro_rgba layout) in the wt_sim0809 fixture folder, and idx["goes5"]["macro"] in its index.json.
A bank west of the coast with a tongue through the Golden Gate, drifting east 3 km per half hour, top 280 m (B = 35), confidence 0.9 (lower in the north-east corner). Tests the viewer plumbing, not the data.
Usage: make_macro_fixture.py [first_data_hour] [last_data_hour]"""
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

LB = Path(r"C:\Users\George\AppData\Local\Temp\wt_sim0809\site\v3\live_beta")
H0 = float(sys.argv[1]) if len(sys.argv) > 1 else 6
H1 = float(sys.argv[2]) if len(sys.argv) > 2 else 12
idx = json.loads((LB / "index.json").read_text())
t0 = dt.datetime.fromisoformat(idx["start_iso"]).astimezone(dt.timezone.utc)
box = (-123.3, 37.2, -121.9, 38.3)
res = 0.01
w, h = int(round((box[2] - box[0]) / res)) + 1, int(round((box[3] - box[1]) / res)) + 1
lon = box[0] + res * np.arange(w)[None, :]
lat = box[3] - res * np.arange(h)[:, None]
out = LB / "macro"
out.mkdir(exist_ok=True)
for f in out.glob("*.png"):
    f.unlink()
frames = []
rng = np.random.default_rng(3)
noise = rng.random((h // 4 + 2, w // 4 + 2))
noise = np.kron(noise, np.ones((4, 4)))[:h, :w]
k = 0
hk = H0
while hk <= H1 + 1e-9:
    t = t0 + dt.timedelta(hours=hk)
    shift = 0.03 * (hk - H0) * 2  # degrees east over time
    coast = -122.52 + shift - np.clip((lat - 37.78) * 0.0, 0, 0)
    p = np.clip((coast - lon) / 0.25 + 0.15, 0, 1) * (0.6 + 0.5 * noise)
    gate = np.exp(-(((lon + 122.52 - shift * 0.4) / 0.08) ** 2 + ((lat - 37.82) / 0.04) ** 2))
    p = np.clip(np.maximum(p, gate * 0.9) - 0.1, 0, 1) * np.clip((lat - 37.3) / 0.15, 0, 1) * np.clip((38.2 - lat) / 0.15, 0, 1)
    c = np.clip(0.95 - 0.5 * np.clip((lon + 122.2) / 0.3, 0, 1) * np.clip((lat - 37.9) / 0.4, 0, 1), 0, 1)
    top = np.where(p > 0.05, 280.0, 0.0)
    rgba = np.empty((h, w, 4), np.uint8)
    rgba[..., 0] = np.rint(255 * p); rgba[..., 1] = np.rint(255 * c); rgba[..., 2] = np.rint(top / 8.0); rgba[..., 3] = 255
    name = f"{t:%Y%m%dT%H%M}.png"
    Image.fromarray(rgba, "RGBA").save(out / name)
    frames.append(dict(t=t.isoformat(), file=name))
    hk += 0.5
(out / "index.json").write_text(json.dumps(dict(schema=1, box=list(box), w=w, h=h, res=res, row0="north", rule_version=1, frames=frames), indent=1))
idx.setdefault("goes5", {})["macro"] = "macro/index.json"
(LB / "index.json").write_text(json.dumps(idx, indent=1))
print("macro frames", len(frames), w, h, frames[0]["t"], frames[-1]["t"])
