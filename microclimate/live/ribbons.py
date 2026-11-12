"""City street ribbons for the live output (PLAN_LIVE_DATA.md 5.2), built offline from live_static.npz (not part of the live job).

Same RIB1 / version 3 layout as site/v3/data/ribbons.bin (see make_ribbons.py), but one ribbon per STREET (centre line = the mean of the left and
right lane rows) instead of one per lane, cls 1 (district-style street), sampled every `stride` locations (default 2 = ~40 m; locations are ~20 m).
`src` = global static row id of the left lane row (ids >= 29,018 are city rows). The wetness of a ribbon sample is the mean of its two lane rows.

Outputs (out_dir): ribbons_city.bin, ribbons_city_meta.json (S, nStrips, streets, strips), live_ribbons.npz (column maps used by the live job:
A_src / A_strips = the shipped district ribbons.bin, B_srcL / B_srcR / B_strips = the city ribbons).
Run: python -m microclimate.live ribbons [--stride N] [--static PATH] [--out DIR]
"""
import json
import math
import struct
from pathlib import Path

import numpy as np

WIDTH_M = 6.5
MAX_SCALE = 2.0
MAX_GAP_M = 45.0
X0, Y0 = 540100.0, 4171500.0


def read_rib(path):
    b = Path(path).read_bytes()
    magic, S, nS, nIdx, width, ver = struct.unpack_from("<IIIIfI", b, 0)
    assert magic == 0x31424952 and ver == 3
    off = 24
    pad = lambda n: n + (-n) % 4  # noqa: E731
    out = {"S": S, "nStrips": nS}
    for name, dt_, n in (("pos", "<f4", 3 * S), ("nrm", "<f4", 2 * S), ("src", "<u4", S), ("st", "<u2", S), ("mask", "<u2", S), ("side", "u1", S),
                         ("cls", "u1", S), ("stripStart", "<u4", nS + 1)):
        out[name] = np.frombuffer(b, dt_, count=n, offset=off)
        off += pad(np.dtype(dt_).itemsize * n)
    return out


def locations(P):
    """Pair the city rows (left, right) into street locations. -> dict arrays: cx, cy, cz, srcL, srcR, way, seg, st."""
    z = P["zone"]
    nd = int(P["n_district"])
    ids = np.nonzero(z == 2)[0]
    way, seg, side = P["way"][ids], P["seg"][ids], P["side"][ids]
    out = []
    i = 0
    n = len(ids)
    while i < n:
        j = i + 1
        if side[i] == 0 and j < n and side[j] == 1 and way[j] == way[i] and seg[j] == seg[i]:
            out.append((ids[i], ids[j]))
            i += 2
        else:
            out.append((ids[i], ids[i]))
            i += 1
    L = np.array(out)
    a, b = L[:, 0], L[:, 1]
    return dict(cx=(P["x"][a] + P["x"][b]) / 2, cy=(P["y"][a] + P["y"][b]) / 2, cz=P["z"][a], srcL=a, srcR=b, way=P["way"][a], seg=P["seg"][a],
                st=P["street"][a])


