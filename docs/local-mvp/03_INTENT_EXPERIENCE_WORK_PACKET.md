# 의도를 먼저 확인하는 제작 경험

구현·검증: VERIFIED · 공개 반영 승인됨 · 2026-09-08

## 제품 판단과 범위

핵심 가치는 사용자가 원하는 청중의 변화와 행동을 질문으로 구체화하고, 그 의도가 조사 질문·근거·슬라이드 구성·시각화까지 이어지는 데 있다. 질문은 입력 폼을 채우기 위한 절차가 아니라 전달 방향을 결정하는 제품 경험으로 보여준다. “의도 100% 반영”은 무검증 보증 문구로 쓰지 않고 사용자가 요약과 전달 전략을 수정·승인할 수 있는 흐름으로 구현한다.

기존 로컬 앱과 세 단계/G1–G5, 승인 해시, 사용자 자료, 실행 모델·권한 경계를 보존한다. 신규 의존성, 인증 변경, 기존 출시 ZIP 수정은 범위 밖이다. 사용자는 검증된 제품 변경의 공개 저장소 기본 브랜치 커밋·푸시를 승인했다. 착수 당시 tracked diff는 깨끗했고 기존 미추적 materials/releases/workstreams와 과거 화면 증거는 보존했다.

## 수용 기준과 증거

| 요구 | 구현 위치 | 완료를 확인할 검사 |
|---|---|---|
| 의도 중심 가치 제안, 빠른 요약과 적절한 질문 | console 경험 모듈·app, intent prompt | 첫 질문 묶음·자유 답변·초안 보존·좁은/넓은 화면 |
| 조사 전 의도와 전달 전략 확인 | intent 계약·G1 검토 | 호환/신규 계약, 미승인 조사 차단, 입력 변경 회귀 |
| 실제 사이트·자료·발췌량·현재 활동·저장 위치 | 연구 activity projection·runner·server·console | 실제 이벤트와 파일에 근거한 집계, 실패/재시도/무효 파일, API·화면 |
| Slide Master 참고 10개 디자인 선택, SVG 및 의도 연결 | catalog·preview assets·선택 명령·디자인 prompt | 10개 distinct preview 렌더, 선택 저장/변경 경계, source reference 존부 |
| 의도에 맞는 시각화와 구조화 | 전달 전략·슬라이드 visual_intent·design prompt | 계약·prompt 검증, 선택과 의미의 추적 표시 |

기존 프로젝트 읽기와 신규 필드의 선택적 확장을 확인한다. 루트가 UI·API 통합, 독립 작업자가 의도 계약·연구 활동·디자인 catalog를 나누어 구현하고 공유 계약을 교차 검토한다. 모델 fixture와 실제 계정 제작, 브라우저 렌더 결과를 별도로 보고한다.

## 검증 기록

| 검증 | 실제 결과와 범위 |
|---|---|
| Python V2 전체 회귀 | **VERIFIED:** Python 3.12.14, 324개 실행·323 PASS·Windows Job Object 전용 1 SKIP, 84.921초 |
| 프런트 회귀 | **VERIFIED:** Node.js, 93 PASS. 질문 응답·초안·출처 URL·10개 선택 markup·초기 메시지 표시 포함 |
| 로컬 설치·배포 경계 회귀 | **VERIFIED:** `test_local*.py` 34 PASS, 8.196초. 새 릴리스 ZIP 생성 검증과 구분 |
| 구조화 질문 | **VERIFIED:** 누락 응답 오류, 3개 선택, 직접 입력의 우선 반영, 상태 갱신 후 초안 보존, 실제 답변 저장. 미승인 연구 차단은 엔진 회귀 |
| 초기 이해와 G1 | **VERIFIED:** 초기 assistant 메시지가 질문 완료 전에 상단에 표시. 이해 요약·전달 전략 6항목·각 페이지의 목적과 시각화가 현재 G1 검토에서 표시됨 |
| 디자인 선택 | **VERIFIED:** 브라우저에서 10개 선택 모두 조작, 실제 선택 저장, 생성창 재개 시 선택 보존, 다른 클라이언트의 변경 수신 시 미편집 radio가 최신값을 따름. G1/G2 보존·G3 재검토·잘못된 preset_id 거절은 엔진/API 회귀 |
| 조사 가시성 | **VERIFIED:** fixture의 실제 로컬 원문 3개·사이트 2개·위치 확인 발췌 3개·주장 3개 표시, 원문 열기, 상세 활동·DB/원문/번들 경로 표시, 갱신 후 펼침 보존. 웹 동작 자체는 공식 이벤트 형식 fixture |
| 렌더·접근성 | **VERIFIED:** CUA 브라우저 1280/1440px와 390×844px에서 질문·디자인·G1 읽기 확인, 가로 넘침 없음. 탭 End 키로 마지막 작업 기록 이동, 콘솔 오류·경고 0. 별도 Chromium 미리보기 10개 렌더·텍스트 캔버스 이탈 0 |
| 구문·공백 | **VERIFIED:** `node --check` 및 `git diff --check` 통과 |
| 독립 검토 | **VERIFIED:** 생성 재시도의 디자인 선택 누락, 실패 후 조사 안내, 생성창 선택 초기화, 목록 로드 실패 후 재요청, 탭 End, 미편집 선택의 SSE 동기화 문제를 수정. 마지막 공유 계약·provider 검토에서 새 차단 문제 없음 |

## 실제 Codex 의도 대화의 지연

