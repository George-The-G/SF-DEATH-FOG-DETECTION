"""Before/after pictures on the REAL beta data: ?beta=1&lab=1, data3d 0 (flat map + guessed top) versus 1 (3D files; an hour without a file has no fog).
usage: shots_real.py <repo site/v3 folder with live_beta> <outdir> <phone|desktop> <frame,frame,...>   (Edge + SwiftShader: software GL, the look is right, timings are not)"""
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
KIND = sys.argv[3]
FRAMES = [float(x) for x in sys.argv[4].split(",")]


class Q(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 8161), partial(Q, directory=str(ROOT)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
DMODES = [int(x) for x in (sys.argv[5] if len(sys.argv) > 5 else "0,1").split(",")]
OUTSFX = sys.argv[6] if len(sys.argv) > 6 else ""
CAMS = {"city": [3900, 8800, 7600, 8300, 0, -8000], "low": [7400, 300, -5200, 6400, 150, -5200]}
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    ctx = b.new_context(viewport={"width": 390, "height": 844} if KIND == "phone" else {"width": 1440, "height": 900}, device_scale_factor=2 if KIND == "phone" else 1,
                        has_touch=KIND == "phone", is_mobile=KIND == "phone")
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errs.append(str(e)[:300]))
    pg.goto("http://127.0.0.1:8161/?beta=1&lab=1&tier=3", wait_until="load")
    pg.wait_for_function("window.__ready", timeout=240000)
    pg.wait_for_timeout(3000)
    pg.evaluate("() => { window.__dbg.setPlaying && window.__dbg.setPlaying(false); }")
    for fr in FRAMES:
        pg.evaluate(f"() => {{ window.__dbg.tNow = {fr}; }}")
        for d3 in DMODES:
            pg.evaluate(f"() => window.__fogLab.fog.setParams({{ data3d: {d3} }})")
            for name, v in CAMS.items():
                pg.evaluate("(v) => { const D = window.__dbg; D.camera.position.set(v[0], v[1], v[2]); D.controls.target.set(v[3], v[4], v[5]); D.controls.update(); }", v)
                pg.wait_for_timeout(9000)
                on = pg.evaluate("() => window.__fogLab.fog.data3d")
                f = OUT / f"{KIND}_frame{int(fr):02d}_data3d{d3}{OUTSFX}_{name}.png"
                pg.screenshot(path=str(f), timeout=180000)
                print(f.name, "3D active:", on, flush=True)
    b.close()
srv.shutdown()
print("page errors:", [e for e in errs][:5])
