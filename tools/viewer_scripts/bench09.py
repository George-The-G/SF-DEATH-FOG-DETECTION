"""Guide 09 B frame time on the real GPU (Edge default GPU, no SwiftShader): the scene pass (what macro adds: wide terrain + cloud slab) macro 0 vs 1 at three cameras,
desktop and phone-sized viewports, GPU-synchronised with a 1-pixel read. Needs the fixtures (make_fixture5.py, make_macro_fixture.py). Usage: bench09.py"""
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

WT = Path(r"C:\Users\George\AppData\Local\Temp\wt_sim0809\site\v3")


class Q(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 8136), partial(Q, directory=str(WT)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
CAMS = {"city": [3900, 8800, 7600, 8300, 0, -8000], "far": [-38000, 14000, 21000, 5000, 0, -9000], "street": [8000, 70, -8000, -9000, 160, -8800]}
JS = """async (n) => {
  const { renderer, scene, camera } = window.__fogLab, gl = renderer.getContext(), px = new Uint8Array(4), ts = [];
  for (let i = 0; i < n + 3; i++) { const t0 = performance.now(); renderer.render(scene, camera); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px); if (i >= 3) ts.push(performance.now() - t0); }
  ts.sort((a, b) => a - b); return { med: ts[ts.length >> 1], min: ts[0] };
}"""
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", args=["--ignore-gpu-blocklist"])
    for kind, vp in (("desktop", {"width": 1280, "height": 800}), ("phone", {"width": 390, "height": 844})):
        pg = b.new_page(viewport=vp); pg.on("pageerror", lambda e: print("ERR", str(e)[:200]))
        pg.goto("http://127.0.0.1:8136/?beta=1&lab=1&tier=3"); pg.wait_for_function("window.__ready", timeout=240000); pg.wait_for_timeout(4000)
        print(kind, pg.evaluate("() => { const gl = window.__fogLab.renderer.getContext(); const e = gl.getExtension('WEBGL_debug_renderer_info'); return e ? gl.getParameter(e.UNMASKED_RENDERER_WEBGL) : 'n/a'; }"))
        for macro in (0, 1):
            pg.evaluate(f"() => window.__fogLab.fog.setParams({{ macro: {macro}, travel: 0 }})")
            for name, v in CAMS.items():
                for _ in range(3):
                    pg.evaluate("() => { window.__dbg.setPlaying && window.__dbg.setPlaying(false); window.__dbg.tNow = 10; }")
                    pg.evaluate("(v) => { const D = window.__dbg; D.camera.position.set(v[0], v[1], v[2]); D.controls.target.set(v[3], v[4], v[5]); D.controls.update(); }", v)
                    pg.wait_for_timeout(2500)
                r = pg.evaluate(JS, 40)
                print(kind, "macro", macro, name, json.dumps({k: round(x, 2) for k, x in r.items()}), pg.evaluate("() => window.__fogLab.macro.info()"))
        pg.close()
    b.close()
srv.shutdown()
