/**
 * Antigravity Sidecar adapter for the portable Death Fog Task Viewer.
 *
 * Bridges Antigravity's `sidecar_sdk` auxiliary pane directly to `tools/task_viewer/server.py`
 * and `tools/task_viewer/index.html` inside the repository.
 */
import { execFile as execFileCb } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

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

/// Invoke `tools/task_viewer/server.py` CLI mode asynchronously and parse its JSON stdout.
export function pyJson(args, stdinText = undefined, opts = {}) {
  const repoRoot = opts.repoRoot || REPO_ROOT;
  const pythonBin = opts.pythonBin || PYTHON_BIN;
  const serverPy = opts.serverPy || SERVER_PY;
  const py = existsSync(pythonBin) ? pythonBin : 'python';
  return new Promise((resolvePromise, rejectPromise) => {
    const child = execFileCb(
      py,
      [serverPy, ...args],
      {
        cwd: repoRoot,
        encoding: 'utf-8',
        timeout: 60000,
        maxBuffer: 8 * 1024 * 1024,
      },
      (err, stdout, stderr) => {
        if (err) {
          const detail = stderr ? `${err.message}: ${stderr.trim()}` : err.message;
          rejectPromise(new Error(detail));
          return;
        }
        try {
          resolvePromise(JSON.parse(stdout));
        } catch (parseErr) {
          rejectPromise(new Error(`Invalid JSON from server.py: ${parseErr.message}`));
        }
      },
    );
    if (stdinText !== undefined && child.stdin) {
      child.stdin.end(stdinText);
    }
  });
}

/// Register Task Viewer routes on a `SidecarApp` (or test mock) using async `server.py` calls.
export function registerSidecarRoutes(app, ResponseClass, options = {}) {
  const repoRoot = options.repoRoot || REPO_ROOT;
  const indexHtml = options.indexHtml || INDEX_HTML;
  const runPy = options.pyJson || ((args, stdinText) => pyJson(args, stdinText, options));
  const taskDirs = new Map();

  /// Cache `task_dir` paths from a state payload so `/api/file` avoids redundant spawns.
  function updateTaskDirCache(state) {
    if (!state || typeof state !== 'object' || !state.tasks || typeof state.tasks !== 'object') return;
    for (const [tid, info] of Object.entries(state.tasks)) {
      if (info && info.task_dir) {
        taskDirs.set(String(tid), String(info.task_dir));
      }
    }
  }

  app.page('/', () => readFileSync(indexHtml, 'utf-8'));

  app.api('/api/state', async (data) => {
    try {
      const wantRefresh = data?.refresh === '1' || data?.refresh === 1 || data?.refresh === true;
      const args = wantRefresh ? ['--state-json', '--refresh'] : ['--state-json'];
      const state = await runPy(args);
      updateTaskDirCache(state);
      return state;
    } catch (err) {
      return {
        repo_root: repoRoot,
        active_task_id: null,
        waves: [],
        tasks: {},
        error: String(err?.message || err),
      };
    }
  }, 'GET');

  app.api('/api/action', async (data) => {
    try {
      return await runPy(['--action-stdin'], JSON.stringify(data || {}));
    } catch (err) {
      return { ok: false, error: String(err?.message || err) };
    }
  }, 'POST');

  app.api('/api/file', async (data) => {
    const id = String(data?.id || '');
    const kind = String(data?.kind || '');
    const name = String(data?.name || '');
    if (!id || !['mockups', 'evidence'].includes(kind) || !name || name.includes('..') || name.includes('/') || name.includes('\\')) {
      return new ResponseClass('not found', { status: 404 });
    }
    try {
      let taskDir = taskDirs.get(id);
      if (!taskDir || !existsSync(taskDir)) {
        const state = await runPy(['--state-json']);
        updateTaskDirCache(state);
        taskDir = taskDirs.get(id);
      }
      if (!taskDir) return new ResponseClass('not found', { status: 404 });
      const base = resolve(taskDir, kind);
      const basePrefix = base.endsWith(sep) ? base : base + sep;
      const full = resolve(base, name);
      if (!full.startsWith(basePrefix) || !existsSync(full)) {
        return new ResponseClass('not found', { status: 404 });
      }
      const contentType = MIME[extname(name).toLowerCase()] || 'application/octet-stream';
      return new ResponseClass(await readFile(full), { contentType });
    } catch {
      return new ResponseClass('not found', { status: 404 });
    }
  }, 'GET');

  return { taskDirs };
}

/// Check whether this module is running directly as the Node entrypoint.
function isEntryPoint() {
  if (!process.argv[1]) return false;
  const selfPath = resolve(fileURLToPath(import.meta.url));
  const argvPath = resolve(process.argv[1]);
  return process.platform === 'win32'
    ? selfPath.toLowerCase() === argvPath.toLowerCase()
    : selfPath === argvPath;
}

/// Launch the `sidecar_sdk` app and serve the Death Fog Task Viewer pane.
export async function startSidecar() {
  const { SidecarApp, Response } = await import('sidecar_sdk');
  const app = new SidecarApp();
  registerSidecarRoutes(app, Response);
  app.run();
  return app;
}

if (isEntryPoint() && process.env.DEATHFOG_SIDECAR_NO_RUN !== '1') {
  await startSidecar();
}
