/** Product projections: server state remains the authority for progress and release. */
import { activeJobs, explanationList, isReadOnly, pendingQuestions, pendingReviews, stageView, operationId } from './core.mjs';

export const CHECKPOINTS = [
  { id: 'G1', label: '내용 확정', stage: 'intent', phase: 'intent', owner: '내 검토' },
  { id: 'G2', label: '근거 확정', stage: 'research', phase: 'research', owner: '내 검토' },
  { id: 'G3', label: '디자인 확정', stage: 'design', phase: 'design_direction', owner: '내 검토' },
  { id: 'G4', label: '파일 검사', stage: 'design', phase: 'design_build', owner: '자동 검사' },
  { id: 'G5', label: '최종 검토', stage: 'design', phase: 'design_review', owner: '독립 검토' },
];

export const ARTIFACT_LABELS = {
  attachment: '제공 자료', source: '조사 원문', intent: '목적과 슬라이드 구성', research: '주장과 근거',
  analysis_report: '분석 보고서', analysis_pdf: '분석 보고서 PDF', direction: '디자인 방향',
  page: '슬라이드 원본', notes: '발표 노트', pptx: 'PowerPoint', contact_sheet: '전체 미리보기',
  render_page: '페이지 미리보기', preview: '디자인 미리보기', review: '검토 기록',
  gate_receipt: '파일 검사 기록', release: '최종 출고 기록', render_provenance: '미리보기 검사 기록',
};

export function providersView(capabilities = {}) {
  return (Array.isArray(capabilities?.providers) ? capabilities.providers : [])
    .filter((provider) => ['codex', 'claude'].includes(provider?.id))
    .map((provider) => ({ ...provider,
      ready: provider.ready === true && provider.auth_mode === 'subscription',
      checked: provider.ready !== null && provider.ready !== undefined,
      label: provider.label || (provider.id === 'codex' ? 'Codex' : 'Claude Code'),
      statusText: provider.ready === true && provider.auth_mode === 'subscription' ? '정액제 로그인 연결됨'
        : provider.auth_mode === 'api_key' ? '정액제 로그인으로 연결해 주세요'
          : provider.ready == null ? '연결 확인 필요' : '준비가 필요합니다',
    }));
}

export function executionSelection(run, preferred = {}) {
  const value = run?.execution || preferred || {};
  return { provider: ['codex', 'claude'].includes(value.provider) ? value.provider : 'codex',
    model: typeof value.model === 'string' && value.model ? value.model : null,
    effort: typeof value.effort === 'string' && value.effort ? value.effort : null };
}

export function providerAnswerPayload(question, response) {
  if (!question?.id || !question.job_id || question.provider_request_id == null) throw new Error('질문의 실행 정보를 다시 불러와 주세요.');
  return { request_id: question.provider_request_id, response, question_id: question.id,
    job_id: question.job_id, provider_id: question.provider_id || 'codex' };
}

export function checkpointView(run = {}) {
  const units = run.progress?.units || run.units || [];
  return CHECKPOINTS.map((checkpoint) => {
    const unit = units.find((item) => item.id === checkpoint.id);
    const review = pendingReviews(run).find((item) => item.gate === checkpoint.id);
    let status = unit?.status === 'VALID' ? 'COMPLETE' : unit?.status === 'STALE' ? 'STALE' : 'NOT_STARTED';
    if (status !== 'COMPLETE' && status !== 'STALE') {
      if (review) status = 'WAITING_REVIEW';
      else if (run.active_phase === checkpoint.phase || (checkpoint.id === 'G2' && run.active_phase === 'research_checkpoint')) {
        if (['FAILED', 'BLOCKED', 'INTERRUPTED', 'CANCELLED'].includes(run.status)) status = run.status;
        else if (activeJobs(run).length) status = 'RUNNING';
      }
    }
    return { ...checkpoint, status, reason: unit?.reason || '', reviewId: review?.id || null };
  });
}

