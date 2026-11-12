"""Reslice the per-night street wetness onto the ribbon samples: streets.u8 [97, N] -> streets_r.u8 [97, S].

Each ribbon sample takes the wetness of its source point (src in ribbons.bin), then every strip is smoothed along its arc,
per frame: kernel [1,2,1]/4, renormalised at the strip ends, applied twice, edge-preserving (a neighbour that differs by more
than EDGE index points gets weight 0), never across strips. The sentinel 255 (missing) survives and never feeds a neighbour.
Also writes the "streets_r", "ribbons", "ribbons_n", "schema" keys into replay/index.json (all other keys kept).

Usage: python reslice_replay.py [replay_dir] [ribbons.bin]
"""
import json
import os
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
EDGE = 25.0


def read_ribbons(path):
    b = Path(path).read_bytes()
    magic, S, nS, nIdx, width, ver = struct.unpack_from("<IIIIfI", b, 0)
    assert magic == 0x31424952, "not a RIB1 file"
    off = 24
    pad = lambda n: n + (-n) % 4  # noqa: E731
    sizes = [("pos", 12 * S), ("nrm", 8 * S), ("src", 4 * S), ("st", 2 * S), ("mask", 2 * S), ("side", S), ("cls", S)] + ([("fade", S)] if ver < 3 else []) + [
             ("stripStart", 4 * (nS + 1))]
    out = {"S": S, "nStrips": nS, "nIdx": nIdx, "width": width, "version": ver}
    dt = {"pos": ("<f4", (S, 3)), "nrm": ("<f4", (S, 2)), "src": ("<u4", (S,)), "st": ("<u2", (S,)), "mask": ("<u2", (S,)),
          "side": ("u1", (S,)), "cls": ("u1", (S,)), "fade": ("u1", (S,)), "stripStart": ("<u4", (nS + 1,))}
    for name, n in sizes:
        d, shp = dt[name]
        out[name] = np.frombuffer(b, d, count=int(np.prod(shp)), offset=off).reshape(shp)
        off += pad(n)
    return out


def smooth(a, passes=2, edge=EDGE):
    """a: uint8 [F, n] along-strip wetness (255 = missing) -> smoothed uint8 [F, n]."""
    miss = a == 255
    v = a.astype(np.float32)
    n = a.shape[1]
    if n < 2:
        return a.copy()
    for _ in range(passes):
        num = 2.0 * v
        den = np.full_like(v, 2.0)
        for sh in (1, -1):
            nb = np.roll(v, sh, axis=1)
            ok = np.roll(~miss, sh, axis=1)
            if sh == 1:
                ok[:, 0] = False
            else:
                ok[:, -1] = False
            ok &= np.abs(nb - v) <= edge
            num += np.where(ok, nb, 0.0)
            den += ok
        v = np.where(miss, 255.0, num / den)
    return np.where(miss, 255, np.clip(np.rint(v), 0, 100)).astype(np.uint8)


def reslice_night(streets_u8, N, rib):
    W = np.fromfile(streets_u8, np.uint8).reshape(-1, N)
    out = W[:, rib["src"]].copy()
    ss = rib["stripStart"]
    for i in range(rib["nStrips"]):
        a, b = int(ss[i]), int(ss[i + 1])
        out[:, a:b] = smooth(out[:, a:b])
    return out


def run(replay_dir=None, ribbons=None):
    rep = Path(replay_dir or ROOT / "site" / "v3" / "replay")
    rib = read_ribbons(ribbons or ROOT / "site" / "v3" / "data" / "ribbons.bin")
    idxp = rep / "index.json"
    index = json.loads(idxp.read_text())
    N = index["nights"][0].get("n_points") or json.loads((ROOT / "site" / "v3" / "data" / "points.json").read_text())["n"]
    for n in index["nights"]:
        out = reslice_night(rep / n["streets"], N, rib)
        assert out.shape[1] == rib["S"]
        (rep / n["id"] / "streets_r.u8").write_bytes(out.tobytes())
        n["streets_r"] = f"{n['id']}/streets_r.u8"
        n["ribbons"] = "data/ribbons.bin"
        n["ribbons_n"] = rib["S"]
    index["schema"] = 2
    st = idxp.stat()
    new = json.dumps(index, indent=1)
    if new != idxp.read_text():
        idxp.write_text(new)
        os.utime(idxp, (st.st_atime, st.st_mtime))   # keep the mtime so the routes pipeline does not see a fresh replay
    return len(index["nights"]), rib["S"]


if __name__ == "__main__":
    print(run(*(sys.argv[1:3])))
