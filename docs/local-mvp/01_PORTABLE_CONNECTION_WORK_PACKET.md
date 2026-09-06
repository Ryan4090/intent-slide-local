# Windows·Mac 및 AI 자동 연결

> 2026-09-07 후속 정책: 아래는 다중 AI 연결을 포함했던 당시 구현 기록입니다. 현재 MVP는 Codex 전용이며 자동 연결·신규 선택 기준은 [Codex 연결 개선](02_CODEX_CONNECTION_WORK_PACKET.md)을 따릅니다. 과거 검증 결과를 이번 변경의 검증으로 재사용하지 않습니다.

상태: **COMPLETE — 클론 실행·공통 렌더 구현 및 아래 검증 범위** · 2026-09-06. v0.1.0의 macOS·수동 AI 연결 제한을 개선했고, 한글 포함 최종 변경까지 Windows x64·Mac ARM64·Intel CI가 모두 성공했다. 이번 Mac의 실제 내장 Codex 2장 제작도 G1–G5 VALID·100%를 완료했다. 최종 Release의 공개 파일·체크섬·원격 다운로드는 해당 Release의 별도 발행 기록을 따른다. 아래 표는 실제 통과한 범위와 남은 검사를 구분한다.

## 제품 흐름과 수용 기준

1. Windows `.cmd`, Mac `.command` 실행 파일을 열면 저장소 내부 환경을 준비하고 실제 서버가 준비된 뒤 인증된 로컬 페이지를 연다. Python·의존성·기본 Codex·PPTX 렌더러·기본 글꼴을 일반 Git 파일로 포함하고 준비 중 외부 다운로드나 다른 저장소 참조 없이 실행한다. 별도 Python·Node.js·npm·Office·글꼴 설치를 요구하지 않는다. Git clone 이후 실행 파일을 한 번 여는 단계는 필요하다. 전역 도구를 자동 설치하거나 보안 설정을 해제하지 않는다.
2. 페이지가 설치·로그인된 AI를 자동 탐색한다. 준비된 기존 선호 도구, 서버 추천, 준비된 첫 도구 순으로 선택한다. 초기 모델 연결을 위한 터미널 명령·API 키 입력은 요구하지 않는다. 최초 Codex 계정 로그인은 화면의 버튼이 공식 CLI 브라우저 인증을 시작한다.
3. Codex·Claude의 기존 인증·실행 계약을 보존하면서 공통 에이전트 프로토콜로 AI 선택을 확장한다. 실제 연결과 지원 기능을 확인한 도구만 실행 가능으로 표시한다. 임의의 웹 챗봇까지 모두 자동 연결된다고 주장하지 않는다. 과금 방식이 확인되지 않은 도구로 자동 전환하지 않는다.
4. Windows와 Mac에서 설치 잠금·서비스 잠금·하위 프로세스 통신·취소를 지원한다. 프로젝트와 산출물은 기존 저장소에 보존한다.
5. 실제 PPTX에서 생성한 개별 페이지와 전체 미리보기를 검사한다. 내장 Pretendard 6종 중 사용하는 서체를 최종 PPTX 안에 원본 그대로 포함하고, G4 이전에 내보내기를 끝낸다. SVG 원본 이미지나 글꼴을 나중에 붙인 다른 PPTX를 원본 후보의 렌더 증거로 대체하지 않는다. G1~G5·진행률·현재 산출물 해시 결속을 유지한다.
6. 도구·모델 변경은 새 작업 또는 명시적 설정 변경에만 적용한다. 자동 탐색은 실행 중 작업의 제공자·모델을 변경하지 않는다.

## 구현 범위와 검증

