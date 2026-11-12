"""Generate MOCK replay data (3 nights) for testing the viewer. Not real simulation output.
Usage: python make_mock_replay.py [out_dir=../replay_mock]
Needs numpy + Pillow.  Reads ../data/points.bin and meta.json.
"""
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
from PIL import Image

here = Path(__file__).resolve().parent
data = here.parent / "data"
out = Path(sys.argv[1]) if len(sys.argv) > 1 else here.parent / "replay_mock"
meta = json.loads((data / "meta.json").read_text())
pj = json.loads((data / "points.json").read_text())
N = pj["n"]
raw = (data / "points.bin").read_bytes()
P = np.frombuffer(raw, np.float32, N * 4).reshape(N, 4)
side = np.frombuffer(raw, np.uint8, N, N * 16)
sti = np.frombuffer(raw, np.uint16, N, N * 18)

STEP, NF, FRAME_MIN = 10, 97, 30
W, H, COLS, CELL = 214, 203, 8, 80
NT = (NF - 1) * STEP // FRAME_MIN + 2          # tiles incl. one spare for cross-fade
ROWS = -(-NT // COLS)
tz = timezone(timedelta(hours=-8))

NIGHTS = [("2026-11-03", "Mon 3 Nov, fog night", "fog", 1),
          ("2026-11-04", "Tue 4 Nov, dry night", "dry", 2),
          ("2026-11-05", "Wed 5 Nov, fog night", "fog", 3)]


def smooth_noise(rng, shape, sigma):
    f = np.fft.fft2(rng.standard_normal(shape))
    ky = np.fft.fftfreq(shape[0])[:, None]; kx = np.fft.fftfreq(shape[1])[None, :]
    f *= np.exp(-(kx ** 2 + ky ** 2) * (np.pi * sigma) ** 2 * 2)
    a = np.real(np.fft.ifft2(f)); a -= a.mean()
    return a / (a.std() + 1e-9)


def make_streets(rng, kind):
    street_bias = rng.normal(0, 0.18, len(pj["streets"]))[sti]
    elev = np.clip(P[:, 1] / 90.0, 0, 1)                 # low = wetter (fog pools in the bowl)
    pt_noise = rng.normal(0, 0.08, N)
    side_bias = np.where(side == 1, 0.07, -0.07)
    peak = 0.85 if kind == "fog" else 0.28
    base = np.clip(peak * (1.0 - 0.45 * elev) + street_bias + side_bias + pt_noise, 0.02, 1.0)
    t = np.linspace(0, 1, NF)
    onset = rng.uniform(0.25, 0.5, N) if kind == "fog" else rng.uniform(0.6, 0.9, N)
    g = 1 / (1 + np.exp(-(t[:, None] - onset[None, :]) * 9))           # smooth rise overnight
    wob = 0.04 * np.sin(2 * np.pi * (t[:, None] * 3 + rng.uniform(0, 1, N)[None, :]))
    w = np.clip(base[None, :] * g + wob * g, 0, 1) * 100
    return np.round(w).astype(np.uint8)


def make_fog(rng, kind):
    amp = 1.0 if kind == "fog" else 0.25
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    big = smooth_noise(rng, (H + 80, W + 400), 24)
    detail = smooth_noise(rng, (H + 80, W + 400), 7)
    tiles = []
    for k in range(NT):
        shift = int(k * 3.0)                              # drifts east ~ 3 cells (240 m) per 30 min
        n = (0.8 * big + 0.35 * detail)[40:40 + H, 200 - shift:200 - shift + W]
        t = k / (NT - 1)
        west = np.clip(1.3 - xx / W * 1.6, 0, 1)         # more fog to the west (ocean side)
        level = amp * (0.25 + 0.75 * np.sin(np.pi * min(1, t * 1.1)) ** 0.8)
        v = np.clip((n * 0.5 + 0.55) * west * level + 0.05 * level - 0.02, 0, 1)
        tiles.append((v * 255).astype(np.uint8))
    sheet = np.zeros((ROWS * H, COLS * W), np.uint8)
    for k, tl in enumerate(tiles):
        r, c = divmod(k, COLS)
        sheet[r * H:(r + 1) * H, c * W:(c + 1) * W] = tl
    return sheet


def make_weather(kind, start):
    t = np.arange(NF) / (NF - 1)
    ts = [(start + timedelta(minutes=STEP * i)).isoformat() for i in range(NF)]
    temp = 14 - 6 * t + (0 if kind == "fog" else -1.5 * t)
    dew = temp - (np.where(kind == "fog", 3.5, 7.0) * (1 - 0.9 * np.clip(t * 1.4, 0, 1)) if kind == "fog" else 7 - 2 * t)
    local_h = (18 + t * 16) % 24
    sun = np.where(local_h > 17.5, 20 - (local_h - 17.5) * 16, np.where(local_h < 6.5, -60, (local_h - 6.5) * 12 - 8))
    return {"t": ts, "temp_c": np.round(temp, 1).tolist(), "dewpoint_c": np.round(dew, 1).tolist(),
            "wind_ms": np.round(4.5 + 1.5 * np.sin(t * 6), 1).tolist(), "wind_dir": [270 + int(10 * np.sin(x * 5)) for x in t],
            "cloud_low_pct": np.round(np.clip((80 if kind == "fog" else 20) * (0.4 + t), 0, 100)).astype(int).tolist(),
            "sun_el": np.round(np.clip(sun, -60, 40), 1).tolist()}


out.mkdir(parents=True, exist_ok=True)
index = {"mock": True, "zone": {"name": "Potrero Hill", "n_points": N}, "nights": []}
for nid, label, kind, seed in NIGHTS:
    rng = np.random.default_rng(seed)
    d = out / nid; d.mkdir(exist_ok=True)
    make_streets(rng, kind).tofile(d / "streets.u8")
    Image.fromarray(make_fog(rng, kind), "L").save(d / "fog.png", optimize=True)
    start = datetime.fromisoformat(nid + "T18:00:00").replace(tzinfo=tz)
    (d / "weather.json").write_text(json.dumps(make_weather(kind, start), separators=(",", ":")))
    index["nights"].append({
        "id": nid, "label": label, "kind": kind, "step_min": STEP, "n_frames": NF, "start_iso": start.isoformat(),
        "streets": f"{nid}/streets.u8", "fog": f"{nid}/fog.png",
        "fog_grid": {"w": W, "h": H, "cols": COLS, "rows": ROWS, "frame_min": FRAME_MIN,
                     "x0": meta["x0"], "y0": meta["y0"], "cell_m": CELL},
        "weather": f"{nid}/weather.json"})
(out / "index.json").write_text(json.dumps(index, indent=1))
print("wrote", out)
