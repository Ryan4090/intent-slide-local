import test from 'node:test';
import assert from 'node:assert/strict';
import { intentExperience, interviewAnswer, safeResearchUrl, designGalleryMarkup, interviewMarkup, deliveryMarkup, researchActivityMarkup } from '../experience.mjs';

test('understanding stays tentative until the current G1 approval is valid', () => {
  const run = { intent: { intent_summary: '임원의 투자 결정을 돕습니다', fields: { audience: { value: '임원' } } }, approvals: [{gate:'G1',valid:false}] };
  assert.equal(intentExperience(run).confirmed, false);
  assert.equal(intentExperience({...run, approvals:[{gate:'G1',valid:true}]}).confirmed, true);
  assert.match(intentExperience({request:'첫 이야기'}).summary, /첫 이야기/);
});

test('early intent messages reach the lead panel before a finished interview without claiming confirmation', () => {
  const run = {active_stage:'intent',request:'첫 이야기',messages:[{role:'assistant',stage:'intent',content:'이 제안으로 청중의 어떤 결정을 도울지 정리하고 있습니다.'}]};
  assert.equal(intentExperience(run).summary,run.messages[0].content);
  assert.equal(intentExperience(run).summaryLabel,'지금 정리하고 있는 내용');
  assert.equal(intentExperience(run).confirmed,false);
  run.questions=[{status:'PENDING',intent_summary:'운영 책임자의 시범 도입 결정을 돕습니다.'}];
  assert.equal(intentExperience(run).summary,run.questions[0].intent_summary);
  assert.equal(intentExperience(run).summaryLabel,'제가 이해한 의도');
});

test('interview uses selected answers or a deliberate free answer and refuses unanswered questions', () => {
  const questions = [{id:'audience',question:'누가 보나요?'},{id:'action',question:'어떤 결정을 원하나요?'}];
  assert.throws(() => interviewAnswer(questions, {'answer-audience':'임원'}), /답변/);
  const text = interviewAnswer(questions, {'answer-audience':'임원','answer-action':'예산 승인','other-action':'시범 운영 승인'});
  assert.match(text, /누가 보나요\?\n임원/);
  assert.match(text, /어떤 결정을 원하나요\?\n시범 운영 승인/);
  assert.doesNotMatch(text, /예산 승인/);
});

test('research links reject credentials, scripting and query secrets', () => {
  assert.equal(safeResearchUrl('javascript:alert(1)'), null);
  assert.equal(safeResearchUrl('https://user:secret@example.org/report'), null);
  assert.equal(safeResearchUrl('https://example.org/report?token=secret#part'), 'https://example.org/report');
});

test('interview and delivery markup escape provider text and preserve entered answers', () => {
  const markup = interviewMarkup({id:'q1',question:'방향 확인',intent_summary:'<script>x</script>',questions:[{id:'q',question:'누구?',why:'전달 깊이',options:[{label:'임원',description:'결정 중심'},{label:'실무자',description:'실행 중심'}]}]}, 'r1', {'answer-q':'임원','other-q':'<내 답>'}, false);
  assert.ok(markup.includes('&lt;script&gt;'));
  assert.ok(markup.includes('&lt;내 답&gt;'));
  assert.ok(markup.includes('checked'));
  assert.ok(!markup.includes('<script>'));
  assert.match(deliveryMarkup({core_message:'승인',visual_principles:['비교'],narrative_arc:'문제 → 근거 → 결정'}), /문제 → 근거 → 결정/);
});

test('catalog gallery renders ten selectable images and isolates untrusted preview URLs', () => {
  const catalog = Array.from({length:10}, (_,i)=>({id:`style-${i}`,label:`디자인 ${i}`,preview_url:`/design-previews/style-${i}.svg`,best_for:['설득'],description:'의도 기반',visualization_patterns:['비교']}));
  const html = designGalleryMarkup(catalog, 'style-4');
  assert.equal((html.match(/type="radio"/g)||[]).length, 10);
  assert.equal((html.match(/<img /g)||[]).length, 10);
  assert.match(html, /value="style-4" checked/);
  assert.ok(!designGalleryMarkup([{...catalog[0], preview_url:'https://outside.test/x.svg'}], null).includes('https://outside'));
});

test('research empty state never fabricates activity or saved output', () => {
  assert.match(researchActivityMarkup(null), /확인되지/);
  const html = researchActivityMarkup({summary:{source_files:0,sites:0,claims:0,verified_excerpts:0},sources:[],events:[],storage:{database:'/local/engine.sqlite3',bundle:null},evidence_note:'검증된 자료만 집계',current_action:'검색 시작'});
  assert.match(html, /검색 시작/);
  assert.match(html, /\/local\/engine.sqlite3/);
  assert.ok(!html.includes('research_bundle.v2.json'));
});
