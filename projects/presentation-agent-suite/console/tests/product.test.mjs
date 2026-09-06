import test from 'node:test';
import assert from 'node:assert/strict';
import { claimCategory, claimConditions, checkpointView, currentArtifacts, displayValue, executionSelection, nextAction, providerAnswerPayload, providersView, releaseView, researchSummary, reviewView, stageFiles, startCreatedProject } from '../product.mjs';

function released() {
  const artifact = (id, kind, extra = {}) => ({ id, run_id: 'r', name: `${id}.${kind === 'pptx' ? 'pptx' : 'png'}`, kind, stage: 'design', sha256: `hash-${id}`, valid: true, ...extra });
  return { id: 'r', status: 'COMPLETE', progress: { percent: 100 },
    candidate: { id: 'c', run_id: 'r', pptx_artifact_id: 'pptx', pptx_sha256: 'hash-pptx', contact_sheet_artifact_id: 'grid', contact_sheet_sha256: 'hash-grid', artifact_ids: ['pptx', 'grid', 'g4', 'page1', 'page2', 'notes'] },
    release: { candidate_id: 'c', run_id: 'r', pptx_artifact_id: 'pptx', pptx_sha256: 'hash-pptx', review_artifact_id: 'g5' },
    artifacts: [artifact('pptx', 'pptx'), artifact('old', 'pptx'), artifact('grid', 'contact_sheet'), artifact('g4', 'gate_receipt'), artifact('g5', 'review'),
      artifact('page2', 'render_page', { name: 'P02.png' }), artifact('page1', 'render_page', { name: 'P01.png' }), artifact('otherpage', 'render_page', { name: 'P00.png' }),
      artifact('notes', 'notes', { name: '01.md' }), artifact('release', 'release', { depends_on: ['pptx', 'grid', 'g4', 'g5'] })] };
}

test('provider readiness requires confirmed subscription auth, never guessed or API-key auth', () => {
  const providers = providersView({ providers: [{ id: 'codex', ready: null, auth_mode: 'unknown' }, { id: 'claude', ready: true, auth_mode: 'api_key' }, { id: 'fake', ready: true }] });
  assert.equal(providers.length, 2);
  assert.equal(providers[0].checked, false);
  assert.equal(providers[1].ready, false);
  assert.match(providers[1].statusText, /정액제/);
  assert.equal(providersView({ providers: [{ id: 'codex', ready: true, auth_mode: 'subscription' }] })[0].ready, true);
  assert.deepEqual(providersView(), []);
});

test('execution selection keeps the per-run model and effort instead of adopting another project preference', () => {
  assert.deepEqual(executionSelection({ execution: { provider: 'claude', model: 'chosen-model', effort: 'high' } }, { provider: 'codex' }), { provider: 'claude', model: 'chosen-model', effort: 'high' });
  assert.deepEqual(executionSelection(null, { provider: 'claude' }), { provider: 'claude', model: null, effort: null });
});

test('all five checkpoints derive completion from valid units and expose revalidation separately', () => {
  const run = { active_phase: 'design_build', jobs: [{ status: 'RUNNING' }], reviews: [{ id: 'review2', gate: 'G2', status: 'PENDING' }],
    progress: { units: [{ id: 'G1', status: 'VALID' }, { id: 'G3', status: 'STALE', reason: '입력 변경' }] } };
  const before = JSON.stringify(run);
  const checkpoints = checkpointView(run);
  assert.deepEqual(checkpoints.map((item) => item.id), ['G1', 'G2', 'G3', 'G4', 'G5']);
  assert.deepEqual(checkpoints.map((item) => item.status), ['COMPLETE', 'WAITING_REVIEW', 'STALE', 'RUNNING', 'NOT_STARTED']);
  assert.equal(checkpoints[2].reason, '입력 변경');
  assert.equal(JSON.stringify(run), before);
});

