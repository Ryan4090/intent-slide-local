# Codex 전용 자동 연결 개선

2026-09-07 · **COMPLETE — 구현 및 아래 검증 범위**. 후속 코드 반영은 Git 커밋으로 추적하며, GitHub Release/태그 발행과 구분한다.

## 목표와 제품 흐름

- **VERIFIED:** 현재 MVP에서 Codex만 자동 연결·신규 선택한다. Claude Code 연동은 MVP 이후로 미루며 Gemini·OpenCode를 선택 화면에서 제외한다.
- **VERIFIED:** HTTP 서비스 시작 시 `--preflight` 없이도 Codex 준비 상태를 백그라운드에서 확인한다. `auto`는 이전 명령 호환용으로 Codex만 의미한다. `--no-runner`는 연결을 실행하지 않는다.
- **VERIFIED:** 기존 Codex 로그인이 있으면 바로 작업실을 사용한다. 탐색 중에는 로그인 버튼을 먼저 표시하지 않는다. 최초 로그인은 공식 CLI가 담당하고 완료된 준비 응답을 받으면 자동으로 작업실에 진입한다.
- **VERIFIED:** 새 프로젝트에는 제공자 선택이 없고 Codex의 실제 모델·추론 옵션을 사용한다. 예전 localStorage의 다른 제공자 선호는 적용하지 않는다.
- **VERIFIED:** 기존 프로젝트/과거 job의 제공자 선택은 자동 변경하지 않는다. 유휴 상태에서 `Codex로 전환 준비` 후 `다음 작업에 적용`을 누르면 모델·추론을 초기화해 명시 전환한다. 취소는 기존 설정을 보존한다.
- **VERIFIED:** 비-Codex 새 HTTP 실행/메시지/승인/답변은 저장 변경 전에 차단한다. 이미 대기 중인 과거 비-Codex job은 파일 준비·CLI 실행 전 BLOCKED로 기록하며 원본 선택을 보존한다. Native adapter와 과거 계약 파싱은 호환용으로 남긴다.

## 변경 파일

- `scripts/local_mvp.py`: 실행/진단 기본값 및 지원 선택을 Codex로 제한.
- `projects/presentation-agent-suite/scripts/presentation_console.py`: HTTP 시작 시 자동 연결 확인.
- `projects/presentation-agent-suite/src/presentation_agents/v2/provider_registry.py`: 과거 계약과 현재 MVP 선택 정책 분리.
- 같은 디렉터리 `runner.py`, `server.py`: 단독 탐색·API/큐 실행 경계.
- `projects/presentation-agent-suite/console/app.mjs`, `product.mjs`, `index.html`, `styles.css`: Codex 전용 연결 화면·자동 진입·고정 제공자·명시 전환·모바일 배치·인증 만료 안내 해제.
- `projects/presentation-agent-suite/console/tests/connection.test.mjs`, `product.test.mjs`, `recovery.test.mjs`: Codex 선택·로그인 상태·지연 응답·기존 선택 보존 회귀.
- `projects/presentation-agent-suite/tests/test_v2_mvp_connection.py`, `test_v2_discovery.py`, `test_v2_execution_selection.py`: 신규 정책과 과거 프로젝트/큐 경계 회귀.
- `tests/test_local_mvp.py`: Codex 런처 계약.
- `README.md`, `docs/local-mvp/INSTALLATION.md`, `AI_COMPATIBILITY.md`, `01_PORTABLE_CONNECTION_WORK_PACKET.md`, 본 문서: 현재 범위와 과거 검증 구분.

## 실제 검증

