import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ApiError, ConsoleApi, activeJobs, boundedPercent, changePayload, commandAttempt, escapeHtml,
  elicitationForm, elicitationResponse, evidenceView, explanationList, fileChangeReviewMarkup, isReadOnly, pageChangeChoices, pageExecutionView, pendingQuestions, pendingReviews,
  percentText, previewKind, progressChange, safeArtifactUrl, sourceUrl, stageView, takeLaunchToken,
} from '../core.mjs';

const jsonResponse = (data, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'Content-Type': 'application/json' },
});

test('model-provided artifact names, messages and quoted attributes are escaped', () => {
  assert.equal(escapeHtml('<img src=x onerror="alert(1)"> & \'link\''), '&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; &#39;link&#39;');
  assert.equal(escapeHtml(null), '');
});

const fileApprovalFixture = (context = {}) => ({
  method: 'item/fileChange/requestApproval', params: { itemId: 'item-a' },
  review_context: { schema_version: 'approval-context.v1', job_id: 'job-a', item_id: 'item-a',
    item_type: 'fileChange', match_status: 'MATCHED', source: 'item/started',
    workspace: '/attempts/job-a', scope: 'inside', truncated: false, omitted_files: 0, notes: [],
    files: [{ path: 'page.svg', resolved_path: '/attempts/job-a/page.svg', scope: 'inside', kind: 'add',
      diff: '+page content', diff_available: true, diff_truncated: false, path_truncated: false, move_to: null }], ...context },
});

test('file approval displays matching paths, edit kinds and scope including a move destination', () => {
  const request = fileApprovalFixture();
  request.review_context.files.push({ path: 'notes.md', resolved_path: '/attempts/job-a/notes.md',
    kind: 'update', scope: 'inside', diff: '-before\n+after', diff_available: true,
    move_to: { path: '../other/notes.md', resolved_path: '/attempts/other/notes.md', scope: 'outside' } });
  request.review_context.files.push({ path: 'old.svg', kind: 'delete', scope: 'unknown' });
  const markup = fileChangeReviewMarkup(request);
  for (const text of ['page.svg', '/attempts/job-a/page.svg', '추가', '수정', '삭제', '시도 작업 폴더 안',
    '시도 작업 폴더 밖', '범위 확인 불가', '이동 대상', '/attempts/other/notes.md', '+after']) assert.ok(markup.includes(text));
  assert.doesNotMatch(markup, /data-provider-decision/);
});

test('missing or mismatched file-change context cannot describe another item as this approval', () => {
  for (const context of [undefined, { match_status: 'MISSING' }, { match_status: 'MISMATCH' },
    { item_id: 'other-item' }, { schema_version: 'unknown' }, { item_type: 'commandExecution' }]) {
    const request = fileApprovalFixture(context);
    if (context === undefined) delete request.review_context;
    const markup = fileChangeReviewMarkup(request);
    assert.match(markup, /파일 변경 정보를 확인할 수 없습니다/);
    assert.doesNotMatch(markup, /page\.svg/);
  }
});

test('missing and truncated file-review data are explicit before approving', () => {
  const request = fileApprovalFixture({ truncated: true, omitted_files: 2, notes: ['일부 이벤트 정보 누락'] });
  request.review_context.files[0] = { path: 'short-path', scope: 'unknown', kind: 'add',
    diff: null, diff_available: false, path_truncated: true };
  request.review_context.files.push({ path: 'page.svg', kind: 'update', scope: 'inside',
    diff: '+partial', diff_available: true, diff_truncated: true });
  const markup = fileChangeReviewMarkup(request);
  for (const text of ['정보가 일부 잘렸습니다', '파일 2개가 생략', '경로가 잘렸습니다',
    '변경 내용이 제공되지 않았습니다', '변경 내용이 일부 잘렸습니다', '일부 이벤트 정보 누락']) assert.ok(markup.includes(text));
});