export function currentArtifacts(run = {}) {
  return (run.artifacts || []).filter((artifact) => artifact.valid === true && (!artifact.run_id || artifact.run_id === run.id));
}

export function releaseView(run = {}) {
  if (run.status !== 'COMPLETE' || run.progress?.percent !== 100 || isReadOnly(run)) return null;
  const release = run.release;
  const candidate = run.candidate;
  if (!release || !candidate || release.run_id !== run.id || candidate.run_id !== run.id
      || release.candidate_id !== candidate.id) return null;
  const artifacts = currentArtifacts(run);
  const byId = new Map(artifacts.map((artifact) => [artifact.id, artifact]));
  const pptx = byId.get(release.pptx_artifact_id);
  const preview = byId.get(candidate.contact_sheet_artifact_id);
  const review = byId.get(release.review_artifact_id);
  const ids = new Set(candidate.artifact_ids || []);
  if (!pptx?.sha256 || pptx.kind !== 'pptx' || candidate.pptx_artifact_id !== pptx.id
      || release.pptx_sha256 !== pptx.sha256 || candidate.pptx_sha256 !== pptx.sha256
      || !preview?.sha256 || preview.kind !== 'contact_sheet' || candidate.contact_sheet_sha256 !== preview.sha256
      || review?.kind !== 'review' || !ids.has(pptx.id) || !ids.has(preview.id)
      || [...ids].some((id) => !byId.has(id))) return null;
  const manifests = artifacts.filter((artifact) => artifact.kind === 'release'
    && artifact.depends_on?.includes(pptx.id) && artifact.depends_on?.includes(review.id));
  if (manifests.length !== 1) return null;
  const candidateFiles = artifacts.filter((artifact) => ids.has(artifact.id));
  return { pptx, preview, review, manifest: manifests[0],
    pages: candidateFiles.filter((artifact) => artifact.kind === 'render_page').sort((a, b) => a.name.localeCompare(b.name, 'ko', { numeric: true })),
    notes: candidateFiles.filter((artifact) => artifact.kind === 'notes').sort((a, b) => a.name.localeCompare(b.name, 'ko', { numeric: true })) };
}

export function nextAction(run = {}) {
  if (isReadOnly(run)) return { kind: 'history', label: '기존 기록 보기', detail: '이 작업은 읽기 전용으로 보존되어 있습니다.' };
  const question = pendingQuestions(run)[0];
  if (question) return { kind: 'question', label: '질문에 답변하기', detail: '답변을 기다리고 있습니다. 아래 질문을 확인해 주세요.' };
  const review = pendingReviews(run)[0];
  if (review) return { kind: 'review', label: `${CHECKPOINTS.find((item) => item.id === review.gate)?.label || '결과'} 검토하기`, reviewId: review.id, detail: '내용을 검토한 뒤 승인하거나 수정을 요청해 주세요.' };
  const release = releaseView(run);
  if (release) return { kind: 'download', label: 'PPTX 다운로드', artifact: release.pptx, detail: '검증과 최종 검토를 마쳤습니다. 완성된 슬라이드를 확인하세요.' };
  if (run.status === 'COMPLETE') return { kind: 'refresh', label: '출고 상태 다시 확인', detail: '최종 파일과 출고 기록이 일치하는지 다시 확인해야 합니다.' };
  if (activeJobs(run).length) return { kind: 'working', label: '작업 진행 중', detail: '현재 단계의 결과를 준비하고 있습니다. 필요한 결정이 생기면 여기에 표시합니다.' };
  if (['FAILED', 'BLOCKED', 'INTERRUPTED', 'CANCELLED', 'STALE', 'REWORK'].includes(run.status)) {
    return { kind: 'resume', label: '이어서 진행하기', detail: '완료된 결과를 보존했습니다. 확인할 사항을 해결한 뒤 이어서 진행하세요.' };
  }
  return { kind: 'run', label: run.active_stage === 'intent' ? '의도 대화 시작하기' : '다음 단계 시작하기', detail: '준비된 입력으로 다음 작업을 시작합니다.' };
}

