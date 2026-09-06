/** One bounded discovery pass. This reads connection facts; it never starts or reconfigures a job. */
export function createDiscovery({ api, isCurrent, onSnapshot, onError, onTimeout,
  schedule = setTimeout, cancel = clearTimeout, timeoutMs = 45000, pollMs = 1000 }) {
  const controller = new AbortController();
  let stopped = false, started = false, pollTimer = null, deadlineTimer = null;
  const current = () => !stopped && isCurrent();
  const stop = () => {
    stopped = true;
    cancel(pollTimer); cancel(deadlineTimer);
    controller.abort();
  };
  const request = async (initial, refresh = false) => {
    if (!current()) { stop(); return; }
    try {
      const result = await api.request(initial ? '/api/v2/capabilities/discover' : '/api/v2/capabilities', {
        ...(initial ? { method: 'POST', body: { refresh } } : {}), signal: controller.signal,
      });
      if (!current()) { stop(); return; }
      onSnapshot(result);
      if (!current()) { stop(); return; }
      if (result?.discovery?.status === 'RUNNING') pollTimer = schedule(() => request(false), pollMs);
      else stop();
    } catch (error) {
      const report = current() && error.name !== 'AbortError';
      stop();
      if (report) onError(error);
    }
  };
  return { stop, start(refresh = false) {
    if (started || !current()) return;
    started = true;
    deadlineTimer = schedule(() => {
      const report = current(); stop();
      if (report) onTimeout();
    }, timeoutMs);
    return request(true, refresh);
  } };
}

/** Login is explicit. Poll state only; credentials stay in the native CLI. */
export function createLogin({ api, isCurrent, onSnapshot, onError, onTimeout,
  schedule = setTimeout, cancel = clearTimeout, timeoutMs = 330000, pollMs = 1000 }) {
  const controller = new AbortController();
  let stopped = false, started = false, pollTimer = null, deadlineTimer = null;
  const current = () => !stopped && isCurrent();
  const stop = () => { stopped = true; cancel(pollTimer); cancel(deadlineTimer); controller.abort(); };
  const poll = async () => {
    if (!current()) { stop(); return; }
    try {
      const snapshot = await api.request('/api/v2/capabilities', { signal: controller.signal });
      if (!current()) { stop(); return; }
      onSnapshot(snapshot);
      if (!current()) { stop(); return; }
      if (['STARTING', 'RUNNING'].includes(snapshot.login?.status) || snapshot.discovery?.status === 'RUNNING') pollTimer = schedule(poll, pollMs);
      else stop();
    } catch (error) { const report = current() && error.name !== 'AbortError'; stop(); if (report) onError(error); }
  };
  return { stop, async start() {
    if (started || !current()) return;
    started = true;
    deadlineTimer = schedule(() => { const report = current(); stop(); if (report) onTimeout(); }, timeoutMs);
    try {
      await api.request('/api/v2/providers/login', { method: 'POST', body: { provider: 'codex' }, signal: controller.signal });
      if (current()) return poll();
    } catch (error) { const report = current() && error.name !== 'AbortError'; stop(); if (report) onError(error); }
  } };
}
