# Windows·Mac 및 AI 자동 연결

상태: **PARTIAL**. 2026-09-06 사용자 요청으로 v0.1.0의 macOS·수동 AI 연결 제한을 개선한다. 아래 검사는 구현 후 수행할 계획이며 아직 통과한 증거가 아니다.

## 제품 흐름과 수용 기준

1. Windows `.cmd`, Mac `.command` 실행 파일을 열면 저장소 내부 환경을 준비하고 실제 서버가 준비된 뒤 인증된 로컬 페이지를 연다. Python·의존성·기본 Codex·PPTX 렌더러를 일반 Git 파일로 포함하고 외부 다운로드나 다른 저장소 참조 없이 실행한다. Git clone 이후 실행 파일을 한 번 여는 단계는 필요하다. 전역 도구를 자동 설치하거나 보안 설정을 해제하지 않는다.
2. 페이지가 설치·로그인된 AI를 자동 탐색한다. 준비된 기존 선호 도구, 서버 추천, 준비된 첫 도구 순으로 선택한다. 초기 모델 연결을 위한 터미널 명령·API 키 입력은 요구하지 않는다. 최초 Codex 계정 로그인은 화면의 버튼이 공식 CLI 브라우저 인증을 시작한다.
3. Codex·Claude의 기존 인증·실행 계약을 보존하면서 공통 에이전트 프로토콜로 AI 선택을 확장한다. 실제 연결과 지원 기능을 확인한 도구만 실행 가능으로 표시한다. 임의의 웹 챗봇까지 모두 자동 연결된다고 주장하지 않는다. 과금 방식이 확인되지 않은 도구로 자동 전환하지 않는다.
4. Windows와 Mac에서 설치 잠금·서비스 잠금·하위 프로세스 통신·취소를 지원한다. 프로젝트와 산출물은 기존 저장소에 보존한다.
5. 실제 PPTX에서 생성한 개별 페이지와 전체 미리보기를 검사하는 공통 렌더 경로를 추가한다. SVG 원본의 이미지를 PPTX 렌더 증거로 대체하지 않는다. G1~G5·진행률·현재 산출물 해시 결속을 유지한다.
6. 도구·모델 변경은 새 작업 또는 명시적 설정 변경에만 적용한다. 자동 탐색은 실행 중 작업의 제공자·모델을 변경하지 않는다.

## 구현 범위와 검증

| 범위 | 주요 파일 | 필수 검사 |
|---|---|---|
| 설치·페이지 열기 | 실행 파일, local_mvp.py, presentation_console.py | 실제 Mac 시작, 경로 공백·한글, 포트 충돌, Windows 실행 검사 |
| 자동 연결 | provider_registry.py, runner.py, server.py | 없음·일부 준비·여러 도구·지연·실패·실행 중 재탐색·인증/CSRF |
| 공통 프로세스 | provider.py, claude_provider.py, 공통 통신 | 큰 출력·정지한 쓰기·취소·자식 종료·권한·실제 Windows 프로세스 |
| AI 확장 | 새 프로토콜 어댑터 | 공식 메시지 계약·인증 필요·사용자 승인·현재 이미지 관측·취소 |
| 렌더 | verification.py, 공통 렌더 모듈 | 실제 PPTX→PDF→PNG·페이지 수/순서·해시·신선도·오류/취소 |
| 사용자 화면 | console | 자동 연결 및 실패 화면, 모델 선택, 반응형·키보드·세션 만료 |
| 배포 | builder·Windows/Mac CI·문서 | 잠금 의존성, 실제 플랫폼 회귀, 배포 allowlist·라이선스·원격 ZIP |

기존 2개 native CLI 로그인은 유지한다. 인증 파일을 읽거나 다른 앱의 구독을 API 인증으로 재사용하지 않는다. 변경이 이전 승인 증거를 무효화하면 재검토를 요청하며, 옛 G4/G5를 새 코드의 검증으로 재사용하지 않는다. 실제 계정이 없는 AI의 모델 호출과 해당 계정별 전체 제작은 **UNVERIFIED**로 남긴다.

## 근거

- 원본 `confirm_ui/server.py`와 live preview는 파일 교환·준비 확인 후 브라우저 열기 방식이다. 원본에 모든 AI의 계정 자동 연결 기능이 있다는 전제는 사용하지 않는다.
- Windows 파일 잠금: https://docs.python.org/3.12/library/msvcrt.html
- LibreOffice 실행·프로필 분리: https://help.libreoffice.org/latest/en-GB/text/shared/guide/start_parameters.html
- PDF 변환: https://help.libreoffice.org/latest/en-US/text/shared/guide/pdf_params.html

완료 증거와 실제 플랫폼별 한계는 구현·검사 이후 이 문서 및 배포 Release에 반영한다.
