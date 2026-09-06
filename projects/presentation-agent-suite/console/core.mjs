/** Shared, side-effect-free view and transport contracts for the local console. */
export const STAGES = [
  { id: 'intent', label: '의도·구조 확정', short: '의도 확정', weight: 20, gate: 'G1', next: '의도·구조 승인' },
  { id: 'research', label: '리서치·분석', short: '리서치·분석', weight: 35, gate: 'G2', next: '리서치·분석 승인' },
  { id: 'design', label: '제작·검증·출고', short: '슬라이드 제작', weight: 45, gate: 'G3–G5', next: '디자인 승인 → 검사 → 출고' },
];

const LABELS = {
  DRAFT: '초안', READY: '준비됨', PENDING: '대기', WAITING: '대기',
  RUNNING: '실행 중', ACTIVE: '진행 중', COMPLETE: '완료', COMPLETED: '완료',
  PASSED: '통과', PASS: '통과', VALID: '유효', APPROVED: '승인됨',
  WAITING_USER: '사용자 검토 대기', WAITING_REVIEW: '검토 대기',
  BLOCKED: '진행 불가', FAILED: '실패', FAIL: '실패', STALE: '재검증 필요',
  INVALID: '무효', INVALIDATED: '무효', INTERRUPTED: '실행 중단', CANCELLED: '취소됨',
  CANCELED: '취소됨', REJECTED: '수정 요청', NEEDS_CHANGES: '수정 필요',
  NOT_STARTED: '시작 전', UNMEASURED: '미계측', SUPPORTED: '입증됨',
  UNSUPPORTED: '미입증', PARTIAL: '부분 입증', UNAVAILABLE: '근거 미확보',
  PROVIDED: '제공 자료', ANSWERED: '답변 완료', OPEN: '검토 필요',
  QUEUED: '실행 대기', WAITING_PROVIDER: 'AI 응답 대기', INTENT_INTERVIEW: '의도 인터뷰',
  RESEARCH_READY: '리서치 준비됨', DESIGN_READY: '디자인 준비됨', DESIGN_BUILD: '페이지 제작 준비',
  CHANGE_ASSESSMENT: '변경 영향 확인', SUPERSEDED: '새 버전으로 대체됨',
  INTENT_REVIEW: '의도 검토 대기', RESEARCH_REVIEW: '연구 검토 대기',
  DESIGN_DIRECTION_REVIEW: '디자인 검토 대기', DESIGN_REVIEW: '독립 검토 대기', REWORK: '수정 작업 필요',
  ASSUMPTION: '가정', PROPOSAL: '제안', VERIFIED: '확인됨', UNVERIFIED: '미검증',
  LEGACY_READ_ONLY: '기존 기록 · 읽기 전용', HISTORICAL: '과거 기록',
};

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

export function statusLabel(status) {
  return LABELS[String(status || 'NOT_STARTED').toUpperCase()] || String(status || '시작 전');
}

export function statusTone(status) {
  const value = String(status || '').toUpperCase();
  if (['COMPLETE', 'COMPLETED', 'PASSED', 'PASS', 'VALID', 'APPROVED', 'SUPPORTED', 'ANSWERED', 'VERIFIED'].includes(value)) return 'success';
  if (['FAILED', 'FAIL', 'BLOCKED', 'INVALID', 'INVALIDATED', 'UNSUPPORTED'].includes(value)) return 'danger';
  if (['STALE', 'WAITING_USER', 'WAITING_REVIEW', 'REJECTED', 'NEEDS_CHANGES', 'PARTIAL', 'UNAVAILABLE', 'OPEN', 'INTENT_REVIEW', 'RESEARCH_REVIEW', 'DESIGN_DIRECTION_REVIEW', 'DESIGN_REVIEW', 'REWORK'].includes(value)) return 'warning';
  if (['RUNNING', 'ACTIVE', 'QUEUED', 'CHANGE_ASSESSMENT', 'INTENT_INTERVIEW'].includes(value)) return 'active';
  return 'neutral';
}

export function boundedPercent(value) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return null;
  return Math.min(100, Math.max(0, Number(value)));
}

export function percentText(value) {
  const percent = boundedPercent(value);
  return percent === null ? '미계측' : `${Math.floor(percent)}%`;
}