test('a waiting question or approval takes priority over another execution action', () => {
  assert.equal(nextAction({ jobs: [{ status: 'WAITING_USER' }], questions: [{ status: 'PENDING' }] }).kind, 'question');
  assert.equal(nextAction({ reviews: [{ gate: 'G1', id: 'g1', status: 'PENDING' }] }).kind, 'review');
  assert.equal(nextAction({ jobs: [{ status: 'RUNNING' }] }).kind, 'working');
  for (const status of ['FAILED', 'BLOCKED', 'INTERRUPTED', 'CANCELLED', 'STALE', 'REWORK']) assert.equal(nextAction({ status }).kind, 'resume');
  assert.equal(nextAction({ active_stage: 'intent' }).label, '의도 대화 시작하기');
});

test('the completed primary action uses the exact release PPTX and candidate-bound page renders', () => {
  const run = released();
  const view = releaseView(run);
  assert.equal(view.pptx.id, 'pptx');
  assert.deepEqual(view.pages.map((page) => page.name), ['P01.png', 'P02.png']);
  assert.equal(view.notes[0].id, 'notes');
  assert.equal(nextAction(run).kind, 'download');
  assert.equal(nextAction(run).artifact.id, 'pptx');
});

test('release download refuses incomplete, stale, cross-run, changed-hash and mismatched-candidate metadata', () => {
  const mutations = [
    (run) => { run.progress.percent = 99; }, (run) => { run.status = 'DESIGN_REVIEW'; },
    (run) => { run.release.pptx_sha256 = 'changed'; }, (run) => { run.candidate.contact_sheet_sha256 = 'changed'; },
    (run) => { run.release.candidate_id = 'other'; }, (run) => { run.release.run_id = 'other'; },
    (run) => { run.artifacts[0].valid = false; }, (run) => { run.artifacts[0].run_id = 'other'; },
    (run) => { run.candidate.artifact_ids.push('missing'); }, (run) => { run.artifacts.at(-1).depends_on = []; },
    (run) => { run.legacy = { path: 'archive' }; },
  ];
  for (const mutate of mutations) { const run = released(); mutate(run); assert.equal(releaseView(run), null); }
  assert.equal(nextAction({ status: 'COMPLETE', progress: { percent: 100 } }).kind, 'refresh');
});

test('current stage results exclude older invalidated versions but preserve them in explicit history', () => {
  const current = { id: 'new', valid: true }, old = { id: 'old', valid: false };
  const view = stageFiles({ stages: [{ id: 'research', inputs: [current, old], outputs: [old, current] }] })[1];
  assert.deepEqual(view.inputs, [current]);
  assert.deepEqual(view.current, [current]);
  assert.deepEqual(view.previous, [old]);
});

test('semantic review requires current artifacts from its exact review bundle', () => {
  const run = { id: 'r', intent: { fields: { audience: { value: '팀장' } } }, artifacts: [{ id: 'i', run_id: 'r', kind: 'intent', valid: true }] };
  const review = { gate: 'G1', bundle_sha256: 'bundle', artifact_ids: ['i'] };
  assert.equal(reviewView(run, review).bound, true);
  assert.equal(reviewView(run, { ...review, artifact_ids: ['missing'] }).bound, false);
  run.artifacts[0].valid = false;
  assert.equal(reviewView(run, review).bound, false);
});

test('research summary retains the distinction between verified facts, assumptions and unresolved evidence', () => {
  const view = researchSummary({ analysis: '결과 요약', limitations: ['기준시점 확인'], sources: [{ id: 's' }],
    claims: [{ evidence_status: 'SUPPORTED' }, { evidence_status: 'ASSUMPTION' }, { evidence_status: 'UNVERIFIED' }] });
  assert.deepEqual(view.analysis, ['결과 요약']);
  assert.equal(view.supported, 1); assert.equal(view.assumptions, 1); assert.equal(view.needsReview, 1);
  assert.equal(view.sources, 1);
});

test('display values do not stringify arbitrary objects or imply a verified artifact for another run', () => {
  assert.equal(displayValue(['목표', 10, { secret: 'not displayed' }]), '목표 · 10');
  assert.deepEqual(currentArtifacts({ id: 'a', artifacts: [{ id: 'b', run_id: 'b', valid: true }] }), []);
});


