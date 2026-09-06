import {
  STAGES, ConsoleApi, activeJobs, boundedPercent, changePayload, commandAttempt, escapeHtml as e,
  elicitationForm, elicitationResponse, evidenceView, fileChangeReviewMarkup, formatTime, isReadOnly,
  operationId, pageChangeChoices, pageExecutionView, pendingQuestions, pendingReviews, percentText, previewKind,
  progressChange, safeArtifactUrl, stageView, statusLabel, statusTone, takeLaunchToken,
} from './core.mjs';
import {
  ARTIFACT_LABELS, CHECKPOINTS, checkpointView, currentArtifacts, displayValue, executionSelection,
  nextAction, providerAnswerPayload, providersView, releaseView, researchSummary, reviewView, stageFiles, startCreatedProject, initialProvider, recoverEventStream, claimCategory, claimConditions,
  modelOptions, effortOptions, validateExecutionSelection, applyDiscoverySnapshot,
} from './product.mjs';
import { createDiscovery, createLogin } from './connection.mjs';

const api = new ConsoleApi();
const $ = (selector, root = document) => root.querySelector(selector);
function consumeLaunchToken() {
  return takeLaunchToken(location, (url) => history.replaceState(null, '', url));
}
// Remove the launch credential before rendering or loading any project state.
let bootstrapToken = consumeLaunchToken();
const state = {
  runs: [], run: null, capabilities: {}, tab: 'work', connected: false, pending: false,
  stream: null, eventSeq: 0, selection: 0, refreshTimer: null, reconnectTimer: null,
  drafts: new Map(), attachmentUrl: null, previewController: null, viewedReviews: new Map(),
  commandAttempt: null, createAttempt: null, uploadAttempt: null,
  bootGeneration: 0,
  changeContext: null,
  screen: 'work', providerBusy: false, preferred: { provider: null, model: null, effort: null },
  authExpired: false, streamGeneration: 0,
  evidenceFilter: 'all', reviewContext: null, providerContext: null,
  discovery: null, discoveryGeneration: 0, discoveryState: 'IDLE', discoveryError: '',
  choiceGeneration: 0, explicitProviderChoice: false, welcomeAfterDiscovery: false,
  login: null, loginBusy: false,
};

try {
  const provider = localStorage.getItem('intent-slide.provider');
  if (provider && /^[a-z][a-z0-9_-]{0,63}$/.test(provider)) state.preferred.provider = provider;
} catch { /* Preferences are optional; no secrets or conversation are stored here. */ }

function selectedProvider(selection = state.preferred) {
  return providersView(state.capabilities).find((provider) => provider.id === selection.provider);
}

function rememberProvider(id) {
  state.choiceGeneration += 1;
  state.explicitProviderChoice = true;
  state.preferred = { provider: id, model: null, effort: null };
  try { localStorage.setItem('intent-slide.provider', id); } catch { /* Optional preference. */ }
}

function discoveryMarkup() {
  const ready = providersView(state.capabilities).filter((provider) => provider.ready);
  const selected = selectedProvider();
  const status = state.discoveryState;
  const message = status === 'RUNNING' ? '설치된 AI와 로그인 상태를 자동으로 확인하고 있습니다.'
    : status === 'DEFERRED' ? '현재 AI 작업이 끝난 뒤 연결 상태를 다시 확인할 수 있습니다.'
      : status === 'TIMEOUT' ? '연결 확인이 오래 걸려 자동 탐색을 멈췄습니다. 확인된 도구는 사용할 수 있습니다.'
        : status === 'ERROR' ? 'AI 연결을 확인하지 못했습니다. 다시 탐색하거나 진단을 확인해 주세요.'
          : ready.length ? `${ready.length}개 AI를 사용할 수 있습니다. ${selected?.ready ? `${selected.label}를 선택했습니다.` : '함께 작업할 AI를 선택해 주세요.'}`
            : '내장 Codex로 시작할 수 있습니다. 처음 한 번 본인 계정으로 로그인해 주세요.';
  return `<div class="discovery-status" role="status" aria-live="polite" ${status === 'RUNNING' ? 'aria-busy="true"' : ''}><div><strong>${status === 'RUNNING' ? 'AI 자동 탐색 중' : ready.length ? '내 AI 연결' : 'AI 연결 확인'}</strong><p>${e(message)}</p>${ready.length ? `<p class="ready-providers">${ready.map((provider) => e(provider.label)).join(' · ')}</p>` : ''}${state.discoveryError ? `<details><summary>연결 진단 상세</summary><p>${e(state.discoveryError)}</p></details>` : ''}</div>${loginMarkup()}<button class="text-button" data-action="discover-providers" ${state.providerBusy ? 'disabled' : ''}>다시 탐색</button></div>`;
}

function loginMarkup() {
  const codex = providersView(state.capabilities).find((p) => p.id === 'codex');
  if (!codex || codex.ready) return '';
  const status = state.capabilities.login?.status;
  const busy = state.loginBusy || ['STARTING', 'RUNNING'].includes(status);
  const message = busy ? '열린 공식 로그인 창에서 본인 계정으로 로그인해 주세요.'
    : status === 'TIMEOUT' ? '로그인 시간이 지났습니다. 다시 시작할 수 있습니다.'
      : status === 'FAILED' ? '로그인을 완료하지 못했습니다. 다시 시도해 주세요.' : 'Codex가 포함되어 있어 별도 설치가 필요 없습니다.';
  return `<div class="browser-login"><p role="status">${e(message)}</p><button class="button primary" data-action="login-codex" ${busy || activeJobs(state.run || {}).length ? 'disabled' : ''}>${busy ? '공식 로그인 대기 중…' : 'Codex 계정으로 로그인 ↗'}</button></div>`;
}

async function loginCodex() {
  if (state.loginBusy || !state.connected || state.authExpired) return;
  stopDiscovery(); state.login?.stop();
  const boot = state.bootGeneration, choice = state.choiceGeneration;
  state.loginBusy = true;
  const isCurrent = () => boot === state.bootGeneration && !state.authExpired;
  state.login = createLogin({ api, isCurrent,
    onSnapshot(capabilities) {
      applyDiscoverySnapshot(state, capabilities, choice);
      state.loginBusy = ['STARTING', 'RUNNING'].includes(capabilities.login?.status);
      state.discoveryState = capabilities.discovery?.status || 'IDLE';
      if (!state.loginBusy && selectedProvider()?.ready) announce('AI 계정을 연결했습니다. 바로 프로젝트를 시작할 수 있습니다.');
      refreshConnectionSurfaces();
    },
    onError(error) { state.loginBusy = false; handleError(error); refreshConnectionSurfaces(); },
    onTimeout() { state.loginBusy = false; announce('로그인 결과를 다시 탐색해 주세요.', 'warning'); refreshConnectionSurfaces(); },
  });
  refreshConnectionSurfaces();
  return state.login.start();
}

function stopDiscovery() {
  state.discoveryGeneration += 1;
  state.discovery?.stop(); state.discovery = null; state.providerBusy = false;
  if (state.discoveryState === 'RUNNING') state.discoveryState = 'IDLE';
}

function refreshConnectionSurfaces() {
  if (state.authExpired) return;
  const focusedId = document.activeElement?.id;
  const focusedProvider = document.activeElement?.name === 'setup-provider' ? document.activeElement.value : null;
  if (state.screen === 'setup') renderSetup();
  else if (!state.run || state.screen === 'home') renderWelcome();
  if ($('#create-dialog').open) {
    const selection = readExecutionForm('create-provider');
    populateProviderSelect('create-provider', selection.provider ? selection : state.preferred);
  }
  if ($('#provider-dialog').open) populateProviderSelect('run-provider', readExecutionForm('run-provider'));
  if (focusedId) document.getElementById(focusedId)?.focus({ preventScroll: true });
  else if (focusedProvider) [...document.querySelectorAll('input[name="setup-provider"]')].find((input) => input.value === focusedProvider)?.focus({ preventScroll: true });
}

function discoverProviders(refresh = false) {
  if (!state.connected || state.authExpired) return;
  stopDiscovery();
  const generation = state.discoveryGeneration, boot = state.bootGeneration, choice = state.choiceGeneration;
  state.providerBusy = true; state.discoveryState = 'RUNNING'; state.discoveryError = '';
  const isCurrent = () => generation === state.discoveryGeneration && boot === state.bootGeneration && !state.authExpired;
  state.discovery = createDiscovery({ api, isCurrent,
    onSnapshot(capabilities) {
      applyDiscoverySnapshot(state, capabilities, choice);
      state.discoveryState = capabilities?.discovery?.status || 'IDLE';
      state.discoveryError = typeof capabilities?.discovery?.reason === 'string' ? capabilities.discovery.reason : '';
      state.providerBusy = state.discoveryState === 'RUNNING';
      if (state.welcomeAfterDiscovery && selectedProvider()?.ready && state.screen === 'setup') {
        state.welcomeAfterDiscovery = false; state.screen = 'home';
      }
      refreshConnectionSurfaces();
    },
    onError(error) {
      state.providerBusy = false; state.discoveryState = 'ERROR'; state.discoveryError = error.message || '';
      if ([401, 403].includes(error.status)) handleError(error); else refreshConnectionSurfaces();
    },
    onTimeout() {
      state.providerBusy = false; state.discoveryState = 'TIMEOUT'; refreshConnectionSurfaces();
    },
  });
  refreshConnectionSurfaces();
  return state.discovery.start(refresh);
}

function badge(status) {
  return `<span class="badge ${statusTone(status)}">${e(statusLabel(status))}</span>`;
}

function announce(message, tone = '') {
  const notice = $('#notice');
  notice.textContent = message;
  notice.className = `notice ${tone}`;
  notice.hidden = !message;
}

function setConnection(status, message) {
  $('#connection').className = `connection ${status}`;
  $('#connection').lastElementChild.textContent = message;
}

function renderRunList() {
  $('#run-count').textContent = state.runs.length;
  $('#run-list').innerHTML = state.runs.length ? state.runs.map((run) => `
    <button class="run-item ${state.run?.id === run.id ? 'selected' : ''}" data-select-run="${e(run.id)}" ${state.run?.id === run.id ? 'aria-current="page"' : ''}>
      <span class="run-item-title">${e(run.title || '이름 없는 프로젝트')}</span>
      <span class="run-item-meta"><span>${e(statusLabel(run.status))}</span><span>${percentText(run.progress?.percent)}</span></span>
    </button>`).join('') : '<p class="muted small">아직 프로젝트가 없습니다.</p>';
}

function renderWelcome() {
  if (state.authExpired) return renderSessionRequired();
  $('#breadcrumb').textContent = '프로젝트 시작';
  $('#main-content').innerHTML = `
    <section class="welcome">
      <span class="eyebrow">FROM INTENT TO IMPACT</span>
      <h1>전하고 싶은 생각을,<br>근거 있는 슬라이드로.</h1>
      <p class="welcome-intro">대화로 의도를 정하고, 조사로 내용을 채우고,<br>검토를 거쳐 슬라이드를 완성합니다.<br>세 단계의 입력과 결과를 내 작업실에서 확인하세요.</p>
      ${discoveryMarkup()}
      <button class="button primary" data-action="new-project">첫 프로젝트 시작하기 <span aria-hidden="true">→</span></button>
      <div class="welcome-track">
        <article class="welcome-stage"><span class="number">01</span><h2>의도·구조 확정</h2><p>청중, 목적, 메시지와 구성을 정리합니다.<br>확정한 내용은 승인본으로 남깁니다.</p></article>
        <article class="welcome-stage"><span class="number">02</span><h2>리서치·분석</h2><p>주장에서 근거까지 연결합니다.<br>가정과 한계도 함께 확인합니다.</p></article>
        <article class="welcome-stage"><span class="number">03</span><h2>제작·검증·출고</h2><p>디자인을 검토하고 페이지를 만듭니다.<br>검사를 통과한 결과물을 전달합니다.</p></article>
      </div>
      <div class="welcome-footnote"><span>입력·출력 버전 기록</span><span>검증 기준으로 계산하는 진행률</span><span>수정 이력과 근거 추적</span></div>
    </section>`;
}