test('file-review markup escapes untrusted paths, diffs and notes without changing decision payloads', () => {
  const attack = '<img src=x onerror="alert(1)"><script>run()</script>';
  const request = fileApprovalFixture({ workspace: attack, notes: [attack] });
  Object.assign(request.review_context.files[0], { path: attack, resolved_path: attack, diff: attack, kind: attack,
    move_to: { path: attack, resolved_path: attack, scope: 'outside' } });
  const before = JSON.stringify(request);
  const markup = fileChangeReviewMarkup(request);
  assert.doesNotMatch(markup, /<img|<script|onerror="/);
  assert.ok(markup.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'));
  assert.match(markup, /종류 확인 불가/);
  assert.equal(JSON.stringify(request), before);
  assert.equal(fileChangeReviewMarkup({ ...request, method: 'item/commandExecution/requestApproval' }), '');
  assert.equal(fileChangeReviewMarkup({ ...request, method: 'mcpServer/elicitation/request' }), '');
  Object.assign(request.review_context.files[0], { kind: 'constructor', scope: '__proto__' });
  const unknown = fileChangeReviewMarkup(request);
  assert.match(unknown, /종류 확인 불가/);
  assert.match(unknown, /범위 확인 불가/);
  assert.doesNotMatch(unknown, /function Object|\[object Object\]/);
});

test('progress preserves unknown values and never rounds 99.99 to 100', () => {
  for (const value of [null, undefined, '', NaN, Infinity, 'not a number']) assert.equal(boundedPercent(value), null);
  assert.equal(percentText(null), '미계측');
  assert.equal(percentText(99.99), '99%');
  assert.equal(percentText(0), '0%');
  assert.equal(boundedPercent(-1), 0);
  assert.equal(boundedPercent(150), 100);
});

test('stage cards show server progress, not a guessed completion based on status', () => {
  const stages = stageView({
    stages: [{ id: 'intent', status: 'COMPLETE', inputs: ['a'], outputs: ['b'] }],
    progress: { stages: [{ id: 'research', percent: 32 }] },
  });
  assert.equal(stages[0].progress.percent, null);
  assert.equal(stages[1].progress.percent, 32);
  assert.deepEqual(stages.map((stage) => stage.weight), [20, 35, 45]);
  assert.deepEqual(stages[0].inputs, ['a']);
  assert.deepEqual(stages[2].outputs, []);
});

const pageExecutionFixture = (job = {}, pages = {}) => ({
  id: 'run-a', progress: { percent: 65 },
  intent: { slides: [{ uid: 's1' }, { uid: 's2' }, { uid: 's3' }] },
  jobs: [{ id: 'job-a', run_id: 'run-a', phase: 'design_build', status: 'RUNNING',
    execution: { pages: { schema_version: 'design-pages.v1', completed: 2, total: 3,
      slide_uids: ['s1', 's2'], artifact_ids: ['preview-a', 'preview-b'],
      status: 'CURRENT', weighted_progress: false, updated_at: '2026-09-06T10:00:00+00:00', ...pages } }, ...job }],
});

test('page milestones use service checkpoints without changing validated overall progress', () => {
  for (const status of ['RUNNING', 'WAITING_USER']) {
    const snapshot = pageExecutionFixture({ status });
    const before = JSON.stringify(snapshot);
    const view = pageExecutionView(snapshot);
    assert.equal(view.mode, 'current');
    assert.equal(view.completed, 2);
    assert.equal(view.total, 3);
    assert.equal(view.label, '슬라이드 작성 2/3');
    assert.match(view.description, /최종 PPTX/);
    assert.equal(JSON.stringify(snapshot), before);
  }
  const allPages = pageExecutionView(pageExecutionFixture({}, { completed: 3 }));
  assert.equal(allPages.label, '슬라이드 작성 3/3');
  assert.match(allPages.description, /별도로/);
});

test('a new attempt waits for its own checkpoint instead of inheriting old page counts', () => {
  const snapshot = pageExecutionFixture({ status: 'FAILED' });
  snapshot.jobs.push({ id: 'job-b', run_id: 'run-a', phase: 'design_build', status: 'RUNNING' });
  const view = pageExecutionView(snapshot);
  assert.equal(view.mode, 'awaiting');
  assert.equal(view.completed, 0);
  assert.equal(view.total, 3);
  assert.match(view.description, /첫 페이지/);
  assert.equal(view.jobId, 'job-b');
  snapshot.jobs.push({ id: 'job-c', phase: 'design_review', status: 'RUNNING' });
  assert.equal(pageExecutionView(snapshot), null);
});

test('failed, blocked and stopped jobs expose page counts only as a past attempt', () => {
  for (const status of ['FAILED', 'BLOCKED', 'CANCELLED', 'INTERRUPTED']) {
    const view = pageExecutionView(pageExecutionFixture({ status }, {
      status: ['CANCELLED', 'INTERRUPTED'].includes(status) ? status : 'CURRENT',
    }));
    assert.equal(view.mode, 'history');
    assert.equal(view.completed, 2);
    assert.match(view.label, /마지막 시도/);
    assert.doesNotMatch(view.label, /^슬라이드 작성 /);
  }
});

test('invalidated checkpoint counts remain historical and adopted G4 counts disappear', () => {
  const view = pageExecutionView(pageExecutionFixture({}, { status: 'STALE', completed: 0, last_completed: 2 }));
  assert.equal(view.mode, 'history');
  assert.equal(view.completed, 2);
  assert.match(view.description, /다시 검사/);
  assert.equal(pageExecutionView(pageExecutionFixture({}, { status: 'SUPERSEDED' })), null);
  assert.equal(pageExecutionView(pageExecutionFixture({ status: 'SUCCEEDED' })), null);
});

test('unknown or malformed execution counters never fabricate page milestones', () => {
  for (const pages of [
    { schema_version: 'other' }, { weighted_progress: true }, { completed: -1 },
    { completed: 4 }, { completed: 1.5 }, { total: 0 }, { total: '3' },
    { completed: null }, { status: 'UNVERIFIED' }, { status: 'STALE', last_completed: 6 },
  ]) assert.equal(pageExecutionView(pageExecutionFixture({}, pages)), null);
  const unknownTotal = pageExecutionFixture({ execution: undefined });
  unknownTotal.intent = null;
  assert.equal(pageExecutionView(unknownTotal), null);
  assert.equal(pageExecutionView(pageExecutionFixture({ run_id: 'foreign' })), null);
  assert.equal(pageExecutionView({ ...pageExecutionFixture(), legacy: { path: 'old' } }), null);
});

test('invalidated or old approvals are absent from the actionable review queue', () => {
  const snapshot = { reviews: [{ id: 'one', status: 'PENDING' }, { id: 'two', status: 'APPROVED' }, { id: 'three', status: 'INVALIDATED' }],
    questions: [{ id: 'a', status: 'PENDING' }, { id: 'b', status: 'ANSWERED' }, { id: 'c', status: 'INVALIDATED' }],
    jobs: [{ id: 'j1', status: 'RUNNING' }, { id: 'j2', status: 'INTERRUPTED' }] };
  assert.deepEqual(pendingReviews(snapshot).map((item) => item.id), ['one']);
  assert.deepEqual(pendingQuestions(snapshot).map((item) => item.id), ['a']);
  assert.deepEqual(activeJobs(snapshot).map((item) => item.id), ['j1']);
});

test('artifact URLs cannot redirect to external or cross-project URLs from model fields', () => {
  assert.equal(safeArtifactUrl({ id: 'slides / one', download_url: 'javascript:alert(1)' }, 'project-a'), '/api/v2/runs/project-a/artifacts/slides%20%2F%20one');
  assert.equal(safeArtifactUrl({ id: 'x', download_url: '/api/v2/runs/other/artifacts/x' }, 'project-b'), '/api/v2/runs/project-b/artifacts/x');
  assert.equal(safeArtifactUrl({ id: 'x' }, null), null);
});

test('active HTML and SVG are text; raster previews require matching MIME and suffix', () => {
  assert.equal(previewKind('payload.svg', 'image/svg+xml'), 'text');
  assert.equal(previewKind('payload.html', 'text/html'), 'text');
  assert.equal(previewKind('cover.png', 'text/html'), 'text');
  assert.equal(previewKind('cover.png', 'image/png'), 'image');
  assert.equal(previewKind('deck.pdf', 'application/pdf'), 'pdf');
  assert.equal(previewKind('deck.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'), 'download');
});

test('progress regression has an explanation and never compares distinct projects', () => {
  const before = { id: 'a', progress: { percent: 85 } };
  const after = { id: 'a', progress: { percent: 55, reason: '핵심 근거가 바뀌었습니다.' } };
  assert.match(progressChange(before, after), /85% → 55%/);
  assert.match(progressChange(before, after), /핵심 근거/);
  assert.equal(progressChange(before, { ...after, id: 'b' }), null);
  assert.equal(progressChange({ id: 'a', progress: { percent: null } }, after), null);
});

test('mutations fail before network access without a browser session token', async () => {
  let calls = 0;
  const api = new ConsoleApi(async () => { calls += 1; return jsonResponse({}); });
  await assert.rejects(api.command({ id: 'a', revision: 1 }, 'approve', {}, 'op-a'), (error) => error instanceof ApiError && error.status === 401);
  assert.equal(calls, 0);
});

test('launch token exchanges only at the session endpoint and does not become the CSRF value', async () => {
  const calls = [];
  const api = new ConsoleApi(async (path, options) => {
    calls.push({ path, options });
    return jsonResponse({ csrf_token: 'csrf-new' });
  });
  await api.connect('bootstrap-once');
  assert.equal(calls[0].path, '/api/v2/session');
  assert.equal(calls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].options.body), { bootstrap_token: 'bootstrap-once' });
  assert.equal(api.csrfToken, 'csrf-new');
  assert.equal(calls[0].options.credentials, 'same-origin');
});