test('provider answers bind request zero to the exact question, job and selected provider', () => {
  const response = { decision: 'accept' };
  const question = { id: 'q', job_id: 'job-b', provider_id: 'claude', provider_request_id: 0 };
  const payload = providerAnswerPayload(question, response);
  assert.deepEqual(payload, { request_id: 0, question_id: 'q', job_id: 'job-b', provider_id: 'claude', response });
  assert.strictEqual(payload.response, response);
  assert.equal(providerAnswerPayload({ ...question, provider_id: undefined }, { action: 'decline' }).provider_id, 'codex');
  for (const field of ['id', 'job_id', 'provider_request_id']) assert.throws(() => providerAnswerPayload({ ...question, [field]: null }, response), /실행 정보/);
});

function file(name = 'brief.txt') { return new File(['reference material'], name, { type: 'text/plain', lastModified: 123 }); }

test('first project work waits for every real attachment and uses each returned revision before running', async () => {
  const calls = [], snapshots = [], attempts = [];
  let revision = 1;
  const api = {
    async request(path, { method, body }) {
      calls.push(body.get('file').name);
      assert.equal(path, '/api/v2/runs/new/attachments'); assert.equal(method, 'POST');
      assert.equal(body.get('expected_revision'), String(revision)); assert.ok(body.get('operation_id'));
      return { id: 'new', revision: ++revision };
    },
    async command(snapshot, command, payload, op) {
      calls.push(command); assert.equal(snapshot.revision, 3); assert.deepEqual(payload, {}); assert.ok(op);
      return { id: 'new', revision: ++revision, jobs: [{ status: 'QUEUED' }] };
    },
  };
  const result = await startCreatedProject(api, { id: 'new', revision: 1 }, [file('a.txt'), file('b.txt')], { onSnapshot: (snapshot) => snapshots.push(snapshot.revision), onUploadAttempt: (attempt) => attempts.push(attempt) });
  assert.deepEqual(calls, ['a.txt', 'b.txt', 'run']); assert.deepEqual(snapshots, [2, 3, 4]);
  assert.equal(result.jobs[0].status, 'QUEUED'); assert.equal(attempts.at(-1), null);
  assert.notEqual(attempts[0].operation_id, attempts[2].operation_id);
});

test('an uncertain initial attachment failure never starts work and retains its deliberate retry identity', async () => {
  let attempt;
  const api = { async request() { throw new Error('connection lost'); }, async command() { assert.fail('must not run before all attachments'); } };
  await assert.rejects(startCreatedProject(api, { id: 'new', revision: 4 }, [file()], { onUploadAttempt: (value) => { attempt = value; } }), /connection lost/);
  assert.equal(attempt.revision, 4); assert.ok(attempt.operation_id); assert.match(attempt.key, /brief.txt/);
});

test('cross-project attachment results cannot redirect the first execution', async () => {
  const api = { async request() { return { id: 'another', revision: 99 }; }, async command() { assert.fail('must not run another project'); } };
  await assert.rejects(startCreatedProject(api, { id: 'new', revision: 1 }, [file()]), /생성한 프로젝트/);
});

test('a project without attachments starts exactly once, preserving its execution selection', async () => {
  const snapshot = { id: 'new', revision: 1, execution: { provider: 'claude', model: null, effort: null } };
  let calls = 0, attempt;
  const api = { async request() { assert.fail('no upload is needed'); }, async command(current, command) { calls++; assert.strictEqual(current, snapshot); assert.equal(command, 'run'); return { ...snapshot, revision: 2 }; } };
  await startCreatedProject(api, snapshot, [], { onRunAttempt: (value) => { attempt = value; } });
  assert.equal(calls, 1); assert.equal(attempt, null); assert.equal(snapshot.execution.provider, 'claude');
});

test('design review only exposes current previews explicitly included in the review bundle', () => {
  const run = { id: 'r', direction: { summary: '큰 제목과 표 중심', preview_artifact_ids: ['image', 'old', 'unrelated'] }, artifacts: [
    { id: 'd', kind: 'direction', valid: true }, { id: 'image', kind: 'preview', name: 'direction.png', valid: true },
    { id: 'old', kind: 'preview', name: 'old.png', valid: false }, { id: 'unrelated', kind: 'preview', name: 'other.png', valid: true },
  ] };
  const view = reviewView(run, { gate: 'G3', bundle_sha256: 'bundle', artifact_ids: ['d', 'image'] });
  assert.equal(view.bound, true); assert.deepEqual(view.previews.map((artifact) => artifact.id), ['image']);
});

