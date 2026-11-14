// Headless triangle/draw-call counter for the viewer (Playwright + SwiftShader). Counts are valid; timings are NOT (software GL).
// usage: node tools/perfbench.cjs "beta=1&lod=1&aa=0"   (query string of the viewer); compare lod=0 vs lod=1.
const { chromium } = require('playwright');
const http = require('http'), fs = require('fs'), path = require('path');
const ROOT = path.resolve(__dirname, '../site/v3'), T3 = process.env.THREE_DIR || path.resolve(__dirname, '../node_modules/three'); // THREE_DIR: an unpacked three@0.160 (npm pack three@0.160.0); the CDN is not reachable in some sandboxes
const mime = { '.js': 'text/javascript', '.json': 'application/json', '.html': 'text/html', '.css': 'text/css', '.png': 'image/png', '.glb': 'model/gltf-binary' };
const srv = http.createServer((q, r) => { let p = decodeURIComponent(q.url.split('?')[0]); if (p === '/') p = '/index.html'; const f = path.join(ROOT, p); fs.readFile(f, (e, d) => { if (e) { r.writeHead(404); r.end(); } else { r.writeHead(200, { 'content-type': mime[path.extname(f)] || 'application/octet-stream' }); r.end(d); } }); }).listen(8124);
(async () => {
  const b = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const pg = await b.newPage({ viewport: { width: 1000, height: 800 } });
  await pg.route('https://cdn.jsdelivr.net/npm/three@0.160.0/**', (rt) => {
    const u = new URL(rt.request().url()).pathname.replace('/npm/three@0.160.0/', ''); const f = path.join(T3, u);
    fs.readFile(f, (e, d) => e ? rt.fulfill({ status: 404 }) : rt.fulfill({ status: 200, contentType: 'text/javascript', body: d }));
  });
  pg.on('pageerror', (e) => console.log('PAGEERR', e.message));
  pg.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') console.log('CON', m.text().slice(0, 200)); });
  await pg.goto('http://localhost:8124/?live=0&' + (process.argv[2] || 'beta=1'));
  await pg.waitForFunction(() => window.__ready, null, { timeout: 150000 });
  const cams = { city: [3900, 8800, 7600, 8300, 0, -8000], mid: [8300, 2500, -2500, 8300, 0, -7000], near: [8300, 300, -7500, 8300, 0, -8200] };
  await pg.evaluate(() => { const f = document.getElementById('fogtoggle'); f.checked = false; f.dispatchEvent(new Event('change', { bubbles: true })); });
  await pg.waitForTimeout(1500);
  for (const n of ['city', 'mid', 'near', 'city']) {
    const o = await pg.evaluate(async (v) => {
      const D = window.__dbg, r = D.renderer; const f0 = r.info.render.frame;
      D.camera.position.set(v[0], v[1], v[2]); D.controls.target.set(v[3], v[4], v[5]); D.controls.update();
      const t0 = performance.now(); while (r.info.render.frame < f0 + 4 && performance.now() - t0 < 40000) await new Promise((res) => setTimeout(res, 100));
      const i = r.info.render;
      return i.triangles + ' tris ' + i.calls + ' calls | ' + (document.getElementById('pf-note') || {}).textContent;
    }, cams[n]);
    console.log(n, o);
  }
  await b.close(); srv.close();
})();
