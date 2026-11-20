// node --test tests_js/task_viewer_sidecar.test.mjs
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

import { pyJson, registerSidecarRoutes } from '../tools/task_viewer/antigravity_sidecar.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..');
const INDEX_HTML_PATH = join(REPO_ROOT, 'tools', 'task_viewer', 'index.html');

/// Minimal Sidecar Response mock matching `sidecar_sdk.Response`.
class MockResponse {
  constructor(body, opts = {}) {
    this.body = body;
    this.status = opts.status ?? 200;
    this.contentType = opts.contentType || 'text/plain';
  }
}

/// Minimal SidecarApp mock recording registered page and API handlers.
function createMockApp() {
  const pages = new Map();
  const apis = new Map();
  return {
    pages,
    apis,
    page(route, handler) {
      pages.set(route, handler);
    },
    api(route, handler, method = 'GET') {
      apis.set(`${method.toUpperCase()} ${route}`, handler);
    },
  };
}

/// Build a lightweight DOM + browser globals mock for evaluating `index.html`.
function createMockDom(fetchImpl) {
  const elements = new Map();

  function makeEl(id = '', tagName = 'DIV') {
    const classes = new Set();
    const el = {
      id,
      tagName,
      innerHTML: '',
      textContent: '',
      value: '',
      checked: false,
      src: '',
      className: '',
      style: {},
      children: [],
      classList: {
        add: (...cls) => cls.forEach((c) => classes.add(c)),
        remove: (...cls) => cls.forEach((c) => classes.delete(c)),
        toggle: (c, force) => {
          const on = force !== undefined ? Boolean(force) : !classes.has(c);
          if (on) classes.add(c);
          else classes.delete(c);
          return on;
        },
        contains: (c) => classes.has(c),
      },
      appendChild(child) {
        this.children.push(child);
        return child;
      },
      removeChild(child) {
        const idx = this.children.indexOf(child);
        if (idx >= 0) this.children.splice(idx, 1);
        return child;
      },
      select() {},
    };
    return el;
  }

  for (const id of [
    'parity-badge',
    'sync-status',
    'toast',
    'toast-msg',
    'toast-actions',
    'switcher-bar',
    'switcher-label',
    'switcher-count',
    'sidebar',
    'detail',
    'convo-modal',
    'convo-modal-body',
  ]) {
    elements.set(id, makeEl(id));
  }

  const storage = new Map();
  const document = {
    body: makeEl('body', 'BODY'),
    activeElement: null,
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, makeEl(id));
      return elements.get(id);
    },
    createElement(tag) {
      return makeEl('', String(tag || 'div').toUpperCase());
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    addEventListener() {},
    execCommand() {
      return false;
    },
  };

  const context = {
    console,
    Date,
    JSON,
    Math,
    Object,
    Array,
    String,
    Number,
    Boolean,
    RegExp,
    Map,
    Set,
    Error,
    SyntaxError,
    TypeError,
    Promise,
    encodeURIComponent,
    decodeURIComponent,
    innerWidth: 1200,
    document,
    navigator: {},
    localStorage: {
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
    },
    sidecar: {
      agent: {
        sendMessage: async () => {},
      },
    },
    fetch: fetchImpl,
    setInterval: () => 1,
    clearInterval: () => {},
    setTimeout: () => 1,
    clearTimeout: () => {},
  };
  context.window = context;
  context.globalThis = context;
  return { context, elements };
}

test('sidecar routes serve index, live state, refresh forwarding, actions, and cached files', async () => {
  const app = createMockApp();
  const pyCalls = [];
  const { taskDirs } = registerSidecarRoutes(app, MockResponse, {
    repoRoot: REPO_ROOT,
    pyJson: async (args, stdinText) => {
      pyCalls.push({ args, stdinText });
      if (args.includes('--refresh')) {
        return {
          repo_root: REPO_ROOT,
          active_task_id: null,
          waves: [],
          tasks: {},
          git_refresh: { ok: true, error: '' },
        };
      }
      return await pyJson(args, stdinText, { repoRoot: REPO_ROOT });
    },
  });

  const pageHtml = app.pages.get('/')();
  assert.match(pageHtml, /<title>Death Fog — Task Viewer<\/title>/);

  const stateHandler = app.apis.get('GET /api/state');
  const state = await stateHandler({});
  assert.ok(state && typeof state.tasks === 'object', 'expected state.tasks object');
  assert.ok(Array.isArray(state.waves), 'expected state.waves array');
  assert.ok(!state.error, `unexpected state error: ${state.error}`);

  const refreshed = await stateHandler({ refresh: '1' });
  assert.deepEqual(pyCalls[1].args, ['--state-json', '--refresh']);
  assert.equal(refreshed.git_refresh?.ok, true);

  const actionHandler = app.apis.get('POST /api/action');
  const firstId = Object.keys(state.tasks)[0];
  if (firstId) {
    const actRes = await actionHandler({ action: 'resume_prompt', task_id: firstId });
    assert.equal(actRes.ok, true);
    assert.ok(typeof actRes.prompt === 'string' && actRes.prompt.length > 0);
  } else {
    const badAct = await actionHandler({ action: 'unknown' });
    assert.equal(badAct.ok, false);
  }

  // Test /api/file caching (hit vs miss) and path traversal rejection in an isolated temp dir.
  const tmpTaskRoot = mkdtempSync(join(tmpdir(), 'df-sidecar-file-'));
  try {
    mkdirSync(join(tmpTaskRoot, 'mockups'), { recursive: true });
    writeFileSync(join(tmpTaskRoot, 'mockups', 'sample.html'), '<h1>Mockup</h1>', 'utf-8');
    writeFileSync(join(tmpTaskRoot, 'TASK.md'), 'secret', 'utf-8');

    const fileApp = createMockApp();
    let stateFetches = 0;
    registerSidecarRoutes(fileApp, MockResponse, {
      repoRoot: REPO_ROOT,
      pyJson: async () => {
        stateFetches += 1;
        return {
          tasks: {
            '999': { id: '999', task_dir: tmpTaskRoot },
          },
        };
      },
    });
    const fileHandler = fileApp.apis.get('GET /api/file');

    const r1 = await fileHandler({ id: '999', kind: 'mockups', name: 'sample.html' });
    assert.equal(r1.status, 200);
    assert.equal(r1.contentType, 'text/html; charset=utf-8');
    assert.equal(Buffer.from(r1.body).toString('utf-8'), '<h1>Mockup</h1>');
    assert.equal(stateFetches, 1);

    const r2 = await fileHandler({ id: '999', kind: 'mockups', name: 'sample.html' });
    assert.equal(r2.status, 200);
    assert.equal(stateFetches, 1, 'expected second /api/file call to use cached task_dir');

    for (const bad of [
      { id: '999', kind: 'mockups', name: '../TASK.md' },
      { id: '999', kind: 'mockups', name: 'sub/sample.html' },
      { id: '999', kind: 'other', name: 'sample.html' },
      { id: '999', kind: 'mockups', name: 'missing.png' },
    ]) {
      const rBad = await fileHandler(bad);
      assert.equal(rBad.status, 404);
    }
  } finally {
    rmSync(tmpTaskRoot, { recursive: true, force: true });
  }

  assert.ok(taskDirs instanceof Map);
});