def build(static_path, out_dir, ribbons_a, stride=2, log=print):
    from .. import city
    P = city.load_static(static_path)
    Lc = locations(P)
    n = len(Lc["cx"])
    order = np.arange(n)
    # ---- chains: same way, consecutive segments, small gaps
    brk = np.ones(n, bool)
    d = np.hypot(np.diff(Lc["cx"]), np.diff(Lc["cy"]))
    same = (Lc["way"][1:] == Lc["way"][:-1]) & (Lc["seg"][1:] - Lc["seg"][:-1] >= 0) & (Lc["seg"][1:] - Lc["seg"][:-1] <= 1) & (d <= MAX_GAP_M)
    brk[1:] = ~same
    starts = np.nonzero(brk)[0]
    ends = np.append(starts[1:], n)
    samples = []   # (x, y, z, srcL, srcR, stname_id, strip)
    strips = []
    names, nidx = [], {}
    for s0, s1 in zip(starts, ends):
        idx = np.arange(s0, s1)
        if len(idx) < 2:
            continue
        keep = list(range(0, len(idx), stride))
        if keep[-1] != len(idx) - 1:
            keep.append(len(idx) - 1)
        idx = idx[keep]
        # split at sharp corners (beyond the mitre clamp) and at duplicate points
        pieces, cur = [], [idx[0]]
        for k in range(1, len(idx) - 1):
            cur.append(idx[k])
            d1 = (Lc["cx"][idx[k]] - Lc["cx"][idx[k - 1]], Lc["cy"][idx[k]] - Lc["cy"][idx[k - 1]])
            d2 = (Lc["cx"][idx[k + 1]] - Lc["cx"][idx[k]], Lc["cy"][idx[k + 1]] - Lc["cy"][idx[k]])
            l1, l2 = math.hypot(*d1), math.hypot(*d2)
            if l1 < 0.05 or l2 < 0.05:
                continue
            h = math.acos(max(-1.0, min(1.0, (d1[0] * d2[0] + d1[1] * d2[1]) / (l1 * l2)))) / 2
            if math.cos(h) < 1 / MAX_SCALE:
                pieces.append(cur)
                cur = [idx[k]]
        cur.append(idx[-1])
        pieces.append(cur)
        for pv in pieces:
            pv = [p for i, p in enumerate(pv) if i == 0 or math.hypot(Lc["cx"][p] - Lc["cx"][pv[i - 1]], Lc["cy"][p] - Lc["cy"][pv[i - 1]]) > 0.05]
            if len(pv) < 2:
                continue
            nm = int(Lc["st"][pv[0]])
            if nm not in nidx:
                nidx[nm] = len(names)
                names.append(nm)
            segn = []
            for a, b in zip(pv[:-1], pv[1:]):
                dx, dy = Lc["cx"][b] - Lc["cx"][a], Lc["cy"][b] - Lc["cy"][a]
                l = math.hypot(dx, dy)
                segn.append((-dy / l, dx / l))
            start = len(samples)
            for i, p in enumerate(pv):
                if i == 0:
                    nx_, ny_, sc = segn[0][0], segn[0][1], 1.0
                elif i == len(pv) - 1:
                    nx_, ny_, sc = segn[-1][0], segn[-1][1], 1.0
                else:
                    sx, sy = segn[i - 1][0] + segn[i][0], segn[i - 1][1] + segn[i][1]
                    ln_ = math.hypot(sx, sy)
                    nx_, ny_, sc = sx / ln_, sy / ln_, min(MAX_SCALE, 2.0 / ln_)
                samples.append((Lc["cx"][p] - X0, float(Lc["cz"][p]), -(Lc["cy"][p] - Y0), nx_ * sc, -ny_ * sc, int(Lc["srcL"][p]), int(Lc["srcR"][p]), nidx[nm]))
            strips.append((start, len(pv), nidx[nm]))
    S, nS = len(samples), len(strips)
    arr = np.array(samples, float)
    pos = arr[:, :3].astype(np.float32)
    nrm = arr[:, 3:5].astype(np.float32)
    srcL, srcR = arr[:, 5].astype(np.uint32), arr[:, 6].astype(np.uint32)
    st = arr[:, 7].astype(np.uint16)
    stripStart = np.array([s[0] for s in strips] + [S], np.uint32)
    bs = np.zeros((nS, 4), np.float32)
    idxs = []
    for i, (a, c, _) in enumerate(strips):
        b = a + c
        off = np.c_[nrm[a:b, 0], np.zeros(b - a), nrm[a:b, 1]] * WIDTH_M / 2
        Pp = np.concatenate([pos[a:b] + off, pos[a:b] - off])
        ctr = (Pp.min(0) + Pp.max(0)) / 2
        bs[i] = (*ctr, np.max(np.linalg.norm(Pp - ctr, axis=1)))
        k = np.arange(a, b - 1) * 2
        idxs.append(np.stack([k, k + 1, k + 2, k + 1, k + 3, k + 2], axis=1).ravel())
    idx = np.concatenate(idxs).astype(np.uint32)
    pad = lambda buf: buf + b"\0" * (-len(buf) % 4)  # noqa: E731
    blob = struct.pack("<IIIIfI", 0x31424952, S, nS, len(idx), WIDTH_M, 3)
    for a_ in (pos, nrm, srcL, st, np.zeros(S, np.uint16), np.zeros(S, np.uint8), np.ones(S, np.uint8), stripStart, bs, idx):
        blob += pad(a_.tobytes())
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ribbons_city.bin").write_bytes(blob)
    sn = [str(P["street_names"][m]) or "(unnamed)" for m in names]
    (out / "ribbons_city_meta.json").write_text(json.dumps({"S": S, "nStrips": nS, "widthM": WIDTH_M, "version": 3, "streets": sn,
                                                           "strips": [[a, c, 1, k] for a, c, k in strips], "stride": stride}, separators=(",", ":")))
    A = read_rib(ribbons_a)
    np.savez_compressed(out / "live_ribbons.npz", A_src=A["src"].astype(np.uint32), A_strips=A["stripStart"].astype(np.uint32), B_srcL=srcL, B_srcR=srcR,
                        B_strips=stripStart)
    info = {"S_city": S, "strips": nS, "locations": int(n), "stride": stride, "bin_bytes": len(blob), "S_district": int(A["S"]),
            "street_names": len(sn)}
    log(json.dumps(info))
    return info