test('commands bind exact revision and operation ID, and 409 approval conflicts are never retried', async () => {
  const calls = [];
  const api = new ConsoleApi(async (path, options) => {
    calls.push({ path, options });
    if (path.endsWith('/session')) return jsonResponse({ csrf_token: 'csrf' });
    return jsonResponse({ error: 'stale revision' }, 409);
  });
  await api.connect();
  await assert.rejects(api.command({ id: 'a', revision: 7 }, 'approve', { review_id: 'g1', bundle_sha256: 'hash' }, 'op-fixed'), (error) => error.status === 409);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].options.headers['X-CSRF-Token'], 'csrf');
  assert.deepEqual(JSON.parse(calls[1].options.body), { operation_id: 'op-fixed', expected_revision: 7, command: 'approve', payload: { review_id: 'g1', bundle_sha256: 'hash' } });
});

test('actual attachment payload stays multipart and includes the CSRF token', async () => {
  let sent;
  const api = new ConsoleApi(async (_path, options) => { sent = options; return jsonResponse({ id: 'a' }); });
  api.csrfToken = 'csrf';
  const body = new FormData();
  body.append('file', new Blob(['source material']), 'source.txt');
  await api.request('/api/v2/runs/a/attachments', { method: 'POST', body });
  assert.equal(sent.body, body);
  assert.equal(sent.headers['Content-Type'], undefined);
  assert.equal(sent.headers['X-CSRF-Token'], 'csrf');
});

