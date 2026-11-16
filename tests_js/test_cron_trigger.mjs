// node tests_js/test_cron_trigger.mjs  (mocked fetch: freshness decision, the one dispatch call, token never logged)
import assert from 'node:assert/strict';
import worker, { check, builtAt } from '../edge/cron-trigger/worker.js';
const TOKEN = 'ghp_SECRET_TOKEN_123', NOW = Date.parse('2026-11-15T08:00:00Z');
const env = { STATUS_URL: 'https://pages.example/status.json', STALE_MIN: '14', REPO: 'o/r', WORKFLOW: 'collect.yml', REF: 'main', GH_DISPATCH_TOKEN: TOKEN };
const mk = (statusBody, { statusOk = true, dispatchStatus = 204, throwStatus = false } = {}) => {
  const calls = [], logs = [];
  const fetch = async (u, o = {}) => {
    calls.push({ u, o });
    if (u.startsWith('https://api.github.com')) return new Response(null, { status: dispatchStatus });
    if (throwStatus) throw new Error('boom');
    return statusOk ? new Response(JSON.stringify(statusBody)) : new Response('x', { status: 500 });
  };
  return { calls, logs, deps: { fetch, now: () => NOW, log: (m) => logs.push(m) } };
};
const iso = (minAgo) => new Date(NOW - minAgo * 60000).toISOString();
const posts = (m) => m.calls.filter((c) => c.u.includes('api.github.com'));
assert.ok(Number.isNaN(builtAt({})));
// fresh: no dispatch (both status formats)
for (const key of ['generated_utc', 'generated_iso']) {
  const m = mk({ [key]: iso(8) }); const r = await check(env, m.deps);
  assert.equal(r.dispatched, false); assert.equal(posts(m).length, 0);
}
// stale: exactly one POST with the right URL, headers and body
{
  const m = mk({ generated_utc: iso(20) }); const r = await check(env, m.deps);
  const d = posts(m); assert.equal(d.length, 1); assert.ok(r.dispatched);
  assert.equal(d[0].u, 'https://api.github.com/repos/o/r/actions/workflows/collect.yml/dispatches');
  assert.equal(d[0].o.method, 'POST'); assert.deepEqual(JSON.parse(d[0].o.body), { ref: 'main' });
  assert.equal(d[0].o.headers.Authorization, `Bearer ${TOKEN}`); assert.equal(d[0].o.headers.Accept, 'application/vnd.github+json'); assert.equal(d[0].o.headers['User-Agent'], 'deathfog-cron');
  assert.ok(!m.calls.some((c) => c.u.includes(TOKEN)), 'token in a URL');
}
// failed / unreadable status fetch still dispatches
for (const opt of [{ statusOk: false }, { throwStatus: true }]) { const m = mk({}, opt); assert.ok((await check(env, m.deps)).dispatched); }
{ const m = mk({ nothing: 1 }); assert.ok((await check(env, m.deps)).dispatched); }   // no build time
// failed dispatch is reported, never thrown; no token = no call
{ const m = mk({ generated_utc: iso(60) }, { dispatchStatus: 403 }); const r = await check(env, m.deps); assert.equal(r.dispatched, false); assert.equal(r.status, 403); }
{ const m = mk({ generated_utc: iso(60) }); const r = await check({ ...env, GH_DISPATCH_TOKEN: undefined }, m.deps); assert.equal(r.dispatched, false); assert.equal(posts(m).length, 0); }
// the token never reaches a log line or a response
for (const body of [{ generated_utc: iso(60) }, { generated_utc: iso(1) }]) { const m = mk(body); await check(env, m.deps); assert.ok(!m.logs.join('\n').includes(TOKEN), 'token logged'); }
const res = await worker.fetch(new Request('https://w.example/')); assert.equal(await res.text(), 'ok');
console.log('cron trigger tests passed');