export function stageFiles(run = {}) {
  return stageView(run).map((stage) => ({ ...stage,
    inputs: stage.inputs.filter((artifact) => artifact.valid === true),
    current: stage.outputs.filter((artifact) => artifact.valid === true),
    previous: stage.outputs.filter((artifact) => artifact.valid !== true),
  }));
}

export function reviewView(run, review) {
  const artifacts = currentArtifacts(run);
  const byId = new Map(artifacts.map((artifact) => [artifact.id, artifact]));
  const ids = review.artifact_ids || [];
  const kind = { G1: 'intent', G2: 'research', G3: 'direction' }[review.gate];
  const bound = Boolean(kind && review.bundle_sha256 && ids.length
    && ids.every((id) => byId.has(id)) && ids.some((id) => byId.get(id).kind === kind));
  const data = review.gate === 'G1' ? run.intent : review.gate === 'G2' ? run.research : run.direction;
  return { bound: bound && Boolean(data), data: data || {},
    artifacts: ids.map((id) => byId.get(id)).filter(Boolean),
    title: CHECKPOINTS.find((item) => item.id === review.gate)?.label || '단계 결과',
    previews: review.gate === 'G3' ? (run.direction?.preview_artifact_ids || []).map((id) => byId.get(id)).filter((artifact) => artifact && ids.includes(artifact.id) && /\.(png|jpe?g|webp)$/i.test(artifact.name)) : [],
  };
}

export function displayValue(value) {
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (Array.isArray(value)) return value.map(displayValue).filter(Boolean).join(' · ');
  return '';
}

/** Plain text only: link targets and working paths are not review copy. */
export function analysisExcerpt(value) {
  const text = explanationList(value).join('\n\n').replace(/```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)/g, '');
  const paragraphs = [];
  for (const block of text.split(/\n\s*\n/)) {
    const lines = block.split('\n');
    const prose = lines.filter((line, index) => {
      const trimmed = line.trim();
      return trimmed && !/^#{1,6}\s|^(?:[-*+]\s|\d+[.)]\s)|^>\s|^\[[^\]]+\]:/.test(trimmed)
        && !/^(?:[-=]{3,}|\*\s*\*\s*\*)$/.test(trimmed)
        && !/^\s*[-=]{3,}\s*$/.test(lines[index + 1] || '')
        && (trimmed.match(/\|/g) || []).length < 2
        && !/^(?:입력\s*경로|파일\s*경로|source_path|artifact_id|run_id|job_id|schema_version)\s*[:：]/i.test(trimmed);
    }).join(' ')
      .replace(/!?\[([^\]\n]*)\]\((?:[^()\n]|\([^()\n]*\))*\)/g, '$1')
      .replace(/\[([^\]\n]+)\]\[[^\]\n]*\]/g, '$1')
      .replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, (_match, bold, underline) => bold || underline)
      .replace(/(^|\s)[*_]([^*_]+)[*_](?=\s|[.,!?]|$)/g, '$1$2')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/(?:file:\/\/\/|\/(?:Users|home|tmp|private|var)\/)[^\s<>"'`]+|(?:\.{1,2}\/)?(?:inputs|artifacts|attempts|\.runtime)\/[^\s<>"'`]+/gi, '참고 자료')
      .replace(/https?:\/\/[^\s<>]+/gi, '출처 링크')
      .replace(/\s+/g, ' ').trim();
    if (!prose) continue;
    const limit = 280;
    if (prose.length <= limit) paragraphs.push(prose);
    else {
      const prefix = prose.slice(0, limit - 1);
      const boundary = prefix.lastIndexOf(' ');
      paragraphs.push(`${boundary > limit * .65 ? prefix.slice(0, boundary) : prefix}…`);
    }
    if (paragraphs.length === 2) break;
  }
  return paragraphs;
}