export function stageView(snapshot = {}) {
  return STAGES.map((stage) => {
    const data = (snapshot.stages || []).find((item) => item.id === stage.id) || {};
    const progress = (snapshot.progress?.stages || []).find((item) => item.id === stage.id) || {};
    return { ...stage, ...data, progress: { ...progress, percent: boundedPercent(progress.percent) },
      inputs: Array.isArray(data.inputs) ? data.inputs : [],
      outputs: Array.isArray(data.outputs) ? data.outputs : [],
      findings: Array.isArray(data.findings) ? data.findings : [] };
  });
}

export function pageChangeChoices(snapshot = {}) {
  const units = snapshot.progress?.units || snapshot.units || [];
  const slides = snapshot.intent?.slides || [];
  const bindings = new Map();
  for (const unit of units.filter((item) => item.stage === 'design' && String(item.id).startsWith('page.'))) {
    const slideUid = unit.id.slice(5);
    const slide = slides.find((item) => item.uid === slideUid);
    const page = { uid: slideUid, label: slide?.display_id || unit.label || slideUid, title: slide?.title || '' };
    for (const id of unit.artifact_ids || []) {
      const pages = bindings.get(id) || [];
      if (!pages.some((item) => item.uid === slideUid)) pages.push(page);
      bindings.set(id, pages);
    }
  }
  return (snapshot.artifacts || []).filter((artifact) => artifact.valid === true
    && artifact.stage === 'design' && ['page', 'notes'].includes(artifact.kind)
    && (!artifact.run_id || artifact.run_id === snapshot.id) && bindings.has(artifact.id))
    .map((artifact) => ({ ...artifact, pages: bindings.get(artifact.id) }));
}

export function changePayload(snapshot, { stage, reason, scope = 'stage', artifactIds = [] }) {
  if (!STAGES.some((item) => item.id === stage)) throw new Error('수정할 단계를 선택해 주세요.');
  if (typeof reason !== 'string' || !reason.trim()) throw new Error('수정 이유와 원하는 결과를 입력해 주세요.');
  if (!['stage', 'pages'].includes(scope)) throw new Error('수정 범위를 다시 선택해 주세요.');
  if (scope === 'stage') return { stage, reason: reason.trim(), artifact_ids: [] };
  if (stage !== 'design') throw new Error('페이지별 수정은 슬라이드 제작 단계에서 선택할 수 있습니다.');
  const ids = [...new Set(artifactIds)];
  if (!ids.length) throw new Error('수정할 페이지 또는 발표 노트를 한 개 이상 선택해 주세요.');
  const eligible = new Set(pageChangeChoices(snapshot).map((artifact) => artifact.id));
  if (ids.some((id) => !eligible.has(id))) throw new Error('선택한 결과의 버전이 바뀌었습니다. 현재 유효한 페이지와 노트를 다시 선택해 주세요.');
  return { stage: 'design', reason: reason.trim(), artifact_ids: ids };
}

export function pendingReviews(snapshot = {}) {
  return (snapshot.reviews || []).filter((item) => ['PENDING', 'OPEN', 'WAITING_USER', 'WAITING_REVIEW'].includes(String(item.status).toUpperCase()));
}

export function pendingQuestions(snapshot = {}) {
  // The service accepts an answer only while the question is exactly PENDING.
  return (snapshot.questions || []).filter((item) => String(item.status).toUpperCase() === 'PENDING');
}

export function isReadOnly(snapshot = {}) {
  return snapshot.read_only === true || Boolean(snapshot.legacy) || snapshot.status === 'LEGACY_READ_ONLY';
}

export function explanationList(value) {
  if (Array.isArray(value)) return value.filter((item) => typeof item === 'string' && item.trim());
  return typeof value === 'string' && value.trim() ? [value] : [];
}

