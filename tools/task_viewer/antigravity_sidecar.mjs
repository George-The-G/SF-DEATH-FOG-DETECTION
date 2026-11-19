/**
 * Antigravity Sidecar adapter for the portable Death Fog Task Viewer.
 *
 * Bridges Antigravity's `sidecar_sdk` auxiliary pane directly to `tools/task_viewer/server.py`
 * and `tools/task_viewer/index.html` inside the repository.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SidecarApp, Response } from 'sidecar_sdk';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = process.env.DEATHFOG_REPO || resolve(__dirname, '..', '..');
const PYTHON_BIN = process.env.DEATHFOG_PYTHON || (
  process.platform === 'win32'
    ? join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
    : join(REPO_ROOT, '.venv', 'bin', 'python')
);
const SERVER_PY = join(REPO_ROOT, 'tools', 'task_viewer', 'server.py');
const INDEX_HTML = join(REPO_ROOT, 'tools', 'task_viewer', 'index.html');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
};

/// Invoke `tools/task_viewer/server.py` CLI mode and parse its JSON stdout.
function pyJson(args, stdinText = undefined) {
  const py = existsSync(PYTHON_BIN) ? PYTHON_BIN : 'python';
  const out = execFileSync(py, [SERVER_PY, ...args], {
    cwd: REPO_ROOT,
    encoding: 'utf-8',
    input: stdinText,
    timeout: 60000,
    maxBuffer: 8 * 1024 * 1024,
  });
  return JSON.parse(out);
}

const app = new SidecarApp();

app.page('/', () => readFileSync(INDEX_HTML, 'utf-8'));

app.api('/api/state', () => {
  try {
    return pyJson(['--state-json']);
  } catch (err) {
    return { repo_root: REPO_ROOT, active_task_id: null, waves: [], tasks: {}, error: String(err.message || err) };
  }
}, 'GET');

app.api('/api/action', (data) => {
  try {
    return pyJson(['--action-stdin'], JSON.stringify(data || {}));
  } catch (err) {
    return { ok: false, error: String(err.message || err) };
  }
}, 'POST');

app.api('/api/file', (data) => {
  const id = String(data?.id || '');
  const kind = String(data?.kind || '');
  const name = String(data?.name || '');
  if (!id || !['mockups', 'evidence'].includes(kind) || !name || name.includes('..') || name.includes('/') || name.includes('\\')) {
    return new Response('not found', { status: 404 });
  }
  try {
    const state = pyJson(['--state-json']);
    const info = state.tasks?.[id];
    if (!info || !info.task_dir) return new Response('not found', { status: 404 });
    const full = resolve(info.task_dir, kind, name);
    const base = resolve(info.task_dir, kind);
    if (!full.startsWith(base) || !existsSync(full)) return new Response('not found', { status: 404 });
    const contentType = MIME[extname(name).toLowerCase()] || 'application/octet-stream';
    return new Response(readFileSync(full), { contentType });
  } catch {
    return new Response('not found', { status: 404 });
  }
}, 'GET');

app.run();
