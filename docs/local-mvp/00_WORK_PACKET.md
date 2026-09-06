# Intent-Slide 로컬 MVP 출시 작업

출시 상태: **PARTIAL**. 로컬 실제 Codex 3장 제작은 **COMPLETE · 100%, G1–G5 VALID**다. 사용자가 제품 코드의 MIT 적용을 승인했으며 제3자 자료의 별도 조건은 유지한다. 아래는 문서 동결 시점의 수용 범위다. 최종 ZIP·원격 발행·배포 다운로드 검증 결과는 해당 GitHub Release와 출시 작업이력에 기록한다.

사용자는 GitHub에서 받아 각자 설치한 Codex 또는 Claude Code의 본인 정액제 로그인으로 사용하는 제품을 선택했다. 서비스가 계정 비밀값을 수집하거나 공동 API 계정으로 요청을 대신 결제하지 않는다. 기존 합성 Node 프로토타입은 과거 실험으로 보존하고, 실제 제작 엔진의 진입점과 문서를 별도로 제공한다.

## 출시 범위와 검증

| ID | 사용자에게 제공하는 결과 | 완료를 판정할 증거 |
|---|---|---|
| M1 | GitHub checkout → 저장소 내부 설치 → 준비 진단 → 작업실 시작 | 새 가상환경 설치, 도움말 무부작용, 누락 CLI·로그인·렌더러 진단 |
| M2 | 본인 Codex 또는 Claude Code 로그인 사용 | 공식 비변형 CLI 사용; 실제 인증·짧은 실행; 미설치·미로그인·API 인증 구분 |
| M3 | 실행 도구 선택이 실제 작업과 재개에 적용 | 큐 등록 시 provider/model/effort 고정; 실행 중 변경 거절; 도구 간 세션 격리 회귀 |
| M4 | 대화 → 리서치·분석 → 제작의 단계별 입력·출력과 검토 | 새 프로젝트 사용자 흐름, G1·G2·G3 검토·수정·재승인, 질문 표시 |
| M5 | 전체 %와 5개 체크포인트를 언제든 파악 | 검증된 완료 단위만 계산; stale·실패·복구·완료 화면; 390/1024/1440px 렌더 |
| M6 | 검증된 최종 PPTX 다운로드와 페이지 확인 | 실제 파일 검사와 이미지 열람이 같은 해시·페이지에 결속; release ID 다운로드 |
| M7 | 재현 가능한 배포와 출처 고지 | 450bd21 원본 Git 객체 manifest, 개인정보·runtime 배포 제외, 의존성·폰트 라이선스 고지 |
| M8 | GitHub의 검토 가능한 출시본 | 사용자 지정 저장소·공개 범위에 따른 commit/release 및 다운로드 확인 |

## 현재 수용 결과

2026-09-06 기준이다. 테스트·실제 모델·UI 관찰·배포 검사를 서로 대신하지 않는다. 상세 증거는 [검증 현황](VERIFICATION.md)과 [설치·배포 검사](PACKAGING_CHECKS.md)에 기록한다.