export function claimCategory(claim) {
  if (claim?.evidence_status === 'PROVIDED') return 'provided';
  if (claim?.evidence_status === 'SUPPORTED') return 'supported';
  if (['ASSUMPTION', 'PROPOSAL'].includes(claim?.evidence_status)) return 'assumptions';
  return 'needsReview';
}

export function claimConditions(claim) {
  return [['unit', '단위'], ['population', '비교 대상'], ['as_of', '기준시점'], ['as_of_note', '시점 조건'], ['formula', '계산식']]
    .map(([key, label]) => ({ label, value: displayValue(claim?.[key]) })).filter((item) => item.value);
}

export function researchSummary(research = {}) {
  const claims = research?.claims || [];
  return { analysis: analysisExcerpt(research?.analysis), limitations: explanationList(research?.limitations),
    provided: claims.filter((claim) => claimCategory(claim) === 'provided').length,
    supported: claims.filter((claim) => claimCategory(claim) === 'supported').length,
    assumptions: claims.filter((claim) => claimCategory(claim) === 'assumptions').length,
    needsReview: claims.filter((claim) => claimCategory(claim) === 'needsReview').length,
    sources: (research?.sources || []).length, messages: research?.messages || [] };
}


/** Attach every selected input before the first worker run; never continue after an upload failure. */
export async function startCreatedProject(api, snapshot, files, { onSnapshot = () => {}, onUploadAttempt = () => {}, onRunAttempt = () => {} } = {}) {
  let current = snapshot.run || snapshot;
  const runId = current.id;
  const accept = (result) => {
    const next = result.run || result;
    if (next.id !== runId || !Number.isInteger(next.revision)) throw new Error('생성한 프로젝트의 최신 상태를 확인하지 못했습니다.');
    current = next;
    onSnapshot(next);
  };
  for (const file of files) {
    if (file.size > 100 * 1024 * 1024) throw new Error('파일 한 개는 100MB 이내로 첨부해 주세요.');
    const attempt = { key: JSON.stringify([runId, file.name, file.size, file.lastModified]), operation_id: operationId(), revision: current.revision };
    onUploadAttempt(attempt);
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('operation_id', attempt.operation_id);
    form.append('expected_revision', String(attempt.revision));
    accept(await api.request(`/api/v2/runs/${encodeURIComponent(runId)}/attachments`, { method: 'POST', body: form }));
    onUploadAttempt(null);
  }
  const attempt = { key: JSON.stringify([runId, 'run', {}]), operation_id: operationId(), snapshot: { id: runId, revision: current.revision } };
  onRunAttempt(attempt);
  accept(await api.command(current, 'run', {}, attempt.operation_id));
  onRunAttempt(null);
  return current;
}

/** EventSource does not expose HTTP errors; probe the authenticated session before retrying. */
export async function recoverEventStream({ verifySession, isCurrent, onExpired, onReconnect, schedule }) {
  if (!isCurrent()) return null;
  try { await verifySession(); }
  catch (error) {
    if ([401, 403].includes(error.status)) {
      if (isCurrent()) onExpired();
      return null;
    }
  }
  return isCurrent() ? schedule(onReconnect, 2500) : null;
}

/** Diagnostics update facts only; a late response must not replace a user's provider selection. */
export async function refreshProviderState(api, state, provider, isCurrent = () => true) {
  const capabilities = await api.request('/api/v2/capabilities/refresh', { method: 'POST', body: { provider } });
  if (isCurrent()) state.capabilities = capabilities;
}

export function initialProvider(capabilities, preferred) {
  if (['codex', 'claude'].includes(preferred)) return preferred;
  return ['codex', 'claude'].includes(capabilities?.default_provider) ? capabilities.default_provider : 'codex';
}
