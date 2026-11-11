"""Notifiers: `stdout` (default) and `ntfy` (free push to your phone via the ntfy app)."""
import urllib.request


def make_notifier(cfg, dry_run=False):
    ncfg = cfg.get("notify", {})
    kind = ncfg.get("type", "stdout")

    def to_stdout(title, body, priority, click):
        print(f"\n=== ALERT [{priority}] {title} ===\n{body}\n")

    def to_ntfy(title, body, priority, click):
        topic = ncfg["ntfy_topic"]                      # pick an unguessable topic name
        server = ncfg.get("ntfy_server", "https://ntfy.sh")
        headers = {"Title": title.encode("ascii", "replace").decode(), "Priority": priority, "Tags": "fog"}
        if click:
            headers["Click"] = click
        req = urllib.request.Request(f"{server}/{topic}", data=body.encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15):
            pass

    if dry_run or kind == "stdout":
        return to_stdout
    if kind == "ntfy":
        return to_ntfy
    raise ValueError(f"unknown notify type {kind!r}")
