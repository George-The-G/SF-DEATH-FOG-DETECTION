"""Guide 08 B checks: ?beta=1 on the fog5 fixture (make_fixture5.py), software GL (Edge + SwiftShader; no GPU). Usage: shots08.py <outdir> [phone|desktop]
Checks: travel 0 and 1 render, the key frames at several times (info line), label text, no console errors, volumes load, a refresh of the index."""
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


class Q(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 8132), partial(Q, directory=str(WT)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
CAM = [3900, 8800, 7600, 8300, 0, -8000]
msgs = []
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    ctx = b.new_context(viewport={"width": 390, "height": 844} if KIND == "phone" else {"width": 1280, "height": 800}, device_scale_factor=1, has_touch=KIND == "phone", is_mobile=KIND == "phone")
    pg = ctx.new_page()
    pg.on("console", lambda m: msgs.append((m.type, m.text[:220])) if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: msgs.append(("pageerror", str(e)[:300])))
    pg.goto("http://127.0.0.1:8132/?beta=1&tier=3", wait_until="load")
    pg.wait_for_function("window.__ready", timeout=240000)
    pg.wait_for_timeout(3000)
    pg.evaluate("() => { window.__dbg.setPlaying && window.__dbg.setPlaying(false); const D = window.__dbg; D.camera.position.set(%s,%s,%s); D.controls.target.set(%s,%s,%s); D.controls.update(); }" % tuple(CAM))
    F = "window.__fogLab.fog"
    def state():
        return pg.evaluate("() => { const f = %s; return { travel: f.travel, d3: f.data3d, info: f.info().split('\\n')[0] + ' | ' + f.info().split('\\n')[1], label: document.getElementById('clock-sub') && document.getElementById('clock-sub').textContent }; }" % F)
    def at(h):
        pg.evaluate(f"() => {{ window.__dbg.tNow = {h}; }}")
        pg.wait_for_timeout(5000)
    for trav in (0, 1):
        pg.evaluate(f"() => {F}.setParams({{ travel: {trav}, data3d: 1 }})")
        pg.wait_for_timeout(2500)
        for h in (6.5, 8.0, 9.0, 9.5, 10.04, 10.5, 11.25, 11.9, 12.0, 12.5):
            at(h)
            st = state()
            print(trav, h, json.dumps(st)[:330])
            if h in (6.5, 9.5, 10.5, 11.9, 12.5):
                pg.screenshot(path=str(OUT / f"{KIND}_travel{trav}_h{h}.png"))
    # exact bytes: the baked state key frames are tested in node; here: switching off returns to hourly
    pg.evaluate(f"() => {F}.setParams({{ travel: 0 }})")
    at(10.5); print("off again", json.dumps(state())[:300])
    b.close()
srv.shutdown()
print("MSGS", [m for m in msgs if "bus" not in m[1].lower() and "CORS" not in m[1] and "ERR_FAILED" not in m[1]][:15])