| ID | 상태 | 실제 수용 범위와 남은 조건 |
|---|---|---|
| M1 | **PARTIALLY_VERIFIED** | 이전 후보 ZIP에서 새 가상환경 설치·Codex 진단·서비스 시작·인증 경계·종료 통과. 최종 공개 저장소 clone과 변경 후 새 ZIP 설치는 미검증 |
| M2 | **PARTIALLY_VERIFIED** | 공식 Desktop Codex CLI의 실제 G1–G5 제작 완료, npm 원래 실행기의 실제 파일 생성·읽기 통과. Claude 베타는 초기화·미로그인 차단·회귀까지이며 실제 구독 모델 실행은 미검증 |
| M3 | **PARTIALLY_VERIFIED** | provider 선택·고정·세션 격리의 회귀, 실제 Codex 질문 응답·같은 후보 G5 재시도 확인. 실제 Claude 작업과 제공자 전환 제작은 미검증 |
| M4 | **PARTIALLY_VERIFIED** | 실제 3단계 산출물과 G1·G2·G3 승인 후 G4·G5 완료. 별도 질문 응답 후 INTENT_REVIEW 15% 재개 확인. 모든 수정·재승인 조합의 실제 사용은 미검증 |
| M5 | **PARTIALLY_VERIFIED** | 실제 진행률·3/3 작성·100%·체크포인트·실패 후 재시도·완료 갤러리 확인, console 67개 회귀 통과. 모든 화면·해상도 조합의 최종 렌더는 미검증 |
| M6 | **PARTIALLY_VERIFIED** | 실제 PPTX SHA·1920×1080 페이지 3개·현재 후보 G5 이미지 열람 결속 통과. 다운로드 클릭 후 PPTX HTTP 200 확인. 브라우저 저장 위치와 PowerPoint 앱 조작은 미검증 |
| M7 | **PARTIALLY_VERIFIED** | 이전 ZIP 재현·독립 파일 집합 검사·새 설치 통과, 원본·의존성·폰트 고지 포함. 브랜드 19개 제외·manifest 경계의 최신 21개 회귀 통과. 제품 MIT 선택 완료. 최종 ZIP 포함 파일·체크섬은 발행 단계에서 확인 |
| M8 | **PARTIALLY_VERIFIED** | 새 공개 저장소 생성 VERIFIED. 문서 동결 시점의 코드 push·release·배포 다운로드는 UNVERIFIED이며 원격 확인 대기. 기존 비공개 Git 이력은 발행하지 않음 |

전체 제작의 정상 완료는 이 3장 실제 작업으로 검증했다. OS 파일 선택기 업로드는 자동 UI 검증 도구의 안전 제어로 미검증이며 서버 회귀로 대체하지 않는다. 최초 G5의 제공자 과부하 실패 이력과 같은 후보 재시도 성공을 함께 보존한다. 사용자 안내 prompt의 후속 변경은 12개 조합 정적 검사만 확인했으며 실제 메시지 효과는 미검증이다.

첫 지원 OS는 **macOS**다. 현재 서비스의 POSIX 잠금과 QuickLook 렌더러에 따른 범위이며, Windows 지원 완료로 표시하지 않는다. 모델과 CLI의 요금·정책은 각 제공자에 따르며 설치된 공식 CLI가 로그인과 사용량을 관리한다.

## 구현 경계

- 기존 3단계 엔진의 G1–G5 승인, SQLite 저장, 해시 무결성, CSRF·루프백 경계를 유지한다.
- CLI는 제공자별 adapter로 연결하고 작업의 실행 선택을 불변 snapshot으로 기록한다.
- UI는 한국어 업무 내용, 현재 필요한 행동, 단계별 산출물, 최종 파일을 먼저 보여준다.
- 개인 프로젝트·원시 대화·인증 데이터·실행 DB는 배포하지 않는다.
- 기존 저장소의 연구·실험 자료는 삭제하거나 새 출시의 검증 증거로 재분류하지 않는다.
- 미검증 실모델 실행·브라우저 화면·배포를 unit test 결과만으로 완료 처리하지 않는다.

## 공식 연동 근거

- [Codex app-server](https://learn.chatgpt.com/docs/app-server): 제품에서 공식 프로토콜로 Codex 실행을 연결한다.
- [Codex 인증](https://learn.chatgpt.com/docs/auth): ChatGPT 로그인과 API 키 사용을 구분한다.
- [Claude Code headless](https://code.claude.com/docs/en/headless): 공식 CLI의 print/stream 기능을 사용한다. `--bare`는 정액제 로그인용으로 사용하지 않는다.
- [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance): 사용자가 비변형 Claude Code에 직접 로그인하는 방식과 제3자의 인증정보 중계를 구분한다.

위 링크는 2026-09-06 직접 조회했다. 구현 프로토콜 세부와 실제 설치 버전의 호환성은 별도 실행 검증 대상이다.
