# macOS 설치와 실행

첫 지원 환경은 **macOS + Python 3.12**입니다. Apple Silicon에서 후보 ZIP 추출본의 새 가상환경 설치·잠금 의존성 검사·Codex 연결 진단을 확인했습니다. Intel Mac, Windows, Linux의 실제 사용은 아직 검증하지 않았습니다.

## 준비

1. Git, Python 3.12와 사용할 공식 CLI를 본인이 설치합니다. CLI 설치 방법은 [Codex 공식 안내](https://developers.openai.com/codex/cli/) 또는 [Claude Code 공식 안내](https://code.claude.com/docs/en/setup)를 따릅니다. 제품은 Python, Codex, Claude Code, Swift 또는 시스템 글꼴을 전역 설치하지 않습니다.
2. Codex는 `codex login`, Claude Code는 `claude auth login`으로 본인 계정에 로그인합니다. 로그인 화면과 인증 저장은 공식 CLI가 관리합니다. 제품에 토큰이나 비밀번호를 입력하지 않습니다.
3. QuickLook 파일 렌더를 사용하려면 macOS의 `qlmanage`와 Swift가 필요합니다. 사용 가능한 OfficeCLI가 있으면 해당 렌더 경로를 사용합니다. 도구가 있다는 사실만으로 실제 파일 렌더 성공을 보장하지는 않습니다.

지원 범위: Codex를 기본 실행 도구로 제공합니다. 검증에 사용한 구독은 Codex이며 두 도구를 모두 설치하거나 추가 구독할 필요는 없습니다.

| 실행 환경 | 2026-09-06 실제 확인 범위 |
|---|---|
| Desktop 포함 Codex CLI 0.153.4 | **VERIFIED** — 실제 3장 제작 COMPLETE 100%, G1–G5 VALID. G4의 1920×1080 페이지 3개·G5의 같은 후보 이미지 열람·완료 UI·다운로드 HTTP 200 확인. 모든 주제·실행 환경을 검증한 의미는 아님 |
| npm Codex 0.153.4 | **PARTIALLY_VERIFIED** — 공식 패키지 해시·helper 탐색 회귀 및 원래 실행기를 통한 실제 모델의 검사 파일 생성·읽기 통과. `npm install` 전체 과정·전체 G1–G5 제작은 **UNVERIFIED** |
| Claude Code 2.1.263 베타 | **PARTIALLY_VERIFIED** — 미로그인 차단·프로토콜 초기화·모의 회귀 확인. 검증 환경에 Claude 구독이 없어 실제 모델 제작은 **UNVERIFIED** |

## 저장소 안에서 설치

공개 주소는 [Ryan4090/intent-slide-local](https://github.com/Ryan4090/intent-slide-local)입니다. 터미널에서 다음을 실행합니다. ZIP 배포본을 사용하는 경우에는 해당 GitHub Release의 manifest·체크섬을 확인하고, 압축을 푼 제품 폴더로 이동한 뒤 `setup`부터 실행합니다. 선택한 공식 CLI 로그인은 먼저 완료해야 합니다.

```sh
git clone https://github.com/Ryan4090/intent-slide-local.git
cd intent-slide-local
./intent-slide setup
./intent-slide doctor --provider codex --connect
./intent-slide start --provider codex
```

기본 명령은 `start`이므로 `./intent-slide --provider codex`도 같은 작업실을 시작합니다. Claude를 사용한다면 선택 명령의 `codex`를 `claude`로 바꿉니다. 서비스가 출력하는 로컬 URL을 브라우저에서 엽니다. 종료는 실행한 터미널에서 `Ctrl+C`입니다.

`setup`은 저장소의 `.venv`에만 설치하며 고정 버전과 wheel 해시를 검사합니다. 첫 설치에는 Python 패키지 다운로드를 위한 네트워크 연결이 필요합니다. 두 번째 실행은 현재 버전과 잠금 파일을 확인하고 이미 일치하면 재설치하지 않습니다. 다른 환경이나 불완전한 `.venv`를 자동 삭제하지 않습니다.

```sh
# 계정 연결을 실행하지 않고 설치 조건만 진단
./intent-slide doctor --provider codex

# AI 작업자를 실행하지 않고 로컬 기록과 검토 화면 사용
./intent-slide start --provider codex --no-runner

# 기본 4317 포트가 사용 중인 경우
./intent-slide start --provider codex --port 4318
```

## 진단 결과 읽기

| 항목 | 확인하는 범위 |
|---|---|
| `dependencies` | 잠금된 23개 Python 패키지의 설치 버전 |
| `provider.installed` | 선택 CLI가 PATH에서 실행 가능한지 |
| `provider.auth_status` | 기본 진단에서는 `UNVERIFIED`; 로그인 파일을 직접 읽지 않음 |
| `connection` | `--connect`를 지정했을 때 선택 CLI가 응답한 구독 로그인 준비 상태 |
| `renderer.available` | 렌더 실행 도구의 존재; 실제 후보는 G4에서 별도 검사 |
| `fonts` | 포함 글꼴 고지의 존재; 다른 컴퓨터의 시스템 글꼴 설치와 PPTX 표시는 별도 확인 |

`ready: true`는 표시된 진단 범위의 준비 상태입니다. 슬라이드 제작이나 G4·G5 통과를 뜻하지 않습니다. `doctor`는 성공 시 0, 준비 미달 시 1을 반환합니다. 설치·실행 오류는 2를 반환합니다.

## 저장되는 위치와 범위

- Python 환경: `.venv/`
- 설치 상태와 임시 파일: `.runtime/`
- 프로젝트·대화·작업 상태·산출물: `projects/presentation-agent-suite/.runtime/live/`
- 선택된 제작 규약: `.claude/skills/`; `.codex/skills/`는 해당 규약을 가리키는 생성된 발견용 파일

실행 자료는 로컬이며 배포 ZIP에 포함되지 않습니다. 현재 파일 제작 adapter는 main SVG와 SVG beautify를 지원합니다. template/native 경로는 필요한 실제 검증 adapter가 없으면 차단합니다. QuickLook의 현재 G4는 전체 미리보기와 폭 1920px 이상의 개별 페이지 PNG를 함께 검사하며, G5는 해당 후보의 페이지 이미지를 모두 확인해야 합니다. OfficeCLI는 기존 전체 미리보기 계약을 유지하며 개별 고해상도 페이지 계약은 적용하지 않습니다.

패키지와 글꼴의 조건은 [제3자 고지](../../THIRD_PARTY_NOTICES.md)를 확인하세요. 특히 PyMuPDF의 별도 AGPL/상용 라이선스 조건은 upstream MIT 고지와 구분됩니다.

제품 코드의 라이선스는 [MIT](../../LICENSE)로 선택 완료됐으며 제3자 자료의 별도 조건은 유지됩니다. 브랜드 파일 19개 제외와 배포 경계 회귀를 반영했습니다. 이 문서의 새 설치 증거는 이전 기술 검증용 ZIP에 한정하며, 최종 배포본과 원격 다운로드 검증 결과는 해당 GitHub Release와 출시 작업이력에 기록합니다.

OS 파일 선택기를 통한 UI 업로드는 자동 검증 도구의 안전 제어로 검사하지 못했습니다. 서버 업로드 회귀 통과와 구분합니다. 최종 PPTX의 다운로드 응답은 확인했지만 브라우저 저장 위치는 미검증입니다. 자세한 증거와 남은 검사는 [검증 현황](VERIFICATION.md)을 확인하세요.
