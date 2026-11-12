"""Publish a finished live build, or record why it was not.
usage: finalize.py <new dir> <out dir> <exit code> <message>
Success (exit 0 and status.json ok): the new files replace the old ones, status.json last, with scheduled: true so the page shows
the real data age and the delayed banner. Failure: the previous output stays and its status.json gains last_run_failed + the reason."""
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

new, out, rc, msg = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), (sys.argv[4] if len(sys.argv) > 4 else " \)
out.mkdir(parents=True, exist_ok=True)
now = dt.datetime.now(dt.timezone.utc).isoformat()


def load(p):
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return {}


ns = load(new / "status.json")
if rc == 0 and ns.get("ok") and (new / "index.json").exists():
    for f in sorted(new.iterdir()):
        if f.name != "status.json":
            shutil.copyfile(f, out / f.name)
    ns["scheduled"] = True
    ns["run_url"] = "{}/{}/actions/runs/{}".format(os.environ.get("GITHUB_SERVER_URL", "https://github.com"), os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_RUN_ID", ""))
    (out / "status.json").write_text(json.dumps(ns, indent=1))
    print("published the new live output, generated", ns.get("generated_iso"))
else:
    prev = load(out / "status.json")
    prev.update(last_run_failed=True, last_failure_iso=now, last_failure=msg or ns.get("last_failure") or f"build exit code {rc}",
                sources_failed=ns.get("sources_failed", prev.get("sources_failed", {})))
    if not (out / "index.json").exists():
        prev["ok"] = False
    (out / "status.json").write_text(json.dumps(prev, indent=1))
    print("build failed (exit", rc, "): previous live output kept;", prev["last_failure"])