test('sidecar routes catch backend errors cleanly without throwing', async () => {
  const app = createMockApp();
  registerSidecarRoutes(app, MockResponse, {
    repoRoot: REPO_ROOT,
    pyJson: async () => {
      throw new Error('python crashed');
    },
  });

  const st = await app.apis.get('GET /api/state')({});
  assert.match(st.error, /python crashed/);
  assert.deepEqual(st.tasks, {});

  const act = await app.apis.get('POST /api/action')({ action: 'resume_prompt' });
  assert.equal(act.ok, false);
  assert.match(act.error, /python crashed/);
});

test('index.html inline script compiles and renders live state + handles errors in DOM simulation', async () => {
  const html = readFileSync(INDEX_HTML_PATH, 'utf-8');
  const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/);
  assert.ok(scriptMatch, 'expected inline <script> block in tools/task_viewer/index.html');
  const scriptSrc = scriptMatch[1];

  // Syntax check via vm.Script compilation.
  const compiled = new vm.Script(scriptSrc, { filename: 'tools/task_viewer/index.html' });

  const liveState = await pyJson(['--state-json'], undefined, { repoRoot: REPO_ROOT });
  assert.ok(liveState && typeof liveState.tasks === 'object');

  const fetchedUrls = [];
  let nextResponse = null;

  const { context, elements } = createMockDom(async (url) => {
    fetchedUrls.push(url);
    if (nextResponse) {
      const fn = nextResponse;
      nextResponse = null;
      return fn(url);
    }
    const payload = url.includes('refresh=1')
      ? { ...liveState, git_refresh: { ok: true, error: '' } }
      : liveState;
    return {
      ok: true,
      status: 200,
      async text() {
        return JSON.stringify(payload);
      },
    };
  });

  vm.createContext(context);
  compiled.runInContext(context);

  // Let initial startup `refresh(false)` settle.
  await new Promise((r) => setTimeout(r, 20));

  assert.equal(fetchedUrls[0], '/api/state', 'startup must call /api/state without ?refresh=1');
  const syncEl = elements.get('sync-status');
  assert.match(syncEl.textContent, /^\d+ · ag$/);
  assert.ok(elements.get('sidebar').innerHTML.length > 0, 'sidebar should render');
  assert.ok(elements.get('detail').innerHTML.length > 0, 'detail should render');

  // Exercise renderDetail across up to 5 tasks in the live state.
  const taskIds = Object.keys(liveState.tasks).slice(0, 5);
  for (const tid of taskIds) {
    context.selectTask(tid);
    assert.ok(elements.get('detail').innerHTML.includes(`#${tid}`), `detail should render task #${tid}`);
  }

  // Manual refresh(true) should hit /api/state?refresh=1.
  await context.refresh(true);
  assert.equal(fetchedUrls.at(-1), '/api/state?refresh=1');
  assert.match(syncEl.textContent, /^\d+ · ag$/);

  // Suppress expected console.error noise during intentional failure tests.
  const origConsoleError = context.console.error;
  context.console = { ...console, error: () => {} };
  try {
    // Backend JSON error field should show Error status + error toast, not Offline.
    nextResponse = async () => ({
      ok: true,
      status: 200,
      async text() {
        return JSON.stringify({ tasks: {}, waves: [], error: 'sidecar failure' });
      },
    });
    await context.refresh(true);
    assert.equal(syncEl.textContent, 'Error');
    assert.match(elements.get('toast-msg').textContent, /sidecar failure/);
    assert.equal(elements.get('toast').classList.contains('err'), true);

    // Non-OK HTTP status should also show Error status.
    nextResponse = async () => ({
      ok: false,
      status: 503,
      async text() {
        return 'unavailable';
      },
    });
    await context.refresh(true);
    assert.equal(syncEl.textContent, 'Error');
    assert.match(elements.get('toast-msg').textContent, /HTTP 503/);

    // Network throw should show Offline status.
    nextResponse = async () => {
      throw new TypeError('Failed to fetch');
    };
    await context.refresh(true);
    assert.equal(syncEl.textContent, 'Offline');
  } finally {
    context.console = { ...console, error: origConsoleError };
  }
});
