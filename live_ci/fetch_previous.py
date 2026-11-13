"""Download the last published live output (site/v3/live on GitHub Pages) so a failed or skipped run never leaves the page empty.
usage: fetch_previous.py <pages live url> <out dir>. Nothing is written unless every file arrived (all or nothing)."""
import json
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

base, out = sys.argv[1].rstrip("/"), Path(sys.argv[2])


def get(name):
    req = urllib.request.Request(f"{base}/{name}?cb={int(time.time())}", headers={"User-Agent": "deathfog-live-ci", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


EXTRAS = "--extras" in sys.argv[3:]   # beta channel: also the on-demand files of the switches that are on (sun / road temperature / film files named in index.json)
idx_raw = get("index.json")
idx = json.loads(idx_raw)
names = ["index.json", "status.json", idx.get("weather"), idx.get("ribbons_city"), idx.get("ribbons_city_meta")] + list(idx.get("frame_files", [])) + list(idx.get("fog_files", []))
if EXTRAS:
    for k in ("sun", "tsurf"):
        names += list((idx.get(k) or {}).get("files") or [])
    fl = (idx.get("film") or {}).get("files") or {}
    for v in fl.values():
        names += list(v)
names = [n for n in dict.fromkeys(names) if n]
tmp = Path(tempfile.mkdtemp())
try:
    for n in names:
        (tmp / n).write_bytes(idx_raw if n == "index.json" else get(n))
except Exception as e:  # noqa: BLE001
    print("previous output incomplete:", e)
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1)
out.mkdir(parents=True, exist_ok=True)
for n in names:
    shutil.copyfile(tmp / n, out / n)
shutil.rmtree(tmp, ignore_errors=True)
print(f"fetched {len(names)} files of the previous live output")
# Beta channel: stations.json and the rolling GOES sunlight cache (dsr_cache.json) are carried too, best effort.
for n in (("stations.json", "dsr_cache.json") if EXTRAS else ()):
    try:
        (out / n).write_bytes(get(n))
        print("fetched", n)
    except Exception as e:  # noqa: BLE001
        print(n, "not fetched:", e)
# Carried model state (state_*.npz, listed in index.json "state_files"): best effort, a missing file only means a cold start, never a failure.
for n in idx.get("state_files", []) or []:
    try:
        if not (n.startswith("state_") and n.endswith(".npz") and "/" not in n and "\\" not in n):
            continue
        (out / n).write_bytes(get(n))
        print("fetched state file", n)
    except Exception as e:  # noqa: BLE001
        print("state file", n, "not fetched:", e)
