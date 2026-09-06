import test from 'node:test';
import assert from 'node:assert/strict';
import { createDiscovery } from '../connection.mjs';
import { applyDiscoverySnapshot, effortOptions, executionSelection, initialProvider, modelOptions, providersView, validateExecutionSelection } from '../product.mjs';

const provider = (id, extra = {}) => ({ id, ready: true, auto_connect: true, ...extra });
function harness(respond) {
  const calls = [], snapshots = [], errors = [], timers = new Map();
  let seq = 0, current = true, timeouts = 0;
  const discovery = createDiscovery({
    api: { request: async (path, options) => { calls.push({ path, options }); return respond(path, options, calls.length); } },
    isCurrent: () => current, onSnapshot: (value) => snapshots.push(value), onError: (error) => errors.push(error), onTimeout: () => timeouts++,
    schedule: (callback, delay) => { const id = ++seq; timers.set(id, { callback, delay }); return id; }, cancel: (id) => timers.delete(id),
  });
  return { discovery, calls, snapshots, errors, timers, expire: () => { current = false; }, get timeouts() { return timeouts; },
    async fire(delay) { const [id, task] = [...timers].find(([, task]) => task.delay === delay) || []; assert.ok(task, `timer ${delay}`); timers.delete(id); await task.callback(); },
  };
}

test('discovery starts exactly once with POST, polls only RUNNING, and never invokes run commands', async () => {
  const fixture = harness((_path, _options, count) => ({ discovery: { status: count === 1 ? 'RUNNING' : 'COMPLETE' } }));
  await fixture.discovery.start(); await fixture.discovery.start();
  assert.equal(fixture.calls.length, 1); assert.equal(fixture.calls[0].path, '/api/v2/capabilities/discover');
  assert.equal(fixture.calls[0].options.method, 'POST'); assert.deepEqual(fixture.calls[0].options.body, { refresh: false });
  await fixture.fire(1000);
  assert.equal(fixture.calls[1].path, '/api/v2/capabilities'); assert.equal(fixture.calls[1].options.method, undefined);
  assert.equal(fixture.snapshots.length, 2); assert.equal(fixture.timers.size, 0);
});

test('a partial discovery exposes a ready provider immediately while the remaining check continues', async () => {
  const capabilities = { discovery: { status: 'RUNNING' }, providers: [provider('codex'), provider('claude', { ready: null })] };
  const fixture = harness(() => capabilities); await fixture.discovery.start();
  assert.strictEqual(fixture.snapshots[0], capabilities); assert.equal(initialProvider(fixture.snapshots[0], null), 'codex');
  assert.ok([...fixture.timers.values()].some((item) => item.delay === 1000)); fixture.discovery.stop();
});

test('DEFERRED and non-running discovery statuses never poll or change active execution', async () => {
  for (const status of ['DEFERRED', 'COMPLETE', 'IDLE']) {
    const fixture = harness(() => ({ discovery: { status } })); await fixture.discovery.start(true);
    assert.equal(fixture.calls.length, 1); assert.deepEqual(fixture.calls[0].options.body, { refresh: true }); assert.equal(fixture.timers.size, 0);
  }
});

test('the 45-second deadline aborts a stalled initial request and ignores its eventual result', async () => {
  let resolve; const fixture = harness(() => new Promise((done) => { resolve = done; }));
  const pending = fixture.discovery.start(); await fixture.fire(45000);
  assert.equal(fixture.timeouts, 1); assert.equal(fixture.calls[0].options.signal.aborted, true);
  resolve({ discovery: { status: 'COMPLETE' }, providers: [provider('codex')] }); await pending;
  assert.equal(fixture.snapshots.length, 0); assert.equal(fixture.timers.size, 0);
});

test('the deadline also stops a running discovery between polls', async () => {
  const fixture = harness(() => ({ discovery: { status: 'RUNNING' } })); await fixture.discovery.start(); await fixture.fire(45000);
  assert.equal(fixture.timeouts, 1); assert.equal(fixture.timers.size, 0); assert.equal(fixture.calls.length, 1);
});