| 범위 | 주요 파일 | 실제 확인 및 남은 검사 |
|---|---|---|
| 설치·페이지 열기 | 실행 파일, portable_bootstrap.py, local_mvp.py | **VERIFIED:** Windows x64·Mac ARM64·Intel의 제한된 기본 PATH에서 클론 준비·내장 CLI·HTTP 시작과 종료. Mac 실제 로컬 페이지 열기. 모든 OS의 파일 더블클릭 화면은 별도 범위 |
| 자동 연결 | provider_registry.py, runner.py, server.py | **VERIFIED:** 없음·일부 준비·여러 도구·지연·실패·실행 중 재탐색의 회귀. Mac에서 실제 Codex 자동 선택·모델 7개·추론 목록 표시. 최초 실제 OAuth는 **UNVERIFIED** |
| 공통 프로세스 | stdio_transport.py, provider.py, claude_provider.py | **VERIFIED:** 3개 플랫폼 CI의 실제 프로세스 통신·취소·자식 종료. 제공자 계정별 모델 동작과는 구분 |
| AI 확장 | acp_provider.py 및 등록된 실행기 | **PARTIALLY_VERIFIED:** 공식 계약·독립 fixture·일부 native 초기화. Claude/Gemini/OpenCode 실제 계정별 전체 제작·G5는 **UNVERIFIED** |
| 렌더 | portable_render.py, font_embedding.py | **VERIFIED:** 3개 플랫폼의 실제 영문 3장 및 한글 PPTX→PNG·순서·해시. PDF의 Pretendard 사용·한글 12개 음절별 실제 PNG 잉크·원본 SHA 불변 확인. Mac ARM64는 실제 PNG 시각 확인과 렌더 전후 앱 서명 보존도 통과 |
| 사용자 화면 | console | **VERIFIED:** Mac의 자동 연결·모델 선택·390px 화면·G1/G2/G3 실제 승인. 콘솔 84개 회귀. OS 파일 선택기·모든 화면/키보드 조합은 **UNVERIFIED** |
| 배포 | builder·Windows/Mac CI·문서 | **VERIFIED:** 일반 Git 파일·고정 SHA·오프라인 준비·allowlist 경계. 현재 배포 파일 약 1.9GB. 최종 Release의 ZIP·체크섬·원격 다운로드는 출시 담당자가 별도 고정 |

기존 2개 native CLI 로그인은 유지한다. 인증 파일을 읽거나 다른 앱의 구독을 API 인증으로 재사용하지 않는다. 변경이 이전 승인 증거를 무효화하면 재검토를 요청하며, 옛 G4/G5를 새 코드의 검증으로 재사용하지 않는다. 실제 계정이 없는 AI의 모델 호출과 해당 계정별 전체 제작은 **UNVERIFIED**로 남긴다.

이번 내장 Codex의 실제 2장 프로젝트는 G1·G2·G3를 작업실에서 승인하고, 내장 LibreOffice의 G4와 현재 이미지에 대한 독립 G5를 통과해 **COMPLETE·100%**를 확인했다. 최종 2장·1920×1080 PNG와 실제 다운로드 HTTP 200도 확인했다. 이전 v0.1.0의 실제 3장·100%는 [역사 기록](VERIFICATION.md#이전-v010-기록)에 보존한다. Windows N·Windows ARM64·Linux와 Microsoft PowerPoint 앱별 실제 표시는 이번 클론 검사의 범위 밖이다.

## 근거

- 원본 `confirm_ui/server.py`와 live preview는 파일 교환·준비 확인 후 브라우저 열기 방식이다. 원본에 모든 AI의 계정 자동 연결 기능이 있다는 전제는 사용하지 않는다.
- Windows 파일 잠금: https://docs.python.org/3.12/library/msvcrt.html
- LibreOffice 실행·프로필 분리: https://help.libreoffice.org/latest/en-GB/text/shared/guide/start_parameters.html
- PDF 변환: https://help.libreoffice.org/latest/en-US/text/shared/guide/pdf_params.html

기본 클론 검사는 [fe5806e · CI 34030351422](https://github.com/Ryan4090/intent-slide-local/actions/runs/34030351422)에서 Windows·Mac ARM·Mac Intel 각 29개 검사로 통과했다. 최종 한글 포함 [5f80a90 · CI 34030968077](https://github.com/Ryan4090/intent-slide-local/actions/runs/34030968077)도 3개 플랫폼 모두 성공했다. Windows는 30개 PASS, Mac ARM·Intel은 각각 29개 PASS와 Windows 전용 1개 SKIP이다. 실제 렌더 5개는 모든 플랫폼에서 SKIP 없이 PASS했다. 완료 결과와 실제 계정별 한계는 [검증 현황](VERIFICATION.md)에 기록한다. 서로 다른 커밋의 검사 수를 합산하거나 옛 완료 증거로 새 후보를 승인하지 않는다.