test('uncertain command retries preserve the first operation ID and revision; distinct decisions do not', () => {
  const payload = { review_id: 'g1', bundle_sha256: 'sha-a' };
  const attempt = commandAttempt({ id: 'a', revision: 4 }, 'approve', payload);
  const retry = commandAttempt({ id: 'a', revision: 5 }, 'approve', payload, attempt);
  assert.equal(retry, attempt);
  assert.equal(retry.snapshot.revision, 4);
  assert.notEqual(commandAttempt({ id: 'b', revision: 4 }, 'approve', payload, attempt).operation_id, attempt.operation_id);
  assert.notEqual(commandAttempt({ id: 'a', revision: 5 }, 'approve', { ...payload, bundle_sha256: 'sha-b' }, attempt).operation_id, attempt.operation_id);
});

test('both initial and later hash navigation consume the one-time credential and preserve project selection', () => {
  const location = { pathname: '/', search: '?view=console', hash: '' };
  const urls = [];
  assert.equal(takeLaunchToken(location, (url) => urls.push(url)), null);
  assert.deepEqual(urls, []);
  location.hash = '#session=fixture-token&run=fixture-run';
  assert.equal(takeLaunchToken(location, (url) => urls.push(url)), 'fixture-token');
  assert.deepEqual(urls, ['/?view=console#run=fixture-run']);
  assert.ok(!urls[0].includes('fixture-token'));
  location.hash = '#run=fixture-run';
  assert.equal(takeLaunchToken(location, (url) => urls.push(url)), null);
  assert.equal(urls.length, 1);
});