function renderSetup() {
  if (state.authExpired) return renderSessionRequired();
  preserveDrafts();
  state.screen = 'setup';
  const providers = providersView(state.capabilities);
  const selected = selectedProvider();
  $('#breadcrumb').textContent = 'AI 연결과 준비';
  $('#main-content').innerHTML = `<section class="setup-page"><span class="eyebrow">시작 준비</span><h1>내 AI 구독으로<br>작업실을 연결하세요.</h1><p class="setup-intro">Intent-Slide는 이 컴퓨터${state.capabilities.platform?.label ? ` (${e(state.capabilities.platform.label)})` : ''}에서 실행됩니다. 설치된 AI 도구의 기존 로그인과 설정을 사용하며, 비밀번호나 API 키를 이 화면에 입력하지 않습니다. 작업에 필요한 요청과 자료는 선택한 AI 제공자에게 전달됩니다.</p><div class="setup-steps"><span>1. 자동 탐색</span><span>2. 내 계정 연결</span><span>3. 첫 대화 시작</span></div>${discoveryMarkup()}<div class="provider-grid">${providers.map((provider) => `<article class="provider-card ${state.preferred.provider === provider.id ? 'selected' : ''}"><label><input type="radio" name="setup-provider" value="${e(provider.id)}" ${state.preferred.provider === provider.id ? 'checked' : ''}><strong>${e(provider.label)}</strong>${provider.beta || provider.id === 'claude' ? '<span class="badge warning">Beta</span>' : ''}</label><p class="provider-status ${provider.ready ? 'ready' : ''}">${e(provider.statusText)}</p>${provider.id === 'claude' ? '<p class="beta-note">실제 슬라이드 제작 검증을 기다리고 있습니다. 연결과 로그인 확인 후 선택할 수 있습니다.</p>' : ''}${provider.billing_notice ? `<p class="billing-notice">${e(provider.billing_notice)}</p>` : ''}${provider.reason ? `<details class="provider-diagnostic"><summary>연결 진단 상세</summary><p>${e(provider.reason)}</p></details>` : ''}${!provider.ready ? `<div class="setup-instructions"><p>준비된 AI 도구와 기존 로그인을 자동으로 찾습니다. 계정 연결이 필요하면 공식 절차에 따라 본인 계정으로 로그인해 주세요.</p>${provider.install_url ? `<details class="optional-tool"><summary>선택 사항 · AI 도구 추가 안내</summary>${safeExternalLink(provider.install_url, '공식 도구 안내 ↗')}</details>` : ''}${provider.login_command && provider.id !== 'codex' ? `<p>로그인 명령</p><code>${e(provider.login_command)}</code>` : ''}${provider.auth_mode === 'api_key' ? '<p>현재 API 키 연결이 감지되었습니다. 정액제 계정으로 로그인한 뒤 다시 확인하세요.</p>' : ''}</div>` : ''}<button class="button secondary" data-check-provider="${e(provider.id)}" ${state.providerBusy ? 'disabled' : ''}>${state.providerBusy ? '연결 확인 중…' : '로그인·연결 다시 확인'}</button></article>`).join('') || '<div class="empty-panel"><h2>AI 연결 정보를 불러오지 못했습니다.</h2><p>작업실 실행 파일이 최신인지 확인하고 다시 연결해 주세요.</p><button class="button secondary" data-action="refresh-capabilities">준비 상태 다시 불러오기</button></div>'}</div><div class="setup-bottom"><div><strong>${selected?.ready ? `${e(selected.label)} 연결 준비 완료` : 'AI 계정 연결을 확인해 주세요'}</strong><p>${selected?.ready ? '이제 첫 이야기를 시작할 수 있습니다.' : '로그인한 뒤 다시 탐색을 눌러 주세요.'}</p></div><button class="button primary" data-action="setup-complete" ${!selected?.ready ? 'disabled' : ''}>작업실 시작하기 →</button>${state.run ? '<button class="text-button" data-action="back-work">현재 프로젝트로 돌아가기</button>' : ''}</div><details class="technical-details"><summary>제작 도구와 진단 상세</summary><p>파일 검사와 미리보기에 사용하는 로컬 도구의 상태입니다.</p><pre class="source-text">${e(JSON.stringify({ rendering: state.capabilities.rendering, ready: state.capabilities.ready, reason: state.capabilities.reason }, null, 2))}</pre></details></section>`;
}

function safeExternalLink(value, label) {
  try {
    const url = new URL(value);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return '';
    return `<a class="text-button" href="${e(url.href)}" target="_blank" rel="noopener noreferrer">${e(label)}</a>`;
  } catch { return ''; }
}

function populateProviderSelect(id, selection) {
  const select = document.getElementById(id);
  const markup = '<option value="">AI를 선택하세요</option>' + providersView(state.capabilities).map((provider) => `<option value="${e(provider.id)}" ${provider.ready ? '' : 'disabled'}>${e(provider.label)}${provider.beta || provider.id === 'claude' ? ' (Beta)' : ''} · ${e(provider.statusText)}</option>`).join('');
  setSelectOptions(select, markup);
  select.value = selection?.provider || '';
  populateExecutionOptions(id, selection || {});
  updateProviderStatus(id);
  if (id === 'run-provider') lockProviderForm();
}

function setSelectOptions(select, markup) {
  if (select.dataset.optionsMarkup !== markup) { select.innerHTML = markup; select.dataset.optionsMarkup = markup; }
}

function lockProviderForm() {
  const busy = state.pending || activeJobs(state.run).length > 0 || state.providerContext?.id !== state.run?.id;
  $('#run-provider').disabled = busy;
  for (const suffix of ['model', 'effort']) {
    const select = document.getElementById(`run-provider-${suffix}`);
    select.disabled = busy || (select.options.length <= 1 && !select.value);
  }
  $('#provider-form button[type="submit"]').disabled = busy;
}

function readExecutionForm(id) {
  return { provider: document.getElementById(id).value,
    model: document.getElementById(`${id}-model`).value || null,
    effort: document.getElementById(`${id}-effort`).value || null };
}

function populateExecutionOptions(id, selection = {}) {
  const provider = selectedProvider({ provider: document.getElementById(id).value });
  const models = modelOptions(provider);
  const model = document.getElementById(`${id}-model`);
  let modelMarkup = '<option value="">AI 도구 기본 모델</option>' + models.map((item) => `<option value="${e(item.value)}">${e(item.label)}</option>`).join('');
  if (selection.model && !models.some((item) => item.value === selection.model)) modelMarkup += `<option value="${e(selection.model)}">${e(selection.model)} · 기존 설정, 목록 확인 필요</option>`;
  setSelectOptions(model, modelMarkup);
  model.value = selection.model || '';
  model.disabled = !models.length && !selection.model;
  const efforts = effortOptions(provider, model.value || null);
  const effort = document.getElementById(`${id}-effort`);
  let effortMarkup = '<option value="">AI 도구 기본 추론 설정</option>' + efforts.map((item) => `<option value="${e(item.value)}">${e(item.label)}</option>`).join('');
  if (selection.effort && !efforts.some((item) => item.value === selection.effort)) effortMarkup += `<option value="${e(selection.effort)}">${e(selection.effort)} · 기존 설정, 지원 확인 필요</option>`;
  setSelectOptions(effort, effortMarkup);
  effort.value = selection.effort || '';
  effort.disabled = !efforts.length && !selection.effort;
}

function updateProviderStatus(id) {
  const provider = selectedProvider({ provider: document.getElementById(id).value });
  document.getElementById(`${id}-status`).textContent = provider ? `${provider.statusText} · ${provider.id === 'claude' ? 'Beta: 모델 옵션은 문서 기반이며 실제 제작·이용 가능 여부는 검증 대기입니다.' : modelOptions(provider).length ? '연결된 AI가 제공한 모델과 추론 옵션입니다.' : '모델 목록이 없어 AI 도구의 기본 설정을 사용합니다.'}` : 'AI 연결을 먼저 확인해 주세요.';
  const billing = document.getElementById(`${id}-billing`);
  billing.textContent = typeof provider?.billing_notice === 'string' ? provider.billing_notice : '';
  billing.hidden = !billing.textContent;
}

function checkProvider() {
  if (state.providerBusy || state.authExpired) return;
  return discoverProviders(true);
}

function startCreate() {
  if (state.authExpired) return renderSessionRequired();
  if (state.pending) return announce('현재 요청을 처리한 뒤 새 프로젝트를 시작해 주세요.', 'warning');
  state.welcomeAfterDiscovery = false;
  if (!selectedProvider()?.ready) { renderSetup(); if (!state.providerBusy) discoverProviders(); return; }
  $('#create-error').hidden = true;
  populateProviderSelect('create-provider', state.preferred);
  $('#create-dialog').showModal();
  $('#project-request').focus();
}