test('session errors end discovery without retry and remain visible to the authentication handler', async () => {
  for (const status of [401, 403]) {
    const error = Object.assign(new Error('session expired'), { status }); const fixture = harness(() => { throw error; });
    await fixture.discovery.start(); assert.deepEqual(fixture.errors, [error]); assert.equal(fixture.timers.size, 0);
  }
});

test('a replaced generation or explicit unmount cannot apply a late discovery result', async () => {
  for (const stop of [(fixture) => fixture.expire(), (fixture) => fixture.discovery.stop()]) {
    let resolve; const fixture = harness(() => new Promise((done) => { resolve = done; }));
    const pending = fixture.discovery.start(); stop(fixture); resolve({ discovery: { status: 'RUNNING' } }); await pending;
    assert.equal(fixture.snapshots.length, 0); assert.equal(fixture.timers.size, 0); assert.equal(fixture.errors.length, 0);
  }
});

test('native billing modes may be manually selected but never enter automatic selection', () => {
  const capabilities = { recommended_provider: 'opencode', providers: [provider('gemini', { auto_connect: false, auth_mode: 'native_unverified', billing_notice: '기존 로그인·모델·과금 설정을 확인하세요.' }), provider('opencode', { auto_connect: false }), provider('claude')] };
  assert.equal(initialProvider(capabilities, 'gemini'), 'claude');
  const gemini = providersView(capabilities)[0]; assert.equal(gemini.ready, true); assert.equal(gemini.autoConnect, false); assert.match(gemini.billing_notice, /과금/);
  assert.deepEqual(validateExecutionSelection(gemini, { provider: 'gemini', model: null, effort: null }), { provider: 'gemini', model: null, effort: null });
  assert.equal(initialProvider({ providers: capabilities.providers.slice(0, 2) }, 'gemini'), null);
});

test('automatic selection skips stale, unknown and ineligible preferences and recommendations', () => {
  const capabilities = { recommended_provider: 'codex', providers: [provider('codex', { ready: false }), provider('claude')] };
  assert.equal(initialProvider(capabilities, 'codex'), 'claude');
  assert.equal(initialProvider({ providers: [provider('codex', { ready: 'true' }), provider('claude', { auto_connect: 'true' })] }, null), null);
});

test('discovery facts cannot overwrite a later explicit choice or mutate a running project', () => {
  const run = Object.freeze({ execution: Object.freeze({ provider: 'gemini', model: 'current', effort: null }), jobs: [{ status: 'RUNNING' }] });
  const state = { run, choiceGeneration: 2, preferred: { provider: 'claude', model: 'selected', effort: 'high' } };
  applyDiscoverySnapshot(state, { providers: [provider('codex')] }, 1);
  assert.deepEqual(state.preferred, { provider: 'claude', model: 'selected', effort: 'high' });
  applyDiscoverySnapshot(state, { providers: [provider('codex')] }, 2);
  assert.equal(state.preferred.provider, 'codex'); assert.strictEqual(state.run, run);
  assert.deepEqual(executionSelection(state.run, state.preferred), { provider: 'gemini', model: 'current', effort: null });
});

test('a manual native-provider choice made before a fresh scan is not silently replaced', () => {
  const state = { choiceGeneration: 3, explicitProviderChoice: true, preferred: { provider: 'gemini', model: null, effort: null } };
  applyDiscoverySnapshot(state, { providers: [provider('codex'), provider('gemini', { auto_connect: false })] }, 3);
  assert.equal(state.preferred.provider, 'gemini');
});

test('models use the provider model/id/value catalog and never create an invented choice', () => {
  const available = modelOptions({ models: [null, { id: 'entry', model: 'actual', displayName: '실제 모델' }, { value: 'native', name: 'Native' }, { id: 'actual' }, { id: '<script>' }] });
  assert.deepEqual(available.map((item) => [item.value, item.label]), [['actual', '실제 모델'], ['native', 'Native']]);
  assert.deepEqual(modelOptions({ models: null }), []);
});

