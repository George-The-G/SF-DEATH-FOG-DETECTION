"""Download the last published live output (site/v3/live on GitHub Pages) so a failed or skipped run never leaves the page empty.
usage: fetch_previous.py <pages live url> <out dir>. Nothing is written unless every file arrived (all or nothing)."""
import http.client
import json
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

base, out = sys.argv[1].rstrip("/"), Path(sys.argv[2])


ATTEMPTS, BACKOFF_S = 4, (2.0, 5.0, 10.0)   # a transient 503 on one file must not cold-start the whole beta state


def _transient(e):
    if isinstance(e, urllib.error.HTTPError):
        return e.code >= 500          # 404 and other 4xx are real answers: no retry
    return isinstance(e, (OSError, http.client.HTTPException))   # timeouts, connection resets, URLError, truncated reads


def get(name):
    for attempt in range(ATTEMPTS):
        req = urllib.request.Request(f"{base}/{name}?cb={int(time.time())}", headers={"User-Agent": "deathfog-live-ci", "Cache-Control": "no-cache"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            if attempt == ATTEMPTS - 1 or not _transient(e):
                raise
            print(f"{name}: {type(e).__name__}: {e}; retry {attempt + 1}/{ATTEMPTS - 1}")
            time.sleep(BACKOFF_S[attempt])


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
# Guide 08: the 5-minute fog folder fog5/ named in index.json "fog5" (beta), best effort: without it the viewer falls back to the hourly fog. Only names of the exact shapes the build writes.
if EXTRAS and idx.get("fog5"):
    try:
        raw5 = get("fog5/index.json")
        d5 = json.loads(raw5)
        n5 = [f.get(k) for f in d5.get("frames", []) for k in ("tile", "vol")]
        n5 = [n for n in dict.fromkeys(n5) if n and re.fullmatch(r"(tile_\d{8}T\d{2}|vol_\d{8}T\d{4})_[0-9a-f]{8}\.png", n)]
        (out / "fog5").mkdir(parents=True, exist_ok=True)
        for n in n5:
            (out / "fog5" / n).write_bytes(get("fog5/" + n))
        (out / "fog5" / "index.json").write_bytes(raw5)
        print(f"fetched fog5/: {len(n5)} files")
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(out / "fog5", ignore_errors=True)
        print("fog5/ not fetched:", e)
# Guide 09: the macro cloud frames (macro/index.json + <yyyymmddThhmm>.png) named in index.json goes5.macro (beta), best effort like fog5/: a skipped or failed build must not leave an index that names a missing folder.
if EXTRAS and (idx.get("goes5") or {}).get("macro") == "macro/index.json":
    try:
        rawm = get("macro/index.json")
        dm = json.loads(rawm)
        nm = [f.get("file") for f in dm.get("frames", [])]
        nm = [n for n in dict.fromkeys(nm) if n and re.fullmatch(r"\d{8}T\d{4}\.png", n)]
        (out / "macro").mkdir(parents=True, exist_ok=True)
        for n in nm:
            (out / "macro" / n).write_bytes(get("macro/" + n))
        (out / "macro" / "index.json").write_bytes(rawm)
        print(f"fetched macro/: {len(nm)} files")
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(out / "macro", ignore_errors=True)
        print("macro/ not fetched:", e)
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