**VERIFIED:** 기존 provider는 추론 설정을 지정하지 않으면 지원되는 가장 높은 강도를 선택했다. 실제 첫 실행은 ultra 요청이었으며, 화면의 기본 설정 안내와 달랐다. 이제 명시적 설정을 보존하고, 미선택 시 모델 목록의 지원되는 `defaultReasoningEffort`를 사용한다. 권장 정보가 없으면 override를 생략하고 현재 연결된 Codex 설정을 유지한다. UI는 이를 **모델 권장 추론 설정**으로 표시한다. [공식 App Server 문서](https://learn.chatgpt.com/docs/app-server)의 모델 기본 정보와 턴별 effort override 계약을 확인했다.

**PARTIALLY_VERIFIED:** 같은 합성 요청을 같은 기본 모델로 두 번 실행했다. 변경 전 질문 도착 112.89초, 변경 후 medium을 요청한 실행의 의도 설명 14.60초·질문 도착 34.97초를 관측했다. 전송한 설정과 관측 시간을 기록하며 일반 응답속도나 처리량을 보장하지 않는다. 세 질문은 청중·원하는 결정·걸림돌을 다뤘고 질문별 전달 영향과 자유 응답을 제공했다. 요청한 3–4장 제약을 유지했다. 두 실행 모두 연구·G1 승인·추가 실행 권한 요청 없이 질문에서 종료했다.

## 변경 파일과 남은 경계

### 대표 디자인의 실제 SVG·PPTX 제작

**VERIFIED:** 격리 Engine에서 합성 한국어 2장과 테스트용 G1/G2/G3 승인을 사용하고, 실제 Native Codex로 아키텍트(system)의 디자인 방향→순차 SVG 작성→노트·PPTX 내보내기→G4 실제 렌더→G5 독립 이미지 검토를 실행했다. 사용자 프로젝트나 승인에는 접근하지 않았다. 참고 ai_ops의 밝은 적청색 구성과 달리 선택한 짙은 청록·민트 3색이 실제 PPTX에 적용됐다. 승인된 제목·순서·사람의 최종 검토 원칙·다음 행동·합성 시나리오 표시를 확인했다.

G4는 macOS Quick Look/WebKit으로 현재 PPTX의 1920×1080 이미지 2장을 렌더하여 PASS했다. G5는 현재 contact sheet와 개별 페이지 2개를 실제 imageView 도구로 관찰한 뒤 PASS했으며, 서비스의 최종 상태는 COMPLETE였다. 루트도 개별 이미지 두 장을 열어 한국어 가독성·배치·잘림·겹침을 확인했다. PPTX XML 독립 검사에서 2장·노트 2개·편집 가능한 도형/텍스트 22개와 19개·선택 3색·합성 표시를 확인했고, 유효 산출물 24개의 현재 SHA가 일치했다.

**검사 도구 경계:** 외부 모니터가 자동으로 시작된 G5까지 따라간 뒤 완료 상태에 추가 실행을 요청하여 종료 코드 1이 발생했다. 제품은 추가 명령을 거절했고 COMPLETE와 산출물을 보존했다. 모델 작업은 재실행하지 않았다. 모니터의 특정 작업 추적·완료 상태 처리를 수정했으나 수정본 전체 실행은 반복하지 않았다. 위 결과는 종료 코드가 아니라 실제 SQLite 작업·G4/G5 영수증·이미지 관찰·파일 해시로 재구성하여 확인했다.

이는 대표 1종의 실생성 증거다. 10종 각각의 실생성, 외부 자료조사, PowerPoint/Windows/Intel Mac에서의 표시, 실제 해석 글꼴과 청중 설득 효과를 입증하지 않는다. 검증용 원문·DB·샘플·실행 기록은 `.runtime/intent-experience-end-to-end/`에만 보관하고 공개 변경에서 제외한다.

### 제품 변경 범위

- `console/experience.mjs`, `app.mjs`, `product.mjs`, `index.html`, `styles.css`, `tests/experience.test.mjs`: 의도 중심 시작·질문·전략·연구 활동·디자인 선택 UI.
- `console/design-previews/*.svg`, `v2/design_catalog.py`, `test_v2_design_catalog.py`: 10개 독자 디자인과 포함된 Slide Master 구조 참고.
- `v2/contracts.py`, `engine.py`, `prompts.py`, `runner.py`, `test_v2_intent_experience.py`, `test_v2_design_identity.py`: 질문과 전략, 디자인 선택의 입력·G3 결속, 단계별 시각화 지침.
- `v2/research_activity.py`, `server.py`, `test_v2_research_activity.py`, `test_v2_experience_api.py`: 실제 활동 및 검증된 근거 projection과 인증된 API.
- `v2/store.py`, `test_v2_design_creation.py`: 생성 재시도의 선택 보존.
- `v2/provider.py`, `test_v2_provider.py`: 명시한 추론 설정과 모델 권장값 사용.
- 제품/console README 및 이 문서: 사용 흐름과 검증 경계.

**UNVERIFIED:** 변경 후 실제 전체 외부 자료조사→10종 각각의 SVG/PPTX→G4/G5 제작, Windows·Intel Mac에서 이번 변경의 실기 실행, 실제 롤백 실행. 렌더러 자체는 수정하지 않았으며 대표 1종의 실생성과 단위검사·미리보기 렌더를 모든 디자인의 품질 보증으로 대체하지 않는다.

**VERIFIED — 공개 반영 범위:** 공개 저장소 기본 브랜치의 기존 대상 코드와 로컬 기준 파일이 같은 Git blob인 것을 확인했다. 승인된 반영 대상은 배포 allowlist에 속하는 제품 파일 35개이며, 공개 저장소의 이력에서만 새 커밋을 만든다. 비공개 저장소 이력, 실제 사용자 대화, runtime DB, 검증용 화면·모델 실행 기록은 공개 변경에 포함하지 않는다. 공개 커밋의 실제 반영 여부는 원격 브랜치와 커밋의 파일 해시를 대조하여 확인한다.
