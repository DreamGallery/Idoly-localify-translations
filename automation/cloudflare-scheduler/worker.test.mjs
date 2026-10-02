import assert from 'node:assert/strict';
import test from 'node:test';
import worker, {dispatch, scheduleKey} from './worker.mjs';

const time = Date.parse('2026-10-02T15:00:00Z');
const env = {GITHUB_DISPATCH_TOKEN: 'test-only', DRY_RUN: 'false'};
const empty = () => Response.json({total_count: 0, workflow_runs: []});

test('uses the Shanghai calendar date', () => {
  assert.equal(scheduleKey(time), 'cloudflare-2026-10-02');
  assert.equal(scheduleKey(Date.parse('2026-10-02T16:00:00Z')), 'cloudflare-2026-10-03');
});
test('production explicitly enables publication and translation on main', async () => {
  const calls = [];
  const result = await dispatch(env, time, async (url, options) => {
    calls.push({url, ...options});
    return calls.length === 1 ? empty() : new Response(null, {status: 204});
  });
  assert.equal(result.status, 'dispatched');
  assert.equal(calls[1].url, 'https://api.github.com/repos/DreamGallery/Idoly-localify-translations/actions/workflows/release.yml/dispatches');
  assert.deepEqual(JSON.parse(calls[1].body), {ref: 'main', inputs: {
    dry_run: false, no_translate: false, schedule_key: 'cloudflare-2026-10-02',
  }});
  assert.equal(calls[1].redirect, 'manual');
});
test('smoke test disables publication and model calls', async () => {
  await dispatch({...env, DRY_RUN: 'true'}, time, async (_, options) => {
    if (!options.method) return empty();
    assert.deepEqual(JSON.parse(options.body).inputs, {
      dry_run: true, no_translate: true, schedule_key: 'cloudflare-test-2026-10-02',
    });
    return new Response(null, {status: 204});
  });
});
test('redelivery skips a run already created today', async () => {
  let calls = 0;
  const result = await dispatch(env, time, async () => {
    calls++;
    return Response.json({total_count: 1, workflow_runs: [{id: 42,
      display_title: 'Scheduled localization release cloudflare-2026-10-02'}]});
  });
  assert.equal(calls, 1);
  assert.equal(result.run_id, 42);
});
test('missing token fails before any network call', async () => {
  await assert.rejects(dispatch({}, time, () => assert.fail()), /Missing/);
});
test('lookup failure never dispatches', async () => {
  await assert.rejects(dispatch(env, time, async () => new Response('', {status: 401})), /HTTP 401/);
});
test('ambiguous POST failure is not retried', async () => {
  let calls = 0;
  await assert.rejects(dispatch(env, time, async () => {
    if (++calls === 1) return empty();
    throw new Error('network timeout');
  }), /network timeout/);
  assert.equal(calls, 2);
});
test('public HTTP requests cannot dispatch releases', async () => {
  assert.equal(worker.fetch().status, 404);
});
