const REPOSITORY = 'DreamGallery/Idoly-localify-translations';
const WORKFLOW = 'release.yml';
const API = `https://api.github.com/repos/${REPOSITORY}/actions/workflows/${WORKFLOW}`;

export function scheduleKey(time, dryRun = false) {
  if (!Number.isFinite(time)) throw new Error('Invalid scheduled time');
  const day = new Date(time + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
  return `${dryRun ? 'cloudflare-test' : 'cloudflare'}-${day}`;
}

export async function dispatch(env, time, request = fetch) {
  const token = env.GITHUB_DISPATCH_TOKEN?.trim();
  if (!token) throw new Error('Missing GITHUB_DISPATCH_TOKEN Secret');
  const dryRun = env.DRY_RUN === 'true';
  const key = scheduleKey(time, dryRun);
  const title = `Scheduled localization release ${key}`;
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'Content-Type': 'application/json',
    'User-Agent': 'Idoly-Localization-Scheduler',
    'X-GitHub-Api-Version': '2022-11-28',
  };
  // Search runs before dispatch so a redelivered event normally reuses today's run.
  // Do not retry an ambiguous POST: GitHub might already have accepted it.
  const since = new Date(time - 24 * 60 * 60 * 1000).toISOString();
  const query = new URLSearchParams({event: 'workflow_dispatch', branch: 'main',
    per_page: '100', created: `>=${since}`});
  const listing = await request(`${API}/runs?${query}`, {
    headers, redirect: 'manual', signal: AbortSignal.timeout(20000),
  });
  if (!listing.ok) throw new Error(`GitHub run lookup failed: HTTP ${listing.status}`);
  const data = await listing.json();
  if (!Array.isArray(data.workflow_runs) || data.total_count > 100) {
    throw new Error('Cannot verify recent runs completely');
  }
  const previous = data.workflow_runs.find(run => run.display_title === title);
  if (previous) return {status: 'already-dispatched', key, run_id: previous.id};
  const response = await request(`${API}/dispatches`, {
    method: 'POST', headers, redirect: 'manual', signal: AbortSignal.timeout(20000),
    body: JSON.stringify({ref: 'main', inputs: {
      dry_run: dryRun, no_translate: dryRun, schedule_key: key,
    }}),
  });
  if (response.status !== 204 && response.status !== 200) {
    throw new Error(`GitHub dispatch failed: HTTP ${response.status}`);
  }
  return {status: 'dispatched', key, dry_run: dryRun};
}

export default {
  async scheduled(controller, env) {
    const result = await dispatch(env, controller.scheduledTime);
    console.log(JSON.stringify(result));
  },
  fetch() {
    return new Response('Not found', {status: 404});
  },
};
