"""SYNTHETIC ribbons fixture (RIB1) so the viewer's ribbon renderer can be built before the real data exists.
Not real data. Writes site/v3/replay_synth/{ribbons.bin, ribbons_meta.json, index.json, <night>/streets_r.u8 (+weather/fog copies)}.
Routes come from data/routes.json (two lanes per route, plus perpendicular stub 'side blocks' every ~150 m);
wetness is the nearest real points.bin row's replay wetness, smoothed along each strip.
Usage: python make_synthetic_ribbons.py [night ids...]   (default 2026-10-01 2026-10-03)
"""
import json, shutil, struct, sys
from pathlib import Path
import numpy as np

here = Path(__file__).resolve().parent
v3 = here.parent
data, replay, out = v3 / "data", v3 / "replay", v3 / "replay_synth"
nights = sys.argv[1:] or ["2026-10-01", "2026-10-03"]
WIDTH, LANE, STEP = 6.5, 3.4, 10.0

pj = json.loads((data / "points.json").read_text())
N = pj["n"]
raw = (data / "points.bin").read_bytes()
P = np.frombuffer(raw, np.float32, N * 4).reshape(N, 4)
pside = np.frombuffer(raw, np.uint8, N, N * 16)
pst = np.frombuffer(raw, np.uint16, N, N * 18)
routes = json.loads((data / "routes.json").read_text())["routes"]

strips = []  # dict(pos[m,3], nrm[m,2], cls, side, mask, fade[m])


def resample(poly, s, step):
    poly = np.asarray(poly, float); s = np.asarray(s, float)
    t = np.arange(0, s[-1] + 1e-6, step)
    return np.stack([np.interp(t, s, poly[:, i]) for i in range(3)], 1)


def unit_normals(c):
    d = np.gradient(c[:, [0, 2]], axis=0)
    d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-6)
    return np.stack([-d[:, 1], d[:, 0]], 1)  # right-hand side (x east, z south)


for r in routes:
    c = resample(r["poly"], r["s"], STEP)
    n = unit_normals(c)
    for side in (0, 1):
        sign = 1 if side else -1
        p = c.copy(); p[:, [0, 2]] += n * LANE * sign
        strips.append(dict(pos=p, nrm=n, cls=0, side=side, mask=1 << r["index"], fade=np.full(len(p), 255)))
    # side blocks: perpendicular stubs every ~150 m, both directions
    for i in range(6, len(c) - 6, 15):
        for dirn in (1, -1):
            L = np.arange(1, 13) * STEP
            base = c[i, [0, 2]] + n[i] * 3.4 * 0 + n[i] * dirn * (L[:, None] + 4)
            ys = np.full(len(L), c[i, 1])
            sc = np.stack([base[:, 0], ys, base[:, 1]], 1)
            tang = np.array([n[i, 1], -n[i, 0]])  # along the main street = lane offset direction for the stub
            sn = np.tile(tang, (len(L), 1))
            for side in (0, 1):
                p = sc.copy(); p[:, [0, 2]] += sn * LANE * (1 if side else -1)
                fade = np.clip(255 * (1 - (np.arange(len(L)) / len(L)) * 0.8), 0, 255).astype(int)
                strips.append(dict(pos=p, nrm=sn, cls=1, side=side, mask=0, fade=fade))

S = sum(len(s["pos"]) for s in strips)
pos = np.concatenate([s["pos"] for s in strips]).astype(np.float32)
nrm = np.concatenate([s["nrm"] for s in strips]).astype(np.float32)
cls = np.concatenate([np.full(len(s["pos"]), s["cls"]) for s in strips]).astype(np.uint8)
side = np.concatenate([np.full(len(s["pos"]), s["side"]) for s in strips]).astype(np.uint8)
mask = np.concatenate([np.full(len(s["pos"]), s["mask"]) for s in strips]).astype(np.uint16)
fade = np.concatenate([s["fade"] for s in strips]).astype(np.uint8)
start = np.cumsum([0] + [len(s["pos"]) for s in strips]).astype(np.uint32)

# nearest points.bin row with the same side
src = np.zeros(S, np.uint32)
for sd in (0, 1):
    sel = np.where(side == sd)[0]; cand = np.where(pside == sd)[0]
    for a in range(0, len(sel), 512):
        q = sel[a:a + 512]
        d = (pos[q, None, 0] - P[None, cand, 0]) ** 2 + (pos[q, None, 2] - P[None, cand, 2]) ** 2
        src[q] = cand[np.argmin(d, 1)]
st = pst[src].astype(np.uint16)

idx = []
bs = np.zeros((len(strips), 4), np.float32)
for k, s in enumerate(strips):
    a, m = int(start[k]), len(s["pos"])
    for i in range(m - 1):
        v = 2 * (a + i)
        idx += [v, v + 1, v + 2, v + 1, v + 3, v + 2]
    c = s["pos"].mean(0); bs[k] = [*c, np.linalg.norm(s["pos"] - c, axis=1).max() + 5]
idx = np.array(idx, np.uint32)


def pad(b):
    return b + b"\0" * (-len(b) % 4)


out.mkdir(exist_ok=True)
blob = struct.pack("<IIIIfI", 0x31424952, S, len(strips), len(idx), WIDTH, 1)
for a in (pos, nrm, src, st, mask, side, cls, fade, start, bs, idx):
    blob += pad(np.ascontiguousarray(a).tobytes())
(out / "ribbons.bin").write_bytes(blob)
(out / "ribbons_meta.json").write_text(json.dumps(dict(
    S=S, nStrips=len(strips), widthM=WIDTH, streets=pj["streets"], synthetic=True,
    strips=[dict(way=k, side=s["side"], street=int(st[start[k]]), cls=s["cls"], route_mask=int(s["mask"]), start=int(start[k]), count=len(s["pos"])) for k, s in enumerate(strips)],
    route_lengths_m=[r["length_m"] for r in routes], side_block_counts=[int((cls == 1).sum())])))

index = json.loads((replay / "index.json").read_text())
keep = [n for n in index["nights"] if n["id"] in nights]
for n in keep:
    d = out / n["id"]; d.mkdir(exist_ok=True)
    u8 = np.fromfile(replay / n["streets"], np.uint8).reshape(n["n_frames"], N)
    w = u8[:, src].astype(np.float32)
    for _ in range(3):  # smooth along each strip
        for k in range(len(strips)):
            a, b = int(start[k]), int(start[k + 1])
            seg = w[:, a:b]; pad_ = np.concatenate([seg[:, :1], seg, seg[:, -1:]], 1)
            w[:, a:b] = (pad_[:, :-2] + 2 * pad_[:, 1:-1] + pad_[:, 2:]) / 4
    (d / "streets_r.u8").write_bytes(np.clip(np.round(w), 0, 100).astype(np.uint8).tobytes())
    for f in (n["weather"], n.get("fog")):
        if f: shutil.copy(replay / f, out / f)
    n["streets_r"] = f"{n['id']}/streets_r.u8"; n["ribbons"] = "replay_synth/ribbons.bin"; n["ribbons_n"] = S
index["nights"] = keep; index["mock"] = True; index["schema"] = 2
(out / "index.json").write_text(json.dumps(index))
print("synthetic ribbons: S=%d strips=%d idx=%d nights=%s" % (S, len(strips), len(idx), [n["id"] for n in keep]))
