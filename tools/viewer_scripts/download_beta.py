"""Download the REAL published beta live folder (index, status, weather, day files, fog sheets, ribbons, 3D files, on-demand extras) into a local viewer folder for screenshots.
usage: python download_beta.py <dest folder, e.g. wt_sim08\\site\\v3\\live_beta>"""
import json
import sys
import time
import urllib.request
from pathlib import Path

B = "https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/v3/live_beta/"
dest = Path(sys.argv[1])
dest.mkdir(parents=True, exist_ok=True)


def get(n):
    for i in range(4):
        try:
            return urllib.request.urlopen(B + n + f"?x={int(time.time())}", timeout=60).read()
        except Exception:
            time.sleep(3)
    return None


idx_raw = get("index.json")
idx = json.loads(idx_raw)
names = ["index.json", "status.json", idx.get("weather"), idx.get("ribbons_city"), idx.get("ribbons_city_meta")] + list(idx.get("frame_files", [])) + list(idx.get("fog_files", []))
names += [x for x in (idx.get("fog3d") or {}).get("files", []) if x]
for k in ("sun", "tsurf"):
    names += list((idx.get(k) or {}).get("files") or [])
for v in ((idx.get("film") or {}).get("files") or {}).values():
    names += list(v)
miss = 0
for n in [n for n in dict.fromkeys(names) if n]:
    d = idx_raw if n == "index.json" else get(n)
    if d is None:
        miss += 1
        print("missing", n)
        continue
    (dest / n).write_bytes(d)
print("downloaded", len(names), "files, missing", miss, "generated", json.loads((dest / "status.json").read_text())["generated_iso"])