function pageFixture() {
  return {
    id: 'run-a', revision: 20,
    intent: { slides: [{ uid: 'slide-a', display_id: 'P01', title: '첫 번째 메시지' }, { uid: 'slide-b', display_id: 'P02', title: '두 번째 메시지' }] },
    units: [
      { id: 'page.slide-a', stage: 'design', status: 'VALID', artifact_ids: ['page-a', 'shared-notes'] },
      { id: 'page.slide-b', stage: 'design', status: 'VALID', artifact_ids: ['page-b', 'shared-notes'] },
    ],
    artifacts: [
      { id: 'page-a', run_id: 'run-a', stage: 'design', kind: 'page', valid: true, name: 'P01.svg', version: 2 },
      { id: 'page-b', run_id: 'run-a', stage: 'design', kind: 'page', valid: true, name: 'P02.svg', version: 1 },
      { id: 'shared-notes', run_id: 'run-a', stage: 'design', kind: 'notes', valid: true, name: 'total.md', version: 2 },
      { id: 'page-a-old', run_id: 'run-a', stage: 'design', kind: 'page', valid: true, name: 'P01.svg', version: 1 },
      { id: 'direction', run_id: 'run-a', stage: 'design', kind: 'direction', valid: true },
      { id: 'pptx', run_id: 'run-a', stage: 'design', kind: 'pptx', valid: true },
    ],
  };
}

test('page choices use current page-unit bindings, preserving stable page identity and shared-note impact', () => {
  const choices = pageChangeChoices(pageFixture());
  assert.deepEqual(choices.map((item) => item.id), ['page-a', 'page-b', 'shared-notes']);
  assert.deepEqual(choices[0].pages, [{ uid: 'slide-a', label: 'P01', title: '첫 번째 메시지' }]);
  assert.deepEqual(choices[2].pages.map((page) => page.label), ['P01', 'P02']);
  // An older valid file with the same filename is not the currently accepted page.
  assert.equal(choices.some((item) => item.id === 'page-a-old'), false);
});

test('scoped rework sends only selected page/notes IDs and never silently widens an empty selection', () => {
  const snapshot = pageFixture();
  assert.deepEqual(changePayload(snapshot, { stage: 'design', scope: 'pages', reason: ' 첫 페이지 제목을 간결하게 ', artifactIds: ['page-a', 'page-a'] }), {
    stage: 'design', reason: '첫 페이지 제목을 간결하게', artifact_ids: ['page-a'],
  });
  assert.deepEqual(changePayload(snapshot, { stage: 'design', scope: 'pages', reason: '노트 표현 수정', artifactIds: ['shared-notes'] }).artifact_ids, ['shared-notes']);
  assert.throws(() => changePayload(snapshot, { stage: 'design', scope: 'pages', reason: '수정', artifactIds: [] }), /한 개 이상/);
  assert.throws(() => changePayload(snapshot, { stage: 'research', scope: 'pages', reason: '수정', artifactIds: ['page-a'] }), /제작 단계/);
});

test('page rework rejects stale, replaced, foreign-run and non-page artifacts before sending a command', () => {
  for (const id of ['missing', 'page-a-old', 'direction', 'pptx']) {
    assert.throws(() => changePayload(pageFixture(), { stage: 'design', scope: 'pages', reason: '수정', artifactIds: [id] }), /버전이 바뀌었습니다/);
  }
  const stale = pageFixture();
  stale.artifacts[0].valid = false;
  assert.throws(() => changePayload(stale, { stage: 'design', scope: 'pages', reason: '수정', artifactIds: ['page-a'] }), /다시 선택/);
  const foreign = pageFixture();
  foreign.artifacts[0].run_id = 'run-b';
  assert.equal(pageChangeChoices(foreign).some((item) => item.id === 'page-a'), false);
});

test('whole-stage rework is explicit and empty reasons or unknown scope are rejected', () => {
  assert.deepEqual(changePayload(pageFixture(), { stage: 'design', scope: 'stage', reason: '디자인 방향 변경', artifactIds: ['page-a'] }), {
    stage: 'design', reason: '디자인 방향 변경', artifact_ids: [],
  });
  assert.throws(() => changePayload(pageFixture(), { stage: 'design', reason: '  ' }), /수정 이유/);
  assert.throws(() => changePayload(pageFixture(), { stage: 'design', reason: '수정', scope: 'unknown' }), /수정 범위/);
});

test('page labels follow reordered display IDs without changing the selected artifact identity', () => {
  const snapshot = pageFixture();
  snapshot.intent.slides[0].display_id = 'P02';
  snapshot.intent.slides[1].display_id = 'P01';
  assert.equal(pageChangeChoices(snapshot).find((item) => item.id === 'page-a').pages[0].label, 'P02');
  assert.deepEqual(changePayload(snapshot, { stage: 'design', scope: 'pages', reason: '제목 수정', artifactIds: ['page-a'] }).artifact_ids, ['page-a']);
});

