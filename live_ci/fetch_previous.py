"""Download the last published live output (site/v3/live on GitHub Pages) so a failed or skipped run never leaves the page empty.
usage: fetch_previous.py <pages live url> <out dir>. Nothing is written unless every file arrived (all or nothing)."""
import json
import re
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
    # multi-scale guide 03/05: the 3D fog atlases named in index.json fog3d.files (null = no file for that hour) only this exact name shape (a name from the remote index is never used as a path otherwise)
    import re
    f3 = idx.get("fog3d") or {}
    names += [n for n in (f3.get("files") or []) if n and re.fullmatch(r"fog3d_\d{3}\.png", n)]
names = [n for n in dict.fromkeys(names) if n]
tmp = Path(tempfile.mkdtemp())
# One consistent version: a deployment that lands while the files are being fetched would mix two versions (sheets of one run with 3D files of the next). index.json is read again after the download
# and the whole fetch is repeated (up to 3 times) when its data_as_of changed; otherwise the previous output counts as incomplete.
for attempt in range(3):
    try:
        for n in names:
            (tmp / n).write_bytes(idx_raw if n == "index.json" else get(n))
        again = json.loads(get("index.json"))
    except Exception as e:  # noqa: BLE001
        print("previous output incomplete:", e)
        shutil.rmtree(tmp, ignore_errors=True)
        sys.exit(1)
    if again.get("data_as_of") == idx.get("data_as_of"):
        break
    print(f"a new version was published while fetching (attempt {attempt + 1}): fetching again")
    idx_raw = get("index.json")
    idx = json.loads(idx_raw)
    names = ["index.json", "status.json", idx.get("weather"), idx.get("ribbons_city"), idx.get("ribbons_city_meta")] + list(idx.get("frame_files", [])) + list(idx.get("fog_files", []))
    if EXTRAS:
        for k in ("sun", "tsurf"):
            names += list((idx.get(k) or {}).get("files") or [])
        for v in ((idx.get("film") or {}).get("files") or {}).values():
            names += list(v)
        names += [n for n in ((idx.get("fog3d") or {}).get("files") or []) if n and re.fullmatch(r"fog3d_\d{3}\.png", n)]
    names = [n for n in dict.fromkeys(names) if n]
else:
    print("previous output kept changing while fetching: treated as incomplete")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1)
out.mkdir(parents=True, exist_ok=True)
for n in names:
    shutil.copyfile(tmp / n, out / n)
shutil.rmtree(tmp, ignore_errors=True)
print(f"fetched {len(names)} files of the previous live output")
# Beta channel: stations.json and the rolling GOES sunlight cache (dsr_cache.json) are carried too, best effort.
for n in (("stations.json", "dsr_cache.json", "fog_base_dbg.png") if EXTRAS else ()):   # fog_base_dbg.png: guide 05 debug picture, best effort
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
# Carried 5-minute GOES store (multi-scale guide 07; files listed in index.json "goes5"."files" as 'goes5/grid.json', 'goes5/<date>/<HH>.npz'): best effort, a missing file only means a cold store.
_g5 = (idx.get("goes5") or {}).get("files") or []
_ok5 = 0
for n in _g5:
    try:
        parts = n.split("/")
        if parts[0] != "goes5" or ".." in parts or n.startswith("/") or "\\" in n or not (n.endswith(".npz") or n.endswith(".json")):
            continue
        (out / n).parent.mkdir(parents=True, exist_ok=True)
        (out / n).write_bytes(get(n))
        _ok5 += 1
    except Exception as e:  # noqa: BLE001
        print("goes5 file", n, "not fetched:", e)
if _g5:
    print(f"fetched {_ok5} of {len(_g5)} goes5 store files")
