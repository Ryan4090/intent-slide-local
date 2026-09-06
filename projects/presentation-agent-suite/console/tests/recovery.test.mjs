import test from 'node:test';
import assert from 'node:assert/strict';
import { initialProvider, recoverEventStream, refreshProviderState, providersView, executionSelection } from '../product.mjs';

test('an expired SSE session stops instead of scheduling another unauthenticated connection', async () => {
  for (const status of [401, 403]) {
    let expired = 0, scheduled = 0, checked = 0;
    await recoverEventStream({ verifySession: async () => { checked++; throw Object.assign(new Error('expired'), { status }); }, isCurrent: () => true,
      onExpired: () => expired++, onReconnect: () => assert.fail('must not reconnect'), schedule: () => scheduled++ });
    assert.equal(scheduled, 0, `HTTP ${status} must stop automatic retries`);
    assert.equal(expired, 1); assert.equal(checked, 1);
  }
});

test('a reachable authenticated session and a temporary outage remain recoverable', async () => {
  for (const verifySession of [async () => ({ csrf_token: 'fixture' }), async () => { throw new TypeError('offline'); }]) {
    let scheduled = 0;
    await recoverEventStream({ verifySession, isCurrent: () => true, onExpired: () => assert.fail('not an authentication expiry'),
      onReconnect: () => {}, schedule: (_callback, delay) => { scheduled++; assert.equal(delay, 2500); } });
    assert.equal(scheduled, 1);
  }
});

test('a late session probe from a replaced stream cannot stop or reconnect the current project', async () => {
  let current = true, finish;
  const pending = recoverEventStream({ verifySession: () => new Promise((resolve) => { finish = resolve; }), isCurrent: () => current,
    onExpired: () => assert.fail('stale probe'), onReconnect: () => assert.fail('stale reconnect'), schedule: () => assert.fail('stale timer') });
  current = false; finish?.(); await pending;
});

test('provider diagnostics preserve a newer selection made while the request is pending', async () => {
  let finish;
  const state = { preferred: { provider: 'codex', model: null, effort: null }, capabilities: {} };
  const api = { request: (_path, { body }) => { assert.equal(body.provider, 'codex'); return new Promise((resolve) => { finish = resolve; }); } };
  const pending = refreshProviderState(api, state, 'codex');
  state.preferred = { provider: 'claude', model: null, effort: null };
  const actual = { default_provider: 'codex', providers: [{ id: 'codex', ready: true, auth_mode: 'subscription' }] };
  finish(actual); await pending;
  assert.equal(state.preferred.provider, 'claude'); assert.strictEqual(state.capabilities, actual);
});

test('first visit always selects ready Codex regardless of saved preference or recommendation', () => {
  const capabilities = { recommended_provider: 'claude', providers: ['codex', 'claude'].map((id) => ({ id, ready: true, auto_connect: true })) };
  assert.equal(initialProvider(capabilities, null), 'codex');
  assert.equal(initialProvider(capabilities, 'codex'), 'codex');
  assert.equal(initialProvider(null, null), null);
  assert.equal(initialProvider({ default_provider: 'unknown' }, 'invalid'), null);
});


test('a diagnostic result from a replaced session cannot overwrite current capability facts', async () => {
  let finish, current = true;
  const state = { preferred: { provider: 'claude' }, capabilities: { reason: 'new session' } };
  const original = state.capabilities;
  const pending = refreshProviderState({ request: () => new Promise((resolve) => { finish = resolve; }) }, state, 'codex', () => current);
  current = false; finish({ reason: 'old session' }); await pending;
  assert.strictEqual(state.capabilities, original); assert.equal(state.preferred.provider, 'claude');
});

test('nullable capability metadata cannot fabricate readiness or fail the initial selection', () => {
  assert.deepEqual(providersView(null), []);
  const [provider] = providersView({ providers: [null, { id: 'codex', ready: null, auth_mode: 'unknown', features: null, models: null }] });
  assert.equal(provider.ready, false); assert.equal(provider.checked, false);
  assert.deepEqual(executionSelection(null, null), { provider: 'codex', model: null, effort: null });
});