export function sourceUrl(value) {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function evidenceView(snapshot = {}) {
  const research = snapshot.research || {};
  const sources = new Map((research.sources || []).map((source) => [source.id, source]));
  const artifacts = new Map((snapshot.artifacts || []).filter((artifact) => !artifact.run_id || artifact.run_id === snapshot.id).map((artifact) => [artifact.id, artifact]));
  return {
    current: [...artifacts.values()].some((artifact) => artifact.kind === 'research' && artifact.valid === true),
    limitations: explanationList(research.limitations),
    claims: (research.claims || []).map((claim) => ({
      ...claim, limitations: explanationList(claim.limitations),
      proofs: (claim.supports || []).map((support) => {
        const source = sources.get(support.source_id);
        const artifact = source && artifacts.get(source.artifact_id);
        const verification = support.verification || {};
        const bound = Boolean(artifact?.valid === true && source.sha256 === artifact.sha256
          && verification.artifact_id === artifact.id && verification.sha256 === artifact.sha256);
        return {
          source_id: support.source_id, title: source?.title || support.source_id,
          locator: support.locator || '', excerpt: support.excerpt || '',
          origin: source?.origin, accessed_at: source?.accessed_at,
          url: sourceUrl(source?.url), artifact_id: artifact?.id || null,
          can_open: bound, verification_status: bound ? verification.status || 'UNVERIFIED' : 'STALE',
          semantic_status: verification.semantic_entailment || 'UNVERIFIED',
        };
      }),
    })),
  };
}

export function fileChangeReviewMarkup(request = {}) {
  if (request.method !== 'item/fileChange/requestApproval') return '';
  const context = request.review_context;
  const itemId = request.params?.itemId;
  if (!context || context.schema_version !== 'approval-context.v1' || context.item_type !== 'fileChange'
      || context.match_status !== 'MATCHED' || typeof itemId !== 'string' || !itemId
      || context.item_id !== itemId) {
    return '<div class="source-proof"><strong>파일 변경 정보를 확인할 수 없습니다</strong><p>현재 승인 요청과 연결된 파일 경로·변경 내용이 없거나 일치하지 않습니다. 아래 요청 원문에는 변경 내용이 없을 수 있습니다.</p></div>';
  }
  const plain = (value, fallback = '') => typeof value === 'string' && value ? value : fallback;
  const scopeBadge = (scope) => {
    const label = scope === 'inside' ? '시도 작업 폴더 안' : scope === 'outside' ? '시도 작업 폴더 밖' : '범위 확인 불가';
    return `<span class="badge ${scope === 'inside' ? 'neutral' : 'warning'}">${label}</span>`;
  };
  const pathMarkup = (file, label) => `<p>${label} · ${scopeBadge(file.scope)}</p><pre class="source-text">${escapeHtml(plain(file.path, '경로가 제공되지 않았습니다'))}</pre>${file.resolved_path && file.resolved_path !== file.path ? `<p>해석된 경로</p><pre class="source-text">${escapeHtml(plain(file.resolved_path, '해석된 경로 확인 불가'))}</pre>` : ''}${file.path_truncated ? '<p>경로가 잘렸습니다. 전체 경로를 이 정보에서 확인할 수 없습니다.</p>' : ''}`;
  const files = Array.isArray(context.files) ? context.files : [];
  const omitted = Number.isInteger(context.omitted_files) && context.omitted_files > 0 ? context.omitted_files : 0;
  const fileMarkup = files.map((file) => {
    if (!file || typeof file !== 'object') return '<p>파일 변경 항목을 읽을 수 없습니다.</p>';
    const kind = file.kind === 'add' ? '추가' : file.kind === 'update' ? '수정' : file.kind === 'delete' ? '삭제' : '종류 확인 불가';
    const hasDiff = file.diff_available === true && typeof file.diff === 'string' && file.diff.length > 0;
    return `<div class="source-proof"><strong>${kind}</strong>${pathMarkup(file, '요청 경로')}${file.move_to && typeof file.move_to === 'object' ? pathMarkup(file.move_to, '이동 대상') : ''}${file.diff_truncated ? '<p>변경 내용이 일부 잘렸습니다. 아래에는 제공된 부분만 표시합니다.</p>' : ''}${hasDiff ? `<details><summary>변경 내용 원문 확인 · 제공된 범위</summary><pre class="source-text">${escapeHtml(file.diff)}</pre></details>` : '<p>변경 내용이 제공되지 않았습니다. 파일 경로만으로 실제 변경을 확인할 수 없습니다.</p>'}</div>`;
  }).join('');
  return `<div class="source-proof"><strong>허용 전에 파일 변경 확인</strong><p>시도 작업 폴더</p><pre class="source-text">${escapeHtml(plain(context.workspace, '작업 폴더 정보 없음'))}</pre><p>변경 범위 · ${scopeBadge(context.scope)}</p>${context.truncated ? '<p>승인 검토 정보가 일부 잘렸습니다.</p>' : ''}${omitted ? `<p>파일 ${omitted}개가 생략되었습니다.</p>` : ''}${explanationList(context.notes).map((note) => `<p>${escapeHtml(note)}</p>`).join('')}${fileMarkup || '<p>파일 목록이 제공되지 않았습니다. 변경할 파일을 확인할 수 없습니다.</p>'}</div>`;
}

export function elicitationForm(params = {}) {
  const unsupported = (reason) => ({ supported: false, reason, fields: [] });
  if (!['form', 'openai/form', 'openaiForm'].includes(params.mode)) return unsupported('이 요청 형식은 작업실에서 입력할 수 없습니다. 요청을 거절하거나 취소할 수 있습니다.');
  const schema = params.requestedSchema;
  if (!schema || schema.type !== 'object' || !schema.properties || Array.isArray(schema.properties)
      || typeof schema.properties !== 'object') return unsupported('지원하는 객체 입력 양식이 아닙니다.');
  const allowedSchema = new Set(['type', 'properties', 'required', 'additionalProperties', '$schema', 'title', 'description']);
  if (Object.keys(schema).some((key) => !allowedSchema.has(key))) return unsupported('지원하지 않는 양식 조건이 포함되어 있습니다.');
  if (schema.additionalProperties !== undefined && typeof schema.additionalProperties !== 'boolean') return unsupported('추가 입력 항목의 조건을 지원하지 않습니다.');
  const required = schema.required || [];
  const entries = Object.entries(schema.properties);
  if (!Array.isArray(required) || required.some((key) => typeof key !== 'string' || !Object.hasOwn(schema.properties, key)) || entries.length > 30) return unsupported('필수 항목 또는 양식 크기를 확인할 수 없습니다.');
  const fields = [];
  const allowedField = new Set(['type', 'title', 'description', 'default', 'minLength', 'maxLength', 'enum', 'enumNames', 'oneOf', 'format']);
  for (const [key, field] of entries) {
    if (!field || typeof field !== 'object' || !['string', 'boolean'].includes(field.type)
        || Object.keys(field).some((name) => !allowedField.has(name)) || field.format) return unsupported(`“${key}” 항목에 지원하지 않는 입력 형식 또는 조건이 있습니다.`);
    let options = null;
    if (field.enum !== undefined && field.oneOf !== undefined) return unsupported(`“${key}” 항목에 중복된 선택 조건이 있습니다.`);
    if (field.enum !== undefined) {
      if (field.type !== 'string' || !Array.isArray(field.enum) || !field.enum.length || field.enum.length > 100 || field.enum.some((value) => typeof value !== 'string' || !value)) return unsupported(`“${key}” 선택지를 확인할 수 없습니다.`);
      options = field.enum.map((value, index) => ({ value, label: typeof field.enumNames?.[index] === 'string' ? field.enumNames[index] : value }));
    } else if (field.oneOf !== undefined) {
      if (field.type !== 'string' || !Array.isArray(field.oneOf) || !field.oneOf.length || field.oneOf.length > 100 || field.oneOf.some((option) => typeof option?.const !== 'string' || !option.const || (option.title != null && typeof option.title !== 'string') || Object.keys(option).some((name) => !['const', 'title'].includes(name)))) return unsupported(`“${key}” 선택 조건을 확인할 수 없습니다.`);
      options = field.oneOf.map((option) => ({ value: option.const, label: option.title || option.const }));
    }
    for (const limit of ['minLength', 'maxLength']) {
      if (field[limit] != null && (field.type !== 'string' || !Number.isInteger(field[limit]) || field[limit] < 0 || field[limit] > 12000)) return unsupported(`“${key}” 길이 조건을 지원하지 않습니다.`);
    }
    if (field.minLength != null && field.maxLength != null && field.minLength > field.maxLength) return unsupported(`“${key}” 길이 조건이 서로 맞지 않습니다.`);
    fields.push({ key, type: field.type, title: field.title || key, description: field.description || '', required: required.includes(key), options, minLength: field.minLength, maxLength: field.maxLength });
  }
  return { supported: true, reason: null, fields };
}

export function elicitationResponse(params, action, values = {}) {
  if (['decline', 'cancel'].includes(action)) return { action };
  if (action !== 'accept') throw new Error('지원하지 않는 추가 정보 응답입니다.');
  const form = elicitationForm(params);
  if (!form.supported) throw new Error(form.reason);
  const entries = [];
  for (const field of form.fields) {
    const value = Object.hasOwn(values, field.key) ? values[field.key] : undefined;
    if (value === undefined || value === null || value === '') {
      if (field.required) throw new Error(`“${field.title}” 항목을 입력해 주세요.`);
      continue;
    }
    if (field.type === 'boolean') {
      if (![true, false, 'true', 'false'].includes(value)) throw new Error(`“${field.title}” 항목에 예 또는 아니요를 선택해 주세요.`);
      entries.push([field.key, value === true || value === 'true']);
    } else {
      if (typeof value !== 'string' || (field.required && !value.trim())) throw new Error(`“${field.title}” 항목에 내용을 입력해 주세요.`);
      if (field.options && !field.options.some((option) => option.value === value)) throw new Error(`“${field.title}” 항목의 선택지가 유효하지 않습니다.`);
      const length = [...value].length;
      if (length < (field.minLength ?? 0) || length > (field.maxLength ?? 12000)) throw new Error(`“${field.title}” 항목의 입력 길이를 확인해 주세요.`);
      entries.push([field.key, value]);
    }
  }
  return { action: 'accept', content: Object.fromEntries(entries) };
}

export function activeJobs(snapshot = {}) {
  return (snapshot.jobs || []).filter((item) => ['RUNNING', 'QUEUED', 'STARTING', 'WAITING_PROVIDER', 'WAITING_USER'].includes(String(item.status).toUpperCase()));
}

export function pageExecutionView(snapshot = {}) {
  // Jobs are appended in attempt order. Never inherit a previous attempt's counters.
  const job = snapshot.jobs?.at(-1);
  if (isReadOnly(snapshot) || !job || job.phase !== 'design_build'
      || (job.run_id && job.run_id !== snapshot.id)) return null;
  const live = ['RUNNING', 'WAITING_USER'].includes(job.status);
  const pages = job.execution?.pages;
  const base = { jobId: job.id, jobStatus: job.status };
  const boundary = '최종 PPTX 파일 검사와 출고 승인은 별도로 진행합니다.';
  if (!pages) {
    const total = snapshot.intent?.slides?.length;
    if (!live || !Number.isInteger(total) || total < 1) return null;
    return { ...base, mode: 'awaiting', completed: 0, total, updatedAt: null,
      label: `슬라이드 작성 0/${total}`, description: `이번 시도의 첫 페이지 체크포인트를 기다리고 있습니다. ${boundary}` };
  }
  const count = (value) => Number.isInteger(value) && value >= 0 && value <= pages.total;
  if (pages.schema_version !== 'design-pages.v1' || pages.weighted_progress !== false
      || !Number.isInteger(pages.total) || pages.total < 1 || !count(pages.completed)) return null;
  if (pages.status === 'SUPERSEDED') return null;
  const values = { ...base, completed: pages.completed, total: pages.total, updatedAt: pages.updated_at };
  if (pages.status === 'CURRENT' && live) {
    return { ...values, mode: 'current', label: `슬라이드 작성 ${pages.completed}/${pages.total}`,
      description: `페이지 작성 체크포인트 검사를 통과한 수입니다. ${boundary}` };
  }
  if (pages.status === 'STALE') {
    if (pages.completed !== 0 || !count(pages.last_completed)) return null;
    return { ...values, completed: pages.last_completed, mode: 'history',
      label: `마지막 시도 · 페이지 검사 ${pages.last_completed}/${pages.total}`,
      description: '입력이 바뀌어 이 작성 기록은 현재 유효하지 않습니다. 페이지를 다시 검사해야 합니다.' };
  }
  if (['CANCELLED', 'INTERRUPTED'].includes(pages.status)
      || (pages.status === 'CURRENT' && ['FAILED', 'BLOCKED', 'CANCELLED', 'INTERRUPTED'].includes(job.status))) {
    return { ...values, mode: 'history', label: `마지막 시도 · 페이지 검사 ${pages.completed}/${pages.total}`,
      description: `중단된 시도에서 체크포인트 검사를 통과한 기록입니다. 새 시도에서 다시 확인합니다. ${boundary}` };
  }
  return null;
}

export function safeArtifactUrl(artifact, runId) {
  if (!runId || !artifact?.id) return null;
  // Construct the scoped endpoint instead of trusting model-provided URLs.
  return `/api/v2/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifact.id)}`;
}

export function previewKind(name = '', contentType = '') {
  const mime = contentType.split(';')[0].trim().toLowerCase();
  if (/\.(png|jpe?g|webp|gif)$/i.test(name) && ['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(mime)) return 'image';
  if (/\.pdf$/i.test(name) && mime === 'application/pdf') return 'pdf';
  if (/\.(md|txt|json|csv|tsv|yaml|yml|svg|html|log)$/i.test(name) || mime.startsWith('text/') || mime === 'application/json') return 'text';
  return 'download';
}

export function formatTime(value, full = false) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.valueOf())) return '시간 미기록';
  return new Intl.DateTimeFormat('ko-KR', full
    ? { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }
    : { hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
}

export function progressChange(previous, next) {
  if (!previous || previous.id !== next?.id) return null;
  const before = boundedPercent(previous.progress?.percent);
  const after = boundedPercent(next.progress?.percent);
  return before !== null && after !== null && after < before
    ? `진행률 ${Math.floor(before)}% → ${Math.floor(after)}%. ${next.progress?.reason || '입력 변경으로 영향을 받은 결과를 재검증합니다.'}` : null;
}

export class ApiError extends Error {
  constructor(status, message, data) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

export function operationId() {
  return globalThis.crypto.randomUUID();
}

export function takeLaunchToken(location, replaceUrl) {
  const parameters = new URLSearchParams(location.hash.slice(1));
  const token = parameters.get('session');
  if (token) {
    parameters.delete('session');
    replaceUrl(`${location.pathname}${location.search}${parameters.size ? `#${parameters}` : ''}`);
  }
  return token;
}

export function commandAttempt(snapshot, command, payload, previous = null) {
  const key = JSON.stringify([snapshot.id, command, payload]);
  if (previous?.key === key) return previous;
  return { key, operation_id: operationId(), snapshot: { id: snapshot.id, revision: snapshot.revision } };
}

export class ConsoleApi {
  constructor(fetcher = globalThis.fetch.bind(globalThis)) {
    this.fetcher = fetcher;
    this.csrfToken = null;
  }

  async request(path, { method = 'GET', body, signal } = {}) {
    const headers = { Accept: 'application/json' };
    if (method !== 'GET') {
      if (!this.csrfToken) throw new ApiError(401, '로컬 세션을 연결한 후 다시 시도하세요.');
      headers['X-CSRF-Token'] = this.csrfToken;
    }
    const isForm = typeof FormData !== 'undefined' && body instanceof FormData;
    if (body !== undefined && !isForm) headers['Content-Type'] = 'application/json';
    const response = await this.fetcher(path, {
      method, headers, credentials: 'same-origin', signal,
      ...(body === undefined ? {} : { body: isForm ? body : JSON.stringify(body) }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = data.error;
      const message = typeof error === 'string' ? error : error?.message || data.message || `요청을 처리하지 못했습니다 (${response.status}).`;
      throw new ApiError(response.status, message, data);
    }
    return data;
  }

  async connect(bootstrapToken = null) {
    let session;
    if (bootstrapToken) {
      const response = await this.fetcher('/api/v2/session', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ bootstrap_token: bootstrapToken }),
      });
      session = await response.json().catch(() => ({}));
      if (!response.ok) throw new ApiError(response.status, '접속 링크가 만료되었거나 이미 사용되었습니다. 실행 터미널의 새 접속 링크로 열어 주세요.');
    } else session = await this.request('/api/v2/session');
    if (!session.csrf_token) throw new ApiError(401, '로컬 세션 토큰을 받지 못했습니다.');
    this.csrfToken = session.csrf_token;
    return session;
  }

  command(snapshot, command, payload = {}, id = operationId()) {
    return this.request(`/api/v2/runs/${encodeURIComponent(snapshot.id)}/commands`, {
      method: 'POST', body: { operation_id: id, expected_revision: snapshot.revision, command, payload },
    });
  }
}