| 항목 | 결과 및 증거 경계 |
|---|---|
| Red → Green | **VERIFIED:** 변경 전 새 MVP 연결 검사에서 8개 assertion/subtest 실패를 확인. 구현 후 해당 5개 테스트 PASS |
| Python 회귀 | **VERIFIED:** Python 3.12.14, `PYTHONPATH=projects/presentation-agent-suite/src .venv/bin/python -m unittest discover -s projects/presentation-agent-suite/tests -p 'test_v2*.py'`: 283개 실행, 280 PASS·3 SKIP, 78.755초 |
| 프런트 회귀 | **VERIFIED:** `node --test projects/presentation-agent-suite/console/tests/*.test.mjs`: 86 PASS |
| 설치/배포 경계 | **VERIFIED:** `.venv/bin/python -m unittest discover -s tests -p 'test_local*.py'`: 34 PASS |
| 실제 연결 | **VERIFIED:** Mac ARM64, 내장 Codex app-server의 기존 로그인으로 `ready=true`, 모델 7개, Codex 단독 노출. `--preflight` 없이 시작한 HTTP에서 확인. 모델 턴은 실행하지 않음 |
| 브라우저 | **VERIFIED:** Playwright Chromium, 1440×1050 / 390×844. 자동 홈·로그인 버튼 없음(기존 로그인), 최초 대기/로그인 성공 후 자동 홈/실패·시간초과 재시도(fixture), 새 프로젝트 Codex·선택 모델 전달, 오래된 Claude 선호 무시, 명시 전환/취소, JS 오류 0 |
| 시각 검사 | **VERIFIED:** 아래 PNG 직접 확인. 최초 모바일 로그인 버튼의 좁은 줄바꿈을 발견해 세로 배치로 수정 후 새 PNG 재확인 |
| 독립 검토 | **VERIFIED:** 공유 계약에 대한 읽기 전용 검토에서 발견한 legacy UI 복구 경로와 메시지 자동 실행 불일치 2건 수정. 재검토에서 새 P1/P2 없음. 검토자는 테스트를 중복 실행하지 않음 |
| 구문/공백 | **VERIFIED:** app.mjs·product.mjs `node --check`, `git diff --check` 통과 |

스크린샷:
- `output/playwright/codex-connected-desktop.png`
- `output/playwright/codex-first-login-desktop.png`
- `output/playwright/codex-first-login-mobile.png`
- `output/playwright/codex-create-mobile.png`
- `output/playwright/codex-explicit-switch-desktop.png`

재현 스크립트는 비배포 `.runtime/verify-codex-ui.cjs`, `.runtime/verify-codex-followup.cjs`에 보존한다. 첫 스크립트는 새 일회용 QA launch 세션이 필요하다. UI의 최초 OAuth 성공/실패는 응답 fixture이고, 실제 Codex 계정 확인과 구분한다. 새 프로젝트 검사는 QA 저장소에 합성 초안을 만들고 run POST를 가로채 모델 호출을 하지 않았다.

## 미검증·배포 경계

- **UNVERIFIED:** 실제 최초 OAuth를 새 계정으로 완료하는 동작, 변경 후 Windows/Intel Mac 실기 및 전체 PPTX 제작. 이번 변경은 인증 파일이나 native 로그인 구현을 수정하지 않았다.
- **UNVERIFIED / SKIP:** 실제 LibreOffice 렌더 2개(이번 일반 .venv 프로세스에 번들 renderer 환경 미주입), Windows Job Object 1개(Mac 실행). 렌더·Windows 전송 구현은 변경하지 않았다.
- **UNVERIFIED:** 실제 롤백 실행. 기존 프로젝트 자동 마이그레이션이 없고 변경 파일은 Git diff로 검토 가능하지만 복원은 실행하지 않았다.
- **배포 범위:** 코드 커밋·푸시와 Release 발행을 구분한다. 공개 코드에는 허용된 제품 파일만 반영한다. release builder로 로컬 ZIP과 파일 목록을 검증하더라도 기존 v0.2.0 태그·Release 첨부 파일을 변경하는 것은 아니다.
- ego-browser에서 DOM/API 확인은 가능했지만 screenshot/CDP capture가 시간초과되어 이미 설치된 Playwright와 대응하는 기존 Chromium으로 검증했다. 새 의존성/브라우저 설치는 하지 않았다.

관련: [기존 배포 구현 기록](01_PORTABLE_CONNECTION_WORK_PACKET.md), [설치 안내](INSTALLATION.md), [호환 범위](AI_COMPATIBILITY.md).
