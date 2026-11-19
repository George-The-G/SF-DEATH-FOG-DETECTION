"""Guide 09 B shots: ?beta=1 on the fog5 + macro fixtures (make_fixture5.py, make_macro_fixture.py), macro 0 and 1, software GL (Edge + SwiftShader; no GPU). Usage: shots09.py <outdir> [phone|desktop]"""
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

WT = Path(r"C:\Users\George\AppData\Local\Temp\wt_sim0809\site\v3")
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
KIND = sys.argv[2] if len(sys.argv) > 2 else "desktop"
HOUR = float(sys.argv[3]) if len(sys.argv) > 3 else 10


class Q(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 8134), partial(Q, directory=str(WT)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
CAMS = {
    "far": [-38000, 14000, 21000, 5000, 0, -9000],
    "coast": [-6000, 4800, 3000, 9000, 0, -9000],
    "city": [3900, 8800, 7600, 8300, 0, -8000],
    "street": [8000, 70, -8000, -9000, 160, -8800],
    "bank": [-12000, 2500, 9000, -40000, 0, -4000],
    "top": [8000, 65000, -7900, 8000, 0, -8000],
}
msgs = []
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    ctx = b.new_context(viewport={"width": 390, "height": 844} if KIND == "phone" else {"width": 1280, "height": 800}, device_scale_factor=1, has_touch=KIND == "phone", is_mobile=KIND == "phone")
    pg = ctx.new_page()
    pg.on("console", lambda m: msgs.append((m.type, m.text[:220])) if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: msgs.append(("pageerror", str(e)[:300])))
    pg.goto("http://127.0.0.1:8134/?beta=1&tier=3", wait_until="load")
    pg.wait_for_function("window.__ready", timeout=240000)
    pg.wait_for_timeout(3000)
    pg.evaluate(f"() => {{ window.__dbg.setPlaying && window.__dbg.setPlaying(false); window.__dbg.tNow = {HOUR}; }}")
    for macro in ((1,) if 'MACRO1' in __import__('os').environ else (0, 1)):
        pg.evaluate(f"() => window.__fogLab.fog.setParams({{ macro: {macro}, travel: 0 }})")
        for name, v in CAMS.items():
            pg.evaluate(f"() => {{ window.__dbg.setPlaying && window.__dbg.setPlaying(false); window.__dbg.tNow = {HOUR}; }}")
            pg.evaluate("(v) => { const D = window.__dbg; D.camera.position.set(v[0], v[1], v[2]); D.controls.target.set(v[3], v[4], v[5]); D.controls.update(); }", v)
            pg.wait_for_timeout(9000 if macro else 6000)
            st = pg.evaluate("() => { const m = window.__fogLab.macro; return { info: m ? m.info() : null, near: window.__dbg.camera.near, far: window.__dbg.camera.far, maxD: window.__dbg.controls.maxDistance }; }")
            pg.screenshot(path=str(OUT / f"{KIND}_macro{macro}_{name}.png"))
            print(macro, name, json.dumps(st))
    b.close()
srv.shutdown()
print("MSGS", [m for m in msgs if "bus" not in m[1].lower() and "CORS" not in m[1] and "ERR_FAILED" not in m[1] and "defines" not in m[1]][:12])
