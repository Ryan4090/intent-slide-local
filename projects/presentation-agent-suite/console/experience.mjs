/** Intent-first product views. Counts and approvals only come from the engine. */
import { escapeHtml as e, formatTime, safeArtifactUrl } from './core.mjs';
import { displayValue } from './product.mjs';

export function intentExperience(run = {}) {
  const pending = (run.questions || []).find((q) => q.status === 'PENDING' && q.intent_summary);
  const intent = run.intent || {};
  const update = run.active_stage === 'intent' && !run.intent
    ? (run.messages || []).filter(message => message.role === 'assistant' && message.stage === 'intent' && message.content).at(-1)?.content : null;
  return {
    summary: pending?.intent_summary || intent.intent_summary || update || run.request || '첫 이야기를 바탕으로 의도를 정리합니다.',
    summaryLabel: pending?.intent_summary || intent.intent_summary ? '제가 이해한 의도' : update ? '지금 정리하고 있는 내용' : '당신의 첫 이야기',
    confirmed: (run.approvals || []).some((a) => a.gate === 'G1' && a.valid === true),
    fields: [['audience','누가 보나요'], ['objective','어떤 변화를 원하나요'], ['success_criteria','성공의 기준']].map(([id,label]) => ({id,label,value:displayValue(intent.fields?.[id]?.value),proposed:intent.fields?.[id]?.state === 'proposed'})),
    strategy: intent.delivery_strategy,
    hasInterpretation: Boolean(pending?.intent_summary || intent.intent_summary || intent.fields),
  };
}

export function deliveryMarkup(strategy) {
  if (!strategy) return '';
  const labels = {audience_shift:'청중의 변화',core_message:'기억할 한 문장',narrative_arc:'설득의 순서',presentation_mode:'전달 방식',visual_principles:'시각화 원칙',constraints:'지킬 조건'};
  return `<div class="delivery-strategy"><h3>이렇게 전달합니다</h3><dl>${Object.entries(labels).filter(([key]) => displayValue(strategy[key])).map(([key,label])=>`<div><dt>${label}</dt><dd>${e(displayValue(strategy[key]))}</dd></div>`).join('')}</dl></div>`;
}

export function intentMarkup(run) {
  const view = intentExperience(run);
  const active = run.active_stage === 'intent';
  const contents = `<div class="intent-statement"><span class="eyebrow">${e(view.summaryLabel)}</span><p>${e(view.summary)}</p></div>${view.fields.some(field => field.value) ? `<div class="intent-lenses">${view.fields.map((field)=>`<div><span>${e(field.label)}</span><strong>${e(field.value || '대화로 함께 정할 내용')}</strong>${field.proposed ? '<small>제안 · 확인해 주세요</small>' : ''}</div>`).join('')}</div>` : ''}${deliveryMarkup(view.strategy)}`;
  if (!active) return `<details class="intent-compass" id="intent-compass-details"><summary>우리의 전달 기준 <span class="badge ${view.confirmed ? 'success' : 'warning'}">${view.confirmed ? '의도 확정' : '재확인 필요'}</span></summary>${contents}</details>`;
  return `<section class="intent-compass" aria-labelledby="intent-compass-title"><div class="section-heading"><div><span class="eyebrow">01 · INTENT FIRST</span><h2 id="intent-compass-title">좋은 슬라이드는, 정확한 질문에서.</h2><p>원하는 변화를 먼저 정하고, 그 기준으로 근거와 표현을 고릅니다.</p></div><span class="badge ${view.confirmed ? 'success' : 'warning'}">${view.confirmed ? '확정된 의도' : '함께 구체화하는 중'}</span></div>${contents}<div class="intent-promise"><span>① 이해한 의도</span><span>② 필요한 질문</span><span>③ 전달 전략 확인</span><small>의도 승인 후 자료조사를 시작합니다.</small></div></section>`;
}

export function interviewAnswer(questions, answers) {
  const parts = questions.map((q) => {
    const answer = String(answers[`other-${q.id}`] || answers[`answer-${q.id}`] || '').trim();
    if (!answer) throw new Error('각 질문에 선택하거나 직접 답변해 주세요.');
    return `${q.question}\n${answer}`;
  });
  const result = parts.join('\n\n');
  if (result.length > 12000) throw new Error('답변은 모두 합쳐 12,000자 이내로 입력해 주세요.');
  return result;
}

export function interviewMarkup(question, runId, draft = {}, pending = false, showSummary = true) {
  return `<form class="question intent-interview" data-interview-form="${e(question.id)}" data-run-id="${e(runId)}"><span class="eyebrow">방향을 정하는 ${question.questions.length}가지 질문</span><h3>${e(question.question)}</h3>${showSummary && question.intent_summary ? `<p class="interview-understanding">${e(question.intent_summary)}</p>` : ''}${question.questions.map((q,index)=>`<fieldset><legend><span class="question-number">${index+1}</span>${e(q.question)}</legend><p class="question-why">${e(q.why)}</p>${q.options?.length ? `<div class="answer-options">${q.options.map((option,optionIndex)=>`<label><input type="radio" id="choice-${e(question.id)}-${e(q.id)}-${optionIndex}" name="answer-${e(q.id)}" value="${e(option.label)}" ${draft[`answer-${q.id}`]===option.label ? 'checked' : ''}><span><strong>${e(option.label)}</strong><small>${e(option.description)}</small></span></label>`).join('')}</div>` : ''}<label for="interview-${e(question.id)}-${e(q.id)}">${q.options?.length ? '직접 답변 · 선택보다 우선 반영합니다' : '내 답변'}</label><textarea id="interview-${e(question.id)}-${e(q.id)}" name="${q.options?.length ? 'other' : 'answer'}-${e(q.id)}" rows="2" maxlength="4000" ${q.options?.length ? '' : 'required'} placeholder="생각이 아직 정해지지 않았다면 그렇게 알려주세요.">${e(draft[`${q.options?.length ? 'other' : 'answer'}-${q.id}`] || '')}</textarea></fieldset>`).join('')}<p class="form-error" data-interview-error role="alert" hidden></p><button class="button primary" type="submit" ${pending ? 'disabled' : ''}>이 답변으로 의도 구체화하기 →</button></form>`;
}

