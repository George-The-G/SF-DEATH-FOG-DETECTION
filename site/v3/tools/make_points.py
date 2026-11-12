"""Pack potrero_points.csv + route.geojson into compact viewer files (site/v3/data/points.bin, points.json).

points.bin layout (little endian): float32[N,4] (x,y,z scene metres, bearing deg), uint8[N] side (0 left, 1 right),
uint8[N] on_route, uint16[N] street index.  Scene frame: x east from meta.x0, y elevation, z = -(north from meta.y0).
Usage: python make_points.py <potrero_points.csv> <out_dir>
"""
import csv, json, sys
from pathlib import Path
import numpy as np
from pyproj import Transformer

csv_path, out = Path(sys.argv[1]), Path(sys.argv[2])
meta = json.loads((out / "meta.json").read_text())
rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
N = len(rows)
streets, sidx = [], {}
arr = np.zeros((N, 4), np.float32)
side = np.zeros(N, np.uint8); route = np.zeros(N, np.uint8); st = np.zeros(N, np.uint16)
for k, r in enumerate(rows):
    assert int(r["id"]) == k
    arr[k] = (float(r["x"]) - meta["x0"], float(r["z"]), -(float(r["y"]) - meta["y0"]), float(r["bearing"]))
    side[k] = 1 if r["side"] == "right" else 0
    route[k] = int(r["on_route"])
    nm = r["street"] or "(unnamed)"
    if nm not in sidx:
        sidx[nm] = len(streets); streets.append(nm)
    st[k] = sidx[nm]
with open(out / "points.bin", "wb") as f:
    f.write(arr.tobytes()); f.write(side.tobytes()); f.write(route.tobytes()); f.write(st.tobytes())

gj = json.loads((out / "route.geojson").read_text())
tr = Transformer.from_crs(4326, 32610, always_xy=True)
def sc(lon, lat, z):
    x, y = tr.transform(lon, lat)
    return [round(x - meta["x0"], 1), round(z, 1), round(-(y - meta["y0"]), 1)]
route_pts, bus = [], None
for f in gj["features"]:
    if f["properties"]["kind"] == "route":
        route_pts = [sc(*c) for c in f["geometry"]["coordinates"]]
for f in gj["features"]:
    if f["properties"]["kind"] == "bus_stop":
        c = f["geometry"]["coordinates"]
        bus = sc(c[0], c[1], route_pts[-1][1] if route_pts else 0); bus.append(f["properties"].get("name", ""))
j = {"n": N, "streets": streets, "route": route_pts, "bus_stop": bus,
     "start": route_pts[0] if route_pts else None}
(out / "points.json").write_text(json.dumps(j, separators=(",", ":")))
print(N, len(streets), arr[:, 0].min(), arr[:, 0].max(), arr[:, 2].min(), arr[:, 2].max(), route.sum())