test('research review excerpts the first prose paragraphs instead of dumping Markdown and local input paths', () => {
  const analysis = '# 분석 보고서\n\n## 핵심 결과\n\n**고객 유지율**이 개선되었습니다. [제공 자료](inputs/customer.csv)를 기준으로 확인했습니다.\n\n| 항목 | 수치 |\n| --- | --- |\n| 유지율 | 80% |\n\n- 내부 메모\n\n비교 대상과 기준시점은 같습니다. `inputs/private/source.json`의 상세값은 보고서에 있습니다.\n\n세 번째 문단은 전체 보고서에만 둡니다.';
  const summary = researchSummary({ analysis });
  assert.equal(summary.analysis.length, 2);
  assert.equal(summary.analysis[0], '고객 유지율이 개선되었습니다. 제공 자료를 기준으로 확인했습니다.');
  assert.match(summary.analysis[1], /비교 대상과 기준시점/);
  assert.doesNotMatch(summary.analysis.join('\n'), /inputs\/|\*\*|^#|\| 항목|세 번째/m);
});

test('research excerpts stay bounded and never convert supplied Markdown links into active HTML', () => {
  const summary = researchSummary({ analysis: `첫 결과입니다. ${'상세 설명 '.repeat(200)}\n\n[확인](javascript:alert(1)) 및 <img src=x onerror=alert(1)>는 원문 텍스트입니다.` });
  assert.ok(summary.analysis.join('').length <= 600);
  assert.match(summary.analysis[0], /…$/);
  assert.doesNotMatch(summary.analysis.join(''), /javascript:/);
  assert.equal(summary.analysis.length, 2);
});

test('provided sources remain distinct from supported evidence and never imply semantic proof', () => {
  const claims = [...Array.from({ length: 10 }, () => ({ evidence_status: 'PROVIDED', verification: { source_locations: 'VERIFIED', semantic_entailment: 'UNVERIFIED' } })),
    { evidence_status: 'SUPPORTED' }, { evidence_status: 'ASSUMPTION' }, { evidence_status: 'PARTIAL' }, { evidence_status: 'VERIFIED' }];
  const before = JSON.stringify(claims);
  const summary = researchSummary({ claims, limitations: ['모집단이 달라 직접 비교하지 않습니다.'] });
  assert.equal(summary.provided, 10); assert.equal(summary.supported, 1); assert.equal(summary.assumptions, 1); assert.equal(summary.needsReview, 2);
  assert.equal(summary.verified, undefined);
  assert.deepEqual(summary.limitations, ['모집단이 달라 직접 비교하지 않습니다.']);
  assert.equal(JSON.stringify(claims), before);
});

test('headings, fenced implementation details and tables alone cannot become an invented prose summary', () => {
  const summary = researchSummary({ analysis: '# 제목\n\n```json\n{"path":"/Users/example/private.json"}\n```\n\n| 항목 | 값 |\n| --- | --- |\n\n- 검토 목록' });
  assert.deepEqual(summary.analysis, []);
});


test('source filters and counts use the same category without accepting a worker verification stamp', () => {
  for (const [status, category] of [['PROVIDED', 'provided'], ['SUPPORTED', 'supported'], ['ASSUMPTION', 'assumptions'], ['PROPOSAL', 'assumptions'], ['PARTIAL', 'needsReview'], ['UNAVAILABLE', 'needsReview'], ['VERIFIED', 'needsReview']]) {
    assert.equal(claimCategory({ evidence_status: status, verification: { semantic_entailment: 'VERIFIED' } }), category);
  }
});

test('comparison conditions preserve numeric scope and calculation text without inventing missing metadata', () => {
  const claim = { unit: '%', population: '동일 조사 대상', as_of: '2026-06', as_of_note: '관측일 기준', formula: '(a - b) / b * 100' };
  assert.deepEqual(claimConditions(claim).map((item) => item.value), ['%', '동일 조사 대상', '2026-06', '관측일 기준', '(a - b) / b * 100']);
  assert.deepEqual(claimConditions({}), []);
});
