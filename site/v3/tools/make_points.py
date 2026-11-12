"""Pack the combined street-point table (grids/zone/points.csv, all zones) into compact viewer files
(site/v3/data/points.bin, points.json). Route geometry goes to data/routes.json (written by `python -m microclimate routes`).

points.bin layout (little endian), N = number of points:
  float32[N,4]  x, y, z scene metres, bearing deg          offset 0
  uint8[N]      side (0 left, 1 right)                      offset 16N
  uint8[N]      on_route (any route)                        offset 17N
  uint16[N]     street index into points.json streets       offset 18N
  uint16[N]     route_mask (bit i = i-th route in routes.json "index")   offset 20N
  uint8[N]      zone index into points.json zones           offset 22N
Scene frame: x east from meta.x0, y elevation, z = -(north from meta.y0).
Usage: python make_points.py <points.csv> <out_dir>
"""
import csv, json, sys
from pathlib import Path
import numpy as np

csv_path, out = Path(sys.argv[1]), Path(sys.argv[2])
meta = json.loads((out / "meta.json").read_text())
rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
N = len(rows)
streets, sidx, zones, zidx = [], {}, [], {}
arr = np.zeros((N, 4), np.float32)
side = np.zeros(N, np.uint8); route = np.zeros(N, np.uint8); st = np.zeros(N, np.uint16)
mask = np.zeros(N, np.uint16); zone = np.zeros(N, np.uint8)
for k, r in enumerate(rows):
    assert int(r["id"]) == k
    arr[k] = (float(r["x"]) - meta["x0"], float(r["z"]), -(float(r["y"]) - meta["y0"]), float(r["bearing"]))
    side[k] = 1 if r["side"] == "right" else 0
    route[k] = int(r["on_route"])
    mask[k] = int(r.get("route_mask") or route[k])
    nm = r["street"] or "(unnamed)"
    if nm not in sidx:
        sidx[nm] = len(streets); streets.append(nm)
    st[k] = sidx[nm]
    zid = r.get("zone") or "potrero"
    if zid not in zidx:
        zidx[zid] = len(zones); zones.append({"id": zid, "row0": k})
    zone[k] = zidx[zid]
for i, z in enumerate(zones):
    z["row1"] = zones[i + 1]["row0"] if i + 1 < len(zones) else N
zj = Path(csv_path).parent / "zones.json"
if zj.exists():
    names = {z["id"]: z["name"] for z in json.loads(zj.read_text())["zones"]}
    for z in zones:
        z["name"] = names.get(z["id"], z["id"])
with open(out / "points.bin", "wb") as f:
    for a in (arr, side, route, st, mask, zone):
        f.write(a.tobytes())
j = {"n": N, "streets": streets, "zones": zones}
(out / "points.json").write_text(json.dumps(j, separators=(",", ":")))
print(N, len(streets), len(zones), arr[:, 0].min(), arr[:, 0].max(), arr[:, 2].min(), arr[:, 2].max(), route.sum())
