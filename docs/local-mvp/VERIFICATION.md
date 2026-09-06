# 로컬 MVP 검증 현황

2026-09-06 · 로컬 제작 검증 **COMPLETE · 100%**, 원격 발행 확인은 **UNVERIFIED**로 전체 출시 상태는 **PARTIAL**입니다. 실제 Codex 3장 제작의 G1–G5가 모두 VALID이며 최종 파일 검사와 이미지 검토를 통과했습니다. 제품 코드의 MIT 라이선스 선택은 완료됐습니다. 최종 ZIP·원격 발행·배포 다운로드 결과는 해당 GitHub Release와 출시 작업이력에 기록합니다.

## 증거 수준

읽어 확인한 원시 검사 로그·파일 해시와 출시 담당자의 실제 UI 관찰 결과를 구분합니다. 테스트 집계는 서로 다른 시점·대상의 실행이며 중복되는 테스트가 있으므로 합산하지 않습니다. Fixture와 mocked subprocess의 PASS를 실제 모델 실행이나 OS 화면 검증으로 대신하지 않습니다.

| 대상 | 확인 결과 | 검증의 경계 |
|---|---|---|
| v2 Python 회귀 | **VERIFIED** — 기존 전체 실행 206 tests, 79.090s, OK | 해당 실행 시점의 결과. 이후 provider·console·배포 변경을 모두 포함하는 최종 전체 검사로 확대하지 않음 |
| Codex·Claude·선택 registry | **VERIFIED** — 대상 46 tests, 7.979s, OK | npm helper 탐색·실패 차단, 세션·취소·질문·권한 처리의 fixture 회귀. 전체 실모델 제작과는 별개 |
| 설치·배포 | **VERIFIED** — 최신 원시 로그 21 tests, 1.029s, OK | 실제 Git tree→tar→ZIP·브랜드 제외·manifest 경계 포함. 설치 subprocess fixture와 실제 fresh 설치 증거는 분리 |
| Console | **VERIFIED** — 최종 원시 로그 67 tests / 67 pass / 0 fail / 0 skipped, 78.6795ms | JavaScript 회귀이며 실제 OS 파일 선택기·모든 화면 렌더의 증명은 아님 |
| 후보 ZIP·fresh 설치·실행 | **VERIFIED** — 동일 SHA 반복 생성, 독립 경로·개인자료 검사, 추출본의 새 `.venv`·Codex 진단·서비스 시작·HTTP 인증 경계·종료 성공 | `b3375d79d6d5` 기술 후보에 한정. 브랜드 제외와 이후 변경을 반영한 최종 공개 ZIP은 별도 검사 |

## 실제 Codex 실행과 세 단계 진행

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

## UI와 미검증 범위

- **VERIFIED:** 출시 담당자의 실제 UI 승인·진행률·재시도·100% 완료 화면·갤러리와 3개 페이지 관찰. 다운로드 클릭에 따른 실제 PPTX GET 응답 HTTP 200도 확인했습니다.
- **UNVERIFIED:** OS 파일 선택기를 통한 실제 UI 업로드. 자동 UI 검증 도구의 안전 제어가 해당 조작을 거부해 완료하지 못했습니다. 다른 조작 수단으로 제어를 우회하지 않았으며, `test_upload_actual_bytes_and_reject_other_run_artifact`의 서버 PASS로 UI 업로드 완료를 주장하지 않습니다.
- **UNVERIFIED:** 브라우저가 내려받은 파일의 저장 위치와 모든 화면·해상도 조합. 실제 다운로드 응답을 로컬 저장 파일 확인으로 확대하지 않습니다.
- **UNVERIFIED:** Intel Mac, Rosetta의 혼합 Node/Python 아키텍처, Windows/Linux 전체 제작, Microsoft PowerPoint 앱에서의 최종 표시.
- **UNVERIFIED:** 실제 PowerPoint 편집 조작, 폰트의 설치·대체·포함 상태, 발표 소요 시간. G5의 실제 렌더 이미지 판독과 구분합니다.
- **PARTIALLY_VERIFIED:** Claude Code 2.1.263 베타. 미로그인 차단, 실제 프로토콜 초기화, 모의 회귀는 확인했습니다. 검증 환경에 Claude 구독이 없어 실제 구독 모델 실행과 전체 제작은 **UNVERIFIED**입니다. 추가 구매·계정 우회·API 과금 전환으로 대체하지 않았습니다.

## 배포와 라이선스 경계

후보의 파일 집합과 기술적 공개 범위는 [설치·배포 검증 기록](PACKAGING_CHECKS.md)에 수치와 해시로 기록합니다. 개인 실행 DB·원시 대화·인증 자료·비공개 Git 이력은 제품 ZIP에 포함하지 않습니다. `b3375d79d6d5`는 브랜드 파일 제외 전의 기술 검증 후보이며 최종 공개 배포본이 아닙니다. `244375d`의 NAVER/Jangpm 관련 19개 파일 제외·manifest 경계 회귀는 통과했습니다. 최종 ZIP의 파일 집합과 SHA는 해당 Release의 manifest·체크섬으로 식별하며 이전 후보의 수치를 옮겨 쓰지 않습니다.

사용자의 명시적 승인으로 제품 코드에 [MIT License](../../LICENSE)를 적용하기로 선택 완료했습니다. 출시 담당자가 GitHub API로 새 공개 저장소 생성을 확인했으므로 이 항목은 **VERIFIED**입니다. 문서 동결 시점의 코드 push·release·원격 다운로드 검증은 **UNVERIFIED**이며 실제 결과를 확인한 뒤 별도 발행 기록에 남깁니다. upstream MIT·글꼴 OFL·별도 Python 의존성 조건은 유지하며 모두 MIT로 재허가한 것으로 해석하지 않습니다. PyMuPDF와 브랜드 이미지의 확인 범위는 [제3자 고지](../../THIRD_PARTY_NOTICES.md)를 따릅니다. 이 기록은 법적 적합성 판단이 아닙니다.

후보 제출 시 사용자에게 다음 행동을 설명하는 prompt 변경 `3208f62`는 12개 조합의 정적 검사를 통과했습니다. 변경 후 실제 모델 메시지의 개선 효과는 **UNVERIFIED**이며 앞선 제작 완료 증거와 구분합니다.

## 근거와 재확인 명령

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
