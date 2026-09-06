# 저장소만으로 실행하기

이 버전은 **Windows 10·11 x64, Mac Apple Silicon·Intel**용 Python·필수 라이브러리·Codex·LibreOffice·기본 Pretendard 글꼴을 저장소에 포함합니다. Git으로 클론한 뒤 별도 Python, Node.js, npm, Office, 글꼴 설치 없이 시작합니다. Windows x64와 Mac 두 아키텍처의 클론 실행 검사는 공개 CI에서 통과했으며, 실제 플랫폼별 검사 범위는 [검증 현황](VERIFICATION.md)에 구분합니다. Windows N·Windows ARM64·Linux는 이번 지원 대상이 아닙니다.

## 처음 실행

1. `git clone https://github.com/Ryan4090/intent-slide-local.git`으로 받습니다.
2. Windows는 **Start Intent-Slide.cmd**, Mac은 **Start Intent-Slide.command**를 엽니다.
3. 저장소 안의 내장 환경을 준비한 뒤 로컬 브라우저 페이지가 열립니다. 기존 Codex 로그인이 있으면 자동으로 연결합니다. 처음이라면 화면의 **Codex 계정으로 로그인**을 눌러 공식 인증 창에서 본인 계정으로 로그인합니다.

Git clone 자체는 프로그램을 실행하지 않습니다. 실행 파일을 한 번 여는 단계가 필요합니다. Git LFS·submodule·외부 로컬 저장소 참조는 사용하지 않습니다. 포함된 압축 파일과 wheel을 검사·해제하므로 첫 시작은 후속 실행보다 오래 걸립니다. 준비 중 외부 패키지를 다운로드하지 않습니다. AI 로그인·모델 실행·웹 리서치는 인터넷과 해당 서비스 이용 권한이 필요합니다.

현재 내장 배포 파일은 약 **1.9GB**입니다. 클론의 Git 이력, 압축 해제한 실행 환경, 제작한 자료가 추가되므로 디스크 사용량을 배포 파일 크기와 같다고 보지 마세요. 첫 준비가 끝나기 전에 실행 터미널을 닫으면 준비를 마치지 못할 수 있습니다.

내장 Codex는 기본 실행 경로입니다. 이미 준비된 Claude Code, Gemini CLI, OpenCode도 탐색하며 지원 기능과 실제 모델 목록을 보여줍니다. 이 세 도구의 별도 실행 파일이나 계정을 번들에 포함하지는 않습니다. 모든 웹 챗봇이나 모든 서비스의 구독을 서로 바꿔 사용하는 구조가 아닙니다. 세부 범위는 [AI 호환성](AI_COMPATIBILITY.md)을 확인하세요.

## 명령어가 필요한 경우

Mac은 `./intent-slide`, Windows는 `intent-slide.cmd`로 같은 명령을 실행할 수 있습니다.

```sh
./intent-slide start --open
./intent-slide doctor
./intent-slide doctor --connect
./intent-slide start --no-runner --open
./intent-slide start --port 4318 --open
```

기본 명령은 `start`입니다. 기본 포트 4317이 사용 중이면 최대 10개 포트 중 사용 가능한 포트를 고릅니다. 같은 프로젝트 작업실이 이미 실행 중이면 확인된 기존 페이지를 엽니다. 실행 터미널의 `Ctrl+C`로 종료합니다.

자동 연결은 로그인 정보를 읽는 공식 CLI의 준비 응답을 사용합니다. Intent-Slide는 비밀번호나 API 키를 입력받지 않고 인증 파일을 직접 해석하지 않습니다. 첫 로그인과 계정의 안전한 저장은 공식 CLI가 담당합니다. 알 수 없는 과금 방식으로 자동 전환하지 않습니다.

## 파일 위치

| 위치 | 내용 |
|---|---|
| `vendor/portable/` | Git에 포함된 검토된 압축 파일·wheel·원문 고지·대응 소스 파일 또는 공식 링크 |
| `.runtime/portable/` | 저장소 내부에 해제한 Python·Codex·렌더러와 환경 |
| `projects/presentation-agent-suite/.runtime/live/` | 프로젝트·대화·검토·PPTX·미리보기 |
| `.claude/skills/`, `.codex/skills/` | 함께 포함한 제작 규약과 도구 |

전역 패키지·글꼴 설치, 쉘 설정 변경, 보호 기능 해제를 수행하지 않습니다. 공식 AI CLI와 OS 앱의 정상 로그인·캐시 동작은 각 도구가 관리합니다. `.runtime` 전체를 지우면 그 아래의 프로젝트 기록도 삭제될 수 있으므로 실행 오류 해결을 위해 작업 폴더를 무작정 삭제하지 마세요.

`doctor`는 준비 검사 성공 시 0, 준비 미달 시 1, 실행 오류 시 2를 반환합니다. 준비 상태는 실제 슬라이드 제작 완료를 뜻하지 않습니다. 실제 PPTX→PDF→개별 PNG는 G4, 같은 후보의 이미지 검토는 G5가 검증합니다.

기본 SVG 제작 경로는 사용하는 Pretendard 서체를 최종 PPTX에 원본 그대로 포함합니다. Regular·Bold·Light·Medium·SemiBold·ExtraBold 6종을 제공하며 전역 글꼴 폴더에 설치하지 않습니다. 임의의 외부 글꼴까지 자동으로 포함한다는 뜻은 아닙니다. 글꼴 포함은 G4 전에 끝나며, 미리보기용으로만 다른 PPTX를 만들어 통과시키지 않습니다. 다른 PowerPoint 버전에서의 실제 표시·편집과 복잡한 외부 템플릿의 모든 글꼴 상속은 별도 확인 범위입니다.

포함된 도구에는 각각의 라이선스가 적용됩니다. [제3자 고지](../../THIRD_PARTY_NOTICES.md), [런타임 검토](PORTABLE_COMPONENT_REVIEW.md), [렌더러 검토](PORTABLE_RENDERER_REVIEW.md)를 확인하세요.
