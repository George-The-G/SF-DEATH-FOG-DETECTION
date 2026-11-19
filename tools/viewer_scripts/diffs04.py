"""Guide 04 regression checks: (1) replay night, beta lab: data3d 1 vs 0 pixel diff; (2) public page: this branch vs the merged v3-dev without the viewer change."""
import io
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright


class Q(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(root, port):
    s = ThreadingHTTPServer(("127.0.0.1", port), partial(Q, directory=str(root)))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


NEW = Path(r"C:\Users\George\AppData\Local\Temp\wt_sim04\site\v3")
OLD = Path(r"C:\Users\George\AppData\Local\Temp\wt_ship03\site\v3")
s1, s2 = serve(NEW, 8141), serve(OLD, 8142)
ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
CAM = [3900, 8800, 7600, 8300, 0, -8000]


def shot(p, url, prep=None, wait=14000):
    b = p.chromium.launch(channel="msedge", args=ARGS)
    pg = b.new_context(viewport={"width": 1000, "height": 700}).new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    pg.goto(url, wait_until="load")
    pg.wait_for_function("window.__ready", timeout=240000)
    pg.wait_for_timeout(3000)
    pg.evaluate("() => { window.__dbg.setPlaying && window.__dbg.setPlaying(false); window.__dbg.tNow = 12; }")
    pg.evaluate("(v) => { const D = window.__dbg; D.camera.position.set(v[0], v[1], v[2]); D.controls.target.set(v[3], v[4], v[5]); D.controls.update(); }", CAM)
    if prep:
        pg.evaluate(prep)
    pg.wait_for_timeout(wait)
    im = Image.open(io.BytesIO(pg.screenshot())).convert("RGB")
    b.close()
    return np.asarray(im).astype(int), errs


def diff(a, b):
    d = np.abs(a - b).max(axis=2)
    return int((d > 0).sum()), int(d.max())


with sync_playwright() as p:
    a, e1 = shot(p, "http://127.0.0.1:8141/?beta=1&lab=1&live=0", "() => window.__fogLab.fog.setParams({ data3d: 0 })")
    b, e2 = shot(p, "http://127.0.0.1:8141/?beta=1&lab=1&live=0", "() => window.__fogLab.fog.setParams({ data3d: 1 })")
    c, e3 = shot(p, "http://127.0.0.1:8141/?beta=1&lab=1&live=0", "() => window.__fogLab.fog.setParams({ data3d: 0 })")
    print("replay night, beta lab: data3d 1 vs 0 differing pixels / max channel diff:", diff(a, b), "| two data3d=0 runs of the same page:", diff(a, c), "errors", e1 + e2)
    x, e4 = shot(p, "http://127.0.0.1:8141/?live=0")
    y, e5 = shot(p, "http://127.0.0.1:8142/?live=0")
    z, e6 = shot(p, "http://127.0.0.1:8142/?live=0")
    print("public page, this branch vs v3-dev:", diff(x, y), "| v3-dev vs itself:", diff(y, z), "errors", e4 + e5 + e6)
s1.shutdown(); s2.shutdown()
