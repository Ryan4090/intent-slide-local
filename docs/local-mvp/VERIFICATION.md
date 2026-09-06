# 내장 런타임 버전 검증 — 2026-09-06

구현·검증 상태: **COMPLETE**. 클론 실행·한글 렌더의 3개 플랫폼 검증과 Mac의 실제 내장 Codex 2장 제작 G1–G5·100%를 완료했다. 최종 공개 ZIP·체크섬·원격 다운로드 결과는 해당 Release에 별도로 기록한다. **CI의 실행 성공, 실제 모델의 제작 완료, GitHub Release 발행은 서로 다른 상태**다. 뒤의 v0.1.0 기록은 이전 버전의 증거이며 현재 후보의 승인을 대신하지 않는다.

## 현재 플랫폼 실행 증거

| 커밋·검사 | Windows x64 | Mac ARM64 | Mac Intel | 실제 확인한 범위 |
|---|---|---|---|---|
| `fe5806e` · [기본 클론 CI 34030351422](https://github.com/Ryan4090/intent-slide-local/actions/runs/34030351422) | **VERIFIED · SUCCESS · 29 tests** | **VERIFIED · SUCCESS · 29 tests** | **VERIFIED · SUCCESS · 29 tests** | 제한된 기본 PATH에서 저장소의 Python·의존성·Codex·LibreOffice 준비, 내장 Codex 0.153.4 실행, 실제 영문 3장 PPTX→고해상도 PNG, HTTP 세션·CSRF·서버 종료 |
| `5f80a90` · [한글 포함 CI 34030968077](https://github.com/Ryan4090/intent-slide-local/actions/runs/34030968077) | **VERIFIED · SUCCESS · 30 PASS** | **VERIFIED · SUCCESS · 29 PASS / 1 SKIP** | **VERIFIED · SUCCESS · 29 PASS / 1 SKIP** | 최종 PPTX의 글꼴 포함·한글 실제 렌더 추가. 모든 플랫폼에서 렌더 5개 SKIP 없이 PASS. Mac의 SKIP은 Windows Job Object 전용 검사 |

최신 CI의 플랫폼별 30개 검사 구성은 공통 전송 11·로그인 4·탐색 4·서비스 6·렌더 5다. 한글 검사는 PDF의 `Pretendard-Regular` 사용과 12개 음절별 실제 PNG 잉크, 제출 PPTX SHA 불변을 확인한다. CI의 임시 PNG 전체를 사람이 시각 검토한 것은 아니며 모든 글꼴·문서에 대한 일반적 보증으로 확대하지 않는다.

이 CI는 AI 계정이나 모델을 호출하지 않는다. Windows는 `windows-latest`, Mac은 `macos-15`·`macos-15-intel`의 GitHub runner에서 수행했다. OS 자체 기능과 runner의 기본 구성이 존재하므로 모든 새 PC·OS 패치·보안 제품 조합을 검증한 것은 아니다. Windows의 공식 MSI에 포함된 동일 VC++ DLL을 앱 전용 경로에 두며 CI에서 필수 DLL 3종의 존재를 확인한다. Windows 10·11 x64가 제품 대상이고 Windows N·Windows ARM64·Linux는 이번 지원·검증 범위 밖이다.

## 현재 제품·제작 증거

| 주장 | 실제 확인 범위 |
|---|---|
| 별도 개발 도구 설치 없는 준비 | **VERIFIED:** 위 3개 플랫폼에서 내장 환경 준비. Python·Node.js·npm·Office·기본 글꼴의 별도 설치 없이 실행한다. 준비는 저장소 파일만 사용하며 모델 실행·로그인·웹 리서치는 인터넷과 본인 계정이 필요 |
| 기본 글꼴을 담은 최종 PPTX | **VERIFIED — 3개 플랫폼 CI 및 Mac ARM64 실물:** main SVG 내보내기가 실제 사용한 Pretendard 원본을 비압축 EOT와 원문 라이선스로 최종 PPTX에 포함. PDF의 실제 `Pretendard-Regular` 사용, 한글 12개 음절별 PNG 잉크, 렌더 전후 PPTX SHA 동일 확인. Mac의 글꼴 없는 고유 서체 대조군은 한글 픽셀이 사라졌으므로 텍스트 추출만으로 통과시키지 않음 |
| 원본 앱 서명 보존 | **VERIFIED — Mac ARM64:** 원본 archive SHA를 검증한 새 앱 사본에서 렌더 전후 `codesign --verify --deep --strict` exit 0. `PYTHONDONTWRITEBYTECODE=1` 유지. 보호 없이 직접 실행한 초기 진단에서 pycache 갱신이 발생한 이력과 구분 |
| 모델 자동 발견·선택 | **VERIFIED — Mac ARM64:** 페이지 최초 방문에서 Codex 준비·자동 선택, 실제 모델 7개와 추론 목록 표시 |
| 이번 실제 Codex 제작 | **VERIFIED — Mac ARM64:** 내장 Codex 0.153.4, GPT-6-Astra/ultra의 합성 2장 프로젝트. G1·G2·G3 실제 UI 승인, G4·G5 VALID, **COMPLETE·100%**. 최종 PPTX와 1920×1080 PNG 2장을 확인했고 다운로드 클릭에 HTTP 200 응답 |
| 현재 UI | **VERIFIED — 출시 담당자 관찰:** 자동 연결·프로젝트 생성·모델/추론 선택·단계 진행·G1/G2/G3 승인. 390px 화면 가로 넘침 없음. 최신 화면의 이전 검사 기록 펼치기와 사용자 대화 원문 표시 확인. 로그인 대기/실패/재시도는 모의 HTTP 응답 검사이며 실제 계정 최초 OAuth는 **UNVERIFIED** |
| 회귀 | **VERIFIED:** 전체 Python 263개 검사 결과 OK, Windows 전용 1개는 Mac에서 SKIP. 콘솔 84개 PASS. 후속 엔진 대상 10개, 글꼴 패키징 13개, 렌더 대상 5개 PASS를 별도 실행으로 기록. 서로 중복되며 합산하지 않음 |
| 기타 AI·계정 | **PARTIALLY_VERIFIED:** Claude/Gemini/OpenCode의 계약·일부 초기화·fixture. 실제 계정별 전체 제작·G5는 **UNVERIFIED**. [AI 호환 범위](AI_COMPATIBILITY.md)의 실행기별 제한을 따름 |

이번 실제 후보의 PPTX SHA-256은 `e7263bd2a026e9d52e021342de3b311f79ecdded1303d78896a61341edd659a2`다. G4는 내장 LibreOffice에서 실제 페이지 2장을 만들었고 G5는 동일 후보의 전체 미리보기와 두 페이지를 도구로 열어 검토했다. root도 두 이미지를 직접 확인했다. 합성 기준 100분·목표 80분, 담당자 지정·주간 측정·2주 뒤 재검토가 유지되며 실제 성과로 단정하지 않는다. 파일의 실제 치수는 16:9이고 내부 비율 이름의 불일치는 작업자 기록에 보존했다. PowerPoint 앱의 직접 표시 검사와 브라우저 다운로드의 최종 저장 위치는 **UNVERIFIED**다.

내장 글꼴은 Regular·Bold·Light·Medium·SemiBold·ExtraBold 6종이다. 글꼴 포함은 최종 내보내기와 G4 이전에 끝나며, G4용 복사본에 뒤늦게 글꼴을 붙이지 않는다. 명시적 글꼴을 사용하는 main SVG 경로와 일반 `pPr`/`lstStyle` 상속을 확인했으며, 임의의 외부 템플릿에서 가능한 모든 상속·외부 글꼴까지 검증한 것은 아니다. Microsoft PowerPoint 앱별 표시·편집과 실제 발표 시간도 **UNVERIFIED**다.

현재 내장 배포 파일은 약 **1.9GB**이며 Git 이력·압축 해제한 환경·사용자 산출물이 차지하는 디스크 공간은 별도다. 원격 코드·CI 확인을 최종 Release ZIP·체크섬·원격 다운로드 검증으로 간주하지 않는다. 최종 발행 결과는 출시 담당자가 해당 Release와 작업이력에 고정한다.

검증 문서를 갱신하며 통과한 검사를 반복 실행하지 않았다. 공개 CI의 완료 상태와 담당자의 원시 검사 로그·실제 UI 관찰을 근거로 구분해 기록했다. 로컬 한글 증거는 `final-helper-tests.log`·`final-font-tests.log`·`implementation-evidence.json`·최종 candidate/PNG·서명 검사 전후 로그이며 실행 폴더와 개인 작업 데이터는 공개 배포에 넣지 않는다.

## 이전 v0.1.0 기록

> 아래는 v0.1.0 당시의 증거와 미검증 상태를 보존한 역사 기록이다. 여기의 100%·QuickLook·운영체제·폰트·원격 발행 상태를 현재 내장 런타임 버전에 적용하지 않는다.


### 당시 로컬 MVP 검증 현황

2026-09-06 · 로컬 제작 검증 **COMPLETE · 100%**, 원격 발행 확인은 **UNVERIFIED**로 전체 출시 상태는 **PARTIAL**입니다. 실제 Codex 3장 제작의 G1–G5가 모두 VALID이며 최종 파일 검사와 이미지 검토를 통과했습니다. 제품 코드의 MIT 라이선스 선택은 완료됐습니다. 최종 ZIP·원격 발행·배포 다운로드 결과는 해당 GitHub Release와 출시 작업이력에 기록합니다.

### 증거 수준

읽어 확인한 원시 검사 로그·파일 해시와 출시 담당자의 실제 UI 관찰 결과를 구분합니다. 테스트 집계는 서로 다른 시점·대상의 실행이며 중복되는 테스트가 있으므로 합산하지 않습니다. Fixture와 mocked subprocess의 PASS를 실제 모델 실행이나 OS 화면 검증으로 대신하지 않습니다.

| 대상 | 확인 결과 | 검증의 경계 |
|---|---|---|
| v2 Python 회귀 | **VERIFIED** — 기존 전체 실행 206 tests, 79.090s, OK | 해당 실행 시점의 결과. 이후 provider·console·배포 변경을 모두 포함하는 최종 전체 검사로 확대하지 않음 |
| Codex·Claude·선택 registry | **VERIFIED** — 대상 46 tests, 7.979s, OK | npm helper 탐색·실패 차단, 세션·취소·질문·권한 처리의 fixture 회귀. 전체 실모델 제작과는 별개 |
| 설치·배포 | **VERIFIED** — 최신 원시 로그 21 tests, 1.029s, OK | 실제 Git tree→tar→ZIP·브랜드 제외·manifest 경계 포함. 설치 subprocess fixture와 실제 fresh 설치 증거는 분리 |
| Console | **VERIFIED** — 최종 원시 로그 67 tests / 67 pass / 0 fail / 0 skipped, 78.6795ms | JavaScript 회귀이며 실제 OS 파일 선택기·모든 화면 렌더의 증명은 아님 |
| 후보 ZIP·fresh 설치·실행 | **VERIFIED** — 동일 SHA 반복 생성, 독립 경로·개인자료 검사, 추출본의 새 `.venv`·Codex 진단·서비스 시작·HTTP 인증 경계·종료 성공 | `b3375d79d6d5` 기술 후보에 한정. 브랜드 제외와 이후 변경을 반영한 최종 공개 ZIP은 별도 검사 |

### 실제 Codex 실행과 세 단계 진행

Desktop에 포함된 Codex CLI 0.153.4와 본인 Codex 구독으로 새 제품의 의도·리서치·디자인·최종 제작을 완료했습니다. 출시 담당자가 실제 작업실의 승인과 **20→40→50→55→63→65%**, 원본 작성 **3/3**, 최종 **100%** 화면을 확인했습니다. 원본 작성 수는 실행 진척이며 최종 G4·G5 완료율로 환산하지 않습니다.

| 사용자 단계 | 실제 확인 결과 | 검증 범위 |
|---|---|---|
| ① 의도·내용 협의 | **VERIFIED** — 실제 모델 산출물과 UI G1 승인 | 별도 인터뷰의 청중 질문에 답한 뒤 INTENT_REVIEW 15% 재개도 확인 |
| ② 리서치·분석 | **VERIFIED** — 실제 결과와 UI G2 승인, 실행 중 진행률 변화 | 이 3장 제작의 승인된 입력·출력과 최종 검토에 한정 |
| ③ 디자인·제작 | **VERIFIED** — G3 승인, 원본 3/3, G4·G5 PASS, COMPLETE 100% | 실제 PPTX·1920×1080 PNG 3개·현재 후보 이미지 검토·다운로드 응답 확인 |

최종 PPTX의 SHA-256은 `3b40c8461d840857e9abf049ecf2b896ed97f6b745895d6c4375cd46905440ed`입니다. 실제 파일의 해시를 G4 receipt·G5 리뷰와 대조했고, 개별 PNG 3개의 크기와 SHA도 receipt와 일치했습니다. G5는 해당 후보의 전체 미리보기와 3개 페이지를 실제 도구로 열람했고 발견 사항 없이 PASS했습니다. 최초 G5는 제공자의 `serverOverloaded`로 실패했으며, 같은 후보를 UI에서 재시도한 뒤 성공했습니다. 실패 이력은 보존했습니다.

현재 QuickLook G4는 전체 미리보기와 폭 1920px 이상의 개별 페이지를 같은 PPTX에 결속하고, G5는 현재 후보의 모든 페이지 이미지 열람을 요구합니다. OfficeCLI는 기존 전체 미리보기 계약을 유지하며 고해상도 개별 페이지 검증은 미지원입니다.

별도의 실제 PPTX 내부 검사는 슬라이드 3개·내용 있는 발표 노트 3개, 주요 수치의 편집 가능한 텍스트, 합성 예시 고지, 애니메이션·전환·그림 노드 부재를 확인했습니다. 이는 이미지 G5와 별도 증거이며 PowerPoint 앱 조작이나 5분 발표 시간 검증을 뜻하지 않습니다.

공식 npm Codex 0.153.4 배포도 독립 검사했습니다. 공개 패키지 해시와 원래 launcher·helper의 무결성을 확인한 뒤, 해당 launcher를 통해 실제 모델이 격리된 검사 파일을 생성하고 다시 읽었습니다. 결과 파일의 내용·크기·SHA가 예상값과 일치했고 작업이 완료됐습니다. 제품 소스와 검사한 실행 파일은 변하지 않았습니다. 이 **단일 파일 생성·읽기 검사는 VERIFIED**이며, `npm install` lifecycle 전체와 npm 경로의 전체 G1–G5 제작은 **UNVERIFIED**입니다. 전역 설치는 수행하지 않았습니다.

### UI와 미검증 범위

- **VERIFIED:** 출시 담당자의 실제 UI 승인·진행률·재시도·100% 완료 화면·갤러리와 3개 페이지 관찰. 다운로드 클릭에 따른 실제 PPTX GET 응답 HTTP 200도 확인했습니다.
- **UNVERIFIED:** OS 파일 선택기를 통한 실제 UI 업로드. 자동 UI 검증 도구의 안전 제어가 해당 조작을 거부해 완료하지 못했습니다. 다른 조작 수단으로 제어를 우회하지 않았으며, `test_upload_actual_bytes_and_reject_other_run_artifact`의 서버 PASS로 UI 업로드 완료를 주장하지 않습니다.
- **UNVERIFIED:** 브라우저가 내려받은 파일의 저장 위치와 모든 화면·해상도 조합. 실제 다운로드 응답을 로컬 저장 파일 확인으로 확대하지 않습니다.
- **UNVERIFIED:** Intel Mac, Rosetta의 혼합 Node/Python 아키텍처, Windows/Linux 전체 제작, Microsoft PowerPoint 앱에서의 최종 표시.
- **UNVERIFIED:** 실제 PowerPoint 편집 조작, 폰트의 설치·대체·포함 상태, 발표 소요 시간. G5의 실제 렌더 이미지 판독과 구분합니다.
- **PARTIALLY_VERIFIED:** Claude Code 2.1.263 베타. 미로그인 차단, 실제 프로토콜 초기화, 모의 회귀는 확인했습니다. 검증 환경에 Claude 구독이 없어 실제 구독 모델 실행과 전체 제작은 **UNVERIFIED**입니다. 추가 구매·계정 우회·API 과금 전환으로 대체하지 않았습니다.

### 배포와 라이선스 경계

후보의 파일 집합과 기술적 공개 범위는 [설치·배포 검증 기록](PACKAGING_CHECKS.md)에 수치와 해시로 기록합니다. 개인 실행 DB·원시 대화·인증 자료·비공개 Git 이력은 제품 ZIP에 포함하지 않습니다. `b3375d79d6d5`는 브랜드 파일 제외 전의 기술 검증 후보이며 최종 공개 배포본이 아닙니다. `244375d`의 NAVER/Jangpm 관련 19개 파일 제외·manifest 경계 회귀는 통과했습니다. 최종 ZIP의 파일 집합과 SHA는 해당 Release의 manifest·체크섬으로 식별하며 이전 후보의 수치를 옮겨 쓰지 않습니다.

사용자의 명시적 승인으로 제품 코드에 [MIT License](../../LICENSE)를 적용하기로 선택 완료했습니다. 출시 담당자가 GitHub API로 새 공개 저장소 생성을 확인했으므로 이 항목은 **VERIFIED**입니다. 문서 동결 시점의 코드 push·release·원격 다운로드 검증은 **UNVERIFIED**이며 실제 결과를 확인한 뒤 별도 발행 기록에 남깁니다. upstream MIT·글꼴 OFL·별도 Python 의존성 조건은 유지하며 모두 MIT로 재허가한 것으로 해석하지 않습니다. PyMuPDF와 브랜드 이미지의 확인 범위는 [제3자 고지](../../THIRD_PARTY_NOTICES.md)를 따릅니다. 이 기록은 법적 적합성 판단이 아닙니다.

후보 제출 시 사용자에게 다음 행동을 설명하는 prompt 변경 `3208f62`는 12개 조합의 정적 검사를 통과했습니다. 변경 후 실제 모델 메시지의 개선 효과는 **UNVERIFIED**이며 앞선 제작 완료 증거와 구분합니다.

### 근거와 재확인 명령

직접 대조한 로컬 근거: `v2-tests.log`, `npm-green.log`, `console-final.log`, `manifest-boundary-green.log`, `archive-mode-green.log`, `fresh-install.log`, `fresh-doctor.json`, `fresh-start.json`, `file-probe-result.json`, `codex-e2e-final.json`, 최종 PPTX·PNG·G4 receipt·G5 리뷰입니다. PPTX 내부 검사의 별도 근거는 `pptx-package-final.json`입니다. 원시 로그와 실행 식별자는 공개 배포에 넣지 않습니다. UI 시각 검사는 출시 담당자의 관찰로 구분합니다.

```sh
# Python 엔진 회귀
PYTHONPATH=projects/presentation-agent-suite/src .venv/bin/python -B -m unittest discover -s projects/presentation-agent-suite/tests -p 'test_v2_*.py' -v

# 제공자 계약만 확인
PYTHONPATH=projects/presentation-agent-suite/src .venv/bin/python -B -m unittest discover -s projects/presentation-agent-suite/tests -p 'test_v2_*provider*.py' -v

# 설치·배포 경계
.venv/bin/python -B -m unittest discover -s tests -p 'test_local_*.py' -v

# Console 회귀
node --test projects/presentation-agent-suite/console/tests/*.test.mjs
```

위 명령은 재확인 방법입니다. 이 문서를 갱신하며 통과한 전체 검사를 반복 실행하지 않았습니다. 최종 발행에서는 최신 변경을 포함한 검사·실제 제작 결과·최종 ZIP을 함께 고정해야 합니다.
