"""Guide 04 shots: ?beta=1 on the fixture live folder, data3d 0 vs 1; software GL (Edge + SwiftShader). Usage: shots04.py <outdir> [phone|desktop]"""
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

WT = Path(r"C:\Users\George\AppData\Local\Temp\wt_sim04\site\v3")
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
KIND = sys.argv[2] if len(sys.argv) > 2 else "desktop"
FRAME = float(sys.argv[3]) if len(sys.argv) > 3 else 10


class Q(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 8131), partial(Q, directory=str(WT)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
CAMS = {"default": [3900, 8800, 7600, 8300, 0, -8000], "top": [8300, 14000, -7200, 8300, 0, -7199], "low": [7400, 300, -5200, 6400, 150, -5200]}
msgs = []
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    ctx = b.new_context(viewport={"width": 390, "height": 844} if KIND == "phone" else {"width": 1440, "height": 900}, device_scale_factor=2 if KIND == "phone" else 1,
                        has_touch=KIND == "phone", is_mobile=KIND == "phone")
    pg = ctx.new_page()
    pg.on("console", lambda m: msgs.append((m.type, m.text[:220])) if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: msgs.append(("pageerror", str(e)[:300])))
    pg.goto("http://127.0.0.1:8131/?beta=1&tier=3", wait_until="load")
    pg.wait_for_function("window.__ready", timeout=240000)
    pg.wait_for_timeout(3000)
    pg.evaluate(f"() => {{ window.__dbg.setPlaying && window.__dbg.setPlaying(false); window.__dbg.tNow = {FRAME}; }}")
    info = pg.evaluate("() => { const f = window.__fogLab.fog; return {params: f.params.data3d, d3: f.data3d, info: f.info()}; }")
    print("state", json.dumps(info)[:300])
    for d3 in (0, 1):
        pg.evaluate(f"() => window.__fogLab.fog.setParams({{ data3d: {d3} }})")
        for name, v in CAMS.items():
            pg.evaluate("(v) => { const D = window.__dbg; D.camera.position.set(v[0], v[1], v[2]); D.controls.target.set(v[3], v[4], v[5]); D.controls.update(); }", v)
            pg.wait_for_timeout(9000)  # still-frame refinement, software GL
            st = pg.evaluate("() => { const f = window.__fogLab.fog; return {d3: f.data3d, phase: f.info().split('\\n')[1]}; }")
            f = OUT / f"{KIND}_data3d{d3}_{name}.png"
            pg.screenshot(path=str(f))
            print(f.name, st)
    b.close()
srv.shutdown()
print("MSGS", [m for m in msgs if "bus" not in m[1].lower() and "CORS" not in m[1] and "ERR_FAILED" not in m[1]][:12])
