"""Carry the satellite cloud-score files of the last published live output (guide 04): cloud_score.json (the rolling history the next build merges into)
and the observed-cloud tiles sat*.png that index.json lists under "sat_score". Best effort: a missing file is normal on the first run and this script
always exits 0. usage: fetch_extras.py <pages live url> <out dir>   (run after fetch_previous.py, which puts index.json into <out dir>)"""
import json
import sys
import time
import urllib.request
from pathlib import Path

base, out = sys.argv[1].rstrip("/"), Path(sys.argv[2])


def get(name):
    req = urllib.request.Request(f"{base}/{name}?cb={int(time.time())}", headers={"User-Agent": "deathfog-live-ci", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main():
    out.mkdir(parents=True, exist_ok=True)
    idx_path = out / "index.json"
    try:
        idx = json.loads(idx_path.read_text() if idx_path.exists() else get("index.json"))
    except Exception as e:  # noqa: BLE001
        print("cloud score: no index.json to read:", e)
        idx = {}
    ss = idx.get("sat_score") or {}
    try:
        (out / ss.get("cloud_score", "cloud_score.json")).write_bytes(get(ss.get("cloud_score", "cloud_score.json")))
        print("cloud score: carried cloud_score.json")
    except Exception as e:  # noqa: BLE001
        print("cloud score: no previous cloud_score.json (", e, ") - history starts with this run")
    files = (ss.get("satellite") or {}).get("files") or []
    got = []
    try:
        for n in files:
            data = get(n)
            (out / n).write_bytes(data)
            got.append(n)
        if files:
            print(f"cloud score: carried {len(files)} satellite tiles")
    except Exception as e:  # noqa: BLE001
        print("cloud score: satellite tiles incomplete, dropped from index.json:", e)
        for n in got:
            (out / n).unlink(missing_ok=True)
        if idx_path.exists() and "satellite" in ss:
            ss.pop("satellite")
            idx["sat_score"] = ss
            idx_path.write_text(json.dumps(idx, indent=1))


try:
    main()
except Exception as e:  # noqa: BLE001
    print("cloud score: carry skipped:", e)
sys.exit(0)
