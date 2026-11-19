#!/usr/bin/env python3
"""Standard local preview server and headless Playwright verifier for site/v3/.

Replaces ad-hoc viewer_scripts/ screenshot runners with a single deterministic CLI.

Usage:
    python tools/preview.py --serve [--port 8130]
    python tools/preview.py --check ["?beta=1&night=2026-10-01"] [--phone] [--out artifacts/preview]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V3_DIR = ROOT / "site" / "v3"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass


def start_server(port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    """Start a background HTTP server serving `site/v3/` and return (server, bound_port)."""
    srv = ThreadingHTTPServer(("127.0.0.1", port), partial(QuietHandler, directory=str(V3_DIR)))
    bound_port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, bound_port


def run_check(query: str, out_dir: Path, phone: bool = False, wait_ms: int = 3500) -> int:
    """Render `site/v3/` with the given query string in headless Edge, save screenshot, and report console/page errors."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    srv, port = start_server(0)
    q = query if query.startswith("?") or not query else f"?{query}"
    url = f"http://127.0.0.1:{port}/{q}"
    errors: list[dict] = []
    kind = "phone" if phone else "desktop"
    shot_path = out_dir / f"preview_{kind}.png"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="msedge",
                args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
            )
            viewport = {"width": 390, "height": 844} if phone else {"width": 1440, "height": 900}
            ctx = browser.new_context(
                viewport=viewport,
                device_scale_factor=2 if phone else 1,
                has_touch=phone,
                is_mobile=phone,
            )
            page = ctx.new_page()
            page.on(
                "console",
                lambda m: errors.append({"type": m.type, "text": m.text[:250]})
                if m.type == "error" and "bus" not in m.text.lower() and "ERR_FAILED" not in m.text
                else None,
            )
            page.on("pageerror", lambda e: errors.append({"type": "pageerror", "text": str(e)[:300]}))
            page.goto(url, wait_until="load")
            page.wait_for_function("window.__ready", timeout=120000)
            page.wait_for_timeout(wait_ms)
            page.screenshot(path=str(shot_path))
            browser.close()
    finally:
        srv.shutdown()

    summary = {"url": url, "kind": kind, "screenshot": str(shot_path), "errors": errors, "ok": len(errors) == 0}
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Serve or headless-verify site/v3/ locally.")
    parser.add_argument("--serve", action="store_true", help="Run a local static HTTP server on site/v3/ until Ctrl+C.")
    parser.add_argument("--port", type=int, default=8130, help="Port for --serve (default: 8130).")
    parser.add_argument("--check", nargs="?", const="?beta=1&night=2026-10-01", help="Headless Playwright verification for query string.")
    parser.add_argument("--phone", action="store_true", help="Use 390x844 dsf2 mobile viewport for --check.")
    parser.add_argument("--out", default=str(ROOT / "artifacts" / "preview"), help="Output directory for --check screenshots.")
    args = parser.parse_args(argv)

    if args.serve:
        srv = ThreadingHTTPServer(("127.0.0.1", args.port), partial(SimpleHTTPRequestHandler, directory=str(V3_DIR)))
        print(f"Serving {V3_DIR} at http://127.0.0.1:{args.port}/ (beta: http://127.0.0.1:{args.port}/?beta=1)")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    query = args.check if args.check is not None else "?beta=1&night=2026-10-01"
    return run_check(query, Path(args.out), phone=args.phone)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