test('only backend-answerable PENDING questions remain actionable across the provider lifecycle', () => {
  const statuses = ['PENDING', 'DISPATCHING', 'ANSWERED', 'RESOLVED', 'INTERRUPTED', 'CANCELLED', 'CANCELED', 'INVALIDATED', 'CLOSED', 'UNKNOWN'];
  assert.deepEqual(pendingQuestions({ questions: statuses.map((status) => ({ id: status, status })) }).map((question) => question.id), ['PENDING']);
});

test('the actual legacy metadata object disables mutation affordances', () => {
  assert.equal(isReadOnly({ legacy: { path: 'archive', historical_status: 'COMPLETE' } }), true);
  assert.equal(isReadOnly({ status: 'LEGACY_READ_ONLY' }), true);
  assert.equal(isReadOnly({ read_only: true }), true);
  assert.equal(isReadOnly({ legacy: false, status: 'COMPLETE' }), false);
  assert.equal(isReadOnly({ status: 'INTENT_INTERVIEW' }), false);
});

function evidenceFixture() {
  return {
    id: 'run-a',
    research: {
      limitations: '실제 분석에 쓰지 않는 검증용 자료입니다.',
      sources: [{ id: 'source-one', title: '제공 자료', artifact_id: 'source-artifact', sha256: 'source-sha', origin: 'provided', url: 'https://example.com/source' }],
      claims: [{ id: 'claim-one', text: '제공 자료에서 확인한 주장', evidence_status: 'PROVIDED', limitations: '표본 범위 안에서 사용', supports: [{ source_id: 'source-one', locator: 'lines:2-3', excerpt: '보존된 근거 문장', verification: { status: 'VERIFIED', artifact_id: 'source-artifact', sha256: 'source-sha', semantic_entailment: 'UNVERIFIED' } }] }],
    },
    artifacts: [
      { id: 'research-artifact', run_id: 'run-a', kind: 'research', valid: true },
      { id: 'source-artifact', run_id: 'run-a', kind: 'attachment', valid: true, sha256: 'source-sha' },
    ],
  };
}

test('research.claims and evidence_status map to the UI, including string limitations', () => {
  const view = evidenceView(evidenceFixture());
  assert.equal(view.current, true);
  assert.equal(view.claims.length, 1);
  assert.equal(view.claims[0].evidence_status, 'PROVIDED');
  assert.deepEqual(view.claims[0].limitations, ['표본 범위 안에서 사용']);
  assert.deepEqual(view.limitations, ['실제 분석에 쓰지 않는 검증용 자료입니다.']);
  assert.deepEqual(explanationList(['하나', '둘']), ['하나', '둘']);
  assert.deepEqual(explanationList(null), []);
});

test('source proofs bind the source ID to the scoped artifact and retain semantic uncertainty', () => {
  const proof = evidenceView(evidenceFixture()).claims[0].proofs[0];
  assert.equal(proof.artifact_id, 'source-artifact');
  assert.equal(proof.can_open, true);
  assert.equal(proof.locator, 'lines:2-3');
  assert.equal(proof.verification_status, 'VERIFIED');
  assert.equal(proof.semantic_status, 'UNVERIFIED');
  assert.equal(proof.url, 'https://example.com/source');
});

test('missing, invalidated, changed or foreign source artifacts cannot show a current verified proof link', () => {
  for (const mutate of [
    (snapshot) => { snapshot.artifacts.pop(); },
    (snapshot) => { snapshot.artifacts[1].valid = false; },
    (snapshot) => { snapshot.artifacts[1].sha256 = 'changed'; },
    (snapshot) => { snapshot.artifacts[1].run_id = 'run-b'; },
    (snapshot) => { snapshot.research.claims[0].supports[0].verification.artifact_id = 'different-artifact'; },
  ]) {
    const snapshot = evidenceFixture(); mutate(snapshot);
    const proof = evidenceView(snapshot).claims[0].proofs[0];
    assert.equal(proof.can_open, false);
    assert.equal(proof.verification_status, 'STALE');
  }
  const staleResearch = evidenceFixture();
  staleResearch.artifacts[0].valid = false;
  assert.equal(evidenceView(staleResearch).current, false);
});