export function designGalleryMarkup(catalog, selected, disabled = false, name = 'design-preset') {
  if (!catalog?.length) return '<p class="muted">디자인 목록을 불러오지 못했습니다. 상태를 새로고침해 주세요.</p>';
  return `<div class="design-gallery">${catalog.map((preset)=>{
    const preview = /^\/design-previews\/[a-z0-9-]+\.svg$/.test(preset.preview_url || '') ? preset.preview_url : null;
    return `<label class="design-card"><input type="radio" id="${e(name)}-${e(preset.id)}" name="${e(name)}" value="${e(preset.id)}" ${selected===preset.id ? 'checked' : ''} ${disabled ? 'disabled' : ''}><span class="design-card-body">${preview ? `<img src="${e(preview)}" alt="${e(preset.label)} · ${e((preset.visualization_patterns || []).join(', '))} 구성 예시" loading="lazy" width="640" height="360">` : ''}<span class="design-card-title">${e(preset.label)}<span class="selected-mark" aria-hidden="true">✓</span></span><span class="design-best-for">${e((preset.best_for || []).join(' · '))}</span><span class="design-description">${e(preset.description)}</span></span></label>`;
  }).join('')}</div>`;
}

export function safeResearchUrl(value) {
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
    url.search = ''; url.hash = '';
    return url.href;
  } catch { return null; }
}

export function researchActivityMarkup(activity, run = {}) {
  if (!activity) return '<p class="muted">조사 활동이 아직 확인되지 않았습니다.</p>';
  const totals = activity.summary || {};
  const storage = activity.storage || {};
  const metric = (key,label) => `<div><strong>${Number.isInteger(totals[key]) && totals[key]>=0 ? totals[key] : '—'}</strong><span>${label}</span></div>`;
  const artifacts = new Map((run.artifacts || []).filter(a=>a.valid).map(a=>[a.id,a]));
  return `<section class="research-live" aria-labelledby="research-live-title"><div class="section-heading"><div><span class="eyebrow">02 · EVIDENCE IN VIEW</span><h2 id="research-live-title">근거가 모이는 과정</h2><p role="status">${e(activity.current_action || '현재 조사 기록을 확인합니다.')}</p></div><button class="text-button" data-research-history>조사 이력 보기 ↗</button></div><div class="research-metrics">${metric('sites','출처 사이트')}${metric('source_files','저장한 원문')}${metric('verified_excerpts','위치 확인 발췌')}${metric('claims','정리한 주장')}</div><p class="small muted">${e(activity.evidence_note || '실제로 저장하고 확인한 결과만 집계합니다.')}</p>${activity.sources?.length ? `<div class="research-sources">${activity.sources.map(source=>{
    const url=safeResearchUrl(source.url), artifact=artifacts.get(source.artifact_id), download=artifact && safeArtifactUrl(artifact,run.id);
    return `<article class="research-source"><div><span class="source-site">${e(source.site || '제공 자료')}</span><h3>${e(source.title || '조사 자료')}</h3><p>발췌 ${e(source.excerpt_count ?? 0)}개 · 주장 ${e(source.claim_count ?? 0)}개 연결</p></div><div class="source-links">${url ? `<a href="${e(url)}" target="_blank" rel="noopener noreferrer">출처 열기 ↗</a>` : ''}${download ? `<button class="text-button" data-artifact="${e(source.artifact_id)}">저장 원문 보기</button>` : ''}</div></article>`;
  }).join('')}</div>` : '<div class="research-empty">저장한 원문이 아직 없습니다. 검색·열람 활동은 아래 이력에 먼저 기록됩니다.</div>'}<details class="research-history" id="research-history-details"><summary>검색·열람 활동 ${activity.events?.length || 0}개 보기</summary>${activity.omitted_events ? `<p class="small muted">화면에는 최근 128개 동작을 표시합니다. 이전 ${e(activity.omitted_events)}개를 포함한 이력은 작업 이력 DB에 보관됩니다.</p>` : ''}${activity.events?.length ? `<ol>${activity.events.slice().reverse().map(event=>`<li><time>${e(formatTime(event.created_at,true))}</time><div><p>${e(event.message || event.action)}</p>${event.site ? `<small>${e(event.site)}</small>` : ''}</div><span class="badge">${e(({started:'진행 중',completed:'확인됨',failed:'실패',STARTED:'진행 중',COMPLETED:'동작 완료',RUNNING:'진행 중',FAILED:'실패',INTERRUPTED:'중단'})[event.status] || event.status || '기록')}</span></li>`).join('')}</ol>` : '<p class="muted">아직 기록된 검색·열람 활동이 없습니다.</p>'}</details><details class="research-storage" id="research-storage-details"><summary>이 기록은 어디에 저장되나요?</summary><p>작업 기록은 이 컴퓨터에 보관됩니다. 원문은 파일 목록에서도 열 수 있습니다.</p>${[['database','작업 이력 DB'],['directory','원문과 산출물 폴더'],['bundle','현재 조사 결과']].filter(([key])=>storage[key]).map(([key,label])=>`<p><strong>${label}</strong><code>${e(storage[key])}</code></p>`).join('')}${!storage.bundle ? '<p class="small muted">조사 결과 묶음은 검증 후 저장되면 여기에 표시됩니다.</p>' : ''}</details></section>`;
}