test('effort choices follow the selected model and the reported default, including a model with none', () => {
  const available = provider('codex', { default_model: 'm1', models: [{ model: 'm1', supportedReasoningEfforts: [{ reasoningEffort: 'high' }, { reasoningEffort: 'low' }] }, { model: 'm2', supportedReasoningEfforts: [] }] });
  assert.deepEqual(effortOptions(available, null).map((item) => item.value), ['high', 'low']);
  assert.deepEqual(effortOptions(available, 'm2'), []); assert.deepEqual(effortOptions(available, 'unknown'), []);
  assert.throws(() => validateExecutionSelection(available, { provider: 'codex', model: 'm2', effort: 'high' }), /추론/);
  assert.deepEqual(validateExecutionSelection(available, { provider: 'codex', model: 'm1', effort: 'low' }), { provider: 'codex', model: 'm1', effort: 'low' });
});

test('provider-wide effort names do not establish support for an unknown or unreported model', () => {
  const available = provider('codex', { efforts: ['ultra'], models: [{ model: 'm1' }] });
  assert.deepEqual(effortOptions(available, null), []); assert.deepEqual(effortOptions(available, 'm1'), []);
  assert.deepEqual(effortOptions(available, 'unknown'), []);
});

test('an existing unavailable selection can be preserved exactly, but cannot authorize a new unsupported choice', () => {
  const available = provider('opencode', { models: null }); const previous = { provider: 'opencode', model: 'old', effort: 'high' };
  assert.deepEqual(validateExecutionSelection(available, previous, previous), previous);
  assert.throws(() => validateExecutionSelection(available, { ...previous, model: 'other' }, previous), /모델/);
  assert.throws(() => validateExecutionSelection(available, { ...previous, effort: 'low' }, previous), /모델/);
  assert.deepEqual(validateExecutionSelection(available, { provider: 'opencode', model: null, effort: null }, previous), { provider: 'opencode', model: null, effort: null });
});

// These tests exercise the explicit login lifecycle, without opening an account.
test('login is one explicit POST followed by bounded read-only state polls', async () => {
  const { createLogin } = await import('../connection.mjs');
  const calls = [], snapshots = [], timers = new Map(); let seq = 0;
  const states = [
    { login: { status: 'RUNNING' }, discovery: { status: 'COMPLETE' } },
    { login: { status: 'COMPLETE' }, discovery: { status: 'RUNNING' } },
    { login: { status: 'COMPLETE' }, discovery: { status: 'COMPLETE' }, providers: [provider('codex')] },
  ];
  const login = createLogin({
    api: { async request(path, options) { calls.push({ path, options }); return options.method ? {} : states.shift(); } },
    isCurrent: () => true, onSnapshot: (value) => snapshots.push(value), onError: (error) => { throw error; }, onTimeout: () => assert.fail('timeout'),
    schedule: (callback, delay) => { const id = ++seq; timers.set(id, { callback, delay }); return id; }, cancel: (id) => timers.delete(id),
  });
  await login.start(); await login.start();
  for (let step = 0; step < 2; step++) {
    const [id, task] = [...timers].find(([, item]) => item.delay === 1000); timers.delete(id); await task.callback();
  }
  assert.equal(calls.filter((item) => item.options.method === 'POST').length, 1);
  assert.deepEqual(calls[0].options.body, { provider: 'codex' });
  assert.equal(snapshots.length, 3); assert.equal(timers.size, 0);
});

test('login timeout cancels transport and ignores a late authenticated result', async () => {
  const { createLogin } = await import('../connection.mjs');
  let resolve, expired = 0, applied = 0, deadline;
  const login = createLogin({ api: { request: () => new Promise((done) => { resolve = done; }) },
    isCurrent: () => true, onSnapshot: () => applied++, onError: assert.fail, onTimeout: () => expired++,
    schedule: (callback) => { deadline = callback; return 1; }, cancel() {},
  });
  const pending = login.start(); deadline(); resolve({ status: 'COMPLETE' }); await pending;
  assert.equal(expired, 1); assert.equal(applied, 0);
});