test('external source links reject scripts, data URLs and embedded credentials', () => {
  for (const url of ['javascript:alert(1)', 'data:text/html,test', 'file:///etc/passwd', 'https://user:password@example.com', 'invalid']) assert.equal(sourceUrl(url), null);
  assert.equal(sourceUrl('https://example.com/page#table-1'), 'https://example.com/page#table-1');
});

function elicitationFixture(mode = 'form') {
  return { mode, serverName: 'fixture-mcp', message: '검증용 추가 정보 요청', requestedSchema: {
    type: 'object', required: ['audience', 'detail', 'include_notes'], properties: {
      audience: { type: 'string', title: '청중', minLength: 2, maxLength: 40 },
      detail: { type: 'string', enum: ['brief', 'full'], enumNames: ['간결하게', '자세하게'] },
      include_notes: { type: 'boolean', title: '발표 노트 포함' },
      optional_context: { type: 'string' },
    },
  } };
}

test('MCP form schemas expose supported primitives and preserve boolean false in the actual response envelope', () => {
  for (const mode of ['form', 'openai/form', 'openaiForm']) {
    const params = elicitationFixture(mode);
    const form = elicitationForm(params);
    assert.equal(form.supported, true);
    assert.equal(form.fields[1].options[0].label, '간결하게');
    assert.equal(form.fields[2].required, true);
    assert.deepEqual(elicitationResponse(params, 'accept', { audience: '운영 담당자', detail: 'brief', include_notes: 'false', optional_context: '', unrequested: 'discard me' }), {
      action: 'accept', content: { audience: '운영 담당자', detail: 'brief', include_notes: false },
    });
  }
});

test('MCP required, enum, boolean and length validation block invalid answers', () => {
  const params = elicitationFixture();
  const valid = { audience: '담당자', detail: 'full', include_notes: true };
  assert.throws(() => elicitationResponse(params, 'accept', { ...valid, audience: '' }), /청중/);
  assert.throws(() => elicitationResponse(params, 'accept', { ...valid, detail: 'invented' }), /선택지/);
  assert.throws(() => elicitationResponse(params, 'accept', { ...valid, include_notes: 'yes' }), /예 또는 아니요/);
  assert.throws(() => elicitationResponse(params, 'accept', { ...valid, audience: '가' }), /길이/);
  assert.throws(() => elicitationResponse(params, 'accept', { ...valid, include_notes: '' }), /발표 노트/);
});

test('MCP titled oneOf enumerations are supported without accepting arbitrary union schemas', () => {
  const params = elicitationFixture();
  params.requestedSchema.properties.detail = { type: 'string', oneOf: [{ const: 'brief', title: '간결한 설명' }, { const: 'full', title: '자세한 설명' }] };
  assert.equal(elicitationForm(params).fields[1].options[1].label, '자세한 설명');
  params.requestedSchema.properties.detail.oneOf.push({ type: 'number' });
  assert.equal(elicitationForm(params).supported, false);
});

test('unsupported MCP URL, number, array, format or conditional schemas retain decline/cancel without leaking form values', () => {
  const variants = [
    { mode: 'url', message: '외부 입력 요청', url: 'https://example.com/authorize' },
    { mode: 'unknown', message: '지원하지 않는 요청' },
    ...[{ type: 'number' }, { type: 'array', items: { type: 'string' } }, { type: 'string', format: 'password' }, { type: 'string', pattern: '^[A-Z]+$' }].map((field) => ({ mode: 'form', requestedSchema: { type: 'object', properties: { value: field } } })),
  ];
  for (const params of variants) {
    assert.equal(elicitationForm(params).supported, false);
    assert.throws(() => elicitationResponse(params, 'accept', { value: 'do not send' }));
    assert.deepEqual(elicitationResponse(params, 'decline', { value: 'do not send' }), { action: 'decline' });
    assert.deepEqual(elicitationResponse(params, 'cancel', { value: 'do not send' }), { action: 'cancel' });
  }
});

test('MCP input schemas with unknown required fields or oversized forms cannot silently drop requested data', () => {
  const params = elicitationFixture();
  params.requestedSchema.required.push('missing');
  assert.equal(elicitationForm(params).supported, false);
  assert.equal(elicitationForm({ mode: 'form', requestedSchema: { type: 'object', properties: Object.fromEntries(Array.from({ length: 31 }, (_, i) => [`field${i}`, { type: 'string' }])) } }).supported, false);
});