function renderProgress(run) {
  const percent = boundedPercent(run.progress?.percent);
  return `<section class="overview" aria-label="전체 진행 상황">
    <div class="total-progress"><span class="eyebrow">검증된 완료율</span>
      <div class="progress-value ${percent === null ? 'unmeasured' : ''}">${percent === null ? '미계측' : `${Math.floor(percent)}<small>%</small>`}</div>
      <div class="progress-bar" role="progressbar" aria-label="전체 완료율" ${percent === null ? 'aria-valuetext="계산 근거 없음"' : `aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100"`}><span style="width:${percent ?? 0}%"></span></div>
      <p class="progress-description">${percent === null ? '계산할 계획이 아직 없습니다.' : '현재 유효한 완료 기준을 합산합니다.'}</p>
      <button class="text-button progress-details" data-action="progress-detail">계산 기준·변경 이유 보기 ↗</button>
    </div>
    <div class="stage-track">${stageView(run).map((stage, index) => `<button class="stage-step ${run.active_stage === stage.id ? 'current' : ''} ${stage.progress.percent === 100 ? 'done' : ''}" data-stage-focus="${stage.id}" aria-label="${e(stage.label)} 입력과 출력 보기">
      <div class="stage-step-top"><span class="stage-index">${stage.progress.percent === 100 ? '✓' : `0${index + 1}`}</span><span class="stage-line"></span></div>
      <div class="stage-step-title">${e(stage.short)}</div>
      <div class="stage-step-summary"><span>전체 중 ${stage.weight}%</span><span class="stage-percent">${percentText(stage.progress.percent)}</span></div>
      <div class="progress-bar"><span style="width:${stage.progress.percent ?? 0}%"></span></div>
      <p class="stage-gate">${e(stage.gate)} · ${e(statusLabel(stage.status))}</p>
    </button>`).join('')}</div>
  </section><ol class="checkpoint-track" aria-label="5개 체크포인트">${checkpointView(run).map((checkpoint) => `<li class="checkpoint ${statusTone(checkpoint.status)}"><button data-checkpoint="${checkpoint.id}" aria-label="${e(checkpoint.label)} · ${e(statusLabel(checkpoint.status))}"><span class="checkpoint-dot" aria-hidden="true">${checkpoint.status === 'COMPLETE' ? '✓' : checkpoint.id.slice(1)}</span><span><strong>${e(checkpoint.label)}</strong><small>${e(statusLabel(checkpoint.status))} · ${e(checkpoint.owner)}</small></span></button>${checkpoint.reason ? `<p>${e(checkpoint.reason)}</p>` : ''}</li>`).join('')}</ol>`;
}

function resolveArtifact(item) {
  return typeof item === 'string' ? state.run.artifacts?.find((artifact) => artifact.id === item) || { id: item, name: item } : item;
}

function artifactRow(item) {
  const artifact = resolveArtifact(item);
  if (!artifact) return '';
  const name = artifact.name || artifact.path || artifact.id;
  const suffix = String(name).split('.').pop().slice(0, 4).toUpperCase();
  return `<button class="artifact-row" data-artifact="${e(artifact.id)}"><span class="file-symbol" aria-hidden="true">${e(suffix)}</span><span class="artifact-copy"><span class="artifact-name">${e(ARTIFACT_LABELS[artifact.kind] || name)}</span><span class="artifact-meta">${e(name)} · ${artifact.valid === true ? '현재 버전' : '이전 버전 · 재검증 필요'}</span></span><span class="artifact-arrow" aria-hidden="true">↗</span></button>`;
}

function findingText(finding) {
  return typeof finding === 'string' ? finding : finding.message || finding.summary || finding.description || finding.title || JSON.stringify(finding);
}

function renderStages() {
  const priorities = { intent: ['intent'], research: ['analysis_pdf', 'analysis_report', 'research'], design: ['pptx', 'contact_sheet', 'direction'] };
  return `<div class="section-heading"><div><h2>세 단계의 입력과 결과</h2><p>현재 쓰이는 자료와 핵심 결과를 먼저 보여드립니다.</p></div></div>${stageFiles(state.run).map((stage, index) => {
    const featured = priorities[stage.id].map((kind) => stage.current.find((artifact) => artifact.kind === kind)).filter(Boolean);
    const other = stage.current.filter((artifact) => !featured.some((item) => item.id === artifact.id));
    return `<section class="stage-panel" id="stage-${stage.id}" tabindex="-1" aria-label="${e(stage.label)}"><header class="stage-panel-header"><h3><span class="number">0${index + 1}</span>${e(stage.label)}</h3><div class="stage-header-actions">${stage.id === 'design' && pageChangeChoices(state.run).length && !isReadOnly(state.run) ? '<button class="text-button" data-action="page-changes">페이지별 수정</button>' : ''}${badge(stage.status)}</div></header><div class="io-grid"><div class="io-column"><div class="io-label">무엇으로 시작했나요? <span>입력 ${stage.inputs.length}개</span></div>${index === 0 ? `<p class="request-excerpt">${e(state.run.request || '대화에서 목적과 구성을 정리합니다.')}</p>` : '<p class="empty-inline">앞 단계에서 확정한 내용과 연결된 자료입니다.</p>'}${stage.inputs.length ? `<details class="artifact-group"><summary>연결된 입력 ${stage.inputs.length}개 보기</summary>${stage.inputs.map(artifactRow).join('')}</details>` : ''}</div><div class="io-column"><div class="io-label">무엇이 완성됐나요? <span>현재 결과 ${stage.current.length}개</span></div>${featured.map(artifactRow).join('') || '<p class="empty-inline">이 단계의 결과를 준비하면 여기에 표시합니다.</p>'}${other.length ? `<details class="artifact-group"><summary>세부 결과 ${other.length}개 보기</summary>${other.map(artifactRow).join('')}</details>` : ''}${stage.previous.length ? `<details class="artifact-group previous-results"><summary>이전 버전 ${stage.previous.length}개</summary>${stage.previous.map(artifactRow).join('')}</details>` : ''}</div></div>${stage.findings.length ? `<div class="stage-findings">${stage.findings.slice(0, 3).map((finding) => `<p>${e(findingText(finding))}</p>`).join('')}</div>` : ''}</section>`;
  }).join('')}`;
}

function renderReviews() {
  const reviews = pendingReviews(state.run);
  return `<section class="review-section" aria-labelledby="reviews-title"><div class="section-heading"><div><h2 id="reviews-title">지금 필요한 검토 <span class="tab-count">${reviews.length}</span></h2><p>대상 결과와 변경 내용을 확인한 뒤 결정하세요.</p></div></div>
    ${reviews.length ? reviews.map((review) => `<article class="review-card"><div class="review-top"><h3>${e(CHECKPOINTS.find((item) => item.id === review.gate)?.label || review.title || '단계 결과 검토')}</h3>${badge(review.status)}</div><p>${e(review.summary || '검토할 결과의 원문과 근거를 확인해 주세요.')}</p><div class="review-actions"><button class="button secondary" data-review-view="${e(review.id)}">검토 대상 보기 ↗</button><button class="button primary" data-review-approve="${e(review.id)}" ${state.pending || state.viewedReviews.get(review.id) !== review.bundle_sha256 ? 'disabled' : ''}>이 버전 승인</button><button class="text-button" data-request-changes="${e(review.stage)}">수정 요청</button></div><div class="review-hash">${state.viewedReviews.get(review.id) === review.bundle_sha256 ? '검토 대상으로 확인한 버전' : '검토 대상을 열면 승인할 수 있습니다.'}</div></article>`).join('') : `<div class="empty-review"><span aria-hidden="true">✓</span><div><strong>현재 대기 중인 승인 요청이 없습니다.</strong><p>다음 단계로 넘어가기 전, 필요한 검토가 여기에 나타납니다.</p></div></div>`}
  </section>`;
}

function renderEvidence() {
  const research = stageView(state.run).find((stage) => stage.id === 'research');
  const evidence = evidenceView(state.run);
  const summary = researchSummary(state.run.research);
  const claims = evidence.claims.filter((claim) => state.evidenceFilter === 'all' || claimCategory(claim) === state.evidenceFilter);
  const reports = currentArtifacts(state.run).filter((artifact) => ['analysis_pdf', 'analysis_report'].includes(artifact.kind));
  const findings = [...(state.run.findings || []), ...research.findings];
  return `<div class="section-heading"><div><h2>주장과 근거</h2><p>조사를 끝냈는지와 주장을 입증했는지는 별도로 확인합니다.</p></div></div><p class="evidence-intro">단위·모집단·기준시점과 원문 위치, 계산과 가정까지 확인하세요. 핵심 주장이 미입증 상태라면 다음 결과에 사용할 수 없습니다.</p>
    ${renderResearchSummary(summary, reports)}<div class="evidence-filters" role="group" aria-label="주장 분류">${[['all', `전체 ${evidence.claims.length}`], ['provided', `제공 자료 근거 ${summary.provided}`], ['supported', `그 밖의 근거 연결 ${summary.supported}`], ['assumptions', `가정·제안 ${summary.assumptions}`], ['needsReview', `확인 필요 ${summary.needsReview}`]].map(([id, label]) => `<button class="button subtle" data-evidence-filter="${id}" aria-pressed="${state.evidenceFilter === id}">${label}</button>`).join('')}</div>
    ${evidence.claims.length && !evidence.current ? '<p class="read-only-banner">이 연구는 현재 유효한 결과 버전이 아닙니다. 이전 주장과 근거를 참고용으로 표시합니다.</p>' : ''}
    ${claims.length ? `<div class="evidence-list">${claims.map((claim) => `<article class="evidence-card">${badge(claim.evidence_status)} <small class="muted">${e(claim.id || '')}</small><h3>${e(claim.text || '')}</h3>${claimConditions(claim).length ? `<dl class="claim-conditions">${claimConditions(claim).map((item) => `<dt>${e(item.label)}</dt><dd>${e(item.value)}</dd>`).join('')}</dl>` : ''}${claim.limitations.length ? `<p>한계 · ${e(claim.limitations.join(' · '))}</p>` : ''}${claim.proofs.map((proof) => `<div class="source-proof"><div><strong>${e(proof.title)}</strong><span class="proof-location">${e(proof.locator)}</span></div><p>${e(proof.excerpt)}</p><div class="proof-verification"><span>원문 위치 검사 ${badge(proof.verification_status)}</span><span>주장 해석 ${badge(proof.semantic_status)}</span></div><div class="proof-actions">${proof.can_open ? `<button class="text-button" data-artifact="${e(proof.artifact_id)}">보존 원문 보기 ↗</button>` : '<span class="muted small">연결된 원문이 없거나 현재 버전과 일치하지 않습니다.</span>'}${proof.url ? `<a href="${e(proof.url)}" target="_blank" rel="noopener noreferrer" class="text-button">출처 웹페이지 ↗</a>` : ''}</div></div>`).join('')}<details><summary>계산·단위·조건 전체 보기</summary><pre class="source-text">${e(JSON.stringify(claim, null, 2))}</pre></details></article>`).join('')}</div>` : `<div class="empty-panel"><h3>${evidence.claims.length ? '이 분류에 해당하는 주장이 없습니다.' : '구조화된 근거가 아직 등록되지 않았습니다.'}</h3><p>리서치 결과가 생성되면 주장과 출처를 연결합니다. 현재 등록된 문서는 아래에서 확인할 수 있습니다.</p></div>`}
    ${evidence.limitations.length ? `<section class="evidence-card" style="margin-top:16px"><h3>연구 전체의 한계</h3>${evidence.limitations.map((text) => `<p>${e(text)}</p>`).join('')}</section>` : ''}
    ${research.outputs.length ? `<section class="stage-panel" style="margin-top:18px"><div class="stage-panel-header"><h3>리서치 결과 원문</h3><span class="muted small">${research.outputs.length}개</span></div><div class="io-column"><details class="artifact-group"><summary>원문 파일 목록 보기</summary>${research.outputs.map(artifactRow).join('')}</details></div></section>` : ''}
    ${findings.length ? `<section class="stage-panel" style="margin-top:18px"><div class="stage-panel-header"><h3>한계와 확인할 사항</h3></div><div class="io-column">${findings.map((finding) => `<p class="evidence-intro">${e(findingText(finding))}</p>`).join('')}</div></section>` : ''}`;
}

function renderTimeline() {
  const events = [...(state.run.events || [])].sort((a, b) => b.seq - a.seq);
  return `<div class="section-heading"><div><h2>작업 기록</h2><p>단계 전환, 승인, 수정, 실행 결과를 시간순으로 기록합니다.</p></div><span class="muted small">${events.length}개 기록</span></div>
    ${state.run.historical_findings?.length ? `<details class="stage-panel"><summary>이전 시도의 검사 기록 ${state.run.historical_findings.length}개</summary><p>완료되거나 새 시도로 대체된 중간 검사 기록입니다. 현재 결과의 검토 상태는 위 체크포인트를 따릅니다.</p>${state.run.historical_findings.map((finding) => `<p>${e(findingText(finding))}</p>`).join('')}</details>` : ''}
    ${events.length ? `<ol class="activity-list">${events.map((event) => `<li class="activity-item"><time class="activity-time" datetime="${e(event.created_at)}">${e(formatTime(event.created_at, true))}</time><div class="activity-content"><small>#${e(event.seq)} · ${e(event.kind)}</small><p>${e(event.message || event.kind)}</p></div></li>`).join('')}</ol>` : '<div class="empty-panel"><h3>아직 실행 기록이 없습니다.</h3><p>첫 대화와 실행부터 변경 이력을 남깁니다.</p></div>'}`;
}

function renderWorkPanel() {
  if (state.tab === 'evidence') return renderEvidence();
  if (state.tab === 'history') return renderTimeline();
  return renderReviews() + renderStages();
}

function renderQuestions() {
  return pendingQuestions(state.run).map((question) => question.provider_request_id !== null && question.provider_request_id !== undefined
    ? renderProviderQuestion(question)
    : `<form class="question" data-question-form="${e(question.id)}" data-run-id="${e(state.run.id)}"><span class="eyebrow">결정이 필요합니다</span><h4>${e(question.question)}</h4>${question.impact ? `<p>${e(question.impact)}</p>` : ''}<label class="small muted" for="answer-${e(question.id)}">답변</label><textarea id="answer-${e(question.id)}" name="answer" required maxlength="12000" placeholder="현재 의도에 맞는 답변을 남겨주세요.">${e(state.drafts.get(`question:${state.run.id}:${question.id}`) || '')}</textarea><button class="button primary" type="submit" ${state.pending ? 'disabled' : ''}>답변 전달</button></form>`).join('');
}

function renderProviderQuestion(question) {
  const request = question.provider_params || {};
  const params = request.params || request;
  const method = request.method || question.provider_method || '';
  if (method === 'mcpServer/elicitation/request') return renderElicitation(question, params);
  const heading = `<span class="eyebrow">${method.includes('requestUserInput') || params.questions ? 'AI의 확인 질문' : '실행 권한 요청'}</span><h4>${e(question.question)}</h4>${question.impact ? `<p>${e(question.impact)}</p>` : ''}`;
  if (method === 'item/tool/requestUserInput' || Array.isArray(params.questions)) {
    if (params.questions.some((item) => item.isSecret)) return `<section class="question">${heading}<p>비밀 정보는 이 작업실에 입력하지 않습니다. 해당 서비스의 공식 로그인·인증 화면에서 연결해 주세요.</p></section>`;
    const draft = state.drafts.get(`provider:${state.run.id}:${question.id}`) || {};
    return `<form class="question provider-question" data-provider-input="${e(question.id)}" data-run-id="${e(state.run.id)}">${heading}${params.questions.map((item, index) => `<fieldset><legend>${e(item.question)}</legend>${item.options?.length ? `<select id="provider-${e(question.id)}-${index}" name="answer-${index}" aria-label="${e(item.header || item.question)}" ${item.isOther ? '' : 'required'}><option value="">선택하세요</option>${item.options.map((option) => `<option value="${e(option.label)}" ${draft[`answer-${index}`] === option.label ? 'selected' : ''}>${e(option.label)} — ${e(option.description)}</option>`).join('')}</select>${item.isOther ? `<label class="small muted">직접 답변<input id="provider-${e(question.id)}-other-${index}" name="other-${index}" placeholder="선택지 대신 입력할 수 있습니다" maxlength="12000" value="${e(draft[`other-${index}`] || '')}"></label>` : ''}` : `<textarea id="provider-${e(question.id)}-${index}" name="answer-${index}" aria-label="${e(item.header || item.question)}" required maxlength="12000">${e(draft[`answer-${index}`] || '')}</textarea>`}</fieldset>`).join('')}<button class="button primary" type="submit" ${state.pending ? 'disabled' : ''}>답변 전달</button></form>`;
  }
  const canDecide = method === 'item/commandExecution/requestApproval' || method === 'item/fileChange/requestApproval' || !!params.command;
  return `<section class="question">${heading}<p>이 요청은 도구 실행 권한에 관한 것입니다. 슬라이드 결과 승인은 별도로 진행합니다.</p>${fileChangeReviewMarkup({ ...request, method, params })}<details><summary>실행할 작업 확인</summary><pre class="source-text">${e(JSON.stringify(params, null, 2))}</pre></details>${canDecide ? `<div class="review-actions"><button class="button primary" data-provider-decision="accept" data-provider-question="${e(question.id)}" ${state.pending ? 'disabled' : ''}>이번 작업 허용</button><button class="button secondary" data-provider-decision="decline" data-provider-question="${e(question.id)}" ${state.pending ? 'disabled' : ''}>거절</button></div>` : method === 'item/permissions/requestApproval' ? `<p>추가 쓰기 영역 등 권한 범위 확장은 지원하지 않습니다.</p><button class="button secondary" data-provider-decision="permissions-deny" data-provider-question="${e(question.id)}" ${state.pending ? 'disabled' : ''}>확장 거절하고 계속</button>` : '<p>이 유형의 요청은 자동 승인하지 않습니다. 실행 환경과 요청 범위를 확인해 주세요.</p>'}</section>`;
}

function renderElicitation(question, params) {
  const form = elicitationForm(params);
  const draft = state.drafts.get(`elicitation:${state.run.id}:${question.id}`) || {};
  const fields = form.fields.map((field, index) => {
    const label = `<label for="elicitation-${e(question.id)}-${index}">${e(field.title)}${field.required ? ' · 필수' : ' · 선택'}</label>${field.description ? `<p>${e(field.description)}</p>` : ''}`;
    const control = field.type === 'boolean' || field.options
      ? `<select id="elicitation-${e(question.id)}-${index}" name="${e(field.key)}" ${field.required ? 'required' : ''}><option value="">선택하세요</option>${(field.type === 'boolean' ? [{ value: 'true', label: '예' }, { value: 'false', label: '아니요' }] : field.options).map((option) => `<option value="${e(option.value)}" ${draft[field.key] === option.value ? 'selected' : ''}>${e(option.label)}</option>`).join('')}</select>`
      : `<input id="elicitation-${e(question.id)}-${index}" name="${e(field.key)}" type="text" maxlength="12000" value="${e(draft[field.key] || '')}" ${field.required ? 'required' : ''} autocomplete="off">`;
    return `<div class="elicitation-field">${label}${control}</div>`;
  }).join('');
  const actions = `<div class="review-actions">${form.supported ? `<button type="submit" class="button primary" ${state.pending ? 'disabled' : ''}>입력 전달</button>` : ''}<button type="button" class="button secondary" data-elicitation-action="decline" data-provider-question="${e(question.id)}" ${state.pending ? 'disabled' : ''}>요청 거절</button><button type="button" class="text-button" data-elicitation-action="cancel" data-provider-question="${e(question.id)}" ${state.pending ? 'disabled' : ''}>요청 취소</button></div>`;
  return `<form class="question provider-question" data-elicitation-form="${e(question.id)}" data-run-id="${e(state.run.id)}"><span class="eyebrow">외부 도구의 추가 정보 요청</span><h4>${e(params.serverName || '연결된 도구')}</h4><p>${e(params.message || question.question)}</p>${form.supported ? fields : `<p>${e(form.reason)}</p>`}${actions}<p class="form-error" role="alert" hidden></p></form>`;
}

function renderChat() {
  const messages = state.run.messages || [];
  return (messages.length ? messages.map((message) => `<article class="message ${['user', 'assistant', 'system'].includes(message.role) ? message.role : 'system'}"><div class="message-header"><span>${message.role === 'user' ? '나' : message.role === 'assistant' ? 'Intent-Slide' : '작업 기록'}</span><span>${e(STAGES.find((stage) => stage.id === message.stage)?.short || '')}</span></div><p class="message-body">${e(message.content)}</p></article>`).join('') : `<div class="chat-empty"><span class="chat-monogram" aria-hidden="true">IS</span><h3>정리되지 않은 생각부터<br>이야기해도 괜찮습니다.</h3><p>슬라이드를 보는 사람과 바라는 행동을 알려주세요. 필요한 질문을 통해 방향을 구체화합니다.</p></div>`);
}

function preserveDrafts() {
  const composer = $('#message-input');
  const composerRunId = $('#message-form')?.dataset.runId;
  if (composer && composerRunId) state.drafts.set(composerRunId, composer.value);
  for (const form of document.querySelectorAll('[data-question-form]')) {
    state.drafts.set(`question:${form.dataset.runId}:${form.dataset.questionForm}`, form.elements.answer.value);
  }
  for (const form of document.querySelectorAll('[data-provider-input]')) {
    state.drafts.set(`provider:${form.dataset.runId}:${form.dataset.providerInput}`, Object.fromEntries(new FormData(form)));
  }
  for (const form of document.querySelectorAll('[data-elicitation-form]')) {
    state.drafts.set(`elicitation:${form.dataset.runId}:${form.dataset.elicitationForm}`, Object.fromEntries(new FormData(form)));
  }
}

function renderPageExecution(run) {
  const view = pageExecutionView(run);
  if (!view) return '';
  return `<section class="execution-milestone ${view.mode}" aria-label="페이지 작성 체크포인트"><div class="execution-heading"><span class="eyebrow">${view.mode === 'history' ? '마지막 시도 기록' : '현재 작성 작업'}</span><strong>${e(view.label)}</strong>${badge(view.jobStatus)}</div>${view.mode !== 'history' ? `<div class="execution-track" role="progressbar" aria-label="체크포인트 검사를 통과한 페이지 수" aria-valuemin="0" aria-valuemax="${view.total}" aria-valuenow="${view.completed}" aria-valuetext="${view.completed}/${view.total}페이지 · 최종 파일 검증 전"><span style="width:${view.completed / view.total * 100}%"></span></div>` : ''}<p>${e(view.description)}${view.updatedAt ? ` <span class="execution-updated">${e(formatTime(view.updatedAt))} 확인</span>` : ''}</p></section>`;
}

function actionMarkup(action) {
  if (action.kind === 'download') return `<a class="button primary" href="${e(safeArtifactUrl(action.artifact, state.run.id))}" download="${e(action.artifact.name)}">${e(action.label)} ↓</a>`;
  return `<button class="button primary" data-next-action="${action.kind}" ${state.pending || action.kind === 'working' ? 'disabled' : ''}>${e(action.label)}${action.kind === 'working' ? '…' : ' →'}</button>`;
}

function renderRelease(run) {
  const release = releaseView(run);
  if (!release) return '';
  return `<section class="release-panel" aria-labelledby="release-title"><div class="release-heading"><div><span class="eyebrow">최종 결과</span><h2 id="release-title">슬라이드가 완성되었습니다.</h2><p>파일 검사와 독립 검토를 통과한 현재 출고본입니다.</p></div>${actionMarkup(nextAction(run))}</div><button class="release-cover" data-artifact="${e(release.preview.id)}"><img src="${e(safeArtifactUrl(release.preview, run.id))}" alt="완성된 슬라이드 전체 미리보기"><span>전체 미리보기 확대 ↗</span></button>${release.pages.length ? `<div class="page-gallery" aria-label="페이지별 미리보기">${release.pages.map((page, index) => `<button data-artifact="${e(page.id)}"><img loading="lazy" src="${e(safeArtifactUrl(page, run.id))}" alt="${index + 1}페이지 미리보기"><span>${e(page.name)} · ${e(run.intent?.slides?.find((slide) => slide.display_id === page.name.replace(/\.[^.]+$/, ''))?.title || '페이지 확대')}</span></button>`).join('')}</div>` : ''}<div class="release-tools"><button class="button secondary" data-action="page-changes">페이지 수정 요청</button><details class="artifact-group"><summary>발표 노트 ${release.notes.length}개</summary>${release.notes.map(artifactRow).join('') || '<p class="empty-inline">별도 발표 노트가 등록되지 않았습니다.</p>'}</details><details class="artifact-group"><summary>검사와 출고 기록</summary>${[release.review, release.manifest].map(artifactRow).join('')}</details></div></section>`;
}

function renderRun() {
  if (state.authExpired) return renderSessionRequired();
  if (state.screen === 'setup') return renderSetup();
  if (state.screen === 'home') return renderWelcome();
  const run = state.run;
  if (!run) return renderWelcome();
  preserveDrafts();
  const activeElement = document.activeElement;
  const focused = activeElement?.id;
  const selection = typeof activeElement?.selectionStart === 'number' ? [activeElement.selectionStart, activeElement.selectionEnd] : null;
  const chatBefore = $('#chat-messages');
  const scrollTop = chatBefore?.scrollTop || 0;
  const atBottom = !chatBefore || chatBefore.scrollHeight - (scrollTop + chatBefore.clientHeight) < 60;
  const isBusy = activeJobs(run).length > 0;
  if ($('#provider-dialog').open) lockProviderForm();
  const readonly = isReadOnly(run);
  const action = nextAction(run);
  const execution = executionSelection(run, state.preferred);
  const provider = selectedProvider(execution);
  $('#breadcrumb').textContent = run.title;
  $('#last-updated').textContent = `${formatTime(run.updated_at)} 갱신`;
  $('#main-content').innerHTML = `<div class="workspace-content"><div class="project-heading"><div><span class="eyebrow">${e(STAGES.find((stage) => stage.id === run.active_stage)?.label || '내 프레젠테이션')}</span><h1>${e(run.title)}</h1><div class="project-subtitle">${badge(run.status)}<span>${e(({hybrid: '제공 자료 + 외부 조사', provided_only: '제공 자료만 사용', external: '외부 조사 중심'})[run.source_mode] || '자료 범위 확인 중')}</span><button class="text-button" data-action="provider-settings" ${state.pending || isBusy || readonly ? 'disabled' : ''}>${e(provider?.label || ({ codex: 'Codex', claude: 'Claude Code' })[execution.provider] || execution.provider)} 설정</button></div></div><div class="project-actions">${actionMarkup(action)}${isBusy ? `<button class="text-button" data-command="cancel" ${state.pending ? 'disabled' : ''}>작업 취소</button>` : ''}${!readonly && action.kind !== 'download' ? `<button class="text-button" data-request-changes="${e(run.active_stage || 'intent')}" ${state.pending ? 'disabled' : ''}>수정 요청</button>` : ''}</div></div>${readonly ? '<p class="read-only-banner">기존 기록을 읽기 전용으로 보존합니다. 새로운 승인이나 진행률을 만들지 않습니다.</p>' : ''}${renderProgress(run)}<div class="next-action"><strong>지금 할 일</strong><span>${e(action.detail)}</span></div>${renderPageExecution(run)}${run.findings?.length ? `<section class="recovery-panel"><h2>확인할 사항</h2>${run.findings.slice(0, 3).map((finding) => `<p>${e(findingText(finding))}</p>`).join('')}<button class="text-button" data-action="setup">AI 연결과 준비 상태 확인</button><details><summary>진행 기록 전체 보기</summary>${run.findings.slice(3).map((finding) => `<p>${e(findingText(finding))}</p>`).join('') || '<p>추가 확인 사항이 없습니다.</p>'}</details></section>` : ''}${pendingQuestions(run).length ? `<section class="decision-panel" id="decision-panel" tabindex="-1" aria-labelledby="decision-title"><div class="section-heading"><div><span class="eyebrow">답변을 기다리고 있어요</span><h2 id="decision-title">다음으로 가기 전에 확인할 내용</h2></div><span class="badge warning">${pendingQuestions(run).length}개</span></div>${renderQuestions()}</section>` : ''}${renderRelease(run)}<div class="workspace-grid"><aside class="conversation" aria-labelledby="chat-title"><header class="conversation-header"><h2 id="chat-title">함께 내용 정리하기</h2><p>목적과 생각을 이야기해 주세요. 필요한 질문은 위에 표시합니다.</p></header><div class="chat-messages" id="chat-messages" role="log" aria-label="프로젝트 대화" aria-live="polite" aria-relevant="additions">${renderChat()}</div><form class="composer" id="message-form" data-run-id="${e(run.id)}"><label for="message-input" class="small muted">의도와 피드백 전달</label><textarea id="message-input" name="content" rows="3" maxlength="20000" placeholder="생각, 자료 설명, 바꾸고 싶은 부분을 남겨주세요." ${readonly ? 'disabled' : ''}>${e(state.drafts.get(run.id) || '')}</textarea><div class="composer-footer"><button class="attach-button" type="button" data-action="attach" ${state.pending || readonly ? 'disabled' : ''}>＋ 자료 첨부</button><button class="button primary send-button" type="submit" ${state.pending || readonly ? 'disabled' : ''}>보내기 ↑</button></div><input type="file" id="attachment-input" hidden></form><p class="composer-help">⌘ / Ctrl + Enter로 전송 · 승인은 검토 화면에서 기록합니다.</p></aside><div class="workspace-left"><div class="tabs" role="tablist" aria-label="작업 정보">${[['work', '단계와 결과'], ['evidence', '근거와 분석'], ['history', '작업 기록']].map(([id, label]) => `<button class="tab" id="tab-${id}" role="tab" aria-selected="${state.tab === id}" aria-controls="work-panel" tabindex="${state.tab === id ? 0 : -1}" data-tab="${id}">${label}${id === 'work' && pendingReviews(run).length ? `<span class="tab-count">${pendingReviews(run).length}</span>` : ''}</button>`).join('')}</div><section id="work-panel" role="tabpanel" aria-labelledby="tab-${state.tab}">${renderWorkPanel()}</section></div></div></div>`;
  const chat = $('#chat-messages');
  chat.scrollTop = atBottom ? chat.scrollHeight : scrollTop;
  if (focused && document.getElementById(focused)) {
    const element = document.getElementById(focused);
    element.focus({ preventScroll: true });
    if (selection && element.setSelectionRange) element.setSelectionRange(...selection);
  }
  renderRunList();
}

function acceptSnapshot(snapshot) {
  if (state.authExpired) return;
  const run = snapshot.run || snapshot;
  if (!run?.id) throw new Error('서버에서 프로젝트 상태를 받지 못했습니다.');
  if (state.run?.id === run.id && run.revision < state.run.revision) return;
  if (state.run?.id === run.id && JSON.stringify(state.run) === JSON.stringify(run)) return;
  const change = progressChange(state.run, run);
  preserveDrafts();
  state.run = run;
  const index = state.runs.findIndex((item) => item.id === run.id);
  if (index === -1) state.runs.unshift(run); else state.runs[index] = run;
  for (const event of run.events || []) state.eventSeq = Math.max(state.eventSeq, Number(event.seq) || 0);
  renderRun();
  if (change) announce(change, 'warning');
}

async function refreshRun(id = state.run?.id) {
  if (!id || state.authExpired) return;
  const generation = state.selection;
  const snapshot = await api.request(`/api/v2/runs/${encodeURIComponent(id)}`);
  if (generation === state.selection && state.run?.id === id) acceptSnapshot(snapshot);
}

function disconnectStream() {
  state.streamGeneration += 1;
  state.stream?.close();
  state.stream = null;
  clearTimeout(state.refreshTimer);
  clearTimeout(state.reconnectTimer);
}

function renderSessionRequired() {
  $('#breadcrumb').textContent = '접속 링크로 다시 연결';
  $('#main-content').innerHTML = `<section class="initial-loading"><span class="eyebrow">다시 연결이 필요합니다</span><h1>작업실 접속이 만료되었습니다.</h1><p>서버가 다시 시작되었거나 접속 정보가 바뀌었습니다.<br>실행 터미널의 새 접속 링크로 이 탭을 열어 주세요.</p><p>작성 중인 대화는 이 탭에 보존했습니다. 자동 재연결은 멈췄습니다.</p><button class="button primary" style="margin-top:22px" data-action="reconnect-session">연결 다시 확인</button></section>`;
}

function expireSession() {
  if (!state.authExpired) preserveDrafts();
  state.authExpired = true;
  state.connected = false;
  state.selection += 1;
  api.csrfToken = null;
  disconnectStream();
  stopDiscovery();
  state.previewController?.abort();
  $('#detail-dialog').close();
  setConnection('offline', '접속 만료 · 새 링크로 연결');
  announce('실행 터미널의 새 접속 링크로 열어 주세요. 자동 재연결은 중지했습니다.', 'warning');
  renderSessionRequired();
  for (const id of ['create-error', 'provider-error', 'changes-error']) {
    const target = document.getElementById(id);
    if (target.closest('dialog')?.open) { target.textContent = '작업실 접속이 만료되었습니다. 입력은 보존했습니다. 창을 닫고 실행 터미널의 새 접속 링크로 연결해 주세요.'; target.hidden = false; }
  }
}

function streamRefreshError(error) {
  if ([401, 403].includes(error.status)) expireSession();
  else setConnection('reconnecting', '최신 상태 확인 중');
}

function connectStream(id) {
  disconnectStream();
  if (!state.connected || state.authExpired || id !== state.run?.id) return;
  const generation = state.streamGeneration;
  const isCurrent = () => generation === state.streamGeneration && !state.authExpired && state.connected && state.run?.id === id;
  const stream = new EventSource(`/api/v2/runs/${encodeURIComponent(id)}/events?after=${state.eventSeq}`);
  state.stream = stream;
  const refresh = () => refreshRun(id).catch((error) => { if (isCurrent()) streamRefreshError(error); });
  stream.onopen = () => {
    if (!isCurrent()) return;
    setConnection('connected', '로컬 엔진 · 실시간 연결');
    refresh();
  };
  const onEvent = (event) => {
    if (!isCurrent()) return;
    let data;
    try { data = JSON.parse(event.data); } catch { return; }
    if (data.run_id && data.run_id !== id) return;
    state.eventSeq = Math.max(state.eventSeq, Number(data.seq) || Number(event.lastEventId) || 0);
    if (data.snapshot && (data.snapshot.id || data.snapshot.run?.id) === id) return acceptSnapshot(data.snapshot);
    clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(refresh, 120);
  };
  stream.onmessage = onEvent;
  for (const name of ['event', 'snapshot', 'run.updated', 'update']) stream.addEventListener(name, onEvent);
  stream.onerror = () => {
    if (!isCurrent() || state.stream !== stream) return;
    stream.close(); state.stream = null;
    clearTimeout(state.refreshTimer); clearTimeout(state.reconnectTimer);
    setConnection('reconnecting', '연결 복구 중 · 기록 보존');
    recoverEventStream({ verifySession: () => api.request('/api/v2/session'), isCurrent,
      onExpired: expireSession, onReconnect: () => { if (isCurrent()) connectStream(id); },
      schedule: (callback, delay) => { state.reconnectTimer = setTimeout(callback, delay); return state.reconnectTimer; },
    }).catch((error) => { if (isCurrent()) handleError(error); });
  };
}

async function selectRun(id) {
  if (state.authExpired) return renderSessionRequired();
  if (state.pending) return announce('현재 요청을 처리한 뒤 프로젝트를 전환해 주세요.', 'warning');
  preserveDrafts();
  stopDiscovery(); state.welcomeAfterDiscovery = false;
  disconnectStream();
  state.selection += 1;
  const generation = state.selection;
  state.eventSeq = 0;
  state.tab = 'work'; state.screen = 'work'; state.reviewContext = null;
  try {
    const snapshot = await api.request(`/api/v2/runs/${encodeURIComponent(id)}`);
    if (generation !== state.selection) return;
    state.run = null;
    acceptSnapshot(snapshot);
    history.replaceState(null, '', `#run=${encodeURIComponent(id)}`);
    announce('');
    connectStream(id);
  } catch (error) {
    if (generation === state.selection) handleError(error);
  }
}

function handleError(error, target = null) {
  if ([401, 403].includes(error.status)) {
    expireSession();
    if (target) { target.textContent = '실행 터미널의 새 접속 링크로 이 탭을 열어 주세요. 입력 내용은 보존했습니다.'; target.hidden = false; }
    return;
  }
  const message = error.status === 409
    ? '다른 작업으로 상태가 바뀌었습니다. 최신 결과를 불러왔습니다. 변경 내용을 확인한 뒤 다시 결정해 주세요.'
    : error.message || '요청을 처리하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도해 주세요.';
  if (target) { target.textContent = message; target.hidden = false; }
  else announce(message, error.status === 409 ? 'warning' : 'error');
  if (error.status === 409) refreshRun().catch(() => {});
}

async function sendCommand(command, payload = {}, expectedSnapshot = state.run) {
  if (state.authExpired) { renderSessionRequired(); return null; }
  if (state.pending || !state.run) return null;
  state.pending = true;
  state.commandAttempt = commandAttempt(expectedSnapshot, command, payload, state.commandAttempt);
  const attempt = state.commandAttempt;
  renderRun();
  try {
    const snapshot = await api.command(attempt.snapshot, command, payload, attempt.operation_id);
    state.commandAttempt = null;
    acceptSnapshot(snapshot);
    announce(command === 'approve' ? '검토한 버전의 승인을 기록했습니다.' : command === 'request_changes' ? '수정 요청을 기록했습니다. 영향받은 결과와 체크포인트를 확인합니다.' : command === 'message' ? '메시지를 기록했습니다. 이어질 작업은 실행 상태에서 확인하세요.' : command === 'cancel' ? '취소 요청을 전달했습니다. 실행 상태에서 종료 결과를 확인합니다.' : '요청을 기록했습니다.');
    return snapshot;
  } catch (error) {
    if (error.status && error.status < 500) state.commandAttempt = null;
    handleError(error);
    throw error;
  } finally {
    state.pending = false;
    renderRun();
  }
}

function openDetail(title, content) {
  $('#detail-title').textContent = title;
  $('#detail-content').innerHTML = content;
  const dialog = $('#detail-dialog');
  if (!dialog.open) dialog.showModal();
}

async function showArtifact(id) {
  const run = state.run;
  let artifact = run.artifacts?.find((item) => item.id === id);
  if (!artifact) {
    artifact = stageView(run).flatMap((stage) => [...stage.inputs, ...stage.outputs]).map(resolveArtifact).find((item) => item?.id === id);
  }
  if (!artifact) return announce('이 결과물이 현재 프로젝트에 없습니다. 상태를 새로고침해 주세요.', 'warning');
  const url = safeArtifactUrl(artifact, run.id);
  const name = artifact.name || artifact.id;
  state.previewController?.abort();
  state.previewController = new AbortController();
  const controller = state.previewController;
  if (state.attachmentUrl) URL.revokeObjectURL(state.attachmentUrl);
  state.attachmentUrl = null;
  const canRequestPageChange = pageChangeChoices(run).some((item) => item.id === artifact.id) && !isReadOnly(run);
  openDetail(name, `${state.reviewContext?.runId === run.id ? `<button class="text-button" data-review-view="${e(state.reviewContext.id)}">← 검토 내용으로 돌아가기</button>` : ''}<details class="technical-details"><summary>파일 버전과 검사 정보</summary><dl class="detail-metadata"><dt>버전</dt><dd>v${e(artifact.version ?? '—')} · ${artifact.valid === false ? '재검증 필요' : artifact.valid === true ? '유효' : '검증 상태 확인 전'}</dd><dt>단계</dt><dd>${e(STAGES.find((item) => item.id === artifact.stage)?.label || artifact.stage || '입력 자료')}</dd><dt>내용 해시</dt><dd>${e(artifact.sha256 || '아직 기록되지 않음')}</dd></dl></details><div id="artifact-preview"><p class="muted small">원문을 불러오는 중입니다.</p></div><div class="download-row">${canRequestPageChange ? `<button class="button secondary" data-change-artifact="${e(artifact.id)}">이 ${artifact.kind === 'notes' ? '발표 노트' : '페이지'} 수정</button>` : ''}<a class="button secondary" href="${e(url)}" download="${e(name)}">파일 다운로드 ↓</a></div>`);
  try {
    const response = await fetch(url, { credentials: 'same-origin', signal: controller.signal });
    if (!response.ok) throw new Error(`파일을 읽지 못했습니다 (${response.status}).`);
    const contentType = response.headers.get('Content-Type') || '';
    const kind = previewKind(name, contentType);
    if (Number(response.headers.get('Content-Length') || 0) > 12 * 1024 * 1024) {
      $('#artifact-preview').innerHTML = '<p class="muted small">12MB보다 큰 파일입니다. 다운로드해서 원문을 확인해 주세요.</p>';
      return;
    }
    if (kind === 'text') {
      const text = await response.text();
      if (controller.signal.aborted) return;
      $('#artifact-preview').innerHTML = `<pre class="source-text">${e(text.slice(0, 180000))}</pre>${text.length > 180000 ? '<p class="muted small">미리보기는 처음 180,000자입니다. 전체 내용은 다운로드로 확인해 주세요.</p>' : ''}`;
    } else if (kind === 'image' || kind === 'pdf') {
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      state.attachmentUrl = URL.createObjectURL(blob);
      $('#artifact-preview').innerHTML = kind === 'image'
        ? `<img class="artifact-preview" src="${e(state.attachmentUrl)}" alt="${e(name)} 미리보기">`
        : `<iframe class="artifact-preview" src="${e(state.attachmentUrl)}" title="${e(name)} PDF 미리보기" sandbox></iframe>`;
    } else {
      $('#artifact-preview').innerHTML = '<div class="empty-panel"><h3>파일로 확인할 수 있는 결과물입니다.</h3><p>PPTX 등 이 형식은 다운로드해서 확인해 주세요. 등록된 렌더 이미지가 있으면 단계 출력에서 함께 볼 수 있습니다.</p></div>';
    }
  } catch (error) {
    if (error.name !== 'AbortError' && $('#artifact-preview')) $('#artifact-preview').textContent = error.message;
  }
}

function renderResearchSummary(summary, reports = []) {
  return `<div class="research-summary"><div class="evidence-counts"><span><strong>${summary.provided}</strong> 제공 자료 근거</span><span><strong>${summary.supported}</strong> 그 밖의 근거 연결</span><span><strong>${summary.assumptions}</strong> 가정·제안</span><span><strong>${summary.needsReview}</strong> 확인 필요</span><span><strong>${summary.sources}</strong> 출처</span></div><p class="evidence-meaning">근거 분류는 출처와의 연결을 뜻합니다. 주장이 원문의 의미에 맞는지와 비교 조건은 별도로 검토해야 합니다.</p><div class="analysis-excerpt"><span class="eyebrow">핵심 문단 발췌</span>${summary.analysis.map((text) => `<p>${e(text)}</p>`).join('') || '<p>전체 분석은 연결된 보고서에서 확인해 주세요.</p>'}</div>${reports.length ? `<div class="analysis-reports"><strong>전체 분석과 상세 비교</strong>${reports.map(artifactRow).join('')}</div>` : '<p class="small muted">전체 내용은 아래 리서치 결과 원문에서 확인할 수 있습니다.</p>'}${summary.limitations.length ? `<div class="review-limitations"><strong>해석할 때 기억할 한계</strong>${summary.limitations.map((text) => `<p>${e(text)}</p>`).join('')}</div>` : ''}</div>`;
}

function semanticReview(review, view) {
  if (!view.bound) return '<p class="read-only-banner">현재 검토본에 연결된 내용이나 파일을 확인할 수 없습니다. 최신 상태를 다시 불러온 뒤 검토해 주세요.</p>';
  const data = view.data;
  if (review.gate === 'G1') {
    const labels = { topic: '주제', audience: '누가 보나요?', objective: '어떤 행동을 바라나요?', success_criteria: '무엇이면 성공인가요?', slide_count: '슬라이드 수' };
    return `<section class="semantic-review"><h3>이 내용을 기준으로 조사합니다.</h3><dl class="intent-summary">${Object.entries(labels).map(([key, label]) => { const field = data.fields?.[key]; return `<dt>${label}</dt><dd>${e(displayValue(field?.value) || '아직 정하지 않았습니다')}${field?.state === 'proposed' ? '<small>제안된 내용 · 승인 전에 확인해 주세요</small>' : ''}</dd>`; }).join('')}</dl>${(data.requirements || []).length ? `<details class="artifact-group"><summary>조사에서 확인할 질문 ${data.requirements.length}개</summary><ol class="slide-outline">${data.requirements.map((item) => `<li><span>${e(data.slides?.find((slide) => slide.uid === item.slide_uid)?.display_id || '')}</span><div><p>${e(item.question)}</p></div></li>`).join('')}</ol></details>` : ''}<h3>전달할 이야기의 순서</h3><ol class="slide-outline">${(data.slides || []).map((slide) => `<li><span>${e(slide.display_id)}</span><div><h4>${e(slide.title)}</h4><p>${e(displayValue(slide.purpose))}</p>${slide.content ? `<p>${e(displayValue(slide.content))}</p>` : ''}</div></li>`).join('')}</ol></section>`;
  }
  if (review.gate === 'G2') return `<section class="semantic-review"><h3>조사로 확인한 내용</h3>${renderResearchSummary(researchSummary(data), view.artifacts.filter((artifact) => ['analysis_pdf', 'analysis_report'].includes(artifact.kind)))}<h3>슬라이드에 전달할 메시지</h3><ol class="slide-outline">${(data.messages || []).map((item) => `<li><span>${e(state.run.intent?.slides?.find((slide) => slide.uid === item.slide_uid)?.display_id || '')}</span><div><p>${e(displayValue(item.message))}</p></div></li>`).join('')}</ol><div class="review-actions"><button class="button secondary" data-action="review-evidence">주장별 근거 자세히 보기 ↗</button></div></section>`;
  return `<section class="semantic-review"><h3>이 방향으로 슬라이드를 제작합니다.</h3><p>${e(displayValue(data.summary) || '연결된 디자인 방향 파일과 미리보기를 확인해 주세요.')}</p>${view.previews.length ? `<div class="direction-previews">${view.previews.map((artifact) => `<button data-artifact="${e(artifact.id)}"><img src="${e(safeArtifactUrl(artifact, state.run.id))}" alt="${e(artifact.name)} 디자인 방향 미리보기"><span>${e(artifact.name)} 확대 ↗</span></button>`).join('')}</div>` : '<p class="muted">별도 미리보기가 등록되지 않았습니다. 아래 디자인 방향 원문을 확인해 주세요.</p>'}<details class="technical-details"><summary>디자인 규칙 자세히 보기</summary><pre class="source-text">${e(JSON.stringify({ design_spec: data.design_spec, spec_lock: data.spec_lock }, null, 2))}</pre></details></section>`;
}

function showReview(id) {
  state.previewController?.abort();
  const review = pendingReviews(state.run).find((item) => item.id === id);
  if (!review) return announce('현재 대기 중인 검토가 아닙니다. 최신 상태를 확인해 주세요.', 'warning');
  const view = reviewView(state.run, review);
  state.reviewContext = { runId: state.run.id, id: review.id, bundle: review.bundle_sha256 };
  openDetail(`${view.title} 검토`, `<p class="evidence-intro">${e(review.summary || '내용이 의도에 맞는지 확인하고 승인하거나 수정을 요청해 주세요.')}</p>${semanticReview(review, view)}<details class="artifact-group review-files"><summary>이 검토에 연결된 파일 ${view.artifacts.length}개</summary>${view.artifacts.map(artifactRow).join('')}</details><details class="technical-details"><summary>검토 버전과 기록 상세</summary><dl class="detail-metadata"><dt>대상 버전</dt><dd>${e(review.bundle_sha256 || '없음')}</dd><dt>프로젝트</dt><dd>${e(state.run.title)} · 개정 ${e(state.run.revision)}</dd></dl></details><div class="review-footer"><button class="button secondary" data-request-changes="${e(review.stage)}">수정 요청</button><button class="button primary" data-review-approve="${e(review.id)}" ${!view.bound || state.pending ? 'disabled' : ''}>내용 확인 · 이 버전 승인</button></div>`);
  if (view.bound) state.viewedReviews.set(review.id, review.bundle_sha256);
  renderRun();
}

function showProgress() {
  state.previewController?.abort();
  const progress = state.run.progress || {};
  const units = progress.units || state.run.units || [];
  openDetail('진행률의 계산 기준', `<p class="evidence-intro">${e(progress.reason || '현재 유효한 완료 단위의 배점을 합산합니다. 실행 시간과 품질 점수는 진행률에 포함하지 않습니다.')}</p><dl class="detail-metadata"><dt>현재 진행률</dt><dd>${percentText(progress.percent)}</dd><dt>기본 배점</dt><dd>의도 확정 20% · 리서치·분석 35% · 제작·검증·출고 45%</dd><dt>100% 조건</dt><dd>현재 후보의 필수 검사와 최종 출고 기록이 모두 유효해야 합니다.</dd></dl>${units.length ? `<table class="unit-table"><thead><tr><th>완료 단위</th><th>배점</th><th>현재 상태</th></tr></thead><tbody>${units.map((unit) => `<tr><td>${e(unit.label || unit.id)}${unit.reason ? `<small>${e(unit.reason)}</small>` : ''}</td><td>${e(unit.weight ?? '—')}</td><td>${badge(unit.status)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted small">계산할 완료 단위가 아직 등록되지 않았습니다. 과거의 완료 기록에서 진행률을 추정하지 않습니다.</p>'}`);
}

function renderChangeScope(selectedIds = null) {
  const stage = $('#changes-stage').value;
  const scope = $('#changes-form').elements.scope.value;
  const selected = new Set(selectedIds || [...document.querySelectorAll('input[name="change_artifact"]:checked')].map((input) => input.value));
  const choices = pageChangeChoices(state.run);
  $('#changes-scope').hidden = stage !== 'design';
  $('#changes-pages').hidden = stage !== 'design' || scope !== 'pages';
  $('#changes-page-options').innerHTML = choices.length ? choices.map((artifact) => `<label class="change-page-option"><input type="checkbox" name="change_artifact" value="${e(artifact.id)}" ${selected.has(artifact.id) ? 'checked' : ''}><span><strong>${e(artifact.pages.map((page) => page.label).join(' · '))} · ${artifact.kind === 'notes' ? '발표 노트' : '페이지'}</strong><span>${e(artifact.name)} · v${e(artifact.version ?? '—')}</span><small>${e(artifact.pages.map((page) => page.title).filter(Boolean).join(' / '))}${artifact.pages.length > 1 ? ` · ${artifact.pages.length}개 페이지에 연결됨` : ''}</small></span></label>`).join('') : '<p class="empty-inline">현재 수정할 수 있는 페이지·발표 노트가 없습니다. 페이지 결과가 검증되어 등록된 뒤 선택할 수 있습니다.</p>';
}

function openChanges(stage, artifactIds = [], scope = 'stage') {
  if (!state.run || state.pending) return;
  state.changeContext = { id: state.run.id, revision: state.run.revision };
  $('#changes-context').textContent = `${state.run.title} · 개정 ${state.run.revision}의 결과를 기준으로 요청합니다.`;
  $('#changes-stage').value = stage;
  $('#changes-form').elements.scope.value = scope;
  $('#changes-error').hidden = true;
  renderChangeScope(artifactIds);
  if (!$('#changes-dialog').open) $('#changes-dialog').showModal();
}

async function sendProviderAnswer(question, response) {
  try { return await sendCommand('provider_answer', providerAnswerPayload(question, response)); }
  catch (error) { handleError(error); return null; }
}

async function upload(file) {
  if (!file || state.pending || !state.run || state.authExpired) return;
  if (file.size > 100 * 1024 * 1024) return announce('파일 한 개는 100MB 이내로 첨부해 주세요.', 'warning');
  state.pending = true;
  renderRun();
  announce(`“${file.name}” 파일을 업로드하고 있습니다.`);
  const form = new FormData();
  const key = JSON.stringify([state.run.id, file.name, file.size, file.lastModified]);
  if (state.uploadAttempt?.key !== key) state.uploadAttempt = { key, operation_id: operationId(), revision: state.run.revision };
  form.append('file', file);
  form.append('operation_id', state.uploadAttempt.operation_id);
  form.append('expected_revision', String(state.uploadAttempt.revision));
  try {
    const snapshot = await api.request(`/api/v2/runs/${encodeURIComponent(state.run.id)}/attachments`, { method: 'POST', body: form });
    state.uploadAttempt = null;
    if (snapshot.id || snapshot.run) acceptSnapshot(snapshot); else await refreshRun();
    announce(`“${file.name}” 원본 파일을 첨부했습니다. 단계 입력에서 확인할 수 있습니다.`);
  } catch (error) { if (error.status && error.status < 500) state.uploadAttempt = null; handleError(error); }
  finally { state.pending = false; renderRun(); }
}

document.addEventListener('click', async (event) => {
  const button = event.target.closest('button, a');
  if (!button || button.disabled) return;
  if (button.hasAttribute('data-close-dialog')) return button.closest('dialog').close();
  if (button.dataset.action === 'reconnect-session') return boot();
  if (state.authExpired) return renderSessionRequired();
  if (button.dataset.selectRun) return selectRun(button.dataset.selectRun);
  if (button.id === 'new-run' || button.dataset.action === 'new-project') return startCreate();
  if (button.dataset.action === 'setup' || button.id === 'capabilities-button') { state.welcomeAfterDiscovery = false; renderSetup(); return; }
  if (button.dataset.action === 'home') { stopDiscovery(); preserveDrafts(); state.welcomeAfterDiscovery = false; state.screen = 'home'; renderWelcome(); return; }
  if (button.dataset.action === 'back-work') { stopDiscovery(); state.welcomeAfterDiscovery = false; state.screen = 'work'; renderRun(); return; }
  if (button.dataset.action === 'setup-complete') {
    if (!selectedProvider()?.ready) return;
    state.screen = 'work';
    if (state.run) { stopDiscovery(); renderRun(); } else { renderWelcome(); startCreate(); }
    return;
  }
  if (button.dataset.action === 'login-codex') return loginCodex();
  if (button.dataset.checkProvider || button.dataset.action === 'discover-providers') return checkProvider();
  if (button.dataset.action === 'refresh-capabilities') {
    return checkProvider();
  }
  if (button.dataset.action === 'provider-settings') {
    if (!state.run || state.pending || activeJobs(state.run).length) return;
    state.providerContext = { id: state.run.id, revision: state.run.revision, execution: executionSelection(state.run) };
    populateProviderSelect('run-provider', state.providerContext.execution);
    $('#run-model-note').textContent = '목록은 연결된 도구의 정보입니다. 기본값을 선택하면 모델과 추론 설정을 도구에 맡깁니다. 실행 중에는 설정을 바꿀 수 없습니다.';
    $('#provider-error').hidden = true;
    $('#provider-dialog').showModal();
    return;
  }
  if (button.dataset.nextAction) {
    const action = nextAction(state.run);
    if (action.kind !== button.dataset.nextAction) return renderRun();
    if (action.kind === 'question') { const panel = $('#decision-panel'); panel?.focus(); panel?.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
    if (action.kind === 'review') return showReview(action.reviewId);
    if (action.kind === 'history') { state.tab = 'history'; renderRun(); return; }
    if (action.kind === 'refresh') return refreshRun().catch(handleError);
    if (['run', 'resume'].includes(action.kind)) {
      if (!selectedProvider(executionSelection(state.run))?.ready) { rememberProvider(executionSelection(state.run).provider); return renderSetup(); }
      return sendCommand(action.kind).catch(() => {});
    }
  }
  if (button.dataset.checkpoint) {
    const checkpoint = checkpointView(state.run).find((item) => item.id === button.dataset.checkpoint);
    if (checkpoint?.reviewId) return showReview(checkpoint.reviewId);
    return showProgress();
  }
  if (button.dataset.evidenceFilter) { state.evidenceFilter = button.dataset.evidenceFilter; renderRun(); return; }
  if (button.dataset.action === 'review-evidence') {
    $('#detail-dialog').close(); state.tab = 'evidence'; renderRun(); $('#tab-evidence')?.focus(); return;
  }
  if (button.dataset.tab) {
    state.tab = button.dataset.tab;
    renderRun();
    return $(`#tab-${state.tab}`).focus();
  }
  if (button.dataset.stageFocus) {
    state.tab = 'work'; renderRun();
    const panel = $(`#stage-${button.dataset.stageFocus}`);
    panel.focus({ preventScroll: true }); panel.scrollIntoView({ behavior: 'smooth', block: 'center' }); return;
  }
  if (button.dataset.artifact) return showArtifact(button.dataset.artifact);
  if (button.dataset.changeArtifact) {
    $('#detail-dialog').close();
    return openChanges('design', [button.dataset.changeArtifact], 'pages');
  }
  if (button.dataset.action === 'page-changes') return openChanges('design', [], 'pages');
  if (button.dataset.reviewView) return showReview(button.dataset.reviewView);
  if (button.dataset.reviewApprove) {
    const review = pendingReviews(state.run).find((item) => item.id === button.dataset.reviewApprove);
    if (!review || state.viewedReviews.get(review.id) !== review.bundle_sha256) return;
    if (!reviewView(state.run, review).bound) return announce('현재 검토 결과가 변경되었습니다. 다시 열어 확인해 주세요.', 'warning');
    try { const result = await sendCommand('approve', { review_id: review.id, bundle_sha256: review.bundle_sha256 }); if (result) $('#detail-dialog').close(); } catch { /* Explicit review is required before retry. */ }
    return;
  }
  if (button.dataset.requestChanges) {
    $('#detail-dialog').close();
    return openChanges(button.dataset.requestChanges);
  }
  if (button.dataset.command) return sendCommand(button.dataset.command).catch(() => {});
  if (button.dataset.providerDecision) {
    const question = state.run.questions?.find((item) => item.id === button.dataset.providerQuestion);
    if (!question) return;
    const response = button.dataset.providerDecision === 'permissions-deny' ? { permissions: {}, scope: 'turn' } : { decision: button.dataset.providerDecision };
    return sendProviderAnswer(question, response);
  }
  if (button.dataset.elicitationAction) {
    const question = state.run.questions?.find((item) => item.id === button.dataset.providerQuestion);
    if (!question) return;
    const params = question.provider_params?.params || {};
    const response = elicitationResponse(params, button.dataset.elicitationAction);
    return sendProviderAnswer(question, response);
  }
  if (button.dataset.action === 'attach') return $('#attachment-input').click();
  if (button.dataset.action === 'progress-detail') return showProgress();
  if (button.id === 'refresh') {
    try { if (state.screen === 'setup') await discoverProviders(true); else if (state.run) await refreshRun(); else await boot(); announce('최신 상태를 불러왔습니다.'); }
    catch (error) { handleError(error); }
  }

});

document.addEventListener('submit', async (event) => {
  const form = event.target;
  if (state.authExpired) { event.preventDefault(); return handleError({ status: 401 }, $('.form-error', form)); }
  if (form.id === 'create-form') {
    event.preventDefault();
    if (state.pending) return;
    const data = new FormData(form);
    const files = [...$('#create-attachments').files];
    const provider = selectedProvider({ provider: data.get('provider') });
    if (!provider?.ready) return handleError(new Error('선택한 AI의 설치와 지원되는 로그인 상태를 먼저 확인해 주세요.'), $('#create-error'));
    if (files.some((file) => file.size > 100 * 1024 * 1024)) return handleError(new Error('파일 한 개는 100MB 이내로 첨부해 주세요.'), $('#create-error'));
    state.pending = true;
    $('#create-submit').disabled = true;
    $('#create-error').hidden = true;
    let created = false;
    try {
      const request = String(data.get('request') || '').trim();
      if (!request) throw new Error('첫 이야기를 입력해 주세요.');
      const execution = validateExecutionSelection(provider, readExecutionForm('create-provider'));
      const payload = { title: String(data.get('title') || '').trim() || request.replace(/\s+/g, ' ').slice(0, 60), request, source_mode: data.get('source_mode'), execution };
      const key = JSON.stringify(payload);
      if (state.createAttempt?.key !== key) state.createAttempt = { key, operation_id: operationId() };
      const snapshot = await api.request('/api/v2/runs', { method: 'POST', body: { ...payload, operation_id: state.createAttempt.operation_id } });
      state.createAttempt = null; created = true;
      stopDiscovery(); disconnectStream(); preserveDrafts(); rememberProvider(provider.id);
      state.selection += 1; state.eventSeq = 0; state.run = null; state.tab = 'work'; state.screen = 'work'; state.reviewContext = null;
      acceptSnapshot(snapshot);
      form.reset(); $('#create-dialog').close();
      history.replaceState(null, '', `#run=${encodeURIComponent(state.run.id)}`);
      announce(files.length ? `참고 자료 ${files.length}개를 첨부한 뒤 의도 대화를 시작합니다.` : '의도 대화를 시작하고 있습니다.');
      await startCreatedProject(api, state.run, files, { onSnapshot: acceptSnapshot, onUploadAttempt: (attempt) => { state.uploadAttempt = attempt; }, onRunAttempt: (attempt) => { state.commandAttempt = attempt; } });
      announce('의도 대화를 시작했습니다. 필요한 질문이 도착하면 답변해 주세요.');
    } catch (error) {
      if (error.status && error.status < 500) { state.createAttempt = null; state.uploadAttempt = null; state.commandAttempt = null; }
      if (created) announce(`프로젝트는 보존되었습니다. ${error.message} 첨부된 자료와 최신 상태를 확인한 뒤 이어서 진행해 주세요.`, 'warning');
      else handleError(error, $('#create-error'));
    } finally {
      state.pending = false; $('#create-submit').disabled = false;
      if (state.run) { renderRun(); connectStream(state.run.id); }
    }
  } else if (form.id === 'provider-form') {
    event.preventDefault();
    if (state.pending) return;
    try {
      if (state.providerContext?.id !== state.run?.id) throw new Error('프로젝트가 바뀌었습니다. 현재 프로젝트에서 설정을 다시 열어 주세요.');
      const provider = selectedProvider({ provider: form.elements.provider.value });
      if (!provider?.ready) throw new Error('선택한 AI의 설치와 지원되는 로그인 상태를 먼저 확인해 주세요.');
      const previous = state.providerContext.execution;
      if (activeJobs(state.run).length) throw new Error('실행을 취소하거나 마친 뒤 AI 설정을 변경해 주세요.');
      const payload = validateExecutionSelection(provider, readExecutionForm('run-provider'), previous);
      const result = await sendCommand('configure_provider', payload, state.providerContext);
      if (result) { rememberProvider(provider.id); $('#provider-dialog').close(); announce('AI 설정을 저장했습니다. 다음 작업부터 적용됩니다.'); }
    } catch (error) {
      handleError(error, $('#provider-error'));
      if (error.status === 409) {
        try { await refreshRun(); state.providerContext = { id: state.run.id, revision: state.run.revision, execution: executionSelection(state.run) }; }
        catch { /* Keep the original revision until a fresh state can be read. */ }
      }
    }
  } else if (form.id === 'message-form') {
    event.preventDefault();
    const content = form.elements.content.value.trim();
    if (!content || state.pending) return;
    const id = state.run.id;
    try {
      await sendCommand('message', { content });
      state.drafts.delete(id);
      if ($('#message-input')) $('#message-input').value = '';
      renderRun();
      $('#message-input')?.focus();
    } catch { /* Keep the draft visible for a deliberate retry. */ }
  } else if (form.dataset.questionForm) {
    event.preventDefault();
    const answer = form.elements.answer.value.trim();
    if (!answer) return;
    sendCommand('answer', { question_id: form.dataset.questionForm, answer }).catch(() => {});
  } else if (form.dataset.providerInput) {
    event.preventDefault();
    const question = state.run.questions?.find((item) => item.id === form.dataset.providerInput);
    if (!question) return;
    const params = question.provider_params?.params || question.provider_params || {};
    const answers = {};
    for (const [index, item] of (params.questions || []).entries()) {
      const value = (form.elements[`other-${index}`]?.value || form.elements[`answer-${index}`]?.value || '').trim();
      if (!value) return announce('모든 질문에 선택하거나 직접 답변해 주세요.', 'warning');
      answers[item.id] = { answers: [value] };
    }
    sendProviderAnswer(question, { answers });
  } else if (form.dataset.elicitationForm) {
    event.preventDefault();
    if (state.pending) return;
    const question = state.run.questions?.find((item) => item.id === form.dataset.elicitationForm);
    if (!question) return;
    try {
      const response = elicitationResponse(question.provider_params?.params || {}, 'accept', Object.fromEntries(new FormData(form)));
      await sendCommand('provider_answer', providerAnswerPayload(question, response));
    } catch (error) {
      const currentForm = [...document.querySelectorAll('[data-elicitation-form]')].find((item) => item.dataset.elicitationForm === form.dataset.elicitationForm);
      handleError(error, currentForm ? $('.form-error', currentForm) : null);
    }
  } else if (form.id === 'changes-form') {
    event.preventDefault();
    if (state.pending) return;
    const data = new FormData(form);
    try {
      if (state.changeContext?.id !== state.run?.id) throw new Error('프로젝트가 바뀌었습니다. 수정할 프로젝트에서 다시 열어 주세요.');
      const payload = changePayload(state.run, { stage: data.get('stage'), reason: String(data.get('reason')), scope: data.get('stage') === 'design' ? data.get('scope') : 'stage', artifactIds: data.getAll('change_artifact') });
      await sendCommand('request_changes', payload, state.changeContext);
      form.reset(); $('#changes-dialog').close();
    } catch (error) {
      handleError(error, $('#changes-error'));
      if (error.status === 409) {
        try {
          await refreshRun();
          state.changeContext = { id: state.run.id, revision: state.run.revision };
          $('#changes-context').textContent = `${state.run.title} · 최신 개정 ${state.run.revision}의 결과를 다시 표시했습니다.`;
          renderChangeScope();
        } catch { /* The next deliberate retry still carries the old revision. */ }
      }
    }
  }
});

document.addEventListener('change', (event) => {
  if (event.target.name === 'setup-provider') { state.welcomeAfterDiscovery = false; rememberProvider(event.target.value); renderSetup(); $('input[name="setup-provider"]:checked')?.focus(); }
  if (['create-provider', 'run-provider'].includes(event.target.id)) {
    state.choiceGeneration += 1;
    populateExecutionOptions(event.target.id); updateProviderStatus(event.target.id);
  }
  if (['create-provider-model', 'run-provider-model'].includes(event.target.id)) {
    state.choiceGeneration += 1;
    populateExecutionOptions(event.target.id.replace(/-model$/, ''), { model: event.target.value || null });
  }
  if (['create-provider-effort', 'run-provider-effort'].includes(event.target.id)) state.choiceGeneration += 1;
  if (event.target.id === 'attachment-input') upload(event.target.files[0]);
  if (event.target.id === 'changes-stage') {
    $('#changes-form').elements.scope.value = 'stage';
    renderChangeScope([]);
  }
  if (event.target.name === 'scope') renderChangeScope();
});

document.addEventListener('keydown', (event) => {
  if (event.target.id === 'message-input' && event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
    event.preventDefault(); $('#message-form').requestSubmit();
  }
  if (event.target.matches('[role="tab"]') && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
    event.preventDefault();
    const tabs = ['work', 'evidence', 'history'];
    const index = tabs.indexOf(state.tab);
    state.tab = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[2] : tabs[(index + (event.key === 'ArrowRight' ? 1 : 2)) % 3];
    renderRun(); $(`#tab-${state.tab}`).focus();
  }
});

$('#detail-dialog').addEventListener('close', () => {
  state.previewController?.abort();
  if (state.attachmentUrl) URL.revokeObjectURL(state.attachmentUrl);
  state.attachmentUrl = null;
});

window.addEventListener('beforeunload', () => { state.login?.stop(); stopDiscovery(); disconnectStream(); });
window.addEventListener('online', () => { if (!state.authExpired) state.run ? connectStream(state.run.id) : boot(); });
window.addEventListener('hashchange', () => {
  const token = consumeLaunchToken();
  if (token) { bootstrapToken = token; boot(); return; }
  const run = new URLSearchParams(location.hash.slice(1)).get('run');
  if (run && state.connected && run !== state.run?.id && state.runs.some((item) => item.id === run)) selectRun(run);
});

async function boot() {
  preserveDrafts(); state.login?.stop(); state.loginBusy = false; stopDiscovery(); disconnectStream(); state.selection += 1;
  const generation = ++state.bootGeneration;
  const choice = state.choiceGeneration;
  try {
    const token = bootstrapToken;
    bootstrapToken = null;
    await api.connect(token);
    if (generation !== state.bootGeneration) return;
    state.authExpired = false; state.connected = true;
    const results = await Promise.allSettled([api.request('/api/v2/runs'), api.request('/api/v2/capabilities')]);
    if (generation !== state.bootGeneration) return;
    if (results[0].status === 'rejected') throw results[0].reason;
    state.runs = results[0].value.runs || [];
    if (results[1].status === 'fulfilled') state.capabilities = results[1].value;
    else { state.capabilities = {}; if ([401, 403].includes(results[1].reason?.status)) throw results[1].reason; }
    if (choice === state.choiceGeneration && !state.explicitProviderChoice) state.preferred.provider = initialProvider(state.capabilities, state.preferred.provider);
    renderRunList();
    setConnection('connected', '로컬 엔진 연결됨');
    $('#last-updated').textContent = '현재 상태 확인됨';
    const requested = new URLSearchParams(location.hash.slice(1)).get('run') || state.run?.id;
    if (requested && state.runs.some((run) => run.id === requested)) await selectRun(requested);
    else if (state.runs.length) await selectRun(state.runs[0].id);
    else if (!selectedProvider()?.ready) { state.welcomeAfterDiscovery = true; renderSetup(); }
    else renderWelcome();
    if (generation === state.bootGeneration && !state.authExpired) discoverProviders();
  } catch (error) {
    if (generation !== state.bootGeneration) return;
    if ([401, 403].includes(error.status)) { expireSession(); return; }
    state.connected = false;
    setConnection('offline', '로컬 엔진 연결 필요');
    $('#main-content').innerHTML = `<section class="initial-loading"><span class="eyebrow">CONNECTION REQUIRED</span><h1>로컬 엔진에 연결하지 못했습니다</h1><p>${error.status === 401 || error.status === 403 ? '실행 터미널의 접속 링크로 열어 주세요. 처음 연결할 때는 일회용 접속 링크가 필요합니다.' : e(error.message)}</p><p>Intent-Slide 작업실이 실행 중인지 확인한 뒤 다시 연결해 주세요.</p><button class="button primary" style="margin-top:22px" id="retry-connect">다시 연결</button></section>`;
    $('#retry-connect').addEventListener('click', boot);
  }
}

boot();
