"""Headless repro of the fog grid artifact (diagnosis only; serves a worktree read-only).
usage: python gridfog_repro.py <site_v3_dir> <three_dir> <outdir> [name=query ...]
e.g.   python gridfog_repro.py wt\\site\\v3 three\\package out phone="beta=1&night=2026-10-01"
Software GL (SwiftShader): the look is valid, timings are not."""
import sys, os, threading, http.server, socketserver, functools, json, mimetypes
from playwright.sync_api import sync_playwright

ROOT, T3, OUT = [os.path.abspath(a) for a in sys.argv[1:4]]
os.makedirs(OUT, exist_ok=True)
runs = [a.split('=', 1) for a in sys.argv[4:]] or [['phone', 'beta=1&night=2026-10-01']]

class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
socketserver.TCPServer.allow_reuse_address = True
srv = socketserver.ThreadingTCPServer(('127.0.0.1', 8131), functools.partial(H, directory=ROOT))
threading.Thread(target=srv.serve_forever, daemon=True).start()

UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
with sync_playwright() as p:
    for name, q in runs:
        mobile = name.startswith('phone')
        b = p.chromium.launch(args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--use-gl=angle'])
        ctx = b.new_context(viewport={'width': 390, 'height': 844} if mobile else {'width': 1000, 'height': 800},
                            device_scale_factor=2 if mobile else 1, is_mobile=mobile, has_touch=mobile, user_agent=UA if mobile else None)
        pg = ctx.new_page()
        def route(rt):
            u = rt.request.url.split('/npm/three@0.160.0/')[1].split('?')[0]
            f = os.path.join(T3, u)
            if os.path.exists(f): rt.fulfill(status=200, content_type='text/javascript', body=open(f, 'rb').read())
            else: rt.fulfill(status=404)
        pg.route('https://cdn.jsdelivr.net/npm/three@0.160.0/**', route)
        pg.on('pageerror', lambda e: print('PAGEERR', e))
        pg.on('console', lambda m: print('CON', m.type, m.text[:200]) if m.type in ('error', 'warning') else None)
        pg.goto('http://127.0.0.1:8131/?' + q)
        pg.wait_for_function('window.__ready', timeout=240000)
        pg.wait_for_timeout(6000)
        info = pg.evaluate('''() => { const D = window.__dbg || {}; const c = D.camera, t = D.controls;
          return { cam: c ? c.position.toArray() : null, tgt: t ? t.target.toArray() : null, pr: D.renderer ? D.renderer.getPixelRatio() : null,
                   fog: D.fog && D.fog.info ? D.fog.info() : null, note: (document.getElementById('pf-note') || {}).textContent } }''')
        print(name, json.dumps(info)[:600])
        pg.screenshot(path=os.path.join(OUT, name + '.png'))
        b.close()
srv.shutdown()
