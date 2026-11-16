// Cloudflare Worker: reliable trigger for the collect workflow (multi-scale guide 10, part A).
// Every 10 minutes: read the published status.json; if the newest build is older than STALE_MIN minutes (or unreadable), ask GitHub to run collect.yml.
// The token (secret GH_DISPATCH_TOKEN) is only ever sent as the Authorization header to api.github.com and is never logged or returned.

const num = (v, d) => { const n = Number(v); return Number.isFinite(n) && n > 0 ? n : d; };

// Build time in ms from either status format (v2: generated_utc, v3 live: generated_iso); NaN when absent.
export function builtAt(status) {
  const s = status && (status.generated_utc || status.generated_iso);
  return s ? Date.parse(s) : NaN;
}

export async function check(env, deps = {}) {
  const fetchFn = deps.fetch || fetch, now = (deps.now || Date.now)(), log = deps.log || console.log;
  const staleMs = num(env.STALE_MIN, 14) * 60000;
  let reason = null, ageMin = null;
  try {
    const r = await fetchFn(`${env.STATUS_URL}?t=${now}`, { headers: { 'Cache-Control': 'no-cache', 'User-Agent': 'deathfog-cron' } });
    if (!r.ok) reason = `status fetch http ${r.status}`;
    else {
      const t = builtAt(await r.json());
      if (!Number.isFinite(t)) reason = 'status has no build time';
      else { ageMin = Math.round((now - t) / 60000); if (now - t > staleMs) reason = `stale ${ageMin} min`; }
    }
  } catch (e) { reason = 'status fetch failed'; }
  if (!reason) { log(`fresh (${ageMin} min): no dispatch`); return { dispatched: false, ageMin }; }
  if (!env.GH_DISPATCH_TOKEN) { log(`${reason}: no GH_DISPATCH_TOKEN secret set, cannot dispatch`); return { dispatched: false, reason, error: 'no token' }; }
  const url = `https://api.github.com/repos/${env.REPO}/actions/workflows/${env.WORKFLOW}/dispatches`;
  try {
    const r = await fetchFn(url, { method: 'POST', body: JSON.stringify({ ref: env.REF || 'main' }),
      headers: { Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`, Accept: 'application/vnd.github+json', 'User-Agent': 'deathfog-cron', 'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28' } });
    log(`${reason}: dispatch ${r.status === 204 ? 'ok (204)' : 'failed http ' + r.status}`);   // GitHub answers 204 with no body on success
    return { dispatched: r.status === 204, reason, status: r.status };
  } catch (e) { log(`${reason}: dispatch request failed`); return { dispatched: false, reason, error: 'request failed' }; }
}

export default {
  scheduled(event, env, ctx) { ctx.waitUntil(check(env)); },
  async fetch() { return new Response('ok', { headers: { 'content-type': 'text/plain' } }); },
};
